"""Regression for the ``1M`` vs ``1m`` bug in timeframe_to_ws_interval.

Old code did ``timeframe.lower()`` before lookup so ``"1M"`` (month)
silently collapsed to ``"1m"`` (minute) and mapped to ``"1"`` instead
of ``"M"``. The fix preserves case for the case-distinct keys and falls
back to a case-insensitive lookup only when it is unambiguous.
"""
from __future__ import annotations

import pytest

from src.runtime.strategy_runner import (
    TIMEFRAME_TO_WS_INTERVAL,
    timeframe_to_ws_interval,
)


class TestTimeframeMappingDistinctCase:
    def test_minute_lowercase(self):
        assert timeframe_to_ws_interval("1m") == "1"

    def test_month_uppercase_is_M_not_1(self):
        # The bug: "1M" must resolve to month ("M"), not minute ("1").
        assert timeframe_to_ws_interval("1M") == "M"

    def test_minute_and_month_distinct(self):
        assert timeframe_to_ws_interval("1m") != timeframe_to_ws_interval("1M")

    def test_week(self):
        assert timeframe_to_ws_interval("1w") == "W"

    def test_day(self):
        assert timeframe_to_ws_interval("1d") == "D"


class TestTimeframeMappingCommon:
    def test_hour(self):
        assert timeframe_to_ws_interval("1h") == "60"

    def test_four_hour(self):
        assert timeframe_to_ws_interval("4h") == "240"

    def test_fifteen_minute(self):
        assert timeframe_to_ws_interval("15m") == "15"


class TestTimeframeMappingCaseInsensitive:
    """Hour/minute forms accept case-variants as a convenience as long as
    the lowercase form is unambiguous (no collision with a case-distinct
    key like 1M)."""

    def test_uppercase_hour(self):
        assert timeframe_to_ws_interval("1H") == "60"

    def test_uppercase_four_hour(self):
        assert timeframe_to_ws_interval("4H") == "240"

    def test_uppercase_fifteen_minute(self):
        assert timeframe_to_ws_interval("15M") == "15"

    def test_uppercase_day(self):
        assert timeframe_to_ws_interval("1D") == "D"

    def test_whitespace_stripped(self):
        assert timeframe_to_ws_interval("  1h  ") == "60"


class TestTimeframeMappingErrors:
    def test_unknown_raises(self):
        with pytest.raises(ValueError) as excinfo:
            timeframe_to_ws_interval("7m")
        assert "unsupported" in str(excinfo.value).lower()

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            timeframe_to_ws_interval("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            timeframe_to_ws_interval(None)  # type: ignore[arg-type]

    def test_known_pairs_are_present(self):
        """Smoke: make sure the dict still carries the keys we promise."""
        for key in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"):
            assert key in TIMEFRAME_TO_WS_INTERVAL
