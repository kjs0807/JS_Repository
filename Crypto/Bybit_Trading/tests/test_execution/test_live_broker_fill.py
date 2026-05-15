"""Stage C-2b: LiveBroker slippage / fill registration hook."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.config import RiskConfig
from src.execution.live_broker import LiveBroker
from src.runtime.fill_logger import FillLogger, STATUS_MISSING_INTENT
from src.runtime.fill_tracker import FillTracker
from src.runtime.order_failure import ALL_CATEGORIES


def _make_broker(tmp_path: Path):
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


# ---------------------------------------------------------------------------
# set_last_bar_close
# ---------------------------------------------------------------------------
class TestSetLastBarClose:
    def test_seeds_intent_price(self, tmp_path):
        broker = _make_broker(tmp_path)
        broker.set_last_bar_close("BTCUSDT", 70_000.0)
        assert broker._last_bar_close["BTCUSDT"] == 70_000.0

    def test_zero_or_negative_ignored(self, tmp_path):
        broker = _make_broker(tmp_path)
        broker.set_last_bar_close("BTCUSDT", 0.0)
        broker.set_last_bar_close("BTCUSDT", -1.0)
        assert "BTCUSDT" not in broker._last_bar_close


# ---------------------------------------------------------------------------
# successful order registers a pending fill
# ---------------------------------------------------------------------------
class TestRegistersPendingOnSuccess:
    def test_success_registers_in_tracker(self, tmp_path):
        broker = _make_broker(tmp_path)
        tracker = FillTracker()
        fl = FillLogger(tmp_path / "fills.jsonl")
        broker.set_fill_tracking(tracker, fl)
        broker.set_last_bar_close("BTCUSDT", 70_000.0)
        broker._rest.place_order.return_value = {"orderId": "OID-1"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "entry",
        )
        assert tracker.pending_count() == 1
        snap = tracker.pending_snapshot()[0]
        assert snap.order_id == "OID-1"
        assert snap.symbol == "BTCUSDT"
        assert snap.side == "Buy"
        assert snap.intent_qty == 0.01
        assert snap.intent_price == 70_000.0
        # No fills.jsonl row yet — reconcile will produce it later.
        assert not (tmp_path / "fills.jsonl").exists()

    def test_success_without_intent_emits_missing_intent_row(self, tmp_path):
        broker = _make_broker(tmp_path)
        tracker = FillTracker()
        fl = FillLogger(tmp_path / "fills.jsonl")
        broker.set_fill_tracking(tracker, fl)
        # NO set_last_bar_close.
        broker._rest.place_order.return_value = {"orderId": "OID-2"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "entry",
        )
        # Tracker stays empty — unreconcilable.
        assert tracker.pending_count() == 0
        # fills.jsonl gets a missing_intent row immediately.
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["status"] == STATUS_MISSING_INTENT
        assert row["order_id"] == "OID-2"
        assert row["intent_price"] is None

    def test_failures_do_not_register_anything(self, tmp_path):
        broker = _make_broker(tmp_path)
        tracker = FillTracker()
        fl = FillLogger(tmp_path / "fills.jsonl")
        broker.set_fill_tracking(tracker, fl)
        broker.set_last_bar_close("BTCUSDT", 70_000.0)
        broker._rest.place_order.side_effect = RuntimeError(
            "ErrCode: 110012, qty too small"
        )
        broker._execute_order(
            "BTCUSDT", "Buy", 0.0001, 68_000.0, None,
            "STRATEGY", "entry",
        )
        assert tracker.pending_count() == 0
        assert not (tmp_path / "fills.jsonl").exists()

    def test_no_tracker_attached_is_quiet_noop(self, tmp_path):
        broker = _make_broker(tmp_path)
        # No set_fill_tracking call.
        broker.set_last_bar_close("BTCUSDT", 70_000.0)
        broker._rest.place_order.return_value = {"orderId": "OID-X"}
        # Must not raise.
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "entry",
        )

    def test_tracker_exception_does_not_break_order_path(self, tmp_path):
        broker = _make_broker(tmp_path)
        tracker = MagicMock()
        tracker.register.side_effect = RuntimeError("tracker broken")
        fl = FillLogger(tmp_path / "fills.jsonl")
        broker.set_fill_tracking(tracker, fl)
        broker.set_last_bar_close("BTCUSDT", 70_000.0)
        broker._rest.place_order.return_value = {"orderId": "OID-X"}
        # Must not raise.
        oid = broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 68_000.0, None,
            "STRATEGY", "entry",
        )
        assert oid == "OID-X"  # success preserved despite telemetry failure
