"""data_manager/db.py 단위 테스트."""
import pytest
import pandas as pd
from src.data_manager.db import DBManager


class TestDBManagerInit:
    def test_initialize_creates_tables(self, tmp_db_path, schema_path):
        db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        db.initialize()
        count = db.get_bar_count("BTCUSDT", "1h")
        assert count == 0

    def test_initialize_idempotent(self, tmp_db_path, schema_path):
        db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        db.initialize()
        db.initialize()  # 두 번 호출해도 에러 없음


class TestDBManagerOHLCV:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_db_path, schema_path, sample_bar_data):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        self.sample_data = sample_bar_data

    def test_upsert_bars(self):
        count = self.db.upsert_bars("BTCUSDT", "1h", self.sample_data)
        assert count > 0

    def test_get_bars_returns_dataframe(self):
        self.db.upsert_bars("BTCUSDT", "1h", self.sample_data)
        df = self.db.get_bars("BTCUSDT", "1h")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10
        assert "close" in df.columns

    def test_get_bars_empty_symbol(self):
        df = self.db.get_bars("NONEXIST", "1h")
        assert df.empty

    def test_get_bars_with_time_range(self):
        self.db.upsert_bars("BTCUSDT", "1h", self.sample_data)
        start = self.sample_data[2]["open_time"]
        end = self.sample_data[7]["open_time"]
        df = self.db.get_bars("BTCUSDT", "1h", start_time=start, end_time=end)
        assert len(df) == 6

    def test_get_bars_with_limit(self):
        self.db.upsert_bars("BTCUSDT", "1h", self.sample_data)
        df = self.db.get_bars("BTCUSDT", "1h", limit=3)
        assert len(df) == 3

    def test_get_bar_count(self):
        self.db.upsert_bars("BTCUSDT", "1h", self.sample_data)
        assert self.db.get_bar_count("BTCUSDT", "1h") == 10

    def test_get_bar_range(self):
        self.db.upsert_bars("BTCUSDT", "1h", self.sample_data)
        min_ts, max_ts = self.db.get_bar_range("BTCUSDT", "1h")
        assert min_ts == self.sample_data[0]["open_time"]
        assert max_ts == self.sample_data[-1]["open_time"]

    def test_upsert_duplicate_updates(self):
        self.db.upsert_bars("BTCUSDT", "1h", self.sample_data)
        updated = [self.sample_data[0].copy()]
        updated[0]["close"] = 99999.0
        self.db.upsert_bars("BTCUSDT", "1h", updated)
        df = self.db.get_bars("BTCUSDT", "1h")
        assert len(df) == 10
        assert df.iloc[0]["close"] == 99999.0

    def test_unsupported_timeframe_raises(self):
        with pytest.raises(ValueError, match="지원하지 않는 타임프레임"):
            self.db.upsert_bars("BTCUSDT", "3m", self.sample_data)


class TestDBManagerProducts:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_db_path, schema_path):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()

    def test_upsert_and_get_product(self):
        products = [{
            "symbol": "BTCUSDT", "base_coin": "BTC", "quote_coin": "USDT",
            "min_qty": 0.001, "qty_step": 0.001, "tick_size": 0.1,
            "min_notional": 5.0, "max_leverage": 100,
            "contract_type": "LinearPerpetual", "updated_at": 1700000000000,
        }]
        self.db.upsert_products(products)
        product = self.db.get_product("BTCUSDT")
        assert product is not None
        assert product["base_coin"] == "BTC"

    def test_get_product_nonexistent(self):
        assert self.db.get_product("NONEXIST") is None


class TestDBManagerTradeLogs:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_db_path, schema_path):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()

    def test_insert_signal_log(self):
        signal_id = self.db.insert_signal({
            "timestamp": "2026-04-11 14:30:00",
            "strategy": "TestStrategy",
            "symbol": "BTCUSDT",
            "direction": "LONG",
        })
        assert signal_id > 0

    def test_insert_trade_log_with_source(self):
        trade_id = self.db.insert_trade_log({
            "strategy": "TestStrategy", "symbol": "BTCUSDT",
            "direction": "LONG", "entry_time": "2026-04-11 14:30:00",
            "entry_price": 65000.0, "quantity": 0.01, "source": "MANUAL",
        })
        assert trade_id > 0
        trades = self.db.get_recent_trades(limit=1)
        assert trades[0]["source"] == "MANUAL"

    def test_trade_log_default_source_is_strategy(self):
        self.db.insert_trade_log({
            "strategy": "TestStrategy", "symbol": "BTCUSDT",
            "direction": "LONG", "entry_time": "2026-04-11 14:30:00",
            "entry_price": 65000.0, "quantity": 0.01,
        })
        trades = self.db.get_recent_trades(limit=1)
        assert trades[0]["source"] == "STRATEGY"


class TestDBManagerFundingOI:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_db_path, schema_path):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()

    def test_funding_rate_upsert_and_get(self):
        rows = [
            {"symbol": "BTCUSDT", "funding_rate": 0.0001, "funding_time": 1700000000000},
            {"symbol": "BTCUSDT", "funding_rate": 0.00015, "funding_time": 1700003600000},
            {"symbol": "ETHUSDT", "funding_rate": 0.00005, "funding_time": 1700000000000},
        ]
        count = self.db.upsert_funding_rates(rows)
        assert count >= 3

        df = self.db.get_funding_rates("BTCUSDT")
        assert len(df) == 2
        assert df.iloc[0]["funding_rate"] == 0.0001

    def test_funding_rate_time_range(self):
        rows = [
            {"symbol": "BTCUSDT", "funding_rate": 0.0001, "funding_time": 1700000000000},
            {"symbol": "BTCUSDT", "funding_rate": 0.0002, "funding_time": 1700003600000},
            {"symbol": "BTCUSDT", "funding_rate": 0.0003, "funding_time": 1700007200000},
        ]
        self.db.upsert_funding_rates(rows)
        df = self.db.get_funding_rates("BTCUSDT", start_time=1700003600000)
        assert len(df) == 2

    def test_open_interest_upsert_and_get(self):
        rows = [
            {"symbol": "BTCUSDT", "open_interest": 10000.0, "open_interest_value": 650000000.0, "timestamp": 1700000000000},
            {"symbol": "BTCUSDT", "open_interest": 10500.0, "open_interest_value": 680000000.0, "timestamp": 1700003600000},
        ]
        count = self.db.upsert_open_interest(rows)
        assert count >= 2
        df = self.db.get_open_interest("BTCUSDT")
        assert len(df) == 2
        assert df.iloc[0]["open_interest"] == 10000.0

    def test_empty_symbol_returns_empty(self):
        df = self.db.get_funding_rates("NONEXIST")
        assert df.empty
        df = self.db.get_open_interest("NONEXIST")
        assert df.empty
