"""Stage C-2b: end-to-end slippage / fill pipeline.

Wires LiveBroker → FillTracker → FillLogger with REAL objects and
exercises:

  set_last_bar_close(intent_price)
        ↓
  broker._execute_order → success → tracker.register(orderId)
        ↓
  (simulated heartbeat tick)
        ↓
  tracker.reconcile_all(rest, fill_logger)
        ↓
  fills.jsonl row with computed slippage_bps

The REST client is mocked but the in-memory pipeline is real.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.config import RiskConfig
from src.execution.live_broker import LiveBroker
from src.runtime.fill_logger import (
    FillLogger,
    STATUS_FILLED,
    STATUS_MISSING_INTENT,
    STATUS_TIMEOUT,
)
from src.runtime.fill_tracker import FillTracker
from src.runtime.order_failure import ALL_CATEGORIES


def _make_broker():
    broker = LiveBroker.__new__(LiveBroker)
    broker._rest = MagicMock()
    broker._alert = MagicMock()
    broker._risk = MagicMock()
    broker._risk.config = RiskConfig()
    broker._risk.daily_pnl = 0.0
    broker._risk.check_order = MagicMock(
        return_value=MagicMock(action="ALLOW", reason=""),
    )
    broker._leverage = 3
    broker._initial_capital = 50_000.0
    broker._positions = {}
    broker._equity = 50_000.0
    broker._failure_counters = {c: 0 for c in ALL_CATEGORIES}
    broker._success_count = 0
    broker._circuit_breaker = None
    broker._order_logger = None
    broker._kill_switch_ref = None
    broker._last_bar_close = {}
    broker._fill_tracker = None
    broker._fill_logger = None
    return broker


def _rows(path: Path):
    return [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


class TestFullSlippagePipeline:
    def test_buy_with_adverse_slippage_lands_in_fills_jsonl(self, tmp_path):
        broker = _make_broker()
        tracker = FillTracker()
        fill_logger = FillLogger(tmp_path / "fills.jsonl")
        broker.set_fill_tracking(tracker, fill_logger)

        # 1) Runner-side: seed intent price on this bar.
        broker.set_last_bar_close("BTCUSDT", 70_000.0)

        # 2) Strategy submits a buy. REST returns the orderId.
        broker._rest.place_order.return_value = {"orderId": "OID-1"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "breakout",
        )
        assert tracker.pending_count() == 1
        assert not (tmp_path / "fills.jsonl").exists()

        # 3) Heartbeat-side: reconcile pulls the fill from REST.
        broker._rest.get_order.return_value = {
            "avgPrice": "70014.0",   # 14 USD above intent
            "cumExecQty": "0.01",    # full fill
            "orderStatus": "Filled",
        }
        emitted = tracker.reconcile_all(broker._rest, fill_logger)
        assert emitted == 1
        assert tracker.pending_count() == 0

        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["order_id"] == "OID-1"
        assert row["status"] == STATUS_FILLED
        assert row["fill_price"] == pytest.approx(70_014.0)
        # 14 / 70000 * 10000 = 2.0 bps adverse for a buy.
        assert row["slippage_bps"] == pytest.approx(2.0, rel=1e-3)
        assert row["slippage_abs"] == pytest.approx(14.0)

    def test_sell_with_favourable_slippage(self, tmp_path):
        broker = _make_broker()
        tracker = FillTracker()
        fill_logger = FillLogger(tmp_path / "fills.jsonl")
        broker.set_fill_tracking(tracker, fill_logger)
        broker.set_last_bar_close("ETHUSDT", 2_500.0)

        broker._rest.place_order.return_value = {"orderId": "OID-S"}
        broker._execute_order(
            "ETHUSDT", "Sell", 0.5, 2_600.0, None,
            "STRATEGY", "exit",
        )

        # Sell filled ABOVE intent → favourable → negative slippage.
        broker._rest.get_order.return_value = {
            "avgPrice": "2502.5", "cumExecQty": "0.5",
        }
        tracker.reconcile_all(broker._rest, fill_logger)
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["slippage_abs"] == pytest.approx(-2.5)
        assert row["slippage_bps"] < 0

    def test_missing_intent_short_circuits_to_missing_intent_row(self, tmp_path):
        """No bar close seeded for this symbol → no pending entry,
        a missing_intent row goes straight to fills.jsonl."""
        broker = _make_broker()
        tracker = FillTracker()
        fill_logger = FillLogger(tmp_path / "fills.jsonl")
        broker.set_fill_tracking(tracker, fill_logger)

        broker._rest.place_order.return_value = {"orderId": "OID-N"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "manual probe",
        )
        assert tracker.pending_count() == 0
        rows = _rows(tmp_path / "fills.jsonl")
        assert rows[0]["status"] == STATUS_MISSING_INTENT
        assert rows[0]["intent_price"] is None


class TestReconciliationDoesNotFeedBreaker:
    """C-2b safety invariant: fill telemetry must never affect the
    circuit breaker. Even when reconciliation observes failures (REST
    errors, timeouts) the breaker count stays at zero."""

    def test_get_order_failures_do_not_record_breaker_events(self, tmp_path):
        broker = _make_broker()
        tracker = FillTracker()
        fill_logger = FillLogger(tmp_path / "fills.jsonl")
        breaker = MagicMock()  # attached for sniffing only
        broker._circuit_breaker = breaker
        broker.set_fill_tracking(tracker, fill_logger)
        broker.set_last_bar_close("BTCUSDT", 70_000.0)

        # 1) Successful place_order — breaker sees ONE success record.
        broker._rest.place_order.return_value = {"orderId": "OID-1"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "entry",
        )
        assert breaker.record.call_count == 1

        # 2) Reconcile hits a REST exception. Breaker must NOT see a
        # second record() call.
        broker._rest.get_order.side_effect = RuntimeError("rate limited")
        tracker.reconcile_all(broker._rest, fill_logger)
        assert breaker.record.call_count == 1  # unchanged

    def test_timeout_aging_does_not_record_breaker_events(self, tmp_path):
        broker = _make_broker()
        # Inject a controllable clock seeded from the broker's submit
        # timestamp after the order, so the tracker's "now" tracks the
        # same reference as the registered pending entry. The broker
        # itself uses time.time() directly inside _execute_order; we
        # only need the tracker's clock to be > submit_ts_ms + timeout
        # for the aging check to fire.
        class _Clk:
            def __init__(self): self.t = 0
            def __call__(self): return self.t
        clk = _Clk()
        tracker = FillTracker(timeout_ms=10, clock=clk)
        fill_logger = FillLogger(tmp_path / "fills.jsonl")
        breaker = MagicMock()
        broker._circuit_breaker = breaker
        broker.set_fill_tracking(tracker, fill_logger)
        broker.set_last_bar_close("BTCUSDT", 70_000.0)
        broker._rest.place_order.return_value = {"orderId": "OID-T"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "entry",
        )
        success_call_count = breaker.record.call_count  # 1
        # Read the real submit_ts the broker recorded, then set the
        # tracker clock past it + timeout so aging triggers.
        registered = tracker.pending_snapshot()[0]
        clk.t = registered.submit_ts_ms + 100  # > timeout_ms (10)
        tracker.reconcile_all(broker._rest, fill_logger)
        # Timeout row produced — breaker untouched.
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["status"] == STATUS_TIMEOUT
        assert breaker.record.call_count == success_call_count
