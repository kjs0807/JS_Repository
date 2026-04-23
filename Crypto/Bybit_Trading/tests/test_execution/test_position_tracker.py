"""execution/position_tracker.py 단위 테스트."""
import pytest
from src.execution.position_tracker import PositionTracker
from src.execution.broker import Position

class TestPositionTracker:
    def setup_method(self):
        self.tracker = PositionTracker()

    def test_open_position(self):
        self.tracker.open(symbol="BTCUSDT", side="LONG", qty=0.01, entry_price=65000.0,
                         entry_time=1700000000000, stop_loss=60000.0, take_profit=70000.0,
                         strategy_name="TestStrategy")
        pos = self.tracker.get("BTCUSDT")
        assert pos is not None
        assert pos.side == "LONG"
        assert pos.qty == 0.01

    def test_get_nonexistent(self):
        assert self.tracker.get("NONEXIST") is None

    def test_close_position(self):
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, 70000.0, "Test")
        closed = self.tracker.close("BTCUSDT")
        assert closed is not None
        assert self.tracker.get("BTCUSDT") is None

    def test_close_nonexistent_returns_none(self):
        assert self.tracker.close("NONEXIST") is None

    def test_get_all(self):
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, 70000.0, "Test")
        self.tracker.open("ETHUSDT", "SHORT", 0.1, 3000.0, 1700000000000, 3200.0, 2800.0, "Test")
        assert len(self.tracker.get_all()) == 2

    def test_update_stop(self):
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, 70000.0, "Test")
        self.tracker.update_stop("BTCUSDT", 62000.0)
        assert self.tracker.get("BTCUSDT").stop_loss == 62000.0

    def test_update_tp(self):
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, 70000.0, "Test")
        self.tracker.update_tp("BTCUSDT", 75000.0)
        assert self.tracker.get("BTCUSDT").take_profit == 75000.0

    def test_update_unrealized_long(self):
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, 70000.0, "Test")
        self.tracker.update_unrealized("BTCUSDT", 66000.0)
        assert abs(self.tracker.get("BTCUSDT").unrealized_pnl - 10.0) < 0.01

    def test_update_unrealized_short(self):
        self.tracker.open("ETHUSDT", "SHORT", 0.1, 3000.0, 1700000000000, 3200.0, 2800.0, "Test")
        self.tracker.update_unrealized("ETHUSDT", 2900.0)
        assert abs(self.tracker.get("ETHUSDT").unrealized_pnl - 10.0) < 0.01

    def test_has_position(self):
        assert self.tracker.has_position("BTCUSDT") is False
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, None, "Test")
        assert self.tracker.has_position("BTCUSDT") is True

    def test_count(self):
        assert self.tracker.count == 0
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, None, "Test")
        assert self.tracker.count == 1

    def test_close_all(self):
        self.tracker.open("BTCUSDT", "LONG", 0.01, 65000.0, 1700000000000, 60000.0, None, "Test")
        self.tracker.open("ETHUSDT", "SHORT", 0.1, 3000.0, 1700000000000, 3200.0, None, "Test")
        closed = self.tracker.close_all()
        assert len(closed) == 2
        assert self.tracker.count == 0
