"""Tests for MTF alignment — critical lookahead prevention."""
import pytest
import pandas as pd

from src.core.types import BarSeries
from src.ml.helpers.mtf_align import get_confirmed
from src.ml.types import MTFData

H = 3_600_000  # 1 hour ms
D = 24 * H


def _bars(start_ms, count, step_ms, symbol, tf):
    rows = [
        {
            "timestamp": start_ms + i * step_ms,
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 10.0,
            "turnover": 1000.0,
        }
        for i in range(count)
    ]
    return BarSeries(
        symbol=symbol,
        timeframe=tf,
        bars=pd.DataFrame(rows),
    )


@pytest.fixture
def mtf():
    h1 = _bars(0, 48, H, "BTCUSDT", "1h")        # 48 bars over 2 days
    h4 = _bars(0, 12, 4 * H, "BTCUSDT", "4h")    # 12 bars
    d1 = _bars(0, 2, D, "BTCUSDT", "1d")         # 2 bars
    return MTFData(
        symbol="BTCUSDT",
        primary_tf="1h",
        series={"1h": h1, "4h": h4, "1d": d1},
    )


class TestGetConfirmed:
    def test_returns_confirmed_4h_bar(self, mtf):
        # 1h bar at index 5 closes at 5h → most recent 4h bar that closed strictly before 5h
        # 4h bar at timestamp=0 covers 0~4h, closes at 4h → confirmed
        # 4h bar at timestamp=4h covers 4h~8h, closes at 8h → NOT confirmed at t=5h
        t = 5 * H
        bar = get_confirmed(timestamp_ms=t, target_tf="4h", mtf=mtf)
        assert bar is not None
        assert bar["timestamp"] == 0  # 4h bar 0~4h

    def test_strictly_before_t(self, mtf):
        # At t = 4h exactly, the 4h bar starting at timestamp=0 closes at 4h.
        # 4h bar 0 close == t → NOT strictly before → must NOT be returned
        # The previous 4h bar does not exist → None
        t = 4 * H
        bar = get_confirmed(timestamp_ms=t, target_tf="4h", mtf=mtf)
        assert bar is None

    def test_returns_confirmed_1d_bar(self, mtf):
        t = 25 * H  # past day 1 close
        bar = get_confirmed(timestamp_ms=t, target_tf="1d", mtf=mtf)
        assert bar is not None
        assert bar["timestamp"] == 0

    def test_returns_none_before_first_confirmation(self, mtf):
        t = 1 * H
        bar = get_confirmed(timestamp_ms=t, target_tf="1d", mtf=mtf)
        assert bar is None

    def test_unknown_tf_raises(self, mtf):
        with pytest.raises(KeyError):
            get_confirmed(timestamp_ms=H, target_tf="2h", mtf=mtf)
