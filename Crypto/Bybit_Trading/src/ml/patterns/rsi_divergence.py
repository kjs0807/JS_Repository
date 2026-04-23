"""RSI Divergence pattern (bull/bear).

Detects regular and hidden RSI divergences on confirmed pivots.
Feature set (~28 columns) spans divergence magnitude, type one-hot,
primary-TF regime, HTF context (only TFs strictly higher than primary), and
bookkeeping. All features are NaN-safe (NaN → 0.0).

Naming convention: regime features are suffixed ``_primary`` — they are
computed on whatever timeframe the caller sets as ``mtf.primary_tf`` (1h,
4h, or 1d). HTF features keep the ``h4_`` / ``d1_`` prefix and are zero
when the referenced TF is not strictly higher than primary.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np

from src.ml.helpers.divergence import detect_divergence, DivergenceInfo
from src.ml.helpers.indicators_mtf import (
    compute_rsi,
    compute_atr,
    compute_ema,
    compute_adx,
    compute_bb_width,
    compute_percentile_rank,
)
from src.ml.helpers.location import (
    rolling_nbar_extremes,
    confirmed_swing_highs_lows,
)
from src.ml.helpers.mtf_align import get_confirmed
from src.ml.patterns.base import BasePattern
from src.ml.types import MTFData, PatternEvent
from src.core.types import BarSeries

_EPS = 1e-12

# Timeframe ordering for "strictly higher than primary" gating on HTF features.
_TF_ORDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]


def _is_higher_tf(target: str, primary: str) -> bool:
    """Return True iff ``target`` is strictly a higher timeframe than ``primary``."""
    if target not in _TF_ORDER or primary not in _TF_ORDER:
        return False
    return _TF_ORDER.index(target) > _TF_ORDER.index(primary)


def _safe(v: float) -> float:
    """Return 0.0 if v is NaN or non-finite, else v."""
    if v != v or not (v - v == 0):  # NaN check + inf check
        return 0.0
    return v


def _build_metadata(info: DivergenceInfo, confirm_index: int) -> Dict[str, Any]:
    """Build the locked metadata schema dict from a DivergenceInfo and confirm bar index.

    ``divergence_strength`` is the original "magnitude of slope mismatch" —
    ``|price_slope - rsi_slope|`` — which is what the v1 reports and the
    locked downstream schema have always meant. ``slope_divergence_ratio``
    (price_slope / rsi_slope, eps-protected and clipped) is exposed as a
    separate field so consumers can use whichever they prefer without
    overloading the same name.
    """
    return {
        # --- locked schema (T1 tests assert these) ---
        "div_type": info.div_type,
        "pivot_bar_index": int(info.second_pivot_idx),
        "confirm_bar_index": int(confirm_index),
        "first_pivot_idx": int(info.first_pivot_idx),
        "second_pivot_idx": int(info.second_pivot_idx),
        "divergence_strength": float(info.strength),  # |price_slope − rsi_slope|
        # --- extra fields for extract_features ---
        "slope_divergence_ratio": float(info.slope_divergence_ratio),
        "price_slope": float(info.price_slope),
        "rsi_slope": float(info.rsi_slope),
        "price_diff_abs": float(info.price_diff_abs),
        "rsi_diff_abs": float(info.rsi_diff_abs),
        "pivot_distance_bars": int(info.pivot_distance_bars),
        "pivot_prominence": float(info.pivot_prominence),
        "intervening_retracement_ratio": float(info.intervening_retracement_ratio),
        "confirmation_lag": float(info.pivot_confirmation_lag),
    }


def _confirmed_bar_position(
    mtf: MTFData, ts: int, tf: str
) -> Tuple[Optional[object], int]:
    """Return (row, int_position) for the most recent confirmed bar on `tf`,
    or (None, -1) if none exists."""
    row = get_confirmed(ts, tf, mtf)
    if row is None:
        return None, -1
    tf_series = mtf.series[tf]
    ts_col = tf_series.bars["timestamp"]
    matches = np.where(ts_col.values == int(row["timestamp"]))[0]
    if len(matches) == 0:
        return row, -1
    return row, int(matches[0])


class RSIDivergence(BasePattern):
    name = "rsi_divergence"
    version = "1.0.0"
    timeframes = ["1h", "4h", "1d"]
    direction = "both"

    def __init__(
        self,
        rsi_period: int = 14,
        lookback: int = 30,
        confirmation_bars: int = 3,
        atr_period: int = 14,
        bb_period: int = 20,
        # 100 hourly bars ≈ 4 days, enough for stable percentile rank without
        # forcing an excessively long warmup. Use a larger value if you have
        # plenty of history and want a longer-memory regime feature.
        percentile_lookback: int = 100,
        htf_ema_period: int = 20,
        htf_rsi_period: int = 14,
        htf_slope_lookback: int = 5,
        rolling_location_n: int = 20,
        warmup_cushion: int = 5,
    ):
        self.rsi_period = rsi_period
        self.lookback = lookback
        self.confirmation_bars = confirmation_bars
        self.atr_period = atr_period
        self.bb_period = bb_period
        self.percentile_lookback = percentile_lookback
        self.htf_ema_period = htf_ema_period
        self.htf_rsi_period = htf_rsi_period
        self.htf_slope_lookback = htf_slope_lookback
        self.rolling_location_n = rolling_location_n
        self.warmup_cushion = warmup_cushion

        # --- primary-TF caches (keyed by id(primary_series)) ---
        self._rsi_cache: Dict[int, np.ndarray] = {}
        self._atr_cache: Dict[int, np.ndarray] = {}
        self._ema_cache: Dict[int, np.ndarray] = {}
        self._adx_cache: Dict[int, np.ndarray] = {}
        self._plus_di_cache: Dict[int, np.ndarray] = {}
        self._minus_di_cache: Dict[int, np.ndarray] = {}
        self._bb_width_cache: Dict[int, np.ndarray] = {}
        self._bb_width_pct_cache: Dict[int, np.ndarray] = {}
        self._atr_norm_cache: Dict[int, np.ndarray] = {}
        self._atr_norm_pct_cache: Dict[int, np.ndarray] = {}
        # Location caches (all keyed by id(primary_series))
        self._roll_high_cache: Dict[int, np.ndarray] = {}
        self._roll_low_cache: Dict[int, np.ndarray] = {}
        self._swing_high_cache: Dict[int, np.ndarray] = {}
        self._swing_low_cache: Dict[int, np.ndarray] = {}

        # --- HTF caches (keyed by id(tf_series)) ---
        self._htf_ema_cache: Dict[int, np.ndarray] = {}
        self._htf_rsi_cache: Dict[int, np.ndarray] = {}

    @property
    def warmup_bars(self) -> int:
        """Minimum primary-TF bars required before detect_at can fire.

        Computed from the configured periods so the value stays consistent
        if the constructor params are changed (e.g. wider lookback or a
        bigger percentile window):
            - divergence: rsi_period + lookback + confirmation_bars
            - regime:     percentile_lookback + atr_period
            - bollinger:  bb_period
            - location:   rolling_location_n
        Plus a small cushion so RSI/ATR have a few non-NaN samples to work with.
        """
        divergence_min = self.rsi_period + self.lookback + self.confirmation_bars
        regime_min = self.percentile_lookback + self.atr_period
        bb_min = self.bb_period
        loc_min = self.rolling_location_n
        return max(divergence_min, regime_min, bb_min, loc_min) + self.warmup_cushion

    # ------------------------------------------------------------------
    # primary-TF cached indicator accessors
    # ------------------------------------------------------------------

    def _get_rsi_primary(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._rsi_cache:
            self._rsi_cache[key] = compute_rsi(series, period=self.rsi_period)
        return self._rsi_cache[key]

    def _get_atr_primary(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._atr_cache:
            self._atr_cache[key] = compute_atr(series, period=self.atr_period)
        return self._atr_cache[key]

    def _get_ema_50_primary(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._ema_cache:
            self._ema_cache[key] = compute_ema(series, period=50)
        return self._ema_cache[key]

    def _get_adx_primary(self, series: BarSeries) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        key = id(series)
        if key not in self._adx_cache:
            adx, plus_di, minus_di = compute_adx(series, period=self.atr_period)
            self._adx_cache[key] = adx
            self._plus_di_cache[key] = plus_di
            self._minus_di_cache[key] = minus_di
        return self._adx_cache[key], self._plus_di_cache[key], self._minus_di_cache[key]

    def _get_bb_width_primary(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._bb_width_cache:
            self._bb_width_cache[key] = compute_bb_width(
                series, period=self.bb_period, std=2.0
            )
        return self._bb_width_cache[key]

    def _get_bb_width_pct_primary(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._bb_width_pct_cache:
            bw = self._get_bb_width_primary(series)
            self._bb_width_pct_cache[key] = compute_percentile_rank(
                bw, lookback=self.percentile_lookback
            )
        return self._bb_width_pct_cache[key]

    def _get_atr_norm_primary(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._atr_norm_cache:
            atr = self._get_atr_primary(series)
            close = series.bars["close"].to_numpy()
            denom = np.where(np.abs(close) < _EPS, _EPS, close)
            self._atr_norm_cache[key] = atr / denom
        return self._atr_norm_cache[key]

    def _get_atr_norm_pct_primary(self, series: BarSeries) -> np.ndarray:
        key = id(series)
        if key not in self._atr_norm_pct_cache:
            atr_norm = self._get_atr_norm_primary(series)
            self._atr_norm_pct_cache[key] = compute_percentile_rank(
                atr_norm, lookback=self.percentile_lookback
            )
        return self._atr_norm_pct_cache[key]

    # ------------------------------------------------------------------
    # Location cached accessors (rolling N-bar extremes + confirmed swings)
    # ------------------------------------------------------------------

    def _get_rolling_extremes(self, series: BarSeries) -> Tuple[np.ndarray, np.ndarray]:
        key = id(series)
        if key not in self._roll_high_cache:
            rh, rl = rolling_nbar_extremes(series, n=self.rolling_location_n)
            self._roll_high_cache[key] = rh
            self._roll_low_cache[key] = rl
        return self._roll_high_cache[key], self._roll_low_cache[key]

    def _get_swing_extremes(self, series: BarSeries) -> Tuple[np.ndarray, np.ndarray]:
        key = id(series)
        if key not in self._swing_high_cache:
            sh, sl = confirmed_swing_highs_lows(
                series, confirmation_bars=self.confirmation_bars
            )
            self._swing_high_cache[key] = sh
            self._swing_low_cache[key] = sl
        return self._swing_high_cache[key], self._swing_low_cache[key]

    # ------------------------------------------------------------------
    # HTF cached indicator accessors
    # ------------------------------------------------------------------

    def _get_tf_ema(self, series: BarSeries, period: int = 20) -> np.ndarray:
        key = id(series)
        if key not in self._htf_ema_cache:
            self._htf_ema_cache[key] = compute_ema(series, period=period)
        return self._htf_ema_cache[key]

    def _get_tf_rsi(self, series: BarSeries, period: int = 14) -> np.ndarray:
        key = id(series)
        if key not in self._htf_rsi_cache:
            self._htf_rsi_cache[key] = compute_rsi(series, period=period)
        return self._htf_rsi_cache[key]

    # ------------------------------------------------------------------
    # HTF feature computation
    # ------------------------------------------------------------------

    def _htf_slope_atr_norm(
        self,
        mtf: MTFData,
        ts: int,
        tf: str,
        entry_close: float,
        entry_atr: float,
    ) -> Tuple[float, float, float]:
        """Return (ema_slope_atr_norm, rsi_value, trend_alignment) at the confirmed
        higher-TF bar.

        - ``ema_slope_atr_norm`` is the EMA slope over ``htf_slope_lookback`` bars,
          divided by the *primary-TF ATR at the event bar*. Using ATR instead of
          entry_close gives a regime-invariant magnitude.
        - ``trend_alignment`` is a cross-TF check: ``1.0`` iff the *primary-TF
          close at the event bar* is above the higher-TF EMA at the most recent
          confirmed higher-TF bar. This is the "primary close vs HTF EMA"
          alignment we agreed on, not "htf close vs htf EMA".
        """
        row, pos = _confirmed_bar_position(mtf, ts, tf)
        if row is None or pos < 0:
            return 0.0, 0.0, 0.0
        series = mtf.series[tf]
        ema = self._get_tf_ema(series, period=self.htf_ema_period)
        rsi = self._get_tf_rsi(series, period=self.htf_rsi_period)

        rsi_val = float(rsi[pos]) if pos < len(rsi) and not np.isnan(rsi[pos]) else 0.0
        ema_val: Optional[float] = None
        if pos < len(ema) and not np.isnan(ema[pos]):
            ema_val = float(ema[pos])

        slope_lookback = self.htf_slope_lookback
        denom = max(float(entry_atr), _EPS)
        if (
            pos >= slope_lookback
            and ema_val is not None
            and not np.isnan(ema[pos - slope_lookback])
        ):
            slope_atr = (ema_val - float(ema[pos - slope_lookback])) / denom
        else:
            slope_atr = 0.0

        # Cross-TF alignment: primary close vs HTF EMA at the confirmed bar
        alignment = (
            1.0 if ema_val is not None and float(entry_close) > ema_val else 0.0
        )
        return _safe(float(slope_atr)), _safe(float(rsi_val)), _safe(float(alignment))

    # ------------------------------------------------------------------
    # BasePattern interface
    # ------------------------------------------------------------------

    def detect_at(self, mtf: MTFData, i: int) -> Optional[PatternEvent]:
        if i < self.warmup_bars:
            return None
        primary = mtf.get_primary()
        if i >= len(primary):
            return None
        rsi = self._get_rsi_primary(primary)
        if np.isnan(rsi[i]):
            return None
        price = primary.bars["close"].to_numpy()

        bull = detect_divergence(
            price=price, indicator=rsi, end_index=i,
            mode="bull", lookback=self.lookback,
            confirmation_bars=self.confirmation_bars,
        )
        if bull is not None:
            ts = int(primary.bars["timestamp"].iloc[i])
            return PatternEvent(
                timestamp_ms=ts,
                bar_index=i,
                symbol=mtf.symbol,
                direction="long",
                metadata=_build_metadata(bull, i),
            )

        bear = detect_divergence(
            price=price, indicator=rsi, end_index=i,
            mode="bear", lookback=self.lookback,
            confirmation_bars=self.confirmation_bars,
        )
        if bear is not None:
            ts = int(primary.bars["timestamp"].iloc[i])
            return PatternEvent(
                timestamp_ms=ts,
                bar_index=i,
                symbol=mtf.symbol,
                direction="short",
                metadata=_build_metadata(bear, i),
            )
        return None

    def extract_features(self, event: PatternEvent, mtf: MTFData) -> Dict[str, float]:
        primary = mtf.get_primary()
        i = event.bar_index

        # --- Pull cached primary-TF indicator arrays ---
        rsi = self._get_rsi_primary(primary)
        atr_raw = self._get_atr_primary(primary)
        atr_norm = self._get_atr_norm_primary(primary)
        atr_norm_pct = self._get_atr_norm_pct_primary(primary)
        adx, plus_di, minus_di = self._get_adx_primary(primary)
        bb_width = self._get_bb_width_primary(primary)
        bb_width_pct = self._get_bb_width_pct_primary(primary)
        roll_high, roll_low = self._get_rolling_extremes(primary)
        swing_high, swing_low = self._get_swing_extremes(primary)

        row = primary.bars.iloc[i]
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        open_ = float(row["open"])
        ts = int(row["timestamp"])

        # ATR at the event bar — used to volatility-normalize slope features
        atr_at_event_raw = (
            float(atr_raw[i])
            if i < len(atr_raw) and not np.isnan(atr_raw[i]) and atr_raw[i] > 0
            else 0.0
        )
        atr_denom = atr_at_event_raw if atr_at_event_raw > 0 else _EPS

        # --- Divergence info from metadata ---
        meta = event.metadata
        div_type: str = meta.get("div_type", "")
        price_slope_raw = float(meta.get("price_slope", 0.0))

        # --- HTF features (ATR-normalized slope, primary-vs-HTF alignment) ---
        # Only compute HTF features for TFs strictly higher than the current
        # primary. When running with primary="4h" the h4_* features become
        # self-reference, so we zero them out. For primary="1d" both are zero.
        primary_tf = mtf.primary_tf
        if _is_higher_tf("4h", primary_tf) and "4h" in mtf.series:
            h4_slope, h4_rsi, h4_align = self._htf_slope_atr_norm(
                mtf, ts, "4h", close, atr_at_event_raw
            )
        else:
            h4_slope, h4_rsi, h4_align = 0.0, 0.0, 0.0
        if _is_higher_tf("1d", primary_tf) and "1d" in mtf.series:
            d1_slope, d1_rsi, d1_align = self._htf_slope_atr_norm(
                mtf, ts, "1d", close, atr_at_event_raw
            )
        else:
            d1_slope, d1_rsi, d1_align = 0.0, 0.0, 0.0

        # --- Location features (ATR-normalized; positive = distance in
        #     the "outside" direction). All use bars strictly before i. ---
        def _dist(value: float, base: float, sign: int) -> float:
            """Signed ATR-normalized distance: sign * (value - base) / atr."""
            if not np.isfinite(value):
                return 0.0
            return float(sign * (value - base) / atr_denom)

        rh_i = float(roll_high[i]) if i < len(roll_high) and not np.isnan(roll_high[i]) else float("nan")
        rl_i = float(roll_low[i])  if i < len(roll_low)  and not np.isnan(roll_low[i])  else float("nan")
        sh_i = float(swing_high[i]) if i < len(swing_high) and not np.isnan(swing_high[i]) else float("nan")
        sl_i = float(swing_low[i])  if i < len(swing_low)  and not np.isnan(swing_low[i])  else float("nan")

        # "distance above rolling/swing high" and "distance below rolling/swing low"
        # are positive when price has broken through the level, negative when
        # still inside the range. This is a signed ATR-measured location.
        dist_roll_high = _dist(close, rh_i, +1)   # > 0 if close above rolling high
        dist_roll_low = _dist(close, rl_i, -1)    # > 0 if close below rolling low
        dist_swing_high = _dist(close, sh_i, +1)  # > 0 if close above prev swing high
        dist_swing_low = _dist(close, sl_i, -1)   # > 0 if close below prev swing low

        feats: Dict[str, float] = {
            # --- Divergence magnitude (raw + ATR-normalized) ---
            "rsi_primary": _safe(float(rsi[i]) if not np.isnan(rsi[i]) else 0.0),
            "divergence_strength": _safe(float(meta.get("divergence_strength", 0.0))),
            "slope_divergence_ratio": _safe(
                float(meta.get("slope_divergence_ratio", 0.0))
            ),
            "price_slope": _safe(price_slope_raw),
            "price_slope_atr_norm": _safe(price_slope_raw / atr_denom),
            "rsi_slope": _safe(float(meta.get("rsi_slope", 0.0))),
            "price_diff_abs": _safe(float(meta.get("price_diff_abs", 0.0))),
            "rsi_diff_abs": _safe(float(meta.get("rsi_diff_abs", 0.0))),
            "pivot_distance_bars": _safe(float(meta.get("pivot_distance_bars", 0))),
            "pivot_prominence": _safe(float(meta.get("pivot_prominence", 0.0))),
            "intervening_retracement_ratio": _safe(
                float(meta.get("intervening_retracement_ratio", 0.0))
            ),
            # --- Divergence type one-hot ---
            "dt_regular_bull": 1.0 if div_type == "regular_bull" else 0.0,
            "dt_regular_bear": 1.0 if div_type == "regular_bear" else 0.0,
            "dt_hidden_bull": 1.0 if div_type == "hidden_bull" else 0.0,
            "dt_hidden_bear": 1.0 if div_type == "hidden_bear" else 0.0,
            # --- Regime (primary-TF) ---
            "adx_primary": _safe(float(adx[i]) if not np.isnan(adx[i]) else 0.0),
            "plus_minus_di_diff_primary": _safe(
                (float(plus_di[i]) - float(minus_di[i]))
                if not (np.isnan(plus_di[i]) or np.isnan(minus_di[i]))
                else 0.0
            ),
            "bb_width_primary": _safe(float(bb_width[i]) if not np.isnan(bb_width[i]) else 0.0),
            "bb_width_pct_primary": _safe(
                float(bb_width_pct[i]) if not np.isnan(bb_width_pct[i]) else 0.0
            ),
            "atr_primary_normalized": _safe(
                float(atr_norm[i]) if not np.isnan(atr_norm[i]) else 0.0
            ),
            "atr_primary_pct": _safe(
                float(atr_norm_pct[i]) if not np.isnan(atr_norm_pct[i]) else 0.0
            ),
            # --- Candle ---
            "candle_body_ratio_primary": _safe(
                float(abs(close - open_) / max(high - low, _EPS))
            ),
            # --- HTF context (ATR-normalized slopes, cross-TF alignment) ---
            "h4_ema_slope_atr_norm": h4_slope,
            "h4_rsi_14": h4_rsi,
            "h4_trend_alignment": h4_align,
            "d1_ema_slope_atr_norm": d1_slope,
            "d1_rsi_14": d1_rsi,
            "d1_trend_alignment": d1_align,
            # --- Location (ATR-normalized signed distances) ---
            "dist_roll_high_atr": _safe(dist_roll_high),
            "dist_roll_low_atr": _safe(dist_roll_low),
            "dist_swing_high_atr": _safe(dist_swing_high),
            "dist_swing_low_atr": _safe(dist_swing_low),
            # --- Bookkeeping ---
            "is_long": 1.0 if event.direction == "long" else 0.0,
            "confirmation_lag": _safe(float(meta.get("confirmation_lag", 0.0))),
        }
        return feats
