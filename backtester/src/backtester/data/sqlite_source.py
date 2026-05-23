"""SQLite-backed OHLCV DataSource (Phase 4 — Bybit_Trading DB integration).

Reads pre-collected OHLCV bars from an external SQLite database whose schema
matches the Bybit_Trading project's convention::

    CREATE TABLE ohlcv_<tf> (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol      TEXT    NOT NULL,
        open_time   INTEGER NOT NULL,   -- Unix milliseconds (bar open)
        open        REAL    NOT NULL,
        high        REAL    NOT NULL,
        low         REAL    NOT NULL,
        close       REAL    NOT NULL,
        volume      REAL    NOT NULL,
        turnover    REAL,
        UNIQUE (symbol, open_time)
    );

The default ``timeframe_table`` map covers ``1m`` / ``5m`` / ``15m`` / ``30m`` /
``1h`` / ``4h`` / ``1d`` and matches Bybit_Trading's table names exactly so
``SQLiteDataSource("Crypto/Bybit_Trading/db/bybit_data.db")`` works out of
the box. Custom DBs can pass an alternative mapping.

Read-only by contract: the source only ``SELECT``s; population /
backfill is the job of upstream collectors (see
``Crypto/Bybit_Trading/scripts/collect_5m_history.py`` for an example).

Why this exists alongside :class:`BybitDataSource`:

- ``BybitDataSource`` maintains a *per-run parquet cache* and lazily fetches
  from Bybit REST on cache miss — good when each backtest run gets its own
  scratch directory and needs incremental fills.
- ``SQLiteDataSource`` reads from a *shared, pre-populated DB* that other
  parts of the project (paper-trading, monitoring) already maintain — good
  when you don't want every backtester run to re-fetch the same bars and
  you trust the existing collector pipeline.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from backtester.core.errors import DataError
from backtester.data.base import (
    GapReport,
    compute_gap_report,
    validate_ohlcv_schema,
)


_DEFAULT_TIMEFRAME_TABLE: dict[str, str] = {
    "1m": "ohlcv_1m",
    "5m": "ohlcv_5m",
    "15m": "ohlcv_15m",
    "30m": "ohlcv_30m",
    "1h": "ohlcv_1h",
    "4h": "ohlcv_4h",
    "1d": "ohlcv_daily",
}


# Use polars ``DataType`` instances throughout (``pl.Float64()``) so the
# dict is uniformly ``DataType``-typed for mypy strict — class objects
# would unify the dict to ``object`` and trip ``pl.DataFrame``'s schema arg.
_EMPTY_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),
    "volume": pl.Float64(),
}


class SQLiteDataSource:
    """OHLCV DataSource backed by a SQLite DB (Bybit_Trading schema)."""

    def __init__(
        self,
        db_path: Path,
        *,
        timeframe_table: dict[str, str] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise DataError(
                f"SQLiteDataSource: DB file not found: {self.db_path}"
            )
        if self.db_path.is_dir():
            raise DataError(
                f"SQLiteDataSource: db_path must be a file, got a directory: "
                f"{self.db_path}"
            )
        self.timeframe_table: dict[str, str] = (
            dict(timeframe_table) if timeframe_table else dict(_DEFAULT_TIMEFRAME_TABLE)
        )

    def _ensure_utc(self, name: str, dt: datetime) -> None:
        if dt.tzinfo is None:
            raise DataError(
                f"{name} must be timezone-aware (UTC), got naive: {dt!r}"
            )
        if dt.utcoffset() != _ZERO_TIMEDELTA:
            raise DataError(
                f"{name} must be UTC (offset 0), got {dt.tzinfo!r}"
            )

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pl.DataFrame, GapReport]:
        self._ensure_utc("start", start)
        self._ensure_utc("end", end)
        if start >= end:
            raise DataError(f"start must be < end: start={start}, end={end}")
        if timeframe not in self.timeframe_table:
            raise DataError(
                f"SQLiteDataSource does not support timeframe {timeframe!r}. "
                f"Supported: {sorted(self.timeframe_table)}"
            )
        table = self.timeframe_table[timeframe]
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        # Read-only connect — also avoids accidentally creating an empty file
        # if the path was a typo.
        uri = f"file:{self.db_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        except sqlite3.OperationalError as e:
            raise DataError(
                f"SQLiteDataSource: cannot open {self.db_path}: {e}"
            ) from e
        try:
            try:
                rows = conn.execute(
                    f"SELECT open_time, open, high, low, close, volume "  # noqa: S608
                    f"FROM {table} "
                    f"WHERE symbol = ? AND open_time >= ? AND open_time <= ? "
                    f"ORDER BY open_time ASC",
                    (symbol, start_ms, end_ms),
                ).fetchall()
            except sqlite3.OperationalError as e:
                # ``no such table`` etc. — surface as DataError with context.
                raise DataError(
                    f"SQLiteDataSource: query failed for table {table!r}: {e}"
                ) from e
        finally:
            conn.close()

        if not rows:
            empty = pl.DataFrame(schema=_EMPTY_SCHEMA)
            return empty, compute_gap_report(empty, symbol, timeframe)

        timestamps = [
            datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc) for r in rows
        ]
        df = pl.DataFrame(
            {
                "timestamp": timestamps,
                "open": [r[1] for r in rows],
                "high": [r[2] for r in rows],
                "low": [r[3] for r in rows],
                "close": [r[4] for r in rows],
                "volume": [r[5] for r in rows],
            }
        ).with_columns(
            pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
        )
        validate_ohlcv_schema(df)
        return df, compute_gap_report(df, symbol, timeframe)


# Sentinel for the UTC-offset check in ``_ensure_utc`` — avoids creating a
# fresh ``timedelta(0)`` per call.
from datetime import timedelta as _td  # noqa: E402

_ZERO_TIMEDELTA = _td(0)


__all__ = ["SQLiteDataSource"]
