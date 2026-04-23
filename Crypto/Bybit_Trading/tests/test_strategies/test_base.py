"""strategies/base.py 단위 테스트."""
from typing import Optional
import pandas as pd
from src.core.types import Bar, BarSeries
from src.execution.broker import Broker, Position, Portfolio, Fill
from src.strategies.base import Strategy


class FakeStrategy:
    name = "FakeStrategy"
    timeframe = "1h"

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        if bar.close > 65000:
            broker.buy(bar.symbol, 0.01, stop_loss=60000.0, reason="test signal")

    def on_fill(self, fill: Fill) -> None:
        pass

    def get_params(self) -> dict:
        return {"threshold": 65000}

    def set_params(self, params: dict) -> None:
        pass

    @property
    def warmup_bars(self) -> int:
        return 20


class TestStrategyProtocol:
    def test_fake_strategy_satisfies_protocol(self):
        assert isinstance(FakeStrategy(), Strategy)

    def test_strategy_has_name(self):
        assert FakeStrategy().name == "FakeStrategy"

    def test_strategy_has_timeframe(self):
        assert FakeStrategy().timeframe == "1h"

    def test_strategy_has_warmup_bars(self):
        assert FakeStrategy().warmup_bars == 20

    def test_strategy_get_set_params(self):
        s = FakeStrategy()
        params = s.get_params()
        assert isinstance(params, dict)
        assert "threshold" in params
        s.set_params({"threshold": 70000})

    def test_strategy_on_bar_calls_broker(self):
        strategy = FakeStrategy()
        calls = []

        class MockBroker:
            def buy(self, symbol, qty, stop_loss, take_profit=None, reason=""):
                calls.append(("buy", symbol, qty))
                return "mock_order"

        bar = Bar("BTCUSDT", 1700000000000, "1h", 65500.0, 66000.0, 65000.0, 65500.0, 1000.0)
        df = pd.DataFrame({
            "open": [65500.0], "high": [66000.0], "low": [65000.0],
            "close": [65500.0], "volume": [1000.0],
        })
        series = BarSeries("BTCUSDT", "1h", df)
        strategy.on_bar(bar, series, MockBroker())
        assert len(calls) == 1
        assert calls[0] == ("buy", "BTCUSDT", 0.01)

    def test_strategy_on_bar_no_signal_below_threshold(self):
        strategy = FakeStrategy()
        calls = []

        class MockBroker:
            def buy(self, symbol, qty, stop_loss, take_profit=None, reason=""):
                calls.append(("buy", symbol, qty))
                return "mock_order"

        bar = Bar("BTCUSDT", 1700000000000, "1h", 64000.0, 64500.0, 63500.0, 64000.0, 1000.0)
        df = pd.DataFrame({
            "open": [64000.0], "high": [64500.0], "low": [63500.0],
            "close": [64000.0], "volume": [1000.0],
        })
        series = BarSeries("BTCUSDT", "1h", df)
        strategy.on_bar(bar, series, MockBroker())
        assert len(calls) == 0


class TestNonConformingStrategy:
    def test_missing_method_not_protocol(self):
        class BadStrategy:
            name = "Bad"
            timeframe = "1h"
            warmup_bars = 10

            def get_params(self):
                return {}

            def set_params(self, p):
                pass

        assert not isinstance(BadStrategy(), Strategy)


import numpy as np
from src.strategies.base import IndicatorCache


class TestIndicatorCache:
    def test_create_cache(self):
        cache = IndicatorCache(arrays={
            "ema": np.array([100.0, 101.0, 102.0]),
            "atr": np.array([1.0, 1.1, 1.2]),
        })
        assert "ema" in cache.arrays
        assert len(cache.arrays["ema"]) == 3

    def test_get_method(self):
        cache = IndicatorCache(arrays={"ema": np.array([100.0, 101.0])})
        result = cache.get("ema")
        assert result[0] == 100.0
        assert result[1] == 101.0

    def test_get_missing_key_raises(self):
        import pytest
        cache = IndicatorCache(arrays={})
        with pytest.raises(KeyError):
            cache.get("nonexistent")
