"""SQLiteDataSource regression — Bybit_Trading-shaped DB integration.

Coverage:

1. Happy path — fetch returns DataFrame with the OHLCV schema, ascending
   timestamp, and a ``GapReport`` with no gaps when the DB is dense.
2. Range filtering — ``start`` / ``end`` clip rows; rows outside the window
   are excluded; the count matches a manually computed expectation.
3. Empty result — symbol/timeframe combo with no rows in the window
   returns an empty DataFrame whose schema still passes
   ``validate_ohlcv_schema``.
4. Unsupported timeframe → ``DataError``.
5. Missing DB file → ``DataError``.
6. Naive datetime / non-UTC tz → ``DataError``.
7. ``start >= end`` → ``DataError``.
8. Custom ``timeframe_table`` mapping is honored.
9. End-to-end via ``BacktestConfig(data_source.type='sqlite')`` — engine
   builds an ``SQLiteDataSource`` and runs a trivial backtest with no
   crash.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.errors import DataError
from backtester.data.sqlite_source import SQLiteDataSource
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


# ---------- helpers ---------------------------------------------------------


def _build_sqlite_db(
    target: Path,
    *,
    symbol: str = "BTCUSDT",
    table: str = "ohlcv_30m",
    base: datetime | None = None,
    n_bars: int = 50,
) -> None:
    """Create a tiny SQLite DB whose schema matches Bybit_Trading."""
    base = base or datetime(2026, 1, 1, tzinfo=UTC)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    conn = sqlite3.connect(str(target))
    try:
        conn.execute(
            f"CREATE TABLE {table} ("  # noqa: S608
            f"id INTEGER PRIMARY KEY AUTOINCREMENT, "
            f"symbol TEXT NOT NULL, "
            f"open_time INTEGER NOT NULL, "
            f"open REAL NOT NULL, high REAL NOT NULL, "
            f"low REAL NOT NULL, close REAL NOT NULL, "
            f"volume REAL NOT NULL, "
            f"turnover REAL, "
            f"UNIQUE(symbol, open_time))"
        )
        rows = []
        for i in range(n_bars):
            ts = base + timedelta(minutes=30 * i)
            ts_ms = int(ts.timestamp() * 1000)
            price = 100.0 + i
            rows.append(
                (
                    symbol,
                    ts_ms,
                    price,
                    price + 0.5,
                    price - 0.5,
                    price + 0.1,
                    1.0,
                )
            )
        conn.executemany(
            f"INSERT INTO {table} "  # noqa: S608
            f"(symbol, open_time, open, high, low, close, volume) "
            f"VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


# ---------- 1. happy path ---------------------------------------------------


def test_fetch_returns_full_window_with_correct_schema(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db, n_bars=20)
    src = SQLiteDataSource(db)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df, gap = src.fetch(
        "BTCUSDT", "30m", base, base + timedelta(minutes=30 * 19)
    )
    assert df.height == 20
    assert df.columns == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert df.schema["timestamp"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert all(df.schema[c] == pl.Float64 for c in ("open", "high", "low", "close", "volume"))
    # Ascending order.
    ts = df["timestamp"].to_list()
    assert ts == sorted(ts)
    # No gaps in dense input.
    assert gap.gaps == []


# ---------- 2. range filtering ---------------------------------------------


def test_fetch_clips_to_requested_window(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db, n_bars=50)
    src = SQLiteDataSource(db)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Window [bar 10, bar 20] inclusive.
    start = base + timedelta(minutes=30 * 10)
    end = base + timedelta(minutes=30 * 20)
    df, _ = src.fetch("BTCUSDT", "30m", start, end)
    assert df.height == 11  # bars 10..20 inclusive
    assert df["timestamp"][0] == start
    assert df["timestamp"][-1] == end


# ---------- 3. empty result -------------------------------------------------


def test_fetch_unknown_symbol_returns_empty_dataframe(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db, symbol="BTCUSDT", n_bars=10)
    src = SQLiteDataSource(db)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df, gap = src.fetch(
        "ETHUSDT", "30m", base, base + timedelta(hours=10)
    )
    assert df.height == 0
    assert df.schema["timestamp"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert gap.gaps == []


# ---------- 4. unsupported timeframe ---------------------------------------


def test_fetch_unknown_timeframe_raises(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db)
    src = SQLiteDataSource(db)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DataError, match="does not support timeframe"):
        src.fetch("BTCUSDT", "7m", base, base + timedelta(hours=1))


# ---------- 5. missing DB file ---------------------------------------------


def test_missing_db_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="not found"):
        SQLiteDataSource(tmp_path / "nope.db")


def test_db_path_is_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="must be a file"):
        SQLiteDataSource(tmp_path)


# ---------- 6. timezone validation -----------------------------------------


def test_naive_datetime_raises(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db)
    src = SQLiteDataSource(db)
    naive = datetime(2026, 1, 1)
    with pytest.raises(DataError, match="must be timezone-aware"):
        src.fetch("BTCUSDT", "30m", naive, naive + timedelta(hours=1))


def test_non_utc_offset_raises(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db)
    src = SQLiteDataSource(db)
    kst = timezone(timedelta(hours=9))
    start = datetime(2026, 1, 1, tzinfo=kst)
    with pytest.raises(DataError, match="must be UTC"):
        src.fetch("BTCUSDT", "30m", start, start + timedelta(hours=1))


# ---------- 7. start >= end ------------------------------------------------


def test_start_after_end_raises(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db)
    src = SQLiteDataSource(db)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DataError, match="start must be < end"):
        src.fetch("BTCUSDT", "30m", base + timedelta(hours=1), base)


# ---------- 8. custom timeframe_table --------------------------------------


def test_custom_timeframe_table(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db, table="bars_thirty", n_bars=10)
    src = SQLiteDataSource(db, timeframe_table={"30m": "bars_thirty"})
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df, _ = src.fetch("BTCUSDT", "30m", base, base + timedelta(hours=10))
    assert df.height == 10


def test_missing_table_raises_dataerror(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db, table="ohlcv_30m", n_bars=10)
    # Custom map points 1d to a non-existent table.
    src = SQLiteDataSource(
        db, timeframe_table={"1d": "ohlcv_daily_does_not_exist"}
    )
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DataError, match="query failed"):
        src.fetch("BTCUSDT", "1d", base, base + timedelta(days=1))


# ---------- 9. end-to-end via BacktestEngine -------------------------------


class _NoopStrategy(BaseStrategy):
    def on_bar(self, ctx: object) -> list[object]:  # type: ignore[override]
        return []


def test_engine_builds_sqlite_data_source(tmp_path: Path) -> None:
    db = tmp_path / "bybit.db"
    _build_sqlite_db(db, n_bars=100)
    sym = "BTCUSDT"
    cfg = BacktestConfig(
        run_id="sqlite_smoke",
        # base_dir doubles as DB path when type='sqlite' (see DataSourceConfig docstring).
        data_source=DataSourceConfig(base_dir=db, type="sqlite"),
        instruments=[
            Instrument(
                symbol=sym,
                asset_class="crypto_perp",
                tick_size=Decimal("0.01"),
                tick_value=Decimal("0.01"),
                contract_multiplier=Decimal("1"),
                quote_currency="USDT",
                base_currency="BTC",
                size_unit="base_asset",
                fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
            )
        ],
        timeframes_per_symbol={sym: ["30m"]},
        primary_symbol=sym,
        primary_timeframe="30m",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    result = BacktestEngine(cfg, _NoopStrategy(), verbose=False).run()
    # Engine produced events.jsonl + a results dir without crashing.
    assert Path(result.events_path).exists()
