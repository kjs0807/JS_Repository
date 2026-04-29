"""LiveBroker helper 메서드 단위 테스트 (round 5)."""
from unittest.mock import MagicMock

from src.execution.live_broker import LiveBroker


def _make_broker() -> LiveBroker:
    """Bypass __init__ to avoid wallet sync."""
    broker = LiveBroker.__new__(LiveBroker)
    broker._rest = MagicMock()
    broker._alert = None
    broker._risk = MagicMock()
    broker._leverage = 3
    broker._initial_capital = 50000.0
    broker._positions = {}
    broker._equity = 50000.0
    return broker


def test_position_idx_for_long_returns_1():
    broker = _make_broker()
    assert broker._position_idx_for_side("LONG") == 1


def test_position_idx_for_short_returns_2():
    broker = _make_broker()
    assert broker._position_idx_for_side("SHORT") == 2


import logging

from src.execution.broker import Position


def _make_broker_with_long_pos() -> LiveBroker:
    broker = _make_broker()
    broker._positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="LONG", qty=0.1, entry_price=100.0,
        entry_time=0, stop_loss=95.0, take_profit=110.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    return broker


def test_update_stop_calls_set_trading_stop_with_position_idx_1_for_long():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_stop("BTCUSDT", 96.5)
    assert broker._rest.set_trading_stop.call_count == 1
    kwargs = broker._rest.set_trading_stop.call_args.kwargs
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["stop_loss"] == 96.5
    assert kwargs["position_idx"] == 1


def test_update_stop_updates_local_only_on_success():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_stop("BTCUSDT", 96.5)
    assert broker._positions["BTCUSDT"].stop_loss == 96.5


def test_update_stop_does_not_update_local_on_api_failure(caplog):
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.side_effect = RuntimeError("boom")
    with caplog.at_level(logging.WARNING, logger="src.execution.live_broker"):
        broker.update_stop("BTCUSDT", 96.5)
    # local 미갱신 (서버 값과 일치 유지)
    assert broker._positions["BTCUSDT"].stop_loss == 95.0
    # WARN 로그 확인
    assert any("set_trading_stop" in rec.message and "BTCUSDT" in rec.message
               for rec in caplog.records)


def test_update_stop_no_op_for_unknown_symbol():
    broker = _make_broker()
    broker.update_stop("BTCUSDT", 96.5)
    assert broker._rest.set_trading_stop.call_count == 0


def test_update_stop_short_uses_position_idx_2():
    broker = _make_broker()
    broker._positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="SHORT", qty=0.1, entry_price=100.0,
        entry_time=0, stop_loss=105.0, take_profit=90.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_stop("BTCUSDT", 103.5)
    kwargs = broker._rest.set_trading_stop.call_args.kwargs
    assert kwargs["position_idx"] == 2


def test_update_tp_calls_set_trading_stop_with_take_profit():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_tp("BTCUSDT", 112.0)
    kwargs = broker._rest.set_trading_stop.call_args.kwargs
    assert kwargs["take_profit"] == 112.0
    assert kwargs["position_idx"] == 1
    assert broker._positions["BTCUSDT"].take_profit == 112.0


def test_update_tp_does_not_update_local_on_api_failure():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.side_effect = RuntimeError("boom")
    broker.update_tp("BTCUSDT", 112.0)
    # 로컬 미갱신 (110.0 그대로)
    assert broker._positions["BTCUSDT"].take_profit == 110.0


def test_manual_update_tp_routes_through_update_tp():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.manual_update_tp("BTCUSDT", 113.0)
    assert broker._rest.set_trading_stop.call_count == 1
    assert broker._positions["BTCUSDT"].take_profit == 113.0


def test_sync_positions_parses_stop_loss_take_profit():
    broker = _make_broker()
    broker._rest.get_positions.return_value = [
        {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "0.1",
            "avgPrice": "100.0",
            "stopLoss": "95.5",
            "takeProfit": "110.5",
            "unrealisedPnl": "0.0",
        },
    ]
    broker.sync_positions()
    pos = broker._positions["BTCUSDT"]
    assert pos.stop_loss == 95.5
    assert pos.take_profit == 110.5
    assert pos.entry_price == 100.0
    assert pos.qty == 0.1


def test_sync_positions_handles_empty_stop_loss_string():
    broker = _make_broker()
    broker._rest.get_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "100.0",
         "stopLoss": "", "takeProfit": "", "unrealisedPnl": "0.0"},
    ]
    broker.sync_positions()
    pos = broker._positions["BTCUSDT"]
    assert pos.stop_loss == 0.0
    assert pos.take_profit is None


def test_sync_positions_handles_zero_stop_loss():
    broker = _make_broker()
    broker._rest.get_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Sell", "size": "0.1", "avgPrice": "100.0",
         "stopLoss": "0", "takeProfit": "0", "unrealisedPnl": "0.0"},
    ]
    broker.sync_positions()
    pos = broker._positions["BTCUSDT"]
    assert pos.stop_loss == 0.0
    assert pos.take_profit is None
    assert pos.side == "SHORT"
