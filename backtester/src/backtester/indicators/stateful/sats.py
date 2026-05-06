"""SATS — Self-Aware Trend System indicator (Pine v1.9.0 port).

Source: ``Self-Aware Trend System [WillyAlgoTrader]`` Pine indicator. The
backtester only needs the signal/level pipeline; UI elements (labels,
dashboard, alerts, self-learning auto-calibration) are dropped.

Pipeline mirrored (Pine section numbers in parentheses):

- ATR / efficiency ratio / volume z-score / structure / momentum (§6, §6.1)
- Trend Quality Index = weighted blend of 4 components, clamped 0..1 (§6.1)
- Adaptive multipliers: legacy ER factor + TQI non-linear factor + asymmetric
  active/passive split + EMA smoothing (§6.2)
- Asymmetric SuperTrend with band ratchet and price-flip + character-flip
  detection (§6.3)
- Dynamic TP scaling with per-leg floors / global ceiling and post-scale
  re-sort (§6.35)
- Pivot-anchored SL with ATR-buffer floor, signal candle close as planned
  entry (§7.5)

Output columns are listed in :func:`SATSIndicator.compute`. The indicator
emits one set of ``sats_*`` columns per ``(symbol, timeframe)`` — register at
most one ``SATSIndicator`` per pair (``IndicatorEngine`` horizontal-concats
indicator outputs and rejects duplicate column names).

Pine parity notes:

- ``barstate.isconfirmed`` is implicit in the backtester (we only ever see
  closed bars), so ``confirmedBuy = flipUp and isWarmedUp`` here.
- ``sourceInput`` defaults to ``close``. Char-flip in Pine compares ``close
  vs source``, so with the default source the char-flip branch never fires —
  matched here to preserve parity.
- ``ta.stdev`` in Pine uses the population estimator (``ddof=0``) — we match.
- ``ta.pivothigh/low`` uses strict ``>`` / ``<`` on both sides.
- TP fields (``sats_entry_price`` / ``sats_sl_price`` / ``sats_tp*_price``)
  are emitted only on signal bars. Non-signal bars carry NaN.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import polars as pl

# ---------- Pine constants --------------------------------------------------

_WARMUP_FLOOR = 50
_MULT_SMOOTH_ALPHA = 0.15

# Bin thresholds (display only — kept here for documentation; not used in
# the backtester output).
_ER_LOW_THRESH = 0.25
_ER_HIGH_THRESH = 0.50
_VOL_LOW_THRESH = 0.7
_VOL_HIGH_THRESH = 1.3

PresetT = Literal["Auto", "Custom", "Scalping", "Default", "Swing", "Crypto 24/7"]
TPModeT = Literal["Fixed", "Dynamic"]


# ---------- Config ----------------------------------------------------------


@dataclass(frozen=True)
class SATSConfig:
    """SATS tuning parameters (Pine inputs flattened to primitive fields)."""

    # ── Main ───────────────────────────────────────────────
    preset: PresetT = "Auto"
    timeframe_minutes: int = 60

    # The values below are Custom-preset inputs in Pine. When ``preset`` is
    # not ``Custom`` the resolver returns preset-table values instead and
    # these fields are unused — kept here so ``preset="Custom"`` users have
    # somewhere to put numbers.
    atr_len: int = 13
    base_mult: float = 2.0
    er_length: int = 20
    rsi_len: int = 14
    sl_atr_mult: float = 1.5

    source_col: str = "close"

    # ── Adaptive engine (legacy ER) ────────────────────────
    use_adaptive: bool = True
    adapt_strength: float = 0.5
    atr_baseline_len: int = 100

    # ── Trend Quality engine ───────────────────────────────
    use_tqi: bool = True
    quality_strength: float = 0.4
    quality_curve: float = 1.5
    mult_smooth: bool = True

    use_asym_bands: bool = True
    asym_strength: float = 0.5
    use_eff_atr: bool = True

    use_char_flip: bool = True
    char_flip_min_age: int = 5
    char_flip_high: float = 0.55
    char_flip_low: float = 0.25

    tqi_weight_er: float = 0.35
    tqi_weight_vol: float = 0.20
    tqi_weight_struct: float = 0.25
    tqi_weight_mom: float = 0.20
    tqi_struct_len: int = 20
    tqi_mom_len: int = 10

    # ── Pivot / volume ─────────────────────────────────────
    pivot_len: int = 3
    vol_len: int = 20

    # ── Risk / TP ──────────────────────────────────────────
    tp_mode: TPModeT = "Fixed"
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    tp3_r: float = 3.0

    dyn_tp_tqi_weight: float = 0.6
    dyn_tp_vol_weight: float = 0.4
    dyn_tp_min_scale: float = 0.5
    dyn_tp_max_scale: float = 2.0
    dyn_tp_floor_r1: float = 0.5
    dyn_tp_ceil_r3: float = 8.0

    trade_max_age_bars: int = 100  # consumed by SATSStrategy, not emitted

    def __post_init__(self) -> None:
        if self.timeframe_minutes <= 0:
            raise ValueError(
                f"timeframe_minutes must be > 0, got {self.timeframe_minutes}"
            )
        for fname, fval in (
            ("atr_len", self.atr_len),
            ("er_length", self.er_length),
            ("rsi_len", self.rsi_len),
            ("atr_baseline_len", self.atr_baseline_len),
            ("tqi_struct_len", self.tqi_struct_len),
            ("tqi_mom_len", self.tqi_mom_len),
            ("pivot_len", self.pivot_len),
            ("vol_len", self.vol_len),
            ("char_flip_min_age", self.char_flip_min_age),
        ):
            if fval < 1:
                raise ValueError(f"{fname} must be >= 1, got {fval}")
        if not (0.0 <= self.quality_strength <= 1.0):
            raise ValueError(
                f"quality_strength must be in [0,1], got {self.quality_strength}"
            )
        if self.quality_curve < 1.0:
            raise ValueError(f"quality_curve must be >= 1.0, got {self.quality_curve}")


# ---------- Preset resolution ----------------------------------------------


def resolve_sats_preset(cfg: SATSConfig) -> tuple[int, float, int, int, float]:
    """Resolve preset → ``(atr_len, base_mult, er_len, rsi_len, sl_mult)``.

    Mirrors the Pine ``switch resolvedPreset`` blocks (§3.5). ``Auto`` falls
    back to a timeframe heuristic; ``Custom`` returns the raw Custom-preset
    inputs from ``cfg``.
    """
    p: str = cfg.preset
    if p == "Auto":
        if cfg.timeframe_minutes <= 5:
            p = "Scalping"
        elif cfg.timeframe_minutes <= 240:
            p = "Default"
        else:
            p = "Swing"

    atr = {"Scalping": 10, "Default": 14, "Swing": 21, "Crypto 24/7": 14}.get(
        p, cfg.atr_len
    )
    mult = {
        "Scalping": 1.5,
        "Default": 2.0,
        "Swing": 2.5,
        "Crypto 24/7": 2.8,
    }.get(p, cfg.base_mult)
    er = {"Scalping": 14, "Default": 20, "Swing": 30, "Crypto 24/7": 20}.get(
        p, cfg.er_length
    )
    rsi = {"Scalping": 9, "Default": 14, "Swing": 21, "Crypto 24/7": 14}.get(
        p, cfg.rsi_len
    )
    sl = {"Scalping": 1.0, "Default": 1.5, "Swing": 2.0, "Crypto 24/7": 2.5}.get(
        p, cfg.sl_atr_mult
    )
    return int(atr), float(mult), int(er), int(rsi), float(sl)


# ---------- scalar helpers --------------------------------------------------


def _safe_div(num: float, den: float, fallback: float = 0.0) -> float:
    if math.isnan(num) or math.isnan(den) or den == 0.0:
        return fallback
    return num / den


def _clamp(v: float, lo: float, hi: float) -> float:
    if math.isnan(v):
        return v
    return max(lo, min(hi, v))


def _map_clamp(
    v: float, in_lo: float, in_hi: float, out_lo: float, out_hi: float
) -> float:
    """Pine ``mapClamp`` — affine map clipped to ``[out_lo, out_hi]``.

    NaN-tolerant: returns ``out_lo`` for NaN (Pine returns NaN, but the
    consumers here always gate on warmup before reading).
    """
    span = in_hi - in_lo
    if math.isnan(v) or span == 0.0:
        return out_lo
    t = (v - in_lo) / span
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return out_lo + t * (out_hi - out_lo)


# ---------- vector helpers --------------------------------------------------


def wilder_rma(values: np.ndarray, length: int) -> np.ndarray:
    """Wilder RMA tolerant of NaN gaps in ``values``.

    Tracks the most recent valid output as ``prev_valid`` instead of reading
    ``out[i - 1]``. This matters when the input is itself a derived series
    (e.g. efficiency ratio, true range during warmup) that has NaN holes:
    naive ``out[i - 1]`` would propagate NaN forever once the previous bar
    was skipped, even if Pine's stateful ``ta.rma`` would have kept its
    internal value across the gap.
    """
    out = np.full(len(values), np.nan, dtype=np.float64)
    acc = 0.0
    count = 0
    prev_valid: float | None = None

    for i, value in enumerate(values):
        if math.isnan(value):
            continue
        if prev_valid is None:
            acc += value
            count += 1
            if count == length:
                out[i] = acc / length
                prev_valid = out[i]
            continue
        out[i] = (prev_valid * (length - 1) + value) / length
        prev_valid = out[i]

    return out


def wilder_atr(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int
) -> np.ndarray:
    n = len(close)
    tr = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return tr
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return wilder_rma(tr, length)


def efficiency_ratio(close: np.ndarray, length: int) -> np.ndarray:
    """``|close[t] - close[t-len]| / sum_{i=0..len-1} |close[t-i] - close[t-i-1]|``."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if n <= length:
        return out
    abs_diff = np.abs(np.diff(close, prepend=np.nan))  # abs_diff[0] = NaN
    # Rolling sum of last `length` abs diffs ending at t (inclusive).
    rolling_vol = (
        pl.Series("d", abs_diff)
        .rolling_sum(window_size=length, min_samples=length)
        .to_numpy()
    )
    for t in range(length, n):
        ch = abs(close[t] - close[t - length])
        vol = rolling_vol[t]
        if math.isnan(vol) or vol == 0.0:
            out[t] = 0.0
        else:
            out[t] = ch / vol
    return out


def volume_zscore(volume: np.ndarray, length: int) -> np.ndarray:
    """``(volume - sma(volume, len)) / stdev(volume, len)`` (population stdev)."""
    n = len(volume)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    s = pl.Series("v", volume)
    mean = s.rolling_mean(window_size=length, min_samples=length).to_numpy()
    std = s.rolling_std(window_size=length, min_samples=length, ddof=0).to_numpy()
    out = np.full(n, np.nan, dtype=np.float64)
    for t in range(n):
        m = mean[t]
        sd = std[t]
        if math.isnan(m) or math.isnan(sd):
            continue
        if sd == 0.0:
            out[t] = 0.0
        else:
            out[t] = (volume[t] - m) / sd
    return out


def rolling_max(values: np.ndarray, length: int) -> np.ndarray:
    return (
        pl.Series("v", values)
        .rolling_max(window_size=length, min_samples=length)
        .to_numpy()
    )


def rolling_min(values: np.ndarray, length: int) -> np.ndarray:
    return (
        pl.Series("v", values)
        .rolling_min(window_size=length, min_samples=length)
        .to_numpy()
    )


def rolling_mean(values: np.ndarray, length: int) -> np.ndarray:
    return (
        pl.Series("v", values)
        .rolling_mean(window_size=length, min_samples=length)
        .to_numpy()
    )


def pivot_high(high: np.ndarray, left: int, right: int) -> np.ndarray:
    """Pine ``ta.pivothigh(high, left, right)``.

    At bar ``t``, returns ``high[t - right]`` if that index is the strict
    maximum over ``[t - right - left, t]`` (window of ``left + right + 1``
    bars centered at ``t - right``); else NaN. Strict ``>`` on both sides.
    """
    n = len(high)
    out = np.full(n, np.nan, dtype=np.float64)
    width = left + right
    for t in range(width, n):
        center = t - right
        v = high[center]
        if math.isnan(v):
            continue
        ok = True
        for i in range(1, left + 1):
            other = high[center - i]
            if math.isnan(other) or not (v > other):
                ok = False
                break
        if ok:
            for i in range(1, right + 1):
                other = high[center + i]
                if math.isnan(other) or not (v > other):
                    ok = False
                    break
        if ok:
            out[t] = v
    return out


def pivot_low(low: np.ndarray, left: int, right: int) -> np.ndarray:
    """Mirror of :func:`pivot_high` with strict ``<`` on both sides."""
    n = len(low)
    out = np.full(n, np.nan, dtype=np.float64)
    width = left + right
    for t in range(width, n):
        center = t - right
        v = low[center]
        if math.isnan(v):
            continue
        ok = True
        for i in range(1, left + 1):
            other = low[center - i]
            if math.isnan(other) or not (v < other):
                ok = False
                break
        if ok:
            for i in range(1, right + 1):
                other = low[center + i]
                if math.isnan(other) or not (v < other):
                    ok = False
                    break
        if ok:
            out[t] = v
    return out


# ---------- TP scaling ------------------------------------------------------


def _calc_dyn_tp_scale(
    tqi: float,
    vol_ratio: float,
    tqi_weight: float,
    vol_weight: float,
    min_scale: float,
    max_scale: float,
) -> float:
    """Pine §5.3 calcDynTpScale — TQI/vol weighted blend → ``[min_scale, max_scale]``."""
    if math.isnan(tqi) or math.isnan(vol_ratio):
        return float("nan")
    tqi_comp = _clamp(tqi, 0.0, 1.0)
    vol_comp = _clamp(_map_clamp(vol_ratio, 0.5, 2.0, 0.0, 1.0), 0.0, 1.0)
    w_sum = tqi_weight + vol_weight
    w_denom = w_sum if w_sum > 0 else 1.0
    raw_scale = (tqi_comp * tqi_weight + vol_comp * vol_weight) / w_denom
    return min_scale + raw_scale * (max_scale - min_scale)


# ---------- Indicator -------------------------------------------------------


@dataclass(frozen=True)
class SATSIndicator:
    """SATS indicator — Pine v1.9.0 port (signals + levels only)."""

    cfg: SATSConfig = field(default_factory=SATSConfig)

    @property
    def name(self) -> str:
        return "sats"

    def required_warmup_bars(self) -> int:
        # Pine §6: warmup = max(WARMUP_FLOOR, atr_len, er_len, rsi_len,
        # vol_len, pivot_len*2+1, mom_len, struct_len) + 10. ``atr_baseline_len``
        # is intentionally excluded — Pine uses ``nz(sma(rawAtr, baseline), rawAtr)``
        # which falls back to raw ATR while the SMA is still warming, so the
        # baseline window does not gate signal emission.
        atr_len, _base_mult, er_len, rsi_len, _sl_mult = resolve_sats_preset(self.cfg)
        return (
            max(
                _WARMUP_FLOOR,
                atr_len,
                er_len,
                rsi_len,
                self.cfg.vol_len,
                self.cfg.pivot_len * 2 + 1,
                self.cfg.tqi_mom_len,
                self.cfg.tqi_struct_len,
            )
            + 10
        )

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        """Compute SATS columns for ``bars``.

        Output columns (always 25, dtypes fixed):

        - ``sats_atr`` (Float64) — effective ATR (Wilder × eff-ATR weighting if enabled)
        - ``sats_raw_atr`` (Float64) — Wilder ATR raw
        - ``sats_er`` (Float64) — efficiency ratio
        - ``sats_vol_ratio`` (Float64) — raw ATR / SMA(raw ATR, baseline)
        - ``sats_tqi`` / ``sats_tqi_er`` / ``sats_tqi_vol`` /
          ``sats_tqi_struct`` / ``sats_tqi_mom`` (Float64)
        - ``sats_active_mult`` / ``sats_passive_mult`` (Float64) —
          smoothed asymmetric band multipliers
        - ``sats_lower_band`` / ``sats_upper_band`` (Float64)
        - ``sats_trend`` (Int8: ±1) / ``sats_st_line`` (Float64)
        - ``sats_signal`` (Int8: ±1 on flip bar, else 0)
        - ``sats_entry_price`` (Float64) — signal candle close, NaN otherwise
        - ``sats_sl_price`` / ``sats_tp1_price`` / ``sats_tp2_price`` /
          ``sats_tp3_price`` (Float64) — NaN on non-signal bars
        - ``sats_tp1_r`` / ``sats_tp2_r`` / ``sats_tp3_r`` (Float64) —
          live R-multiples used at signal time
        - ``sats_ready`` (Boolean) — warmup gate
        """
        n = bars.height
        if n == 0:
            return _empty_output()

        cfg = self.cfg
        atr_len, base_mult, er_len, _rsi_len, sl_mult = resolve_sats_preset(cfg)

        high = bars["high"].to_numpy().astype(np.float64, copy=False)
        low = bars["low"].to_numpy().astype(np.float64, copy=False)
        close = bars["close"].to_numpy().astype(np.float64, copy=False)
        if cfg.source_col in bars.columns:
            source = (
                bars[cfg.source_col].to_numpy().astype(np.float64, copy=False)
            )
        else:
            source = close
        if "volume" in bars.columns:
            volume = bars["volume"].to_numpy().astype(np.float64, copy=False)
        else:
            volume = np.zeros(n, dtype=np.float64)

        # ── §6 base calculations ──────────────────────────
        raw_atr = wilder_atr(high, low, close, atr_len)
        atr_baseline_raw = rolling_mean(raw_atr, cfg.atr_baseline_len)
        # Pine: nz(atrBaseline, rawAtr) — fall back to rawAtr when SMA still warming.
        atr_baseline = np.where(
            np.isnan(atr_baseline_raw), raw_atr, atr_baseline_raw
        )
        vol_ratio = np.full(n, 1.0, dtype=np.float64)
        for t in range(n):
            vol_ratio[t] = _safe_div(raw_atr[t], atr_baseline[t], 1.0)

        er = efficiency_ratio(close, er_len)

        if cfg.use_eff_atr:
            atr_value = np.where(
                np.isnan(raw_atr) | np.isnan(er),
                raw_atr,
                raw_atr * (0.5 + 0.5 * er),
            )
        else:
            atr_value = raw_atr.copy()

        # ── §6.1 TQI components ───────────────────────────
        tqi_er = np.clip(er, 0.0, 1.0)

        struct_hi = rolling_max(high, cfg.tqi_struct_len)
        struct_lo = rolling_min(low, cfg.tqi_struct_len)
        # ``has_volume`` per-bar — Pine: nz(volume, 0) > 0
        has_volume = np.nan_to_num(volume, nan=0.0) > 0.0
        # tqiVol fallback path uses volRatio mapping.
        vol_z = volume_zscore(volume, cfg.vol_len)

        # ── §6.4 pivots ───────────────────────────────────
        pv_high = pivot_high(high, cfg.pivot_len, cfg.pivot_len)
        pv_low = pivot_low(low, cfg.pivot_len, cfg.pivot_len)

        # ── per-bar recursive loop ────────────────────────
        out = _compute_sats_recursive(
            high=high,
            low=low,
            close=close,
            source=source,
            raw_atr=raw_atr,
            atr_value=atr_value,
            vol_ratio=vol_ratio,
            er=er,
            tqi_er=tqi_er,
            struct_hi=struct_hi,
            struct_lo=struct_lo,
            has_volume=has_volume,
            vol_z=vol_z,
            pv_high=pv_high,
            pv_low=pv_low,
            cfg=cfg,
            base_mult=base_mult,
            sl_mult=sl_mult,
            warmup_bars=self.required_warmup_bars(),
        )
        return pl.DataFrame(out)


def _empty_output() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "sats_atr": pl.Float64,
            "sats_raw_atr": pl.Float64,
            "sats_er": pl.Float64,
            "sats_vol_ratio": pl.Float64,
            "sats_tqi": pl.Float64,
            "sats_tqi_er": pl.Float64,
            "sats_tqi_vol": pl.Float64,
            "sats_tqi_struct": pl.Float64,
            "sats_tqi_mom": pl.Float64,
            "sats_active_mult": pl.Float64,
            "sats_passive_mult": pl.Float64,
            "sats_lower_band": pl.Float64,
            "sats_upper_band": pl.Float64,
            "sats_trend": pl.Int8,
            "sats_st_line": pl.Float64,
            "sats_signal": pl.Int8,
            "sats_entry_price": pl.Float64,
            "sats_sl_price": pl.Float64,
            "sats_tp1_price": pl.Float64,
            "sats_tp2_price": pl.Float64,
            "sats_tp3_price": pl.Float64,
            "sats_tp1_r": pl.Float64,
            "sats_tp2_r": pl.Float64,
            "sats_tp3_r": pl.Float64,
            "sats_ready": pl.Boolean,
        }
    )


def _compute_sats_recursive(
    *,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    source: np.ndarray,
    raw_atr: np.ndarray,
    atr_value: np.ndarray,
    vol_ratio: np.ndarray,
    er: np.ndarray,
    tqi_er: np.ndarray,
    struct_hi: np.ndarray,
    struct_lo: np.ndarray,
    has_volume: np.ndarray,
    vol_z: np.ndarray,
    pv_high: np.ndarray,
    pv_low: np.ndarray,
    cfg: SATSConfig,
    base_mult: float,
    sl_mult: float,
    warmup_bars: int,
) -> dict[str, np.ndarray]:
    """Single per-bar pass implementing Pine §6.1–§7.5 in order.

    Causality: every write at ``t`` reads only state stored from previous
    bars and arrays already filled at ``i <= t``. No look-ahead.
    """
    n = len(close)

    # output arrays
    sats_atr = atr_value.copy()
    sats_raw_atr = raw_atr.copy()
    sats_er_out = er.copy()
    sats_vol_ratio = vol_ratio.copy()
    sats_tqi = np.full(n, np.nan, dtype=np.float64)
    sats_tqi_er_out = np.full(n, np.nan, dtype=np.float64)
    sats_tqi_vol = np.full(n, np.nan, dtype=np.float64)
    sats_tqi_struct = np.full(n, np.nan, dtype=np.float64)
    sats_tqi_mom = np.full(n, np.nan, dtype=np.float64)
    sats_active_mult = np.full(n, np.nan, dtype=np.float64)
    sats_passive_mult = np.full(n, np.nan, dtype=np.float64)
    sats_lower_band = np.full(n, np.nan, dtype=np.float64)
    sats_upper_band = np.full(n, np.nan, dtype=np.float64)
    sats_trend = np.ones(n, dtype=np.int8)
    sats_st_line = np.full(n, np.nan, dtype=np.float64)
    sats_signal = np.zeros(n, dtype=np.int8)
    sats_entry_price = np.full(n, np.nan, dtype=np.float64)
    sats_sl_price = np.full(n, np.nan, dtype=np.float64)
    sats_tp1_price = np.full(n, np.nan, dtype=np.float64)
    sats_tp2_price = np.full(n, np.nan, dtype=np.float64)
    sats_tp3_price = np.full(n, np.nan, dtype=np.float64)
    sats_tp1_r = np.full(n, np.nan, dtype=np.float64)
    sats_tp2_r = np.full(n, np.nan, dtype=np.float64)
    sats_tp3_r = np.full(n, np.nan, dtype=np.float64)
    sats_ready = np.zeros(n, dtype=bool)

    # state
    active_mult_sm = float("nan")
    passive_mult_sm = float("nan")
    lower_band = float("nan")
    upper_band = float("nan")
    st_trend = 1
    trend_start_bar = 0
    last_pivot_high = float("nan")
    last_pivot_low = float("nan")
    prev_tqi: float = 0.5  # Pine: nz(tqi[1], 0.5)

    # TP fixed-order pre-fix (Pine §3.5 tail).
    fixed_tp1 = min(cfg.tp1_r, cfg.tp2_r, cfg.tp3_r)
    fixed_tp3 = max(cfg.tp1_r, cfg.tp2_r, cfg.tp3_r)
    fixed_tp2 = cfg.tp1_r + cfg.tp2_r + cfg.tp3_r - fixed_tp1 - fixed_tp3

    use_dyn_tp = cfg.tp_mode == "Dynamic"
    eff_quality = cfg.quality_strength  # auto-calibration is dropped

    for t in range(n):
        # ── TQI components ─────────────────────────────
        tqi_er_t = tqi_er[t]
        if has_volume[t]:
            tqi_vol_t = _map_clamp(vol_z[t], -1.0, 2.0, 0.0, 1.0)
        else:
            tqi_vol_t = _map_clamp(vol_ratio[t], 0.6, 1.8, 0.0, 1.0)

        # Structure: |pricePos - 0.5| * 2 ∈ [0, 1].
        s_hi = struct_hi[t]
        s_lo = struct_lo[t]
        if math.isnan(s_hi) or math.isnan(s_lo):
            tqi_struct_t = float("nan")
        else:
            rng = s_hi - s_lo
            price_pos = _safe_div(close[t] - s_lo, rng, 0.5)
            tqi_struct_t = _clamp(abs(price_pos - 0.5) * 2.0, 0.0, 1.0)

        # Momentum persistence: count last `tqi_mom_len` 1-bar changes
        # whose sign matches the window-change sign.
        mom_len = cfg.tqi_mom_len
        if t < mom_len:
            tqi_mom_t = float("nan")
        else:
            window_change = close[t] - close[t - mom_len]
            aligned = 0
            for i in range(mom_len):
                bar_change = close[t - i] - close[t - i - 1]
                if window_change > 0 and bar_change > 0:
                    aligned += 1
                elif window_change < 0 and bar_change < 0:
                    aligned += 1
            tqi_mom_t = aligned / mom_len

        # Weighted TQI blend.
        if cfg.use_tqi:
            comps = (tqi_er_t, tqi_vol_t, tqi_struct_t, tqi_mom_t)
            if any(math.isnan(c) for c in comps):
                tqi_t = float("nan")
            else:
                w_sum = (
                    cfg.tqi_weight_er
                    + cfg.tqi_weight_vol
                    + cfg.tqi_weight_struct
                    + cfg.tqi_weight_mom
                )
                w_denom = w_sum if w_sum > 0 else 1.0
                raw = (
                    tqi_er_t * cfg.tqi_weight_er
                    + tqi_vol_t * cfg.tqi_weight_vol
                    + tqi_struct_t * cfg.tqi_weight_struct
                    + tqi_mom_t * cfg.tqi_weight_mom
                ) / w_denom
                tqi_t = _clamp(raw, 0.0, 1.0)
        else:
            tqi_t = 0.5

        sats_tqi_er_out[t] = tqi_er_t
        sats_tqi_vol[t] = tqi_vol_t
        sats_tqi_struct[t] = tqi_struct_t
        sats_tqi_mom[t] = tqi_mom_t
        sats_tqi[t] = tqi_t

        # ── §6.2 adaptive multipliers ────────────────────
        er_t = er[t]
        if cfg.use_adaptive and not math.isnan(er_t):
            legacy_factor = 1.0 + cfg.adapt_strength * (0.5 - er_t)
        else:
            legacy_factor = 1.0

        if cfg.use_tqi and not math.isnan(tqi_t):
            quality_dev = (1.0 - tqi_t) ** cfg.quality_curve
        else:
            quality_dev = 0.5
        tqi_mult = 1.0 - eff_quality + eff_quality * (0.6 + 0.8 * quality_dev)
        sym_mult = base_mult * legacy_factor * tqi_mult

        if cfg.use_tqi and cfg.use_asym_bands and not math.isnan(tqi_t):
            asym_tighten = 1.0 - cfg.asym_strength * tqi_t * 0.3
            asym_widen = 1.0 + cfg.asym_strength * tqi_t * 0.4
            active_raw = sym_mult * asym_tighten
            passive_raw = sym_mult * asym_widen
        else:
            active_raw = sym_mult
            passive_raw = sym_mult

        if math.isnan(active_mult_sm):
            active_mult_sm = active_raw
        elif cfg.mult_smooth:
            active_mult_sm = (
                active_mult_sm * (1.0 - _MULT_SMOOTH_ALPHA)
                + active_raw * _MULT_SMOOTH_ALPHA
            )
        else:
            active_mult_sm = active_raw

        if math.isnan(passive_mult_sm):
            passive_mult_sm = passive_raw
        elif cfg.mult_smooth:
            passive_mult_sm = (
                passive_mult_sm * (1.0 - _MULT_SMOOTH_ALPHA)
                + passive_raw * _MULT_SMOOTH_ALPHA
            )
        else:
            passive_mult_sm = passive_raw

        sats_active_mult[t] = active_mult_sm
        sats_passive_mult[t] = passive_mult_sm

        # ── §6.3 SuperTrend bands (recursion) ────────────
        prev_trend = st_trend  # already 1 on first bar by init
        if prev_trend == 1:
            lower_mult = active_mult_sm
            upper_mult = passive_mult_sm
        else:
            lower_mult = passive_mult_sm
            upper_mult = active_mult_sm

        atr_t = atr_value[t]
        src_t = source[t]
        if math.isnan(atr_t) or math.isnan(src_t):
            lower_raw = float("nan")
            upper_raw = float("nan")
        else:
            lower_raw = src_t - lower_mult * atr_t
            upper_raw = src_t + upper_mult * atr_t

        # Ratchet
        if math.isnan(lower_band) or math.isnan(lower_raw):
            new_lower = lower_raw
        else:
            prev_close = close[t - 1] if t > 0 else float("nan")
            if not math.isnan(prev_close) and prev_close > lower_band:
                new_lower = max(lower_raw, lower_band)
            else:
                new_lower = lower_raw
        if math.isnan(upper_band) or math.isnan(upper_raw):
            new_upper = upper_raw
        else:
            prev_close = close[t - 1] if t > 0 else float("nan")
            if not math.isnan(prev_close) and prev_close < upper_band:
                new_upper = min(upper_raw, upper_band)
            else:
                new_upper = upper_raw

        # Flip detection — uses prev band values (ratchet's last-bar state).
        prev_lower = lower_band
        prev_upper = upper_band
        c_t = close[t]
        price_flip_up = (
            prev_trend == -1
            and not math.isnan(prev_upper)
            and c_t > prev_upper
        )
        price_flip_dn = (
            prev_trend == 1
            and not math.isnan(prev_lower)
            and c_t < prev_lower
        )

        trend_age = t - trend_start_bar
        char_base = (
            cfg.use_char_flip
            and cfg.use_tqi
            and not math.isnan(tqi_t)
            and prev_tqi > cfg.char_flip_high
            and tqi_t < cfg.char_flip_low
            and trend_age >= cfg.char_flip_min_age
        )
        char_flip_dn = char_base and prev_trend == 1 and c_t < src_t
        char_flip_up = char_base and prev_trend == -1 and c_t > src_t

        flip_up = price_flip_up or char_flip_up
        flip_dn = price_flip_dn or char_flip_dn

        if flip_up:
            new_trend = 1
        elif flip_dn:
            new_trend = -1
        else:
            new_trend = prev_trend

        # Commit band state, trend, st_line.
        lower_band = new_lower
        upper_band = new_upper
        if new_trend != prev_trend:
            trend_start_bar = t
        st_trend = new_trend

        sats_lower_band[t] = lower_band
        sats_upper_band[t] = upper_band
        sats_trend[t] = st_trend
        if st_trend == 1:
            sats_st_line[t] = lower_band
        else:
            sats_st_line[t] = upper_band

        # Signal flip (this bar transitioned).
        signal_dir = 0
        if st_trend != prev_trend:
            signal_dir = st_trend
        # ── §6.4 pivot tracking (update before SL uses it) ────
        if not math.isnan(pv_high[t]):
            last_pivot_high = pv_high[t]
        if not math.isnan(pv_low[t]):
            last_pivot_low = pv_low[t]

        # ── §6.35 dynamic TP ──────────────────────────────
        if use_dyn_tp:
            dyn_scale = _calc_dyn_tp_scale(
                tqi_t,
                vol_ratio[t],
                cfg.dyn_tp_tqi_weight,
                cfg.dyn_tp_vol_weight,
                cfg.dyn_tp_min_scale,
                cfg.dyn_tp_max_scale,
            )
        else:
            dyn_scale = 1.0

        if use_dyn_tp and not math.isnan(dyn_scale):
            tp1_floor = cfg.dyn_tp_floor_r1
            denom = max(fixed_tp1, 0.01)
            tp2_floor = cfg.dyn_tp_floor_r1 * (fixed_tp2 / denom)
            tp3_floor = cfg.dyn_tp_floor_r1 * (fixed_tp3 / denom)
            ceil_r3 = cfg.dyn_tp_ceil_r3
            eff1 = _clamp(fixed_tp1 * dyn_scale, tp1_floor, ceil_r3)
            eff2 = _clamp(fixed_tp2 * dyn_scale, tp2_floor, ceil_r3)
            eff3 = _clamp(fixed_tp3 * dyn_scale, tp3_floor, ceil_r3)
        else:
            eff1, eff2, eff3 = fixed_tp1, fixed_tp2, fixed_tp3

        live_tp1 = min(eff1, eff2, eff3)
        live_tp3 = max(eff1, eff2, eff3)
        live_tp2 = eff1 + eff2 + eff3 - live_tp1 - live_tp3

        # ── §7 warmup gate ────────────────────────────────
        is_warm = t >= warmup_bars
        sats_ready[t] = is_warm

        # ── §7.5 signal → entry/SL/TP plan ────────────────
        if is_warm and signal_dir != 0:
            sats_signal[t] = signal_dir
            entry_t = c_t  # signal candle close
            atr_for_sl = atr_t  # effective ATR
            if signal_dir == 1:
                sl_base = (
                    last_pivot_low
                    if not math.isnan(last_pivot_low)
                    else low[t]
                )
                raw_sl = sl_base - sl_mult * atr_for_sl
                min_sl = entry_t - sl_mult * atr_for_sl
                sl_t = min(raw_sl, min_sl)
                risk = entry_t - sl_t
                tp1_t = entry_t + risk * live_tp1
                tp2_t = entry_t + risk * live_tp2
                tp3_t = entry_t + risk * live_tp3
            else:
                sl_base = (
                    last_pivot_high
                    if not math.isnan(last_pivot_high)
                    else high[t]
                )
                raw_sl = sl_base + sl_mult * atr_for_sl
                min_sl = entry_t + sl_mult * atr_for_sl
                sl_t = max(raw_sl, min_sl)
                risk = sl_t - entry_t
                tp1_t = entry_t - risk * live_tp1
                tp2_t = entry_t - risk * live_tp2
                tp3_t = entry_t - risk * live_tp3
            sats_entry_price[t] = entry_t
            sats_sl_price[t] = sl_t
            sats_tp1_price[t] = tp1_t
            sats_tp2_price[t] = tp2_t
            sats_tp3_price[t] = tp3_t
            sats_tp1_r[t] = live_tp1
            sats_tp2_r[t] = live_tp2
            sats_tp3_r[t] = live_tp3

        # state carry — TQI for next bar's char-flip check.
        prev_tqi = tqi_t if not math.isnan(tqi_t) else 0.5

    return {
        "sats_atr": sats_atr,
        "sats_raw_atr": sats_raw_atr,
        "sats_er": sats_er_out,
        "sats_vol_ratio": sats_vol_ratio,
        "sats_tqi": sats_tqi,
        "sats_tqi_er": sats_tqi_er_out,
        "sats_tqi_vol": sats_tqi_vol,
        "sats_tqi_struct": sats_tqi_struct,
        "sats_tqi_mom": sats_tqi_mom,
        "sats_active_mult": sats_active_mult,
        "sats_passive_mult": sats_passive_mult,
        "sats_lower_band": sats_lower_band,
        "sats_upper_band": sats_upper_band,
        "sats_trend": sats_trend,
        "sats_st_line": sats_st_line,
        "sats_signal": sats_signal,
        "sats_entry_price": sats_entry_price,
        "sats_sl_price": sats_sl_price,
        "sats_tp1_price": sats_tp1_price,
        "sats_tp2_price": sats_tp2_price,
        "sats_tp3_price": sats_tp3_price,
        "sats_tp1_r": sats_tp1_r,
        "sats_tp2_r": sats_tp2_r,
        "sats_tp3_r": sats_tp3_r,
        "sats_ready": sats_ready,
    }


__all__ = [
    "SATSConfig",
    "SATSIndicator",
    "resolve_sats_preset",
    "wilder_rma",
    "wilder_atr",
    "efficiency_ratio",
    "volume_zscore",
    "rolling_max",
    "rolling_min",
    "rolling_mean",
    "pivot_high",
    "pivot_low",
]
