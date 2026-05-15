"""Stage B-5: order-failure circuit breaker."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.runtime.circuit_breaker import CircuitBreaker
from src.runtime.kill_switch import KillSwitch, FLAG_FILENAME


class _Clock:
    """Manual clock so tests can drive the sliding window deterministically."""
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ---------------------------------------------------------------------------
# constructor validation
# ---------------------------------------------------------------------------
class TestConstructor:
    @pytest.mark.parametrize("kwargs", [
        {"window_seconds": 0},
        {"window_seconds": -1},
        {"failure_rate_threshold": 0.0},
        {"failure_rate_threshold": 1.5},
        {"min_sample": 0},
        {"min_failures": 0},
    ])
    def test_invalid_params_raise(self, kwargs):
        defaults = dict(
            window_seconds=60, failure_rate_threshold=0.5,
            min_sample=2, min_failures=1,
        )
        defaults.update(kwargs)
        with pytest.raises(ValueError):
            CircuitBreaker(**defaults)

    def test_default_min_failures_is_two(self):
        cb = CircuitBreaker(
            window_seconds=60, failure_rate_threshold=0.10, min_sample=5,
        )
        assert cb.stats()["min_failures"] == 2


# ---------------------------------------------------------------------------
# Stage C-1 hotfix: stats() must expose top_category. Runner / snapshot
# both read it; without this key those fields silently show "".
# ---------------------------------------------------------------------------
class TestStatsTopCategory:
    def test_top_category_present_even_when_empty(self):
        cb = CircuitBreaker(
            window_seconds=60, failure_rate_threshold=0.10, min_sample=5,
        )
        stats = cb.stats()
        assert "top_category" in stats
        # No events yet -> top_category default is "other".
        assert stats["top_category"] == "other"

    def test_top_category_reflects_most_common_failure(self):
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.10,
            min_sample=2, min_failures=2,
            clock=clock,
        )
        cb.record(False, "min_qty")
        cb.record(False, "min_qty")
        cb.record(False, "qty_step")
        assert cb.stats()["top_category"] == "min_qty"


# ---------------------------------------------------------------------------
# Stage C-1: min_failures gate (sensitivity tuning, B2 decision)
# ---------------------------------------------------------------------------
class TestMinFailuresGate:
    def test_one_failure_in_five_does_not_trip(self):
        """C-1 sensitivity rule: a single transient failure in a 5-sample
        window must not be enough to trip the breaker even when the
        failure rate (20%) is above the 10% threshold."""
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.10,
            min_sample=5, min_failures=2,
            clock=clock,
        )
        for _ in range(4):
            cb.record(True, "")
        cb.record(False, "min_qty")
        # 1/5 = 20% >= 10%, but failures < min_failures so NO trip.
        assert cb.tripped is False

    def test_two_failures_above_threshold_trips(self):
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.10,
            min_sample=5, min_failures=2,
            clock=clock,
        )
        for _ in range(3):
            cb.record(True, "")
        for _ in range(2):
            cb.record(False, "min_qty")
        # 2/5 = 40% >= 10% AND failures (2) >= min_failures (2). Trip.
        assert cb.tripped is True

    def test_breaker_ineligible_events_skip_window(self):
        """C-1 (B2): risk_reject / kill_switch_block must not feed the
        breaker — even if many of them arrive, the breaker stays cold."""
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.10,
            min_sample=5, min_failures=2,
            clock=clock,
        )
        for _ in range(20):
            cb.record(False, "risk_reject", breaker_eligible=False)
        assert cb.tripped is False
        assert cb.stats()["total"] == 0
        assert cb.stats()["failures"] == 0


# ---------------------------------------------------------------------------
# does NOT trip below threshold or below min_sample
# ---------------------------------------------------------------------------
class TestBelowThreshold:
    def test_below_min_sample_no_trip(self):
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.10, min_sample=5,
            clock=clock,
        )
        for _ in range(4):
            cb.record(False, "other")
        assert cb.tripped is False
        assert cb.stats()["total"] == 4
        assert cb.stats()["failures"] == 4

    def test_below_rate_no_trip(self):
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=5,
            clock=clock,
        )
        for _ in range(8):
            cb.record(True, "")
        for _ in range(2):
            cb.record(False, "min_qty")
        assert cb.tripped is False
        assert pytest.approx(cb.stats()["rate"], rel=1e-9) == 0.2


# ---------------------------------------------------------------------------
# trips when threshold + min_sample both satisfied
# ---------------------------------------------------------------------------
class TestTrip:
    def test_trips_and_engages_kill_switch(self, tmp_path):
        clock = _Clock()
        run_dir = tmp_path / "rd"
        run_dir.mkdir()
        ks = KillSwitch(run_dir=run_dir)
        alert = MagicMock()
        cb = CircuitBreaker(
            kill_switch=ks, alert_manager=alert,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=4,
            min_failures=2,
            clock=clock,
        )
        for _ in range(2):
            cb.record(True, "")
        for _ in range(2):
            cb.record(False, "min_qty")
        # 2/4 = 50%, >= 0.50 -> trip.
        assert cb.tripped is True
        assert (run_dir / FLAG_FILENAME).exists()
        body = (run_dir / FLAG_FILENAME).read_text(encoding="utf-8")
        assert "circuit_breaker" in body
        # C-1: structured alert is preferred over on_error.
        alert.on_breaker_tripped.assert_called_once()
        # killed switch sees engaged state through file flag:
        assert ks.is_new_entry_disabled() is True

    def test_does_not_re_trip_once_tripped(self, tmp_path):
        clock = _Clock()
        run_dir = tmp_path / "rd"
        run_dir.mkdir()
        ks = KillSwitch(run_dir=run_dir)
        alert = MagicMock()
        cb = CircuitBreaker(
            kill_switch=ks, alert_manager=alert,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=2,
            min_failures=2,
            clock=clock,
        )
        cb.record(False, "min_qty")
        cb.record(False, "min_qty")
        assert cb.tripped is True
        assert alert.on_breaker_tripped.call_count == 1
        # More failures must not trigger a second alert / second flag write.
        cb.record(False, "min_qty")
        cb.record(False, "min_qty")
        assert alert.on_breaker_tripped.call_count == 1


# ---------------------------------------------------------------------------
# sliding window evicts old events
# ---------------------------------------------------------------------------
class TestSlidingWindow:
    def test_old_events_drop_out(self):
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=2,
            clock=clock,
        )
        # 2 failures inside the window:
        cb.record(False, "min_qty")
        cb.record(False, "min_qty")
        # Step past the window: those events evict on next record.
        clock.advance(120)
        cb.record(True, "")
        # 2 of those 3 events are stale; the surviving event is success.
        assert cb.stats()["total"] == 1
        assert cb.stats()["failures"] == 0

    def test_eviction_does_not_un_trip(self):
        """Once tripped, eviction must not silently re-arm the breaker."""
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=2,
            clock=clock,
        )
        cb.record(False, "min_qty")
        cb.record(False, "min_qty")
        assert cb.tripped is True
        clock.advance(120)
        cb.record(True, "")
        assert cb.tripped is True   # operator must call reset()


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------
class TestReset:
    def test_reset_clears_tripped_and_history(self):
        clock = _Clock()
        cb = CircuitBreaker(
            kill_switch=None, alert_manager=None,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=2,
            clock=clock,
        )
        cb.record(False, "x"); cb.record(False, "x")
        assert cb.tripped is True
        cb.reset()
        assert cb.tripped is False
        assert cb.stats()["total"] == 0


# ---------------------------------------------------------------------------
# kill_switch hook failure is logged but does not crash record()
# ---------------------------------------------------------------------------
class TestRobustness:
    def test_kill_switch_engage_failure_does_not_propagate(self, caplog):
        clock = _Clock()
        ks = MagicMock()
        ks.engage_via_file.side_effect = OSError("disk full")
        alert = MagicMock()
        cb = CircuitBreaker(
            kill_switch=ks, alert_manager=alert,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=2,
            clock=clock,
        )
        import logging
        with caplog.at_level(logging.ERROR, logger="src.runtime.circuit_breaker"):
            cb.record(False, "x")
            cb.record(False, "x")
        assert cb.tripped is True
        assert any("failed to engage" in r.message for r in caplog.records)

    def test_alert_failure_does_not_propagate(self, tmp_path):
        clock = _Clock()
        run_dir = tmp_path / "rd"; run_dir.mkdir()
        ks = KillSwitch(run_dir=run_dir)
        alert = MagicMock()
        alert.on_error.side_effect = RuntimeError("telegram down")
        cb = CircuitBreaker(
            kill_switch=ks, alert_manager=alert,
            window_seconds=60, failure_rate_threshold=0.50, min_sample=2,
            clock=clock,
        )
        cb.record(False, "x")
        cb.record(False, "x")
        assert cb.tripped is True
        # flag still written even though alert failed:
        assert (run_dir / FLAG_FILENAME).exists()
