"""execution/broker.py 데이터 타입 테스트."""
from src.execution.broker import Position, Portfolio, Fill, Order

class TestPosition:
    def test_create_position(self):
        pos = Position(symbol="BTCUSDT", side="LONG", qty=0.01, entry_price=65000.0,
                       entry_time=1700000000000, stop_loss=60000.0, take_profit=70000.0,
                       unrealized_pnl=0.0, strategy_name="TestStrategy")
        assert pos.symbol == "BTCUSDT"
        assert pos.side == "LONG"
        assert pos.entry_price == 65000.0

    def test_position_without_tp(self):
        pos = Position(symbol="ETHUSDT", side="SHORT", qty=0.1, entry_price=3000.0,
                       entry_time=1700000000000, stop_loss=3200.0, take_profit=None,
                       unrealized_pnl=-10.0, strategy_name="MeanReversion")
        assert pos.take_profit is None

class TestPortfolio:
    def test_create_portfolio(self):
        port = Portfolio(initial_capital=50000.0, equity=50500.0, available_margin=48000.0,
                        used_margin=2500.0, realized_pnl=500.0, daily_pnl=125.0, positions=[])
        assert port.equity == 50500.0
        assert len(port.positions) == 0

    def test_portfolio_with_positions(self):
        pos = Position(symbol="BTCUSDT", side="LONG", qty=0.01, entry_price=65000.0,
                       entry_time=1700000000000, stop_loss=60000.0, take_profit=70000.0,
                       unrealized_pnl=50.0, strategy_name="Test")
        port = Portfolio(initial_capital=50000.0, equity=50050.0, available_margin=47000.0,
                        used_margin=3000.0, realized_pnl=0.0, daily_pnl=0.0, positions=[pos])
        assert len(port.positions) == 1

class TestFill:
    def test_create_fill(self):
        fill = Fill(order_id="abc123", symbol="BTCUSDT", side="BUY", qty=0.01,
                   price=65012.5, fee=0.36, timestamp=1700000000000, fill_type="ENTRY")
        assert fill.price == 65012.5
        assert fill.fill_type == "ENTRY"

    def test_fill_is_frozen(self):
        fill = Fill(order_id="x", symbol="BTCUSDT", side="BUY", qty=0.01,
                   price=65000.0, fee=0.3, timestamp=1700000000000, fill_type="ENTRY")
        try:
            fill.price = 99999.0
            assert False, "frozen"
        except AttributeError:
            pass

class TestOrder:
    def test_create_strategy_order(self):
        order = Order(order_id="ord001", symbol="BTCUSDT", side="BUY", qty=0.01,
                     order_type="MARKET", stop_loss=60000.0, take_profit=70000.0,
                     strategy_name="MomentumBreakout", source="STRATEGY",
                     reason="BB upper breakout", created_at=1700000000000)
        assert order.source == "STRATEGY"

    def test_create_manual_order(self):
        order = Order(order_id="ord002", symbol="ETHUSDT", side="SELL", qty=0.1,
                     order_type="MARKET", stop_loss=3200.0, take_profit=None,
                     strategy_name="MANUAL", source="MANUAL",
                     reason="수동 숏", created_at=1700000000000)
        assert order.source == "MANUAL"

    def test_order_is_frozen(self):
        order = Order(order_id="x", symbol="BTCUSDT", side="BUY", qty=0.01,
                     order_type="MARKET", stop_loss=60000.0, take_profit=None,
                     strategy_name="Test", source="STRATEGY", reason="test",
                     created_at=1700000000000)
        try:
            order.qty = 999.0
            assert False, "frozen"
        except AttributeError:
            pass
