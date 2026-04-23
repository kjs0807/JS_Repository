"""Candle pattern helpers (no TA-Lib).

All functions take a BarSeries and an integer index i. They return bool or float.
None reference data after index i (lookahead-safe).
"""
from __future__ import annotations

from src.core.types import BarSeries


def _row(series: BarSeries, i: int):
    return series.bars.iloc[i]


def _body(row) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _range(row) -> float:
    return max(float(row["high"]) - float(row["low"]), 1e-12)


def is_bullish_engulfing(series: BarSeries, i: int) -> bool:
    if i < 1:
        return False
    prev = _row(series, i - 1)
    cur = _row(series, i)
    if not (float(prev["close"]) < float(prev["open"])):  # prev must be red
        return False
    if not (float(cur["close"]) > float(cur["open"])):    # cur must be green
        return False
    return float(cur["close"]) >= float(prev["open"]) and float(cur["open"]) <= float(prev["close"])


def is_bearish_engulfing(series: BarSeries, i: int) -> bool:
    if i < 1:
        return False
    prev = _row(series, i - 1)
    cur = _row(series, i)
    if not (float(prev["close"]) > float(prev["open"])):
        return False
    if not (float(cur["close"]) < float(cur["open"])):
        return False
    return float(cur["open"]) >= float(prev["close"]) and float(cur["close"]) <= float(prev["open"])


def is_hammer(series: BarSeries, i: int, body_max: float = 0.3,
              lower_wick_min: float = 0.5) -> bool:
    row = _row(series, i)
    rng = _range(row)
    body = _body(row)
    body_top = max(float(row["open"]), float(row["close"]))
    body_bot = min(float(row["open"]), float(row["close"]))
    upper_wick = float(row["high"]) - body_top
    lower_wick = body_bot - float(row["low"])
    if body / rng > body_max:
        return False
    if lower_wick / rng < lower_wick_min:
        return False
    if upper_wick > body * 1.5 and upper_wick > 0:
        return False
    return True


def is_shooting_star(series: BarSeries, i: int, body_max: float = 0.3,
                     upper_wick_min: float = 0.5) -> bool:
    row = _row(series, i)
    rng = _range(row)
    body = _body(row)
    body_top = max(float(row["open"]), float(row["close"]))
    body_bot = min(float(row["open"]), float(row["close"]))
    upper_wick = float(row["high"]) - body_top
    lower_wick = body_bot - float(row["low"])
    if body / rng > body_max:
        return False
    if upper_wick / rng < upper_wick_min:
        return False
    if lower_wick / rng > 0.25:
        return False
    return True


def is_doji(series: BarSeries, i: int, body_threshold: float = 0.1) -> bool:
    row = _row(series, i)
    return _body(row) / _range(row) < body_threshold


def candle_body_ratio(series: BarSeries, i: int) -> float:
    row = _row(series, i)
    return _body(row) / _range(row)


def candle_wick_ratio(series: BarSeries, i: int) -> float:
    return 1.0 - candle_body_ratio(series, i)


__all__ = [
    "is_bullish_engulfing", "is_bearish_engulfing",
    "is_hammer", "is_shooting_star", "is_doji",
    "candle_body_ratio", "candle_wick_ratio",
]
