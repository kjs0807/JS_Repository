"""data_manager/collector.py 단위 테스트."""
from src.core.types import Bar
from src.data_manager.collector import Collector


class TestCollector:
    def test_normalize_kline(self):
        raw = {
            "start": "1700000000000", "open": "40000.00", "high": "40150.00",
            "low": "39900.00", "close": "40050.00", "volume": "1000.00",
            "turnover": "40025000.00",
        }
        bar = Collector.normalize_kline(raw, symbol="BTCUSDT", timeframe="1h")
        assert isinstance(bar, Bar)
        assert bar.symbol == "BTCUSDT"
        assert bar.timestamp == 1700000000000
        assert bar.open == 40000.0
        assert bar.close == 40050.0
        assert bar.turnover == 40025000.0

    def test_normalize_kline_list(self):
        raw_list = ["1700000000000", "40000", "40150", "39900", "40050", "1000", "40025000"]
        bar = Collector.normalize_kline_list(raw_list, symbol="BTCUSDT", timeframe="1h")
        assert isinstance(bar, Bar)
        assert bar.timestamp == 1700000000000
        assert bar.close == 40050.0

    def test_bars_to_db_rows(self):
        bars = [
            Bar("BTCUSDT", 1700000000000, "1h", 40000, 40150, 39900, 40050, 1000, 40025000),
            Bar("BTCUSDT", 1700003600000, "1h", 40050, 40200, 39950, 40100, 1100, 44110000),
        ]
        rows = Collector.bars_to_db_rows(bars)
        assert len(rows) == 2
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["open_time"] == 1700000000000
        assert rows[1]["close"] == 40100.0
