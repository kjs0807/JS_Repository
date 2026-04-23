"""backtester/walk_forward.py 단위 테스트."""
import pytest
from src.core.config import BacktestConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.backtester.walk_forward import WalkForwardAnalyzer, WalkForwardResult
from src.backtester.config import WalkForwardConfig

class WFStrategy:
    name = "WFStrategy"
    timeframe = "1h"
    def __init__(self, period=5): self.period = period
    def on_bar(self, bar, series, broker):
        if not broker.get_position(bar.symbol):
            broker.buy(bar.symbol, 0.01, stop_loss=bar.close*0.95, reason="wf")
    def on_fill(self, fill): pass
    def get_params(self): return {"period": self.period}
    def set_params(self, params):
        if "period" in params: self.period = params["period"]
    @property
    def warmup_bars(self): return self.period

def _setup_large_db(db, n_bars=500):
    base_ts = 1700000000000
    bars = [{"symbol": "BTCUSDT", "open_time": base_ts + i*3600000,
             "open": 40000.0+(i%50)*10, "high": 40100.0+(i%50)*10,
             "low": 39900.0+(i%50)*10, "close": 40050.0+(i%50)*10,
             "volume": 1000.0, "turnover": 40000000.0} for i in range(n_bars)]
    db.upsert_bars("BTCUSDT", "1h", bars)

class TestWalkForwardAnalyzer:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_db_path, schema_path):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        _setup_large_db(self.db, 500)
        self.config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)

    def test_returns_result(self):
        wf = WalkForwardAnalyzer(WalkForwardConfig(is_months=2, oos_months=1, min_windows=1))
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        result = wf.run(type(WFStrategy()), {"period": [3, 5]}, feed, self.config, "BTCUSDT")
        assert isinstance(result, WalkForwardResult)
        assert result.strategy_name == "WFStrategy"

    def test_has_windows(self):
        wf = WalkForwardAnalyzer(WalkForwardConfig(is_months=2, oos_months=1, min_windows=1))
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        result = wf.run(type(WFStrategy()), {"period": [3, 5]}, feed, self.config, "BTCUSDT")
        assert len(result.windows) > 0

    def test_window_has_is_oos_results(self):
        wf = WalkForwardAnalyzer(WalkForwardConfig(is_months=2, oos_months=1, min_windows=1))
        feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        result = wf.run(type(WFStrategy()), {"period": [3, 5]}, feed, self.config, "BTCUSDT")
        for w in result.windows:
            assert w.is_result is not None
            assert w.oos_result is not None
            assert w.best_params is not None

    def test_insufficient_data_returns_empty(self):
        import os
        db2_path = os.path.join(os.path.dirname(self.db.db_path), "test2.db")
        db2 = DBManager(db_path=db2_path, schema_path=self.db.schema_path)
        db2.initialize()
        _setup_large_db(db2, 10)
        wf = WalkForwardAnalyzer(WalkForwardConfig(is_months=6, oos_months=2, min_windows=3))
        feed = HistoricalDataFeed(db=db2, symbols=["BTCUSDT"], timeframe="1h")
        result = wf.run(type(WFStrategy()), {"period": [5]}, feed, self.config, "BTCUSDT")
        assert len(result.windows) == 0
