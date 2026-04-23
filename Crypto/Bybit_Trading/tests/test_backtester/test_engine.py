"""backtester/engine.py 단위 테스트."""
import pytest
import pandas as pd
from src.core.types import Bar, BarSeries
from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.execution.broker import Broker
from src.backtester.engine import BacktestEngine, BacktestResult

class SimpleStrategy:
    name = "SimpleAlwaysBuy"
    timeframe = "1h"
    def on_bar(self, bar: Bar, series: BarSeries, broker) -> None:
        if not broker.get_position(bar.symbol):
            broker.buy(bar.symbol, 0.01, stop_loss=bar.close * 0.95,
                      take_profit=bar.close * 1.05, reason="always buy")
    def on_fill(self, fill): pass
    def get_params(self): return {}
    def set_params(self, params): pass
    @property
    def warmup_bars(self): return 5

class NeverTradeStrategy:
    name = "NeverTrade"
    timeframe = "1h"
    def on_bar(self, bar, series, broker): pass
    def on_fill(self, fill): pass
    def get_params(self): return {}
    def set_params(self, params): pass
    @property
    def warmup_bars(self): return 5

class TestBacktestEngine:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_db_path, schema_path, sample_bar_data):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        self.db.upsert_bars("BTCUSDT", "1h", sample_bar_data)
        self.feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        self.config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        self.engine = BacktestEngine()

    def test_run_returns_result(self):
        result = self.engine.run(SimpleStrategy(), self.feed, self.config, symbol="BTCUSDT")
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "SimpleAlwaysBuy"
        assert result.symbol == "BTCUSDT"

    def test_run_with_trades(self):
        result = self.engine.run(SimpleStrategy(), self.feed, self.config, symbol="BTCUSDT")
        assert result.total_trades > 0
        assert len(result.trades) > 0
        assert len(result.equity_curve) > 0

    def test_run_no_trades_strategy(self):
        result = self.engine.run(NeverTradeStrategy(), self.feed, self.config, symbol="BTCUSDT")
        assert result.total_trades == 0
        assert result.total_pnl == 0.0

    def test_run_respects_warmup(self):
        calls = []
        class TrackingStrategy:
            name = "Tracking"
            timeframe = "1h"
            def on_bar(self, bar, series, broker): calls.append(bar.timestamp)
            def on_fill(self, fill): pass
            def get_params(self): return {}
            def set_params(self, p): pass
            @property
            def warmup_bars(self): return 5
        self.engine.run(TrackingStrategy(), self.feed, self.config, symbol="BTCUSDT")
        assert len(calls) == 5  # 10 bars, warmup=5, so bars 6-10

    def test_equity_curve_starts_with_initial(self):
        result = self.engine.run(NeverTradeStrategy(), self.feed, self.config, symbol="BTCUSDT")
        assert result.equity_curve[0] == 50000.0

    def test_result_has_metrics(self):
        result = self.engine.run(SimpleStrategy(), self.feed, self.config, symbol="BTCUSDT")
        assert hasattr(result, "sharpe_ratio")
        assert hasattr(result, "max_drawdown")
        assert hasattr(result, "profit_factor")
        assert hasattr(result, "win_rate")

    def test_feed_resets_between_runs(self):
        r1 = self.engine.run(SimpleStrategy(), self.feed, self.config, symbol="BTCUSDT")
        r2 = self.engine.run(SimpleStrategy(), self.feed, self.config, symbol="BTCUSDT")
        assert r1.total_trades == r2.total_trades
