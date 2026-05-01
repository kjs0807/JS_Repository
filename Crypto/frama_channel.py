"""FRAMA Channel [BigBeluga] - Python standalone port.

Original Pine Script:
- "FRAMA Channel [BigBeluga]"
- Licensed under CC BY-NC-SA 4.0 by BigBeluga

This module is intended as a TradingView parity helper before integrating the
indicator into the backtester. Important Pine quirks preserved here:
- volatility is SMA(high - low, 200), not ATR
- alpha is clamped to [0.01, 1]
- Filt is assigned twice per bar; the second SMA-smoothed Filt is the value
  referenced by Filt[1] on the next bar
- for bar_index < N + 1, the SMA(5) input is price(hl2), not raw Filt
- breakout uses hlc3, while neutral color reset uses close crossing Filt
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def _sma_at(values: np.ndarray, idx: int, window: int) -> float:
    """Return Pine-like SMA at one bar, NaN until a full finite window exists."""
    start = idx - window + 1
    if start < 0:
        return np.nan
    chunk = values[start:idx + 1]
    if np.isnan(chunk).any():
        return np.nan
    return float(chunk.mean())


def _compute_frama(price: np.ndarray, high: np.ndarray, low: np.ndarray, n: int) -> np.ndarray:
    """Compute final Pine-compatible Filt series.

    Pine does:

        Filt := na(Filt) ? price : alpha * price + (1 - alpha) * Filt[1]
        Filt := ta.sma((bar_index < N + 1) ? price : Filt, 5)

    Because the same series is overwritten, the next bar's Filt[1] is the
    smoothed value. A raw-FRAMA pass followed by a separate SMA pass is not
    equivalent.
    """
    n_bars = len(price)
    half = n // 2
    filt = np.full(n_bars, np.nan, dtype=np.float64)
    sma_input = np.full(n_bars, np.nan, dtype=np.float64)

    for t in range(n_bars):
        alpha = 0.01

        if t >= n - 1:
            window_hi = high[t - n + 1:t + 1]
            window_lo = low[t - n + 1:t + 1]
            n3 = (window_hi.max() - window_lo.min()) / n

            # Pine high[0]..high[N/2-1] is the recent half.
            first_hi = high[t - half + 1:t + 1]
            first_lo = low[t - half + 1:t + 1]
            n1 = (first_hi.max() - first_lo.min()) / half

            # Pine high[N/2]..high[N-1] is the older half.
            second_hi = high[t - n + 1:t - half + 1]
            second_lo = low[t - n + 1:t - half + 1]
            n2 = (second_hi.max() - second_lo.min()) / half

            if n1 > 0 and n2 > 0 and n3 > 0:
                dimen = (np.log(n1 + n2) - np.log(n3)) / np.log(2.0)
                alpha = np.exp(-4.6 * (dimen - 1.0))
                alpha = min(max(float(alpha), 0.01), 1.0)

        prev_filt = filt[t - 1] if t > 0 else np.nan
        if np.isnan(prev_filt):
            raw_candidate = price[t]
        else:
            raw_candidate = alpha * price[t] + (1.0 - alpha) * prev_filt

        sma_input[t] = price[t] if t < n + 1 else raw_candidate
        filt[t] = _sma_at(sma_input, t, 5)

    return filt


def _crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ta.crossover(a, b)."""
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    prev_a[0] = np.nan
    prev_b[0] = np.nan
    out = (a > b) & (prev_a <= prev_b)
    out[np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)] = False
    return out


def _crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ta.crossunder(a, b)."""
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    prev_a[0] = np.nan
    prev_b[0] = np.nan
    out = (a < b) & (prev_a >= prev_b)
    out[np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)] = False
    return out


def _apply_signal_dedup(
    break_up: np.ndarray,
    break_dn: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Replicate the label counter logic from the Pine script."""
    n_bars = len(break_up)
    signal_long = np.zeros(n_bars, dtype=bool)
    signal_short = np.zeros(n_bars, dtype=bool)
    count1 = 0
    count2 = 0

    for t in range(n_bars):
        if break_up[t]:
            count2 = 0
            count1 += 1
            if count1 == 1:
                signal_long[t] = True
        if break_dn[t]:
            count1 = 0
            count2 += 1
            if count2 == 1:
                signal_short[t] = True

    return signal_long, signal_short


def _compute_color_state(
    close: np.ndarray,
    filt: np.ndarray,
    break_up: np.ndarray,
    break_dn: np.ndarray,
) -> np.ndarray:
    """Replicate the Pine color state transitions."""
    n_bars = len(close)
    state = np.empty(n_bars, dtype=object)
    close_cross = _crossover(close, filt) | _crossunder(close, filt)
    current = "neutral"

    for t in range(n_bars):
        if close_cross[t]:
            current = "neutral"
        if break_up[t]:
            current = "long"
        if break_dn[t]:
            current = "short"
        state[t] = current

    return state


def compute_frama_channel(
    df: pd.DataFrame,
    N: int = 26,
    distance: float = 1.5,
    p_vol_mode: str = "price",
    signal_dedup: bool = True,
) -> pd.DataFrame:
    """Compute FRAMA Channel columns for an OHLCV DataFrame.

    Required columns: open, high, low, close, volume.
    Added columns:
    hl2, hlc3, volatility, frama, upper_band, lower_band, break_up, break_dn,
    signal_long, signal_short, color_state, p_vol.
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if df.empty:
        out = df.copy()
        for col in (
            "hl2", "hlc3", "volatility", "frama", "upper_band", "lower_band", "p_vol"
        ):
            out[col] = pd.Series(dtype="float64")
        for col in ("break_up", "break_dn", "signal_long", "signal_short"):
            out[col] = pd.Series(dtype="bool")
        out["color_state"] = pd.Series(dtype="object")
        return out
    if N < 2 or N % 2 != 0:
        raise ValueError(f"N must be even and >= 2, got {N}")
    if distance < 0.3:
        raise ValueError(f"distance must be >= 0.3, got {distance}")
    if p_vol_mode not in ("price", "volume"):
        raise ValueError(f"p_vol_mode must be 'price' or 'volume', got {p_vol_mode}")

    out = df.copy()
    high = out["high"].to_numpy(dtype=np.float64)
    low = out["low"].to_numpy(dtype=np.float64)
    close = out["close"].to_numpy(dtype=np.float64)
    volume = out["volume"].to_numpy(dtype=np.float64)

    hl2 = (high + low) / 2.0
    hlc3 = (high + low + close) / 3.0

    volatility = (
        pd.Series(high - low)
        .rolling(window=200, min_periods=200)
        .mean()
        .to_numpy()
    )

    if p_vol_mode == "price":
        p_vol = close.copy()
    else:
        p_vol = (
            pd.Series(volume)
            .rolling(window=10, min_periods=10)
            .mean()
            .round(2)
            .to_numpy()
        )

    frama = _compute_frama(hl2, high, low, N)
    upper_band = frama + volatility * distance
    lower_band = frama - volatility * distance

    break_up = _crossover(hlc3, upper_band)
    break_dn = _crossunder(hlc3, lower_band)
    if signal_dedup:
        signal_long, signal_short = _apply_signal_dedup(break_up, break_dn)
    else:
        signal_long = break_up.copy()
        signal_short = break_dn.copy()

    out["hl2"] = hl2
    out["hlc3"] = hlc3
    out["volatility"] = volatility
    out["frama"] = frama
    out["upper_band"] = upper_band
    out["lower_band"] = lower_band
    out["break_up"] = break_up
    out["break_dn"] = break_dn
    out["signal_long"] = signal_long
    out["signal_short"] = signal_short
    out["color_state"] = _compute_color_state(close, frama, break_up, break_dn)
    out["p_vol"] = p_vol
    return out


@dataclass
class VerificationResult:
    match_rate: float
    matched: list[dict[str, Any]]
    missed_in_python: list[dict[str, Any]]
    extra_in_python: list[dict[str, Any]]
    tv_total: int
    py_total: int

    def summary(self) -> str:
        return (
            f"Match rate: {self.match_rate * 100:.2f}%\n"
            f"  TV signals: {self.tv_total}\n"
            f"  Python signals: {self.py_total}\n"
            f"  Matched: {len(self.matched)}\n"
            f"  Missed in Python: {len(self.missed_in_python)}\n"
            f"  Extra in Python: {len(self.extra_in_python)}"
        )


def verify_against_tradingview(
    df: pd.DataFrame,
    tv_signals: list[dict[str, Any]],
    tolerance_bars: int = 0,
    timestamp_col: str | None = None,
) -> VerificationResult:
    """Compare Python signal timestamps with TradingView-exported signals."""
    if "signal_long" not in df.columns or "signal_short" not in df.columns:
        raise ValueError(
            "df must contain 'signal_long' and 'signal_short'. "
            "Run compute_frama_channel() first."
        )

    ts = df[timestamp_col] if timestamp_col else pd.Series(df.index, index=df.index)
    py_signals: list[dict[str, Any]] = []
    for i in range(len(df)):
        if bool(df["signal_long"].iloc[i]):
            py_signals.append({"timestamp": ts.iloc[i], "direction": "long"})
        if bool(df["signal_short"].iloc[i]):
            py_signals.append({"timestamp": ts.iloc[i], "direction": "short"})

    matched: list[dict[str, Any]] = []
    py_used: set[int] = set()
    for tv_sig in tv_signals:
        tv_ts = pd.Timestamp(tv_sig["timestamp"])
        tv_dir = tv_sig["direction"]
        candidates: list[tuple[int, float]] = []
        for j, py_sig in enumerate(py_signals):
            if j in py_used or py_sig["direction"] != tv_dir:
                continue
            try:
                diff = abs((pd.Timestamp(py_sig["timestamp"]) - tv_ts).total_seconds())
            except (TypeError, ValueError):
                if py_sig["timestamp"] == tv_ts:
                    diff = 0.0
                else:
                    continue
            candidates.append((j, diff))

        if not candidates:
            continue
        candidates.sort(key=lambda x: x[1])
        best_j, best_diff = candidates[0]
        if tolerance_bars == 0 and best_diff != 0:
            continue
        matched.append(
            {
                "tv_timestamp": tv_ts,
                "py_timestamp": py_signals[best_j]["timestamp"],
                "direction": tv_dir,
                "time_diff_seconds": best_diff,
            }
        )
        py_used.add(best_j)

    matched_tv = {(m["tv_timestamp"], m["direction"]) for m in matched}
    missed = [
        s for s in tv_signals
        if (pd.Timestamp(s["timestamp"]), s["direction"]) not in matched_tv
    ]
    extra = [py_signals[j] for j in range(len(py_signals)) if j not in py_used]
    tv_total = len(tv_signals)
    match_rate = len(matched) / tv_total if tv_total > 0 else 0.0
    return VerificationResult(
        match_rate=match_rate,
        matched=matched,
        missed_in_python=missed,
        extra_in_python=extra,
        tv_total=tv_total,
        py_total=len(py_signals),
    )


if __name__ == "__main__":
    np.random.seed(42)
    rows = 500
    dates = pd.date_range("2024-01-01", periods=rows, freq="1h", tz="UTC")
    base = 50000 + np.cumsum(np.random.randn(rows) * 100)
    high = base + np.abs(np.random.randn(rows)) * 50
    low = base - np.abs(np.random.randn(rows)) * 50
    close = base + np.random.randn(rows) * 30
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.abs(np.random.randn(rows)) * 1000 + 500
    sample = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )

    result = compute_frama_channel(sample, N=26, distance=1.5, p_vol_mode="price")
    long_signals = result[result["signal_long"]]
    short_signals = result[result["signal_short"]]
    print(f"=== FRAMA Channel result ({rows} bars) ===")
    print(f"Long signals (dedup): {len(long_signals)}")
    print(f"Short signals (dedup): {len(short_signals)}")
    print(f"Raw break_up: {int(result['break_up'].sum())}")
    print(f"Raw break_dn: {int(result['break_dn'].sum())}")

    if len(long_signals) > 0:
        first_long = long_signals.iloc[0]
        print("\nFirst long signal:")
        print(f"  timestamp: {long_signals.index[0]}")
        print(f"  hlc3: {first_long['hlc3']:.2f}")
        print(f"  upper_band: {first_long['upper_band']:.2f}")
        print(f"  frama: {first_long['frama']:.2f}")
