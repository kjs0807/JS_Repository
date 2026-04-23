"""strategies/registry.py 단위 테스트."""
import pytest
from src.strategies.registry import StrategyRegistry


class DummyStrategyA:
    name = "DummyA"
    timeframe = "1h"

    def __init__(self, period: int = 20, threshold: float = 0.5):
        self.period = period
        self.threshold = threshold

    def on_bar(self, bar, series, broker):
        pass

    def on_fill(self, fill):
        pass

    def get_params(self):
        return {"period": self.period, "threshold": self.threshold}

    def set_params(self, params):
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    @property
    def warmup_bars(self):
        return self.period + 5


class DummyStrategyB:
    name = "DummyB"
    timeframe = "4h"

    def __init__(self, fast: int = 12, slow: int = 26):
        self.fast = fast
        self.slow = slow

    def on_bar(self, bar, series, broker):
        pass

    def on_fill(self, fill):
        pass

    def get_params(self):
        return {"fast": self.fast, "slow": self.slow}

    def set_params(self, params):
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    @property
    def warmup_bars(self):
        return self.slow + 5


class TestStrategyRegistry:
    def setup_method(self):
        self.registry = StrategyRegistry()

    def test_register_strategy(self):
        self.registry.register(DummyStrategyA, param_space={"period": [10, 20, 30], "threshold": [0.3, 0.5]})
        assert len(self.registry.list_all()) == 1

    def test_register_multiple(self):
        self.registry.register(DummyStrategyA, {"period": [20]})
        self.registry.register(DummyStrategyB, {"fast": [12], "slow": [26]})
        assert len(self.registry.list_all()) == 2

    def test_list_all_returns_info(self):
        self.registry.register(DummyStrategyA, {"period": [10, 20]})
        info = self.registry.list_all()
        assert info[0]["name"] == "DummyA"
        assert info[0]["timeframe"] == "1h"
        assert "period" in info[0]["param_space"]

    def test_get_by_name(self):
        self.registry.register(DummyStrategyA, {"period": [20]})
        s = self.registry.get("DummyA")
        assert s.name == "DummyA"
        assert s.get_params()["period"] == 20

    def test_get_with_params(self):
        self.registry.register(DummyStrategyA, {"period": [20]})
        s = self.registry.get("DummyA", params={"period": 30, "threshold": 0.8})
        assert s.get_params()["period"] == 30
        assert s.get_params()["threshold"] == 0.8

    def test_get_nonexistent_raises(self):
        with pytest.raises(KeyError, match="등록되지 않은 전략"):
            self.registry.get("NonExistent")

    def test_get_candidates(self):
        self.registry.register(DummyStrategyA, {"period": [10, 20], "threshold": [0.5]})
        candidates = self.registry.get_candidates()
        assert len(candidates) == 2
        for strategy, params in candidates:
            assert strategy.name == "DummyA"
            assert "period" in params

    def test_get_candidates_multiple(self):
        self.registry.register(DummyStrategyA, {"period": [10, 20], "threshold": [0.5]})
        self.registry.register(DummyStrategyB, {"fast": [12], "slow": [20, 26]})
        assert len(self.registry.get_candidates()) == 4

    def test_candidate_params_applied(self):
        self.registry.register(DummyStrategyA, {"period": [10, 30], "threshold": [0.5]})
        periods = {c[1]["period"] for c in self.registry.get_candidates()}
        assert periods == {10, 30}

    def test_duplicate_register_overwrites(self):
        self.registry.register(DummyStrategyA, {"period": [10]})
        self.registry.register(DummyStrategyA, {"period": [20, 30]})
        assert len(self.registry.get_candidates()) == 2
