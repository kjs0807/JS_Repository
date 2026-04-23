"""Tests for candle pattern helpers."""
import pandas as pd

from src.core.types import BarSeries
from src.ml.helpers.candle import (
    is_bullish_engulfing, is_bearish_engulfing,
    is_hammer, is_shooting_star, is_doji,
    candle_body_ratio, candle_wick_ratio,
)


def _series(*ohlc_tuples):
    """Build a BarSeries from a sequence of (open, high, low, close)."""
    rows = []
    for i, (o, h, l, c) in enumerate(ohlc_tuples):
        rows.append({
            "timestamp": i * 60_000,
            "open": o, "high": h, "low": l, "close": c,
            "volume": 1.0, "turnover": 1.0,
        })
    return BarSeries(symbol="X", timeframe="1h", bars=pd.DataFrame(rows))


def test_bullish_engulfing():
    s = _series(
        (110, 111, 104, 105),  # red bar
        (104, 115, 103, 114),  # green engulfs prior red body
    )
    assert is_bullish_engulfing(s, i=1) is True


def test_not_bullish_engulfing_when_prev_green():
    s = _series(
        (100, 110, 99, 109),
        (108, 115, 107, 114),
    )
    assert is_bullish_engulfing(s, i=1) is False


def test_bearish_engulfing():
    s = _series(
        (100, 106, 99, 105),
        (106, 107, 98, 99),
    )
    assert is_bearish_engulfing(s, i=1) is True


def test_hammer():
    # Small body near top, long lower wick, no significant upper wick
    s = _series((100, 101, 92, 100.5))
    assert is_hammer(s, i=0) is True


def test_not_hammer_when_short_wick():
    s = _series((100, 102, 99, 101))
    assert is_hammer(s, i=0) is False


def test_shooting_star():
    s = _series((100, 110, 99.5, 100.3))
    assert is_shooting_star(s, i=0) is True


def test_doji():
    s = _series((100, 101, 99, 100.05))
    assert is_doji(s, i=0, body_threshold=0.1) is True


def test_body_ratio():
    s = _series((100, 110, 90, 105))
    # body = 5, range = 20
    assert abs(candle_body_ratio(s, i=0) - 0.25) < 1e-9


def test_wick_ratio():
    s = _series((100, 110, 90, 105))
    # body = 5, total wicks = 15, range = 20 → wick_ratio = 0.75
    assert abs(candle_wick_ratio(s, i=0) - 0.75) < 1e-9
