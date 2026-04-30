"""ParquetDataSource (spec §3.1, Phase 1 유일한 DataSource).

`{base_dir}/{symbol_sanitized}_{timeframe}.parquet` 형식의 파일을 읽어
[start, end] 범위로 잘라 (DataFrame, GapReport)를 반환한다.

스키마 검증: OHLCV_SCHEMA 위반 시 DataError.
정렬 검증: timestamp 오름차순 아니면 DataError.
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
    """spec §11 — 모든 시간은 UTC tz-aware. naive 또는 non-UTC면 DataError."""
    if dt.tzinfo is None:
        raise DataError(
            f"{name} must be timezone-aware (UTC), got naive datetime: {dt!r}"
        )
    offset = dt.utcoffset()
    if offset != timedelta(0):
        raise DataError(
            f"{name} must be UTC (offset 0), got tzinfo={dt.tzinfo!r} offset={offset}"
        )


class ParquetDataSource:
    """로컬 Parquet 파일 기반 DataSource."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        if not self.base_dir.exists():
            raise DataError(f"ParquetDataSource base_dir does not exist: {self.base_dir}")
        if not self.base_dir.is_dir():
            raise DataError(f"ParquetDataSource base_dir is not a directory: {self.base_dir}")

    def _resolve_path(self, symbol: str, timeframe: str) -> Path:
        filename = f"{sanitize_symbol(symbol)}_{timeframe}.parquet"
        return self.base_dir / filename

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pl.DataFrame, GapReport]:
        # 입력 검증: UTC-aware + 범위 정합
        _ensure_utc_aware("start", start)
        _ensure_utc_aware("end", end)
        if start >= end:
            raise DataError(f"start must be < end: start={start}, end={end}")

        path = self._resolve_path(symbol, timeframe)
        if not path.exists():
            raise DataError(f"Parquet file not found: {path}")

        df = pl.read_parquet(path)
        validate_ohlcv_schema(df)

        # 정렬 + strictly increasing 검증 (중복 timestamp 차단)
        ts = df["timestamp"]
        if df.height >= 2:
            if ts.n_unique() != df.height:
                raise DataError(
                    f"timestamp column has duplicates: {path} "
                    f"({df.height - ts.n_unique()} duplicate(s))"
                )
            if not ts.is_sorted():
                raise DataError(f"timestamp column is not sorted ascending: {path}")

        # [start, end] 범위 필터 (양 끝 포함)
        df = df.filter((pl.col("timestamp") >= start) & (pl.col("timestamp") <= end))

        gap_report = compute_gap_report(df, symbol=symbol, timeframe=timeframe)
        return df, gap_report
