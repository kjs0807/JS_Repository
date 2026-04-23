"""backtester/optimizer.py 단위 테스트."""
import pytest
from src.core.config import BacktestConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.backtester.optimizer import GridSearchOptimizer, OptResult
from src.backtester.engine import BacktestEngine

class ParamStrategy:
    name = "ParamStrategy"
    timeframe = "1h"
    def __init__(self, threshold: float = 0.0):
        self.threshold = threshold
    def on_bar(self, bar, series, broker):
        if not broker.get_position(bar.symbol) and bar.close > self.threshold:
            broker.buy(bar.symbol, 0.01, stop_loss=bar.close*0.95, reason="param test")
    def on_fill(self, fill): pass
    def get_params(self): return {"threshold": self.threshold}
    def set_params(self, params):
        if "threshold" in params: self.threshold = params["threshold"]
    @property
    def warmup_bars(self): return 3

class TestGridSearchOptimizer:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_db_path, schema_path, sample_bar_data):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        self.db.upsert_bars("BTCUSDT", "1h", sample_bar_data)

    def test_returns_result(self):
        optimizer = GridSearchOptimizer(BacktestEngine())
        config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        result = optimizer.run(ParamStrategy, {"threshold": [40000.0, 40500.0]}, feed, config, "BTCUSDT")
        assert isinstance(result, OptResult)
        assert "threshold" in result.best_params
        assert len(result.all_results) == 2

    def test_best_has_highest_score(self):
        optimizer = GridSearchOptimizer(BacktestEngine())
        config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        result = optimizer.run(ParamStrategy, {"threshold": [40000.0, 40200.0, 40500.0]}, feed, config, "BTCUSDT")
        scores = [r[1] for r in result.all_results]
        assert result.best_score >= max(s for s in scores if s is not None)

    def test_single_param(self):
        optimizer = GridSearchOptimizer(BacktestEngine())
        config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        result = optimizer.run(ParamStrategy, {"threshold": [40000.0]}, feed, config, "BTCUSDT")
        assert len(result.all_results) == 1
