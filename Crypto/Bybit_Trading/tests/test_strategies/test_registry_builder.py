"""registry_builder 테스트."""
import pytest
from src.strategies.registry_builder import (
    build_strategy_registry,
    get_strategy_config,
    STRATEGY_NAMES,
    STRATEGY_CONFIGS,
)


EXPECTED_STRATEGIES = {
    "DonchianTrendFilter",
    "DonchianFixedRR",
    "BBKCSqueeze",
    # 2026-04-14 rule-based improvement round variants
    "DonchianFixedRRTrendFilter",
    "DonchianTrendFilterADX20",
    "DonchianTrendFilterADX25",
    "BBKCSqueezeHTFTrend",
}


class TestRegistryBuilder:
    def test_strategy_names_are_registered(self):
        assert set(STRATEGY_NAMES) == EXPECTED_STRATEGIES
        for name in STRATEGY_NAMES:
            assert isinstance(name, str)

    def test_build_registry_returns_all(self):
        registry = build_strategy_registry()
        info_list = registry.list_all()
        names = {info["name"] for info in info_list}
        assert len(names) == len(EXPECTED_STRATEGIES)

    def test_get_strategy_config_valid(self):
        cfg = get_strategy_config("BBKCSqueeze")
        assert "cls" in cfg
        assert "coarse_grid" in cfg
        assert "symbols" in cfg
        assert "timeframes" in cfg
        assert isinstance(cfg["coarse_grid"], dict)

    def test_get_strategy_config_invalid_raises(self):
        with pytest.raises(KeyError):
            get_strategy_config("NonExistent")

    def test_all_strategies_have_no_reference_symbols(self):
        """Donchian과 BBKCSqueeze는 단일 심볼 전략."""
        for name in STRATEGY_NAMES:
            cfg = get_strategy_config(name)
            assert cfg.get("reference_symbols", []) == []

    def test_all_strategies_use_default_universe(self):
        """제너럴 구조: 모든 전략이 SYMBOLS_DEFAULT/TIMEFRAMES_DEFAULT 사용."""
        from src.strategies.registry_builder import SYMBOLS_DEFAULT, TIMEFRAMES_DEFAULT
        for name in STRATEGY_NAMES:
            cfg = get_strategy_config(name)
            assert cfg["symbols"] == SYMBOLS_DEFAULT
            assert cfg["timeframes"] == TIMEFRAMES_DEFAULT
