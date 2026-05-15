"""Stage B-4 / B-5: LiveBroker._execute_order failure classification +
circuit-breaker wiring."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.config import RiskConfig
from src.execution.live_broker import LiveBroker
from src.runtime.order_failure import OrderFailureCategory


def _make_broker() -> LiveBroker:
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
    # Stage B-4 / B-5 attrs that the real __init__ would set:
    from src.runtime.order_failure import ALL_CATEGORIES
    broker._failure_counters = {c: 0 for c in ALL_CATEGORIES}
    broker._success_count = 0
    broker._circuit_breaker = None
    return broker


# ---------------------------------------------------------------------------
# success path
# ---------------------------------------------------------------------------
def test_success_increments_success_count():
    broker = _make_broker()
    broker._rest.place_order.return_value = {"orderId": "OID-123"}
    oid = broker._execute_order(
        "BTCUSDT", "Buy", 0.01, 75_000.0, None, "STRATEGY", "test",
    )
    assert oid == "OID-123"
    assert broker.get_order_success_count() == 1
    assert all(v == 0 for v in broker.get_failure_counters().values())


# ---------------------------------------------------------------------------
# pybit raised - classified
# ---------------------------------------------------------------------------
def test_exception_classified_min_notional():
    broker = _make_broker()
    broker._rest.place_order.side_effect = RuntimeError(
        "ErrCode: 110007, ErrMsg: Order does not meet minimum order value"
    )
    oid = broker._execute_order(
        "BTCUSDT", "Buy", 0.001, 75_000.0, None, "STRATEGY", "test",
    )
    assert oid == ""
    counters = broker.get_failure_counters()
    assert counters[OrderFailureCategory.MIN_NOTIONAL] == 1
    # alert was called with the category in the body
    assert broker._alert.on_error.called
    body = broker._alert.on_error.call_args[0][0]
    assert OrderFailureCategory.MIN_NOTIONAL in body


def test_exception_classified_qty_step():
    broker = _make_broker()
    broker._rest.place_order.side_effect = RuntimeError(
        "ErrCode: 110017, qty precision invalid"
    )
    broker._execute_order(
        "ETHUSDT", "Sell", 3.131, 2_300.0, None, "STRATEGY", "test",
    )
    assert broker.get_failure_counters()[OrderFailureCategory.QTY_STEP] == 1


# ---------------------------------------------------------------------------
# dict-error shape (rest_client.place_order returns {"error": retMsg})
# ---------------------------------------------------------------------------
def test_dict_error_classified():
    broker = _make_broker()
    broker._rest.place_order.return_value = {
        "error": "position idx not match position mode",
    }
    oid = broker._execute_order(
        "BTCUSDT", "Buy", 0.01, 75_000.0, None, "STRATEGY", "test",
    )
    assert oid == ""
    counters = broker.get_failure_counters()
    assert counters[OrderFailureCategory.POSITION_IDX] == 1


# ---------------------------------------------------------------------------
# RiskManager REJECT
# ---------------------------------------------------------------------------
def test_risk_reject_increments_risk_reject_counter():
    broker = _make_broker()
    broker._risk.check_order = MagicMock(
        return_value=MagicMock(action="REJECT", reason="daily limit"),
    )
    oid = broker._execute_order(
        "BTCUSDT", "Buy", 0.01, 75_000.0, None, "STRATEGY", "test",
    )
    assert oid == ""
    counters = broker.get_failure_counters()
    assert counters[OrderFailureCategory.RISK_REJECT] == 1
    # place_order must not have been called when risk rejected:
    broker._rest.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# missing orderId in success-shaped response
# ---------------------------------------------------------------------------
def test_missing_order_id_counts_as_other_failure():
    broker = _make_broker()
    broker._rest.place_order.return_value = {"some": "junk"}
    oid = broker._execute_order(
        "BTCUSDT", "Buy", 0.01, 75_000.0, None, "STRATEGY", "test",
    )
    assert oid == ""
    counters = broker.get_failure_counters()
    assert counters[OrderFailureCategory.OTHER] == 1


# ---------------------------------------------------------------------------
# B-5 wiring: circuit breaker sees every outcome
# ---------------------------------------------------------------------------
def test_circuit_breaker_record_called_on_success():
    broker = _make_broker()
    cb = MagicMock()
    broker.set_circuit_breaker(cb)
    broker._rest.place_order.return_value = {"orderId": "X"}
    broker._execute_order("BTCUSDT", "Buy", 0.01, 75_000.0, None, "STRATEGY", "t")
    cb.record.assert_called_once_with(success=True, category="")


def test_circuit_breaker_record_called_on_failure_with_category():
    broker = _make_broker()
    cb = MagicMock()
    broker.set_circuit_breaker(cb)
    broker._rest.place_order.side_effect = RuntimeError(
        "ErrCode: 110012, qty lower than min order qty"
    )
    broker._execute_order("BTCUSDT", "Buy", 0.0001, 75_000.0, None, "STRATEGY", "t")
    cb.record.assert_called_once_with(
        success=False, category=OrderFailureCategory.MIN_QTY,
    )


def test_circuit_breaker_record_exception_does_not_crash_order_path(caplog):
    broker = _make_broker()
    cb = MagicMock()
    cb.record.side_effect = RuntimeError("breaker storage failed")
    broker.set_circuit_breaker(cb)
    broker._rest.place_order.return_value = {"orderId": "X"}
    import logging
    with caplog.at_level(logging.WARNING, logger="src.execution.live_broker"):
        oid = broker._execute_order(
            "BTCUSDT", "Buy", 0.01, 75_000.0, None, "STRATEGY", "t",
        )
    assert oid == "X"
    assert any("circuit_breaker" in r.message for r in caplog.records)
