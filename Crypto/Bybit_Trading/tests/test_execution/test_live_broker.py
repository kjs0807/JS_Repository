"""execution/live_broker.py 단위 테스트 (mock 기반)."""
import pytest
from unittest.mock import MagicMock
from src.core.config import RiskConfig
from src.execution.live_broker import LiveBroker
from src.execution.broker import Position

class TestLiveBroker:
    def setup_method(self):
        self.mock_rest = MagicMock()
        self.mock_alert = MagicMock()
        self.mock_rest.get_wallet_balance.return_value = {"equity": 50000.0, "available": 48000.0}
        self.mock_rest.get_positions.return_value = []
        self.broker = LiveBroker(rest_client=self.mock_rest, alert_manager=self.mock_alert,
            risk_config=RiskConfig(max_concurrent=10, daily_loss_limit_pct=0.50, max_drawdown_pct=0.50),
            leverage=3, initial_capital=50000.0)

    def test_buy_calls_rest(self):
        self.mock_rest.place_order.return_value = {"orderId": "o123"}
        assert self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="test") == "o123"
        self.mock_rest.place_order.assert_called_once()

    def test_sell_calls_rest(self):
        self.mock_rest.place_order.return_value = {"orderId": "o456"}
        assert self.broker.sell("ETHUSDT", 0.1, stop_loss=3200.0, reason="short") == "o456"

    def test_close_calls_rest(self):
        self.broker._positions["BTCUSDT"] = Position("BTCUSDT","LONG",0.01,65000.0,
            1700000000000,60000.0,70000.0,0.0,"Test")
        self.mock_rest.place_order.return_value = {"orderId": "close1"}
        assert self.broker.close("BTCUSDT", reason="manual") == "close1"

    def test_get_portfolio(self):
        port = self.broker.get_portfolio()
        assert port.equity == 50000.0 and port.initial_capital == 50000.0

    def test_get_position_none(self):
        assert self.broker.get_position("BTCUSDT") is None

    def test_get_positions_empty(self):
        assert self.broker.get_positions() == []

    def test_sync_positions_from_api(self):
        self.mock_rest.get_positions.return_value = [
            {"symbol":"BTCUSDT","side":"Buy","size":"0.01","avgPrice":"65000.0",
             "unrealisedPnl":"50.0","leverage":"3"}]
        self.broker.sync_positions()
        pos = self.broker.get_position("BTCUSDT")
        assert pos is not None and pos.side == "LONG" and pos.qty == 0.01

    def test_calc_qty(self):
        assert abs(self.broker.calc_qty("BTCUSDT", risk_pct=0.02, stop_distance=1000.0) - 1.0) < 0.01

    def test_manual_buy(self):
        self.mock_rest.place_order.return_value = {"orderId": "m1"}
        assert self.broker.manual_buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="수동") == "m1"

    def test_manual_close(self):
        self.broker._positions["BTCUSDT"] = Position("BTCUSDT","LONG",0.01,65000.0,
            1700000000000,60000.0,70000.0,0.0,"Test")
        self.mock_rest.place_order.return_value = {"orderId": "mc1"}
        assert self.broker.manual_close("BTCUSDT", reason="수동") == "mc1"

    def test_alert_on_buy(self):
        self.mock_rest.place_order.return_value = {"orderId": "o1"}
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="test")
        self.mock_alert.on_trade_entry.assert_called_once()
