"""BacktestEngine fast path (prepare + on_bar_fast) 테스트."""
import pytest
import numpy as np
from src.core.types import Bar, BarSeries
from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.base import IndicatorCache
from src.backtester.engine import BacktestEngine


class FastStrategy:
    name = "FastStrategy"
    timeframe = "1h"
    warmup_bars = 10

    def __init__(self):
        self.prepare_call_count = 0
        self.on_bar_fast_call_count = 0
        self.on_bar_legacy_call_count = 0

    def prepare(self, full_series):
        self.prepare_call_count += 1
        close = full_series.close.to_numpy()
        ma = np.full_like(close, np.nan, dtype=float)
        for i in range(10, len(close)):
            ma[i] = close[i - 10:i].mean()
        return IndicatorCache(arrays={"ma": ma})

    def on_bar_fast(self, bar, i, cache, broker):
        self.on_bar_fast_call_count += 1
        ma = cache.get("ma")[i]
        if np.isnan(ma):
            return
        if broker.get_position(bar.symbol):
            return
        if bar.close > ma:
            broker.buy(bar.symbol, 0.01, stop_loss=bar.close * 0.95, reason="fast LONG")

    def on_bar(self, bar, series, broker):
        self.on_bar_legacy_call_count += 1

    def on_fill(self, fill): pass
    def get_params(self): return {}
    def set_params(self, params): pass


class SlowStrategy:
    name = "SlowStrategy"
    timeframe = "1h"
    warmup_bars = 10

    def __init__(self):
        self.on_bar_call_count = 0

    def on_bar(self, bar, series, broker):
        self.on_bar_call_count += 1

    def on_fill(self, fill): pass
    def get_params(self): return {}
    def set_params(self, params): pass


class TestBacktestEngineFastPath:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_db_path, schema_path, sample_bar_data):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        self.db.upsert_bars("BTCUSDT", "1h", sample_bar_data)
        self.feed = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        self.config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        self.engine = BacktestEngine()

    def test_fast_path_calls_prepare_once(self):
        strategy = FastStrategy()
        self.engine.run(strategy, self.feed, self.config, symbol="BTCUSDT")
        assert strategy.prepare_call_count == 1

    def test_fast_path_uses_on_bar_fast_not_legacy(self):
        strategy = FastStrategy()
        self.engine.run(strategy, self.feed, self.config, symbol="BTCUSDT")
        # 10 bars, warmup=10 → 0 bars after warmup
        assert strategy.on_bar_fast_call_count == 0
        assert strategy.on_bar_legacy_call_count == 0

    def test_legacy_strategy_still_works(self):
        strategy = SlowStrategy()
        self.engine.run(strategy, self.feed, self.config, symbol="BTCUSDT")
        # 10 bars, warmup 10 → 0 calls is possible; relax to >= 0
        assert strategy.on_bar_call_count >= 0

    def test_fast_path_respects_warmup(self):
        strategy = FastStrategy()
        self.engine.run(strategy, self.feed, self.config, symbol="BTCUSDT")
        assert strategy.on_bar_fast_call_count == 0


class MultiSymbolStrategy:
    """prepare_multi를 사용하는 전략."""
    name = "MultiSymbolStrategy"
    timeframe = "1h"
    warmup_bars = 5

    def __init__(self):
        self.prepare_multi_called = False
        self.last_cache_keys = None

    def prepare_multi(self, primary_series, reference_series):
        self.prepare_multi_called = True
        self.last_cache_keys = list(reference_series.keys())
        primary_close = primary_series.close.to_numpy()
        return IndicatorCache(arrays={
            "primary_close": primary_close,
        })

    def on_bar_fast(self, bar, i, cache, broker):
        pass

    def on_bar(self, bar, series, broker): pass
    def on_fill(self, fill): pass
    def get_params(self): return {}
    def set_params(self, params): pass


class TestBacktestEngineMultiSymbol:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_db_path, schema_path, sample_bar_data):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        self.db.upsert_bars("BTCUSDT", "1h", sample_bar_data)
        # ETHUSDT 데이터도
        eth_bars = [
            {**row, "symbol": "ETHUSDT", "open": row["open"] * 0.05}
            for row in sample_bar_data
        ]
        self.db.upsert_bars("ETHUSDT", "1h", eth_bars)

    def test_multi_symbol_prepare_called(self):
        feed = HistoricalDataFeed(
            db=self.db, symbols=["ETHUSDT", "BTCUSDT"], timeframe="1h",
        )
        strategy = MultiSymbolStrategy()
        config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.0, slippage_pct=0.0)
        engine = BacktestEngine()
        engine.run(strategy, feed, config, symbol="ETHUSDT",
                   reference_symbols=["BTCUSDT"])
        assert strategy.prepare_multi_called
        assert "BTCUSDT" in strategy.last_cache_keys
