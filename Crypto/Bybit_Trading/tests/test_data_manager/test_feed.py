"""data_manager/feed.py 단위 테스트."""
import pytest
import pandas as pd
from src.core.types import Bar, BarSeries
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.core.types import BarSeries


class TestHistoricalDataFeed:
    @pytest.fixture(autouse=True)
    def setup_feed(self, tmp_db_path, schema_path, sample_bar_data):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        self.db.upsert_bars("BTCUSDT", "1h", sample_bar_data)
        self.feed = HistoricalDataFeed(
            db=self.db, symbols=["BTCUSDT"], timeframe="1h",
        )

    def test_has_next_initially_true(self):
        assert self.feed.has_next() is True

    def test_next_bar_returns_bar(self):
        bar = self.feed.next_bar("BTCUSDT")
        assert isinstance(bar, Bar)
        assert bar.symbol == "BTCUSDT"
        assert bar.timeframe == "1h"

    def test_next_bar_advances(self):
        bar1 = self.feed.next_bar("BTCUSDT")
        bar2 = self.feed.next_bar("BTCUSDT")
        assert bar2.timestamp > bar1.timestamp

    def test_next_bar_returns_none_when_exhausted(self):
        for _ in range(10):
            self.feed.next_bar("BTCUSDT")
        bar = self.feed.next_bar("BTCUSDT")
        assert bar is None

    def test_has_next_false_when_exhausted(self):
        for _ in range(10):
            self.feed.next_bar("BTCUSDT")
        assert self.feed.has_next() is False

    def test_get_history_returns_bar_series(self):
        for _ in range(5):
            self.feed.next_bar("BTCUSDT")
        series = self.feed.get_history("BTCUSDT", lookback=3)
        assert isinstance(series, BarSeries)
        assert len(series) == 3
        assert series.symbol == "BTCUSDT"

    def test_get_history_limited_by_available(self):
        self.feed.next_bar("BTCUSDT")
        self.feed.next_bar("BTCUSDT")
        series = self.feed.get_history("BTCUSDT", lookback=10)
        assert len(series) == 2

    def test_bar_count(self):
        assert self.feed.bar_count == 0
        self.feed.next_bar("BTCUSDT")
        assert self.feed.bar_count == 1
        self.feed.next_bar("BTCUSDT")
        assert self.feed.bar_count == 2

    def test_reset(self):
        for _ in range(5):
            self.feed.next_bar("BTCUSDT")
        self.feed.reset()
        assert self.feed.bar_count == 0
        assert self.feed.has_next() is True
        bar = self.feed.next_bar("BTCUSDT")
        assert bar is not None


class TestHistoricalDataFeedFullSeries:
    @pytest.fixture(autouse=True)
    def setup_feed(self, tmp_db_path, schema_path, sample_bar_data):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        self.db.upsert_bars("BTCUSDT", "1h", sample_bar_data)
        self.feed = HistoricalDataFeed(
            db=self.db, symbols=["BTCUSDT"], timeframe="1h",
        )

    def test_get_full_series_returns_bar_series(self):
        series = self.feed.get_full_series("BTCUSDT")
        assert isinstance(series, BarSeries)
        assert series.symbol == "BTCUSDT"
        assert series.timeframe == "1h"

    def test_get_full_series_has_all_bars(self):
        series = self.feed.get_full_series("BTCUSDT")
        assert len(series) == 10

    def test_get_full_series_before_any_next_bar(self):
        series = self.feed.get_full_series("BTCUSDT")
        assert len(series) == 10

    def test_get_full_series_not_affected_by_index(self):
        self.feed.next_bar("BTCUSDT")
        self.feed.next_bar("BTCUSDT")
        series = self.feed.get_full_series("BTCUSDT")
        assert len(series) == 10

    def test_get_full_series_missing_symbol_returns_empty(self):
        series = self.feed.get_full_series("NONEXIST")
        assert len(series) == 0
