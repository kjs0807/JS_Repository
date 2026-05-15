"""Stage C-1 hotfix: StrategyTradeRunner alert wiring.

These tests target the ``_check_state_edge_alerts`` helper directly
so we can verify the duplicate-alert hotfix without spinning up the
full WS / DB / broker pipeline.

Hotfix invariant (the reason this file exists):
    CircuitBreaker fires ``on_breaker_tripped`` itself the moment it
    trips. The runner must NOT re-fire it from the next heartbeat tick;
    otherwise a single trip produces two Telegram messages.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.runtime.strategy_runner import StrategyTradeRunner


def _make_runner_for_alert_test(*, alert, kill_switch, circuit_breaker):
    """Build a runner skeleton with __new__ so __init__ side-effects
    (which probe the strategy factory) are skipped. Only the fields
    ``_check_state_edge_alerts`` reads are populated."""
    r = StrategyTradeRunner.__new__(StrategyTradeRunner)
    r._alert = alert
    r._kill_switch = kill_switch
    r._circuit_breaker = circuit_breaker
    r._ks_alerted = False
    return r


# ---------------------------------------------------------------------------
# Hotfix #1: runner must NOT emit on_breaker_tripped — the breaker does.
# ---------------------------------------------------------------------------
class TestNoBreakerDuplicate:
    def test_tripped_breaker_is_not_re_alerted_by_runner(self):
        alert = MagicMock()
        breaker = SimpleNamespace(
            tripped=True,
            stats=lambda: {
                "rate": 0.4, "failures": 2, "total": 5,
                "window_seconds": 3600, "top_category": "min_qty",
            },
        )
        runner = _make_runner_for_alert_test(
            alert=alert, kill_switch=None, circuit_breaker=breaker,
        )
        # Edge-check fired multiple times — must never produce an alert.
        for _ in range(5):
            runner._check_state_edge_alerts()
        assert alert.on_breaker_tripped.call_count == 0

    def test_kill_switch_alert_fires_once(self):
        alert = MagicMock()
        # Mock kill switch claiming "engaged".
        ks = SimpleNamespace(
            is_new_entry_disabled=lambda: True,
            reason=lambda: "file disable_new_entry.flag",
        )
        runner = _make_runner_for_alert_test(
            alert=alert, kill_switch=ks, circuit_breaker=None,
        )
        for _ in range(5):
            runner._check_state_edge_alerts()
        assert alert.on_kill_switch_engaged.call_count == 1
        kwargs = alert.on_kill_switch_engaged.call_args.kwargs
        assert "disable_new_entry.flag" in kwargs["reason"]

    def test_kill_switch_not_engaged_no_alert(self):
        alert = MagicMock()
        ks = SimpleNamespace(
            is_new_entry_disabled=lambda: False,
            reason=lambda: "",
        )
        runner = _make_runner_for_alert_test(
            alert=alert, kill_switch=ks, circuit_breaker=None,
        )
        runner._check_state_edge_alerts()
        assert alert.on_kill_switch_engaged.call_count == 0
