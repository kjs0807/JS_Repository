"""BBKCFilterPattern -- ML entry-approval filter for BBKCSqueeze.

This pattern wraps the squeeze-release bars where BBKCSqueeze would have
opened a position, so an XGBoost model can approve or reject each entry
via threshold without changing BBKCSqueeze's direction logic or TP/SL
targets.

PARITY CONTRACT (hard requirement, enforced by test_bbkc_filter.py):
    For any MTFData primary series, the set of bars where
    ``detect_at`` returns a non-None PatternEvent MUST equal exactly
    the set of bars where ``BBKCSqueeze.on_bar_fast`` would have
    called ``broker.buy`` / ``broker.sell`` given an empty position
    state. ``metadata["direction"]`` must match the side of that
    call.

Why this contract matters: the label supplied to the ML model is the
outcome of BBKCSqueeze's own pct TP/SL exit. If the pattern ever emits
an event at a bar BBKCSqueeze wouldn't have traded, the label reflects
an entry that was never actually taken at deployment and the model
learns a distribution that doesn't exist at runtime.

OFFICIAL SUPPORT: primary_tf="1h" only. BBKCSqueeze itself is only
validated at 1h. The ``_is_higher_tf`` guard on ``h4_*`` features
exists so the primary_tf="4h" regression test can assert zero-fill,
but production runs should use 1h.

POSITION STATE: BBKCSqueeze skips new entries when a position is
already open (``if pos is not None: return``). This pattern does NOT
replicate that check -- the state lives in PatternMLFilterStrategy
(``self._open``) and is applied at execution time. detect_at remains
stateless so validator.evaluate_holdout and no-lookahead tests work
without extra plumbing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np

from src.core.types import BarSeries
from src.ml.helpers.indicators_mtf import (
    compute_adx,
    compute_atr,
    compute_bb_width,
    compute_ema,
    compute_percentile_rank,
)
from src.ml.helpers.location import rolling_nbar_extremes
from src.ml.helpers.mtf_align import get_confirmed
from src.ml.patterns.base import BasePattern
from src.ml.types import MTFData, PatternEvent
from src.strategies.indicators.momentum import bollinger, keltner
from src.strategies.indicators.oscillator import rsi as rsi_indicator

_EPS = 1e-12

# NOTE: duplicated from rsi_divergence.py / engulfing_mtf.py on purpose.
# If a fourth pattern needs this, refactor to src/ml/helpers/tf_order.py.
_TF_ORDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]


def _is_higher_tf(target: str, primary: str) -> bool:
    if target not in _TF_ORDER or primary not in _TF_ORDER:
        return False
    return _TF_ORDER.index(target) > _TF_ORDER.index(primary)


class BBKCFilterPattern(BasePattern):
    """ML entry-approval filter that mirrors BBKCSqueeze's entry conditions."""

    name = "bbkc_filter"
    version = "1.0.0"
    timeframes = ["1h", "4h", "1d"]
    direction = "both"

    def __init__(
        self,
        # --- BBKCSqueeze parity knobs (must match src/strategies/bbkc_squeeze.py defaults) ---
        bb_period: int = 20,
        bb_std: float = 1.5,
        kc_period: int = 20,
        kc_atr_period: int = 14,
        kc_mult: float = 1.0,
        rsi_period: int = 14,
        rsi_filter: float = 70.0,
        # --- ML feature knobs ---
        atr_period: int = 14,
        percentile_lookback: int = 100,
        htf_ema_period: int = 20,
        htf_slope_lookback: int = 5,
        rolling_location_n: int = 20,
        warmup_cushion: int = 5,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_atr_period = kc_atr_period
        self.kc_mult = kc_mult
        self.rsi_period = rsi_period
        self.rsi_filter = rsi_filter
        self.atr_period = atr_period
        self.percentile_lookback = percentile_lookback
        self.htf_ema_period = htf_ema_period
        self.htf_slope_lookback = htf_slope_lookback
        self.rolling_location_n = rolling_location_n
        self.warmup_cushion = warmup_cushion

        # Caches keyed by id(primary_series). Fresh instance per symbol in
        # run_pipeline/backtest prevents cross-symbol id collisions.
        self._bb_cache: Dict[int, Any] = {}
        self._kc_cache: Dict[int, Any] = {}
        self._rsi_cache: Dict[int, np.ndarray] = {}
        self._squeeze_cache: Dict[int, np.ndarray] = {}
        self._squeeze_duration_cache: Dict[int, np.ndarray] = {}
        self._atr_cache: Dict[int, np.ndarray] = {}
        self._atr_norm_cache: Dict[int, np.ndarray] = {}
        self._atr_norm_pct_cache: Dict[int, np.ndarray] = {}
        self._bb_width_pct_cache: Dict[int, np.ndarray] = {}
        self._adx_cache: Dict[int, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._roll_high_cache: Dict[int, np.ndarray] = {}
        self._roll_low_cache: Dict[int, np.ndarray] = {}
        self._htf_ema_cache: Dict[int, np.ndarray] = {}

    @property
    def warmup_bars(self) -> int:
        """Minimum primary-TF bars before detect_at can fire.

        Matches BBKCSqueeze.warmup_bars (max of BB/KC/RSI/ATR periods + 10)
        plus the regime/location windows the ML features need. 100-bar
        percentile_lookback dominates in practice.
        """
        squeeze_min = (
            max(self.bb_period, self.kc_period, self.kc_atr_period, self.rsi_period)
            + 10
        )
        regime_min = self.percentile_lookback + self.atr_period
        loc_min = self.rolling_location_n
        return max(squeeze_min, regime_min, loc_min) + self.warmup_cushion

    # ------------------------------------------------------------------
    # Indicator accessors (cached per primary series identity)
    # ------------------------------------------------------------------

    def _get_bb(self, primary: BarSeries):
        key = id(primary)
        if key not in self._bb_cache:
            self._bb_cache[key] = bollinger(
                primary, period=self.bb_period, std=self.bb_std,
            )
        return self._bb_cache[key]

    def _get_kc(self, primary: BarSeries):
        key = id(primary)
        if key not in self._kc_cache:
            self._kc_cache[key] = keltner(
                primary,
                ema_period=self.kc_period,
                atr_period=self.kc_atr_period,
                atr_mult=self.kc_mult,
            )
        return self._kc_cache[key]

    def _get_rsi(self, primary: BarSeries) -> np.ndarray:
        key = id(primary)
        if key not in self._rsi_cache:
            self._rsi_cache[key] = rsi_indicator(
                primary, period=self.rsi_period,
            ).values
        return self._rsi_cache[key]

    def _get_squeeze_on(self, primary: BarSeries) -> np.ndarray:
        key = id(primary)
        if key not in self._squeeze_cache:
            bb = self._get_bb(primary)
            kc = self._get_kc(primary)
            on = ((bb.upper < kc.upper) & (bb.lower > kc.lower)).astype(float)
            self._squeeze_cache[key] = on
        return self._squeeze_cache[key]

    def _get_squeeze_duration(self, primary: BarSeries) -> np.ndarray:
        """For each bar i, consecutive bars ending at i where squeeze_on == 1.

        At the release bar (squeeze_on[i] == 0, squeeze_on[i-1] == 1),
        ``duration[i-1]`` gives the length of the squeeze that just ended.
        ``duration[i]`` itself is 0 at release.
        """
        key = id(primary)
        if key not in self._squeeze_duration_cache:
            on = self._get_squeeze_on(primary)
            n = len(on)
            dur = np.zeros(n, dtype=float)
            run = 0.0
            for i in range(n):
                v = on[i]
                if v != v:  # NaN check
                    run = 0.0
                elif v >= 1.0:
                    run += 1.0
                else:
                    run = 0.0
                dur[i] = run
            self._squeeze_duration_cache[key] = dur
        return self._squeeze_duration_cache[key]

    def _get_atr(self, primary: BarSeries) -> np.ndarray:
        key = id(primary)
        if key not in self._atr_cache:
            self._atr_cache[key] = compute_atr(primary, period=self.atr_period)
        return self._atr_cache[key]

    def _get_atr_norm(self, primary: BarSeries) -> np.ndarray:
        key = id(primary)
        if key not in self._atr_norm_cache:
            atr = self._get_atr(primary)
            close = primary.bars["close"].to_numpy()
            denom = np.where(np.abs(close) < _EPS, _EPS, close)
            self._atr_norm_cache[key] = atr / denom
        return self._atr_norm_cache[key]

    def _get_atr_norm_pct(self, primary: BarSeries) -> np.ndarray:
        key = id(primary)
        if key not in self._atr_norm_pct_cache:
            atr_norm = self._get_atr_norm(primary)
            self._atr_norm_pct_cache[key] = compute_percentile_rank(
                atr_norm, lookback=self.percentile_lookback,
            )
        return self._atr_norm_pct_cache[key]

    def _get_bb_width_pct(self, primary: BarSeries) -> np.ndarray:
        key = id(primary)
        if key not in self._bb_width_pct_cache:
            # Intentional: std=2.0 here differs from BBKCSqueeze's
            # bb_std=1.5 used in _get_bb for event detection. This
            # feature is the generic "volatility compression percentile"
            # regime indicator (standard BB with 2-sigma), NOT a linear
            # transform of the detection BB. Keeping it on the standard
            # 2-sigma width gives the model a regime signal that is
            # independent of the strategy's squeeze definition.
            bw = compute_bb_width(primary, period=self.bb_period, std=2.0)
            self._bb_width_pct_cache[key] = compute_percentile_rank(
                bw, lookback=self.percentile_lookback,
            )
        return self._bb_width_pct_cache[key]

    def _get_adx(
        self, primary: BarSeries
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        key = id(primary)
        if key not in self._adx_cache:
            self._adx_cache[key] = compute_adx(primary, period=self.atr_period)
        return self._adx_cache[key]

    def _get_rolling_extremes(
        self, primary: BarSeries
    ) -> Tuple[np.ndarray, np.ndarray]:
        key = id(primary)
        if key not in self._roll_high_cache:
            rh, rl = rolling_nbar_extremes(primary, n=self.rolling_location_n)
            self._roll_high_cache[key] = rh
            self._roll_low_cache[key] = rl
        return self._roll_high_cache[key], self._roll_low_cache[key]

    def _get_htf_ema(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._htf_ema_cache:
            self._htf_ema_cache[key] = compute_ema(
                series, period=self.htf_ema_period,
            )
        return self._htf_ema_cache[key]

    # ------------------------------------------------------------------
    # BasePattern interface
    # ------------------------------------------------------------------

    def detect_at(self, mtf: MTFData, i: int) -> Optional[PatternEvent]:
        """Parity with BBKCSqueeze.on_bar_fast entry logic.

        Replicates bbkc_squeeze.py:69-114 step-by-step:
          1. i >= 1 (need prev bar for squeeze edge detection)
          2. squeeze_prev >= 1.0 and squeeze_now < 1.0  (release edge)
          3. close > bb_mid and rsi < rsi_filter  -> long
             close < bb_mid and rsi > 100 - rsi_filter -> short
             else -> no event
        """
        if i < self.warmup_bars:
            return None
        if i < 1:
            return None
        primary = mtf.get_primary()
        if i >= len(primary):
            return None

        squeeze_on = self._get_squeeze_on(primary)
        squeeze_now = squeeze_on[i]
        squeeze_prev = squeeze_on[i - 1]
        if np.isnan(squeeze_now) or np.isnan(squeeze_prev):
            return None
        if not (squeeze_prev >= 1.0 and squeeze_now < 1.0):
            return None

        bb = self._get_bb(primary)
        rsi_arr = self._get_rsi(primary)
        bb_mid_i = bb.mid[i]
        rsi_i = rsi_arr[i]
        if np.isnan(bb_mid_i) or np.isnan(rsi_i):
            return None

        close = float(primary.bars["close"].iloc[i])
        if close > bb_mid_i and rsi_i < self.rsi_filter:
            direction = "long"
        elif close < bb_mid_i and rsi_i > (100.0 - self.rsi_filter):
            direction = "short"
        else:
            return None

        ts = int(primary.bars["timestamp"].iloc[i])
        return PatternEvent(
            timestamp_ms=ts,
            bar_index=i,
            symbol=mtf.symbol,
            direction=direction,
            metadata={
                "squeeze_release": True,
                "rsi_at_breakout": float(rsi_i),
                "close_vs_bb_mid": float(close - float(bb_mid_i)),
            },
        )

    def extract_features(
        self, event: PatternEvent, mtf: MTFData,
    ) -> Dict[str, float]:
        primary = mtf.get_primary()
        i = event.bar_index

        bb = self._get_bb(primary)
        kc = self._get_kc(primary)
        atr = self._get_atr(primary)
        atr_norm_pct = self._get_atr_norm_pct(primary)
        bb_width_pct = self._get_bb_width_pct(primary)
        adx, _plus, _minus = self._get_adx(primary)
        roll_high, roll_low = self._get_rolling_extremes(primary)
        duration = self._get_squeeze_duration(primary)

        close = float(primary.bars["close"].iloc[i])
        ts = int(primary.bars["timestamp"].iloc[i])

        atr_raw = (
            float(atr[i])
            if i < len(atr) and not np.isnan(atr[i]) and atr[i] > 0
            else 0.0
        )
        atr_denom = atr_raw if atr_raw > 0 else _EPS

        # Length of the squeeze that just ended. At the release bar i,
        # duration[i] == 0 (squeeze_on[i] == 0); duration[i-1] is the
        # length of the run that was just broken.
        sq_dur = float(duration[i - 1]) if i >= 1 else 0.0

        bb_upper_i = float(bb.upper[i]) if not np.isnan(bb.upper[i]) else close
        bb_lower_i = float(bb.lower[i]) if not np.isnan(bb.lower[i]) else close
        kc_upper_i = float(kc.upper[i]) if not np.isnan(kc.upper[i]) else close
        kc_lower_i = float(kc.lower[i]) if not np.isnan(kc.lower[i]) else close
        bb_width = max(bb_upper_i - bb_lower_i, _EPS)
        kc_width = max(kc_upper_i - kc_lower_i, _EPS)
        width_ratio = bb_width / kc_width

        bb_mid_i = (
            float(bb.mid[i]) if not np.isnan(bb.mid[i]) else close
        )
        breakout_magnitude_atr = (close - bb_mid_i) / atr_denom

        rh_i = (
            float(roll_high[i])
            if i < len(roll_high) and not np.isnan(roll_high[i])
            else float("nan")
        )
        rl_i = (
            float(roll_low[i])
            if i < len(roll_low) and not np.isnan(roll_low[i])
            else float("nan")
        )

        def _dist(value: float, base: float, sign: int) -> float:
            if not np.isfinite(base):
                return 0.0
            return float(sign * (value - base) / atr_denom)

        dist_roll_high = _dist(close, rh_i, +1)
        dist_roll_low = _dist(close, rl_i, -1)

        # HTF features: 1h is the officially supported primary_tf. The
        # _is_higher_tf guard zero-fills h4_* when primary_tf >= 4h so the
        # regression test for primary=4h can verify no self-reference.
        primary_tf = mtf.primary_tf
        if _is_higher_tf("4h", primary_tf) and "4h" in mtf.series:
            h4_slope, h4_align = self._htf_slope_align(
                mtf, ts, "4h", close, atr_denom,
            )
        else:
            h4_slope, h4_align = 0.0, 0.0

        feats: Dict[str, float] = {
            # Squeeze quality (3)
            "squeeze_duration_bars": sq_dur,
            "bb_kc_width_ratio": float(width_ratio),
            "breakout_magnitude_atr": (
                float(breakout_magnitude_atr)
                if np.isfinite(breakout_magnitude_atr)
                else 0.0
            ),
            # Volatility regime (2)
            "atr_primary_pct": (
                float(atr_norm_pct[i])
                if i < len(atr_norm_pct) and not np.isnan(atr_norm_pct[i])
                else 0.0
            ),
            "bb_width_pct_primary": (
                float(bb_width_pct[i])
                if i < len(bb_width_pct) and not np.isnan(bb_width_pct[i])
                else 0.0
            ),
            # Trend regime (1)
            "adx_primary": (
                float(adx[i])
                if i < len(adx) and not np.isnan(adx[i])
                else 0.0
            ),
            # Location (2)
            "dist_roll_high_atr": dist_roll_high,
            "dist_roll_low_atr": dist_roll_low,
            # HTF context (2) -- gated 1h-only in production
            "h4_ema_slope_atr_norm": h4_slope,
            "h4_trend_alignment": h4_align,
            # Meta (1)
            "is_long": 1.0 if event.direction == "long" else 0.0,
        }
        return feats

    def _htf_slope_align(
        self,
        mtf: MTFData,
        ts: int,
        tf: str,
        entry_close: float,
        atr_denom: float,
    ) -> Tuple[float, float]:
        """ATR-normalized EMA slope + 1h-close-vs-HTF-EMA alignment bit.

        Uses only the most recent confirmed HTF bar (strict lookahead safety
        via get_confirmed).
        """
        row = get_confirmed(ts, tf, mtf)
        if row is None:
            return 0.0, 0.0
        series = mtf.series[tf]
        ema = self._get_htf_ema(series)
        ts_col = series.bars["timestamp"].values
        matches = np.where(ts_col == int(row["timestamp"]))[0]
        if len(matches) == 0:
            return 0.0, 0.0
        pos = int(matches[0])
        if pos >= len(ema) or np.isnan(ema[pos]):
            return 0.0, 0.0
        ema_val = float(ema[pos])
        if (
            pos >= self.htf_slope_lookback
            and not np.isnan(ema[pos - self.htf_slope_lookback])
        ):
            slope = (
                (ema_val - float(ema[pos - self.htf_slope_lookback]))
                / max(atr_denom, _EPS)
            )
        else:
            slope = 0.0
        align = 1.0 if entry_close > ema_val else 0.0
        return float(slope), float(align)


__all__ = ["BBKCFilterPattern"]
