"""Tests for multi-timeframe collection."""
from unittest.mock import MagicMock

from src.data_manager.collector import collect_klines_mtf


class TestCollectKlinesMTF:
    def test_collects_each_timeframe(self):
        rest = MagicMock()
        # Each call returns 5 bars
        rest.get_klines.return_value = [
            {
                "symbol": "BTCUSDT",
                "open_time": 1700000000000 + i * 3600_000,
                "open": 50000.0,
                "high": 50100.0,
                "low": 49900.0,
                "close": 50050.0,
                "volume": 100.0,
                "turnover": 5_000_000.0,
            }
            for i in range(5)
        ]
        db = MagicMock()
        db.upsert_ohlcv.return_value = 5

        result = collect_klines_mtf(
            rest_client=rest,
            db_manager=db,
            symbol="BTCUSDT",
            timeframes=["1h", "4h", "1d"],
            start_ms=1700000000000,
            end_ms=1700000000000 + 24 * 3600_000,
        )

        assert result == {"1h": 5, "4h": 5, "1d": 5}
        # 3 calls to API (one per TF)
        assert rest.get_klines.call_count == 3
        assert db.upsert_ohlcv.call_count == 3

    def test_handles_empty_timeframe(self):
        rest = MagicMock()
        rest.get_klines.return_value = []
        db = MagicMock()
        db.upsert_ohlcv.return_value = 0

        result = collect_klines_mtf(
            rest_client=rest,
            db_manager=db,
            symbol="ETHUSDT",
            timeframes=["1h"],
            start_ms=1, end_ms=2,
        )
        assert result == {"1h": 0}
