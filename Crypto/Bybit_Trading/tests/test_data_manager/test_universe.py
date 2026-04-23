"""data_manager/universe.py 단위 테스트."""
from src.core.config import DataConfig
from src.data_manager.universe import UniverseManager


class TestUniverseManager:
    def test_filter_meme_coins(self):
        config = DataConfig()
        um = UniverseManager(config)
        symbols = ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "SOLUSDT", "PEPEUSDT"]
        filtered = um.filter_meme_coins(symbols)
        assert "BTCUSDT" in filtered
        assert "ETHUSDT" in filtered
        assert "SOLUSDT" in filtered
        assert "DOGEUSDT" not in filtered
        assert "PEPEUSDT" not in filtered

    def test_filter_preserves_order(self):
        config = DataConfig()
        um = UniverseManager(config)
        symbols = ["SOLUSDT", "BTCUSDT", "SHIBUSDT", "ETHUSDT"]
        filtered = um.filter_meme_coins(symbols)
        assert filtered == ["SOLUSDT", "BTCUSDT", "ETHUSDT"]

    def test_empty_blacklist(self):
        config = DataConfig(meme_blacklist=[])
        um = UniverseManager(config)
        symbols = ["DOGEUSDT", "BTCUSDT"]
        filtered = um.filter_meme_coins(symbols)
        assert filtered == ["DOGEUSDT", "BTCUSDT"]

    def test_limit_universe_size(self):
        config = DataConfig(universe_size=3)
        um = UniverseManager(config)
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"]
        limited = um.limit(symbols)
        assert len(limited) == 3
        assert limited == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    def test_build_universe_filters_and_limits(self):
        config = DataConfig(universe_size=3, meme_blacklist=["DOGEUSDT"])
        um = UniverseManager(config)
        raw = ["BTCUSDT", "DOGEUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]
        result = um.build(raw)
        assert len(result) == 3
        assert "DOGEUSDT" not in result
