"""execution/order_manager.py 단위 테스트."""
import pytest
from src.execution.order_manager import OrderManager
from src.execution.broker import Order, Fill

class TestOrderManager:
    def setup_method(self):
        self.mgr = OrderManager()

    def test_create_order_returns_id(self):
        order_id = self.mgr.create(symbol="BTCUSDT", side="BUY", qty=0.01,
                                   order_type="MARKET", stop_loss=60000.0, take_profit=70000.0,
                                   strategy_name="TestStrategy", source="STRATEGY", reason="test entry")
        assert isinstance(order_id, str)
        assert len(order_id) > 0

    def test_get_pending_orders(self):
        self.mgr.create("BTCUSDT", "BUY", 0.01, "MARKET", 60000.0, 70000.0, "Test", "STRATEGY", "test")
        self.mgr.create("ETHUSDT", "SELL", 0.1, "MARKET", 3200.0, 2800.0, "Test", "STRATEGY", "test")
        assert len(self.mgr.get_pending()) == 2

    def test_fill_order(self):
        order_id = self.mgr.create("BTCUSDT", "BUY", 0.01, "MARKET", 60000.0, 70000.0,
                                   "Test", "STRATEGY", "test")
        fill = self.mgr.fill(order_id, price=65012.5, fee=0.36, timestamp=1700000000000, fill_type="ENTRY")
        assert isinstance(fill, Fill)
        assert fill.price == 65012.5
        assert len(self.mgr.get_pending()) == 0

    def test_fill_nonexistent_returns_none(self):
        assert self.mgr.fill("nonexistent", 65000.0, 0.3, 1700000000000, "ENTRY") is None

    def test_cancel_order(self):
        order_id = self.mgr.create("BTCUSDT", "BUY", 0.01, "MARKET", 60000.0, 70000.0,
                                   "Test", "STRATEGY", "test")
        assert self.mgr.cancel(order_id) is True
        assert len(self.mgr.get_pending()) == 0

    def test_cancel_nonexistent(self):
        assert self.mgr.cancel("nonexistent") is False

    def test_get_order(self):
        order_id = self.mgr.create("BTCUSDT", "BUY", 0.01, "MARKET", 60000.0, 70000.0,
                                   "Test", "STRATEGY", "reason")
        order = self.mgr.get_order(order_id)
        assert order is not None
        assert order.reason == "reason"

    def test_get_fills(self):
        oid1 = self.mgr.create("BTCUSDT", "BUY", 0.01, "MARKET", 60000.0, 70000.0, "Test", "STRATEGY", "t")
        oid2 = self.mgr.create("ETHUSDT", "SELL", 0.1, "MARKET", 3200.0, None, "Test", "STRATEGY", "t")
        self.mgr.fill(oid1, 65000.0, 0.36, 1700000000000, "ENTRY")
        self.mgr.fill(oid2, 3000.0, 0.17, 1700000001000, "ENTRY")
        assert len(self.mgr.get_fills()) == 2

    def test_clear_pending(self):
        self.mgr.create("BTCUSDT", "BUY", 0.01, "MARKET", 60000.0, None, "Test", "STRATEGY", "t")
        self.mgr.clear_pending()
        assert len(self.mgr.get_pending()) == 0
