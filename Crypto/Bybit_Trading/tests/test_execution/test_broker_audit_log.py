"""Stage C-1: end-to-end audit log coverage.

These tests verify that both broker classes (LiveBroker and BbkcBroker)
write to ``orders.jsonl`` with the unified schema for every outcome
shape the C-1 plan enumerated:

  * success
  * exchange reject (pybit raised retCode != 0)
  * exchange fail (success-shaped reply, no orderId)
  * risk_reject (breaker_eligible=False)
  * kill_switch_block (breaker_eligible=False)
  * universe_block (breaker_eligible=False)
  * qty_below_min (breaker_eligible=False)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.config import RiskConfig
from src.execution.bbkc_demo_broker import BbkcBroker
from src.execution.live_broker import LiveBroker
from src.runtime.kill_switch import KillSwitch, FLAG_FILENAME
from src.runtime.order_failure import ALL_CATEGORIES, OrderFailureCategory
from src.runtime.order_logger import (
    OrderLogger,
    RESULT_EXCHANGE_FAIL,
    RESULT_EXCHANGE_REJECT,
    RESULT_KILL_SWITCH_BLOCK,
    RESULT_QTY_BELOW_MIN,
    RESULT_RISK_REJECT,
    RESULT_SUCCESS,
    RESULT_UNIVERSE_BLOCK,
)


def _read(path: Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ---------------------------------------------------------------------------
# LiveBroker — outcome coverage via _execute_order
# ---------------------------------------------------------------------------
def _live_broker(tmp_path: Path) -> LiveBroker:
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
    broker._order_logger = OrderLogger(tmp_path / "orders.jsonl")
    broker._kill_switch_ref = None
    return broker


class TestLiveBrokerAudit:
    def test_success_row(self, tmp_path):
        broker = _live_broker(tmp_path)
        broker._rest.place_order.return_value = {"orderId": "OID-1"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 70_000.0, 80_000.0,
            "STRATEGY", "entry signal",
        )
        rows = _read(tmp_path / "orders.jsonl")
        assert len(rows) == 1
        row = rows[0]
        assert row["result"] == RESULT_SUCCESS
        assert row["order_id"] == "OID-1"
        assert row["breaker_eligible"] is True
        assert row["equity_snapshot"] == 50_000.0

    def test_exchange_reject_row(self, tmp_path):
        broker = _live_broker(tmp_path)
        broker._rest.place_order.side_effect = RuntimeError(
            "ErrCode: 110012, qty lower than min order qty",
        )
        broker._execute_order(
            "BTCUSDT", "Buy", 0.0001, 70_000.0, None,
            "STRATEGY", "entry",
        )
        row = _read(tmp_path / "orders.jsonl")[0]
        assert row["result"] == RESULT_EXCHANGE_REJECT
        assert row["failure_category"] == OrderFailureCategory.MIN_QTY
        assert row["breaker_eligible"] is True

    def test_exchange_fail_row_no_order_id(self, tmp_path):
        broker = _live_broker(tmp_path)
        broker._rest.place_order.return_value = {"some": "junk"}
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 70_000.0, None,
            "STRATEGY", "entry",
        )
        row = _read(tmp_path / "orders.jsonl")[0]
        assert row["result"] == RESULT_EXCHANGE_FAIL
        assert row["failure_category"] == OrderFailureCategory.OTHER

    def test_risk_reject_row_breaker_ineligible(self, tmp_path):
        broker = _live_broker(tmp_path)
        broker._risk.check_order = MagicMock(
            return_value=MagicMock(action="REJECT", reason="daily limit"),
        )
        broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 70_000.0, None,
            "STRATEGY", "entry",
        )
        row = _read(tmp_path / "orders.jsonl")[0]
        assert row["result"] == RESULT_RISK_REJECT
        assert row["failure_category"] == OrderFailureCategory.RISK_REJECT
        assert row["breaker_eligible"] is False
        assert row["failure_message"] == "daily limit"


# ---------------------------------------------------------------------------
# BbkcBroker — pre-flight blocks
# ---------------------------------------------------------------------------
def _bbkc_broker(tmp_path: Path, *, kill_switch=None) -> BbkcBroker:
    broker = BbkcBroker.__new__(BbkcBroker)
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
    broker._run_dir = tmp_path
    broker._orders_path = tmp_path / "orders.jsonl"
    broker._symbols_allowed = {"BTCUSDT", "ETHUSDT"}
    broker._qty_step = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._min_qty = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._per_symbol_max_pos_pct = {}
    broker._kill_switch = kill_switch
    broker._failure_counters = {c: 0 for c in ALL_CATEGORIES}
    broker._success_count = 0
    broker._circuit_breaker = None
    broker._order_logger = OrderLogger(tmp_path / "orders.jsonl")
    broker._kill_switch_ref = kill_switch
    return broker


class TestBbkcBrokerAudit:
    def test_universe_block_row(self, tmp_path):
        broker = _bbkc_broker(tmp_path)
        # SOLUSDT not in allowed universe.
        result = broker.buy("SOLUSDT", 0.01, stop_loss=50.0)
        assert result == ""
        rows = _read(tmp_path / "orders.jsonl")
        assert len(rows) == 1
        assert rows[0]["result"] == RESULT_UNIVERSE_BLOCK
        assert rows[0]["breaker_eligible"] is False
        assert rows[0]["symbol"] == "SOLUSDT"

    def test_kill_switch_block_row(self, tmp_path):
        run_dir = tmp_path / "rd"
        run_dir.mkdir()
        (run_dir / FLAG_FILENAME).touch()
        ks = KillSwitch(run_dir=run_dir)
        broker = _bbkc_broker(tmp_path, kill_switch=ks)
        result = broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0)
        assert result == ""
        rows = _read(tmp_path / "orders.jsonl")
        assert rows[0]["result"] == RESULT_KILL_SWITCH_BLOCK
        assert rows[0]["kill_switch_engaged"] is True
        assert rows[0]["breaker_eligible"] is False

    def test_qty_below_min_row(self, tmp_path):
        broker = _bbkc_broker(tmp_path)
        # BTC qty_step=0.001, min=0.001; pass 0.0001 -> rounds to 0.
        result = broker.buy("BTCUSDT", 0.0001, stop_loss=70_000.0)
        assert result == ""
        rows = _read(tmp_path / "orders.jsonl")
        assert rows[0]["result"] == RESULT_QTY_BELOW_MIN
        assert rows[0]["breaker_eligible"] is False
