"""execution/backtest_broker.py 단위 테스트."""
import pytest
from src.core.types import Bar
from src.core.config import BacktestConfig, RiskConfig
from src.execution.backtest_broker import BacktestBroker

def _make_bar(timestamp, open, high, low, close, symbol="BTCUSDT"):
    return Bar(symbol=symbol, timestamp=timestamp, timeframe="1h",
               open=open, high=high, low=low, close=close, volume=1000.0)

class TestBacktestBrokerBasic:
    def setup_method(self):
        self.config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
        self.risk_config = RiskConfig(max_concurrent=10, daily_loss_limit_pct=0.10, max_drawdown_pct=0.20)
        self.broker = BacktestBroker(self.config, self.risk_config)

    def test_initial_portfolio(self):
        port = self.broker.get_portfolio()
        assert port.initial_capital == 50000.0
        assert port.equity == 50000.0
        assert len(port.positions) == 0

    def test_buy_creates_pending(self):
        oid = self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, take_profit=70000.0, reason="test")
        assert isinstance(oid, str)
        assert self.broker.get_position("BTCUSDT") is None

    def test_sell_creates_pending(self):
        oid = self.broker.sell("ETHUSDT", 0.1, stop_loss=3200.0, reason="short")
        assert isinstance(oid, str)
        assert self.broker.get_position("ETHUSDT") is None

    def test_get_positions_empty(self):
        assert self.broker.get_positions() == []

class TestBacktestBrokerProcessBar:
    def setup_method(self):
        self.config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        self.risk_config = RiskConfig(max_concurrent=10, daily_loss_limit_pct=0.50, max_drawdown_pct=0.50)
        self.broker = BacktestBroker(self.config, self.risk_config)

    def test_pending_fills_on_next_bar_open(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, take_profit=70000.0, reason="test")
        bar = _make_bar(1700003600000, 65000.0, 66000.0, 64000.0, 65500.0)
        self.broker.process_bar(bar)
        pos = self.broker.get_position("BTCUSDT")
        assert pos is not None
        assert pos.side == "LONG"
        assert pos.entry_price == 65000.0

    def test_short_fills_on_next_bar_open(self):
        self.broker.sell("ETHUSDT", 0.1, stop_loss=3200.0, reason="short")
        bar = _make_bar(1700003600000, 3000.0, 3100.0, 2900.0, 2950.0, "ETHUSDT")
        self.broker.process_bar(bar)
        pos = self.broker.get_position("ETHUSDT")
        assert pos is not None
        assert pos.side == "SHORT"
        assert pos.entry_price == 3000.0

    def test_stop_loss_long(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=64000.0, reason="test")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64500.0, 65500.0)
        self.broker.process_bar(bar1)
        assert self.broker.get_position("BTCUSDT") is not None
        bar2 = _make_bar(1700007200000, 65000.0, 65500.0, 63000.0, 63500.0)
        self.broker.process_bar(bar2)
        assert self.broker.get_position("BTCUSDT") is None

    def test_stop_loss_short(self):
        self.broker.sell("ETHUSDT", 0.1, stop_loss=3200.0, reason="test")
        bar1 = _make_bar(1700003600000, 3000.0, 3100.0, 2900.0, 2950.0, "ETHUSDT")
        self.broker.process_bar(bar1)
        assert self.broker.get_position("ETHUSDT") is not None
        bar2 = _make_bar(1700007200000, 3100.0, 3300.0, 3000.0, 3150.0, "ETHUSDT")
        self.broker.process_bar(bar2)
        assert self.broker.get_position("ETHUSDT") is None

    def test_take_profit_long(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, take_profit=67000.0, reason="test")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64500.0, 65500.0)
        self.broker.process_bar(bar1)
        bar2 = _make_bar(1700007200000, 66000.0, 68000.0, 65500.0, 67500.0)
        self.broker.process_bar(bar2)
        assert self.broker.get_position("BTCUSDT") is None

    def test_take_profit_short(self):
        self.broker.sell("ETHUSDT", 0.1, stop_loss=3200.0, take_profit=2800.0, reason="test")
        bar1 = _make_bar(1700003600000, 3000.0, 3100.0, 2900.0, 2950.0, "ETHUSDT")
        self.broker.process_bar(bar1)
        bar2 = _make_bar(1700007200000, 2900.0, 2950.0, 2700.0, 2750.0, "ETHUSDT")
        self.broker.process_bar(bar2)
        assert self.broker.get_position("ETHUSDT") is None

    def test_close_position(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="test")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64500.0, 65500.0)
        self.broker.process_bar(bar1)
        self.broker.close("BTCUSDT", reason="manual")
        bar2 = _make_bar(1700007200000, 66000.0, 67000.0, 65000.0, 66500.0)
        self.broker.process_bar(bar2)
        assert self.broker.get_position("BTCUSDT") is None

    def test_close_all(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="test")
        self.broker.sell("ETHUSDT", 0.1, stop_loss=3200.0, reason="test")
        bar1_btc = _make_bar(1700003600000, 65000.0, 66000.0, 64000.0, 65500.0)
        bar1_eth = _make_bar(1700003600000, 3000.0, 3100.0, 2900.0, 2950.0, "ETHUSDT")
        self.broker.process_bar(bar1_btc)
        self.broker.process_bar(bar1_eth)
        assert len(self.broker.get_positions()) == 2
        self.broker.close_all(reason="end")
        assert len(self.broker.get_positions()) == 0

    def test_gap_down_open_stop(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=64000.0, reason="test")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64500.0, 65500.0)
        self.broker.process_bar(bar1)
        bar2 = _make_bar(1700007200000, 63000.0, 63500.0, 62000.0, 62500.0)
        self.broker.process_bar(bar2)
        assert self.broker.get_position("BTCUSDT") is None

    def test_equity_updates_after_profitable_trade(self):
        self.broker.buy("BTCUSDT", 1.0, stop_loss=60000.0, take_profit=66000.0, reason="test")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64000.0, 65500.0)
        self.broker.process_bar(bar1)
        bar2 = _make_bar(1700007200000, 65500.0, 67000.0, 65000.0, 66500.0)
        self.broker.process_bar(bar2)
        assert self.broker.get_portfolio().equity > 50000.0

    def test_calc_qty(self):
        qty = self.broker.calc_qty("BTCUSDT", risk_pct=0.02, stop_distance=1000.0)
        assert abs(qty - 1.0) < 0.01

    def test_get_trades(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, take_profit=67000.0, reason="test")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64500.0, 65500.0)
        self.broker.process_bar(bar1)
        bar2 = _make_bar(1700007200000, 66000.0, 68000.0, 65500.0, 67500.0)
        self.broker.process_bar(bar2)
        assert len(self.broker.get_trades()) >= 1

    def test_get_equity_curve(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="test")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64500.0, 65500.0)
        self.broker.process_bar(bar1)
        curve = self.broker.get_equity_curve()
        assert len(curve) > 0
        assert curve[0] == 50000.0

class TestBacktestBrokerCosts:
    def setup_method(self):
        self.config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.001, slippage_pct=0.0)
        self.risk_config = RiskConfig(max_concurrent=10, daily_loss_limit_pct=0.50, max_drawdown_pct=0.50)
        self.broker = BacktestBroker(self.config, self.risk_config)

    def test_fee_deducted_on_entry(self):
        self.broker.buy("BTCUSDT", 1.0, stop_loss=60000.0, reason="test")
        bar = _make_bar(1700003600000, 65000.0, 66000.0, 64000.0, 65500.0)
        self.broker.process_bar(bar)
        assert self.broker.get_portfolio().equity < 50000.0

class TestBacktestBrokerManual:
    def setup_method(self):
        self.config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        self.risk_config = RiskConfig(max_concurrent=10, daily_loss_limit_pct=0.50, max_drawdown_pct=0.50)
        self.broker = BacktestBroker(self.config, self.risk_config)

    def test_manual_buy(self):
        oid = self.broker.manual_buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="수동")
        assert isinstance(oid, str)

    def test_manual_close(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="entry")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64000.0, 65500.0)
        self.broker.process_bar(bar1)
        self.broker.manual_close("BTCUSDT", reason="수동 청산")
        bar2 = _make_bar(1700007200000, 66000.0, 67000.0, 65000.0, 66500.0)
        self.broker.process_bar(bar2)
        assert self.broker.get_position("BTCUSDT") is None

    def test_manual_update_stop(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, reason="entry")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64000.0, 65500.0)
        self.broker.process_bar(bar1)
        self.broker.manual_update_stop("BTCUSDT", 63000.0)
        assert self.broker.get_position("BTCUSDT").stop_loss == 63000.0

    def test_manual_update_tp(self):
        self.broker.buy("BTCUSDT", 0.01, stop_loss=60000.0, take_profit=70000.0, reason="entry")
        bar1 = _make_bar(1700003600000, 65000.0, 66000.0, 64000.0, 65500.0)
        self.broker.process_bar(bar1)
        self.broker.manual_update_tp("BTCUSDT", 75000.0)
        assert self.broker.get_position("BTCUSDT").take_profit == 75000.0
