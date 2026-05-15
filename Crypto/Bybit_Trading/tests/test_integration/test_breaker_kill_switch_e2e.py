"""Stage C-2a: end-to-end breaker → kill switch → block flow.

These tests wire ALL the safety/observability layers together with
real instances (not mocks) so the failure → trip → engage → block
chain is exercised exactly the way it would behave in production:

  rest_client (mocked retCode != 0 failures)
        ↓
  LiveBroker._execute_order
        ↓ classify
  CircuitBreaker.record (real)
        ↓ trip
  KillSwitch.engage_via_file (real, writes disable_new_entry.flag)
        ↓
  BbkcBroker.buy on next call → kill_switch_block in orders.jsonl
        ↓
  close() / update_stop / update_tp still pass (managing existing
  positions must keep working)

The only mock is the REST client — everything else is the same
object graph the live runner builds via ``scripts/run_strategy_trade.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.config import RiskConfig
from src.execution.bbkc_demo_broker import BbkcBroker
from src.execution.broker import Position
from src.runtime.circuit_breaker import CircuitBreaker
from src.runtime.kill_switch import KillSwitch, FLAG_FILENAME
from src.runtime.order_failure import ALL_CATEGORIES, OrderFailureCategory
from src.runtime.order_logger import (
    OrderLogger,
    RESULT_EXCHANGE_REJECT,
    RESULT_KILL_SWITCH_BLOCK,
    RESULT_RISK_REJECT,
    RESULT_SUCCESS,
)


# ---------------------------------------------------------------------------
# Object-graph builder — mirror what scripts/run_strategy_trade.py builds.
# ---------------------------------------------------------------------------
def _build_real_graph(
    run_dir: Path,
    *,
    min_sample: int = 5,
    min_failures: int = 2,
    threshold: float = 0.10,
    risk_action: str = "ALLOW",
    risk_reason: str = "",
):
    """Construct a BbkcBroker wired to REAL CircuitBreaker + KillSwitch
    + OrderLogger, with a MagicMock REST client. Returns ``(broker, cb,
    ks, ol, rest)`` so tests can drive the rest_client side_effect."""
    run_dir.mkdir(parents=True, exist_ok=True)

    kill_switch = KillSwitch(run_dir=run_dir)
    alert = MagicMock()
    circuit_breaker = CircuitBreaker(
        kill_switch=kill_switch,
        alert_manager=alert,
        window_seconds=3600.0,
        failure_rate_threshold=threshold,
        min_sample=min_sample,
        min_failures=min_failures,
    )
    order_logger = OrderLogger(run_dir / "orders.jsonl")

    broker = BbkcBroker.__new__(BbkcBroker)
    broker._rest = MagicMock()
    broker._alert = alert
    broker._risk = MagicMock()
    broker._risk.config = RiskConfig()
    broker._risk.daily_pnl = 0.0
    broker._risk.check_order = MagicMock(
        return_value=MagicMock(action=risk_action, reason=risk_reason),
    )
    broker._leverage = 3
    broker._initial_capital = 50_000.0
    broker._positions = {}
    broker._equity = 50_000.0
    broker._run_dir = run_dir
    broker._orders_path = run_dir / "orders.jsonl"
    broker._symbols_allowed = {"BTCUSDT", "ETHUSDT"}
    broker._qty_step = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._min_qty = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._per_symbol_max_pos_pct = {}
    broker._kill_switch = kill_switch
    broker._failure_counters = {c: 0 for c in ALL_CATEGORIES}
    broker._success_count = 0
    broker._circuit_breaker = circuit_breaker
    broker._order_logger = order_logger
    broker._kill_switch_ref = kill_switch
    # C-2b: fill-tracking attrs default disabled — these e2e tests
    # focus on the breaker / kill-switch flow, not slippage telemetry.
    broker._last_bar_close = {}
    broker._fill_tracker = None
    broker._fill_logger = None
    return broker, circuit_breaker, kill_switch, order_logger, broker._rest


def _read_rows(path: Path):
    return [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


# ---------------------------------------------------------------------------
# Scenario 1: 5 exchange failures → breaker trips → flag written →
#             next buy is blocked → close still works.
# ---------------------------------------------------------------------------
class TestExchangeFailuresTripBreakerAndEngageKillSwitch:
    def test_full_flow_blocks_new_entries_but_lets_close_through(self, tmp_path):
        run_dir = tmp_path / "run"
        broker, cb, ks, ol, rest = _build_real_graph(
            run_dir, min_sample=5, min_failures=2, threshold=0.10,
        )

        # Inject 5 retCode=110012 (MIN_QTY) failures on place_order. The
        # 6th call should never happen because the kill switch blocks
        # the next buy() before we reach the REST layer.
        rest.place_order.side_effect = RuntimeError(
            "ErrCode: 110012, ErrMsg: Order qty lower than the minimum order qty"
        )

        for _ in range(5):
            assert broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0) == ""

        # Breaker tripped after the 5th failure: total=5, failures=5,
        # rate=100% >= 10%, failures(5) >= min_failures(2). Trip.
        assert cb.tripped is True
        assert (run_dir / FLAG_FILENAME).exists()
        flag_body = (run_dir / FLAG_FILENAME).read_text(encoding="utf-8")
        assert "circuit_breaker" in flag_body

        # 6th buy: kill switch is now engaged → BbkcBroker._check_kill_switch
        # returns False BEFORE we reach _execute_order, so the failure
        # counter does NOT increment and the next REST call is never made.
        prev_rest_calls = rest.place_order.call_count
        assert broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0) == ""
        assert rest.place_order.call_count == prev_rest_calls  # unchanged

        # Orders.jsonl: 5 exchange_reject + 1 kill_switch_block.
        rows = _read_rows(run_dir / "orders.jsonl")
        rejects = [r for r in rows if r["result"] == RESULT_EXCHANGE_REJECT]
        blocks = [r for r in rows if r["result"] == RESULT_KILL_SWITCH_BLOCK]
        assert len(rejects) == 5
        assert len(blocks) == 1
        # Every reject row classified MIN_QTY, breaker_eligible=True.
        for r in rejects:
            assert r["failure_category"] == OrderFailureCategory.MIN_QTY
            assert r["breaker_eligible"] is True
        # Block row: breaker_eligible=False, kill_switch_engaged=True.
        assert blocks[0]["breaker_eligible"] is False
        assert blocks[0]["kill_switch_engaged"] is True

        # Breaker stats: top_category=min_qty, exactly 5 events.
        stats = cb.stats()
        assert stats["total"] == 5
        assert stats["failures"] == 5
        assert stats["tripped"] is True
        assert stats["top_category"] == OrderFailureCategory.MIN_QTY

    def test_close_passes_through_kill_switch(self, tmp_path):
        """A position opened before the trip must still be closeable
        after kill switch engagement — only NEW entries are paused."""
        run_dir = tmp_path / "run"
        broker, cb, ks, ol, rest = _build_real_graph(run_dir)

        # Manually engage the kill switch (operator path) without
        # going through the breaker.
        ks.engage_via_file(message="operator manual")
        assert ks.is_new_entry_disabled() is True

        # Seed an open long position.
        broker._positions["BTCUSDT"] = Position(
            symbol="BTCUSDT", side="LONG", qty=0.01,
            entry_price=70_000.0, entry_time=0,
            stop_loss=68_000.0, take_profit=75_000.0,
            unrealized_pnl=0.0, strategy_name="STRATEGY",
        )

        # Close succeeds — REST returns an orderId.
        rest.place_order.return_value = {"orderId": "CLOSE-1"}
        oid = broker.close("BTCUSDT", reason="trail")
        assert oid == "CLOSE-1"
        # Position cleared locally on success.
        assert "BTCUSDT" not in broker._positions

        # Now a new buy must be blocked.
        oid2 = broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0)
        assert oid2 == ""

        # Orders.jsonl: close success + kill_switch_block on the buy.
        rows = _read_rows(run_dir / "orders.jsonl")
        kinds = [r["result"] for r in rows]
        assert RESULT_SUCCESS in kinds  # close
        assert RESULT_KILL_SWITCH_BLOCK in kinds

    def test_update_stop_and_update_tp_unaffected_by_kill_switch(self, tmp_path):
        run_dir = tmp_path / "run"
        broker, cb, ks, ol, rest = _build_real_graph(run_dir)
        ks.engage_via_file(message="operator manual")

        broker._positions["BTCUSDT"] = Position(
            symbol="BTCUSDT", side="LONG", qty=0.01,
            entry_price=70_000.0, entry_time=0,
            stop_loss=68_000.0, take_profit=75_000.0,
            unrealized_pnl=0.0, strategy_name="STRATEGY",
        )

        # update_stop / update_tp call rest.set_trading_stop, NOT
        # place_order. The kill switch does not gate them.
        broker.update_stop("BTCUSDT", new_stop=69_000.0)
        broker.update_tp("BTCUSDT", new_tp=76_000.0)
        assert broker._positions["BTCUSDT"].stop_loss == 69_000.0
        assert broker._positions["BTCUSDT"].take_profit == 76_000.0
        # set_trading_stop was called twice (once for SL, once for TP).
        assert rest.set_trading_stop.call_count == 2


# ---------------------------------------------------------------------------
# Scenario 2: risk_reject does NOT trip the breaker (B2 invariant).
# ---------------------------------------------------------------------------
class TestRiskRejectDoesNotTripBreaker:
    def test_many_risk_rejects_keep_breaker_cold(self, tmp_path):
        run_dir = tmp_path / "run"
        broker, cb, ks, ol, rest = _build_real_graph(
            run_dir,
            min_sample=5, min_failures=2, threshold=0.10,
            risk_action="REJECT", risk_reason="daily loss limit",
        )

        for _ in range(20):
            assert broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0) == ""

        # Even after 20 risk_reject outcomes the breaker is cold and
        # the kill switch flag does NOT exist.
        assert cb.tripped is False
        assert cb.stats()["total"] == 0   # nothing fed the window
        assert not (run_dir / FLAG_FILENAME).exists()

        # rest.place_order was never reached.
        rest.place_order.assert_not_called()

        # Counter still captures the rejects.
        assert (
            broker.get_failure_counters()[OrderFailureCategory.RISK_REJECT] == 20
        )

        # All 20 audit rows are risk_reject with breaker_eligible=False.
        rows = _read_rows(run_dir / "orders.jsonl")
        assert len(rows) == 20
        for r in rows:
            assert r["result"] == RESULT_RISK_REJECT
            assert r["breaker_eligible"] is False


# ---------------------------------------------------------------------------
# Scenario 3: min_failures gate prevents 1-of-5 transient trip.
# ---------------------------------------------------------------------------
class TestMinFailuresGateUnderRealisticMix:
    def test_one_transient_in_five_does_not_engage_kill_switch(self, tmp_path):
        run_dir = tmp_path / "run"
        broker, cb, ks, ol, rest = _build_real_graph(
            run_dir, min_sample=5, min_failures=2, threshold=0.10,
        )

        # 4 successes followed by 1 transient network blip.
        def _router(*args, **kwargs):
            call_n = rest.place_order.call_count
            if call_n <= 4:
                return {"orderId": f"OID-{call_n}"}
            raise RuntimeError("ErrCode: 10006, Too many requests")
        rest.place_order.side_effect = _router

        for _ in range(5):
            broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0)

        # rate = 1/5 = 20% >= 10%, BUT failures(1) < min_failures(2) — no trip.
        assert cb.tripped is False
        assert not (run_dir / FLAG_FILENAME).exists()
        stats = cb.stats()
        assert stats["total"] == 5
        assert stats["failures"] == 1
