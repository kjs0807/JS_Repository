"""backtester/explorer.py 단위 테스트."""
import pytest
from src.core.config import BacktestConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.registry import StrategyRegistry
from src.backtester.explorer import StrategyExplorer, ExplorationReport
from src.backtester.config import ScreeningCriteria

class ExploreStrategy:
    name = "ExploreStrategy"
    timeframe = "1h"
    def __init__(self, threshold=40000.0): self.threshold = threshold
    def on_bar(self, bar, series, broker):
        if not broker.get_position(bar.symbol) and bar.close > self.threshold:
            broker.buy(bar.symbol, 0.01, stop_loss=bar.close*0.95,
                      take_profit=bar.close*1.05, reason="explore")
    def on_fill(self, fill): pass
    def get_params(self): return {"threshold": self.threshold}
    def set_params(self, params):
        if "threshold" in params: self.threshold = params["threshold"]
    @property
    def warmup_bars(self): return 3

class TestStrategyExplorer:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_db_path, schema_path):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        base_ts = 1700000000000
        bars = [{"symbol": "BTCUSDT", "open_time": base_ts + i*3600000,
                 "open": 40000.0+i*10, "high": 40100.0+i*10, "low": 39900.0+i*10,
                 "close": 40050.0+i*10, "volume": 1000.0, "turnover": 40000000.0}
                for i in range(100)]
        self.db.upsert_bars("BTCUSDT", "1h", bars)
        self.registry = StrategyRegistry()
        self.registry.register(ExploreStrategy, {"threshold": [40000.0, 40200.0]})

    def test_explore_returns_report(self):
        explorer = StrategyExplorer()
        config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        report = explorer.explore(registry=self.registry, data_feed=feed,
                                  config=config, symbols=["BTCUSDT"])
        assert isinstance(report, ExplorationReport)
        assert len(report.screened) > 0

    def test_report_has_summary(self):
        explorer = StrategyExplorer()
        config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        report = explorer.explore(self.registry, feed, config, ["BTCUSDT"])
        summary = report.summary()
        assert isinstance(summary, str) and "ExploreStrategy" in summary
