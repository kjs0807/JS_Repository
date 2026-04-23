"""Build a dataset of confirmed daily RSI divergence events.

Reuses ``src/ml/helpers/divergence.py::detect_divergence`` — the
detection algorithm itself is fine; what failed for trade-level ML
was the *label*, not the *detector*. We keep the detector and change
what comes next.

Input: a pandas DataFrame with a ``close`` column (daily OHLCV).
Output: a DataFrame with one row per confirmed divergence (bull or
bear), each row carrying features suitable for regime analysis.

Rows are keyed by:
- ``symbol``
- ``timestamp_ms`` — timestamp of the *second* pivot (the one that
  just got confirmed), i.e. the earliest time you could have acted on
  the divergence in real time

Feature columns (per event):
- ``div_type`` : regular_bull / regular_bear / hidden_bull / hidden_bear
- ``price_first`` / ``price_second``
- ``rsi_first`` / ``rsi_second``
- ``pivot_distance_bars``
- ``slope_divergence_ratio``
- ``pivot_prominence``
- ``intervening_retracement_ratio``
- ``rsi_at_second`` : same as rsi_second, named for clarity
- ``atr_14_pct_at_second`` : volatility-normalized price at event time
- ``price_trend_100d_pct`` : close / close[-100] - 1
- ``rsi_zscore_200d`` : z-score of RSI within last 200 bars
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.ml.helpers.divergence import detect_divergence


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI. Same algorithm as strategy indicator but vectorized
    on a 1-D numpy array to avoid pulling strategy-layer dependencies."""
    n = len(close)
    out = np.full(n, np.nan, dtype=float)
    if n <= period:
        return out
    diff = np.diff(close)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, n):
        if i == period:
            ag, al = avg_gain, avg_loss
        else:
            ag = (ag * (period - 1) + gains[i - 1]) / period
            al = (al * (period - 1) + losses[i - 1]) / period
        if al < 1e-12:
            out[i] = 100.0
        else:
            rs = ag / al
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _atr_pct(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14,
) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    atr = np.full(n, np.nan, dtype=float)
    if n > period:
        first_atr = float(np.mean(tr[1 : period + 1]))
        atr[period] = first_atr
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr / np.where(close > 0, close, 1.0)


def _rsi_zscore(rsi: np.ndarray, window: int = 200) -> np.ndarray:
    n = len(rsi)
    out = np.full(n, np.nan, dtype=float)
    for i in range(window, n):
        block = rsi[i - window : i]
        block = block[~np.isnan(block)]
        if len(block) < 20:
            continue
        mu = float(np.mean(block))
        sigma = float(np.std(block, ddof=1))
        if sigma < 1e-9:
            continue
        out[i] = (rsi[i] - mu) / sigma
    return out


def _trend_100d_pct(close: np.ndarray, window: int = 100) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan, dtype=float)
    for i in range(window, n):
        if close[i - window] > 0:
            out[i] = close[i] / close[i - window] - 1.0
    return out


@dataclass(frozen=True)
class BuildConfig:
    rsi_period: int = 14
    atr_period: int = 14
    confirmation_bars: int = 3  # daily; 3 bars = 3 days on each side
    lookback_bars: int = 30     # max distance between first and second pivot
    min_history_bars: int = 200  # need enough history for z-score


def build_divergence_events(
    df: pd.DataFrame, symbol: str, cfg: Optional[BuildConfig] = None,
) -> pd.DataFrame:
    """Scan a daily OHLCV dataframe and emit one row per confirmed
    divergence event (bull or bear). See module docstring for schema.

    The incoming frame must have columns ``open_time`` (ms), ``open``,
    ``high``, ``low``, ``close``. Additional columns are ignored.
    """
    if cfg is None:
        cfg = BuildConfig()
    required = {"open_time", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"build_divergence_events missing cols: {missing}")
    df = df.sort_values("open_time").reset_index(drop=True)
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    ts = df["open_time"].to_numpy(dtype=np.int64)

    rsi = _rsi(close, period=cfg.rsi_period)
    atr_p = _atr_pct(high, low, close, period=cfg.atr_period)
    rsi_z = _rsi_zscore(rsi, window=200)
    trend_100 = _trend_100d_pct(close, window=100)

    events: List[Dict[str, Any]] = []
    first_valid = max(cfg.min_history_bars, 2 * cfg.confirmation_bars + 1)

    for end_i in range(first_valid, n):
        for mode in ("bull", "bear"):
            info = detect_divergence(
                price=close,
                indicator=rsi,
                end_index=end_i,
                mode=mode,  # type: ignore[arg-type]
                confirmation_bars=cfg.confirmation_bars,
                lookback=cfg.lookback_bars,
            )
            if info is None:
                continue
            p2 = info.second_pivot_idx
            events.append({
                "symbol": symbol,
                "timestamp_ms": int(ts[p2]),
                "bar_index": int(p2),
                "detection_index": int(end_i),
                "div_type": info.div_type,
                "price_first": float(close[info.first_pivot_idx]),
                "price_second": float(close[p2]),
                "rsi_first": float(rsi[info.first_pivot_idx]),
                "rsi_second": float(rsi[p2]),
                "rsi_at_second": float(rsi[p2]),
                "pivot_distance_bars": int(info.pivot_distance_bars),
                "slope_divergence_ratio": float(info.slope_divergence_ratio),
                "pivot_prominence": float(info.pivot_prominence),
                "intervening_retracement_ratio": float(
                    info.intervening_retracement_ratio,
                ),
                "atr_14_pct_at_second": float(atr_p[p2]) if not np.isnan(atr_p[p2]) else 0.0,
                "price_trend_100d_pct": float(trend_100[p2]) if not np.isnan(trend_100[p2]) else 0.0,
                "rsi_zscore_200d": float(rsi_z[p2]) if not np.isnan(rsi_z[p2]) else 0.0,
            })

    return pd.DataFrame(events)


__all__ = ["BuildConfig", "build_divergence_events"]
