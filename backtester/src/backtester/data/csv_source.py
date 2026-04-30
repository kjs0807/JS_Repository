"""CSVDataSource (Phase 1.5 PR 9, spec §16 ``data/csv_source.py``).

``ParquetDataSource``와 동일 인터페이스로 ``{base_dir}/{symbol}_{timeframe}.csv`` 파일을
읽는다. CSV는 컬럼 dtype 정보가 없어 명시적 캐스팅이 필요하다:

- ``timestamp``: ISO8601 문자열 → ``Datetime("us", time_zone="UTC")``. tz 없으면 DataError.
- ``open/high/low/close/volume``: ``Float64`` 캐스트.

읽은 후 ``validate_ohlcv_schema``를 통해 dtype/null 검증, 중복/정렬 검증, ``[start, end]``
필터 후 ``GapReport``와 함께 반환한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

from backtester.core.errors import DataError
from backtester.data.base import (
    GapReport,
    compute_gap_report,
    sanitize_symbol,
    validate_ohlcv_schema,
)


def _ensure_utc_aware(name: str, dt: datetime) -> None:
    """ParquetDataSource와 동일한 가드 — UTC tz-aware 강제."""
    if dt.tzinfo is None:
        raise DataError(
            f"{name} must be timezone-aware (UTC), got naive datetime: {dt!r}"
        )
    offset = dt.utcoffset()
    if offset != timedelta(0):
        raise DataError(
            f"{name} must be UTC (offset 0), got tzinfo={dt.tzinfo!r} offset={offset}"
        )


class CSVDataSource:
    """로컬 CSV 파일 기반 DataSource (Phase 1.5).

    파일 형식 (헤더 필수, UTF-8)::

        timestamp,open,high,low,close,volume
        2026-01-01T00:00:00+00:00,100.0,101.0,99.0,100.5,1.0
        ...

    timestamp는 ISO8601 + UTC offset (``+00:00`` 또는 ``Z``). naive datetime 거부.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        if not self.base_dir.exists():
            raise DataError(f"CSVDataSource base_dir does not exist: {self.base_dir}")
        if not self.base_dir.is_dir():
            raise DataError(f"CSVDataSource base_dir is not a directory: {self.base_dir}")

    def _resolve_path(self, symbol: str, timeframe: str) -> Path:
        filename = f"{sanitize_symbol(symbol)}_{timeframe}.csv"
        return self.base_dir / filename

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pl.DataFrame, GapReport]:
        _ensure_utc_aware("start", start)
        _ensure_utc_aware("end", end)
        if start >= end:
            raise DataError(f"start must be < end: start={start}, end={end}")

        path = self._resolve_path(symbol, timeframe)
        if not path.exists():
            raise DataError(f"CSV file not found: {path}")

        # CSV 읽기 — timestamp는 일단 String 으로 받아 명시적 캐스팅. 다른 컬럼은 Float64.
        try:
            df = pl.read_csv(
                path,
                schema_overrides={
                    "timestamp": pl.String,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Float64,
                },
            )
        except (pl.exceptions.PolarsError, OSError, ValueError) as e:
            raise DataError(f"Failed to read CSV {path}: {e}") from e

        for required in ("timestamp", "open", "high", "low", "close", "volume"):
            if required not in df.columns:
                raise DataError(
                    f"CSV {path} missing required column: {required!r}"
                )

        # ISO8601 문자열 → Datetime("us", time_zone="UTC"). naive 또는 비-UTC offset 차단.
        try:
            df = df.with_columns(
                pl.col("timestamp")
                .str.to_datetime(time_unit="us", time_zone="UTC")
                .alias("timestamp")
            )
        except pl.exceptions.PolarsError as e:
            raise DataError(
                f"Failed to parse 'timestamp' column in {path} as ISO8601 UTC: {e}"
            ) from e

        # 표준 OHLCV 컬럼만 유지 (CSV에 추가 컬럼이 있어도 무시)
        df = df.select(["timestamp", "open", "high", "low", "close", "volume"])

        validate_ohlcv_schema(df)

        ts = df["timestamp"]
        if df.height >= 2:
            if ts.n_unique() != df.height:
                raise DataError(
                    f"timestamp column has duplicates: {path} "
                    f"({df.height - ts.n_unique()} duplicate(s))"
                )
            if not ts.is_sorted():
                raise DataError(f"timestamp column is not sorted ascending: {path}")

        df = df.filter((pl.col("timestamp") >= start) & (pl.col("timestamp") <= end))
        gap_report = compute_gap_report(df, symbol=symbol, timeframe=timeframe)
        return df, gap_report
