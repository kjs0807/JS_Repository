"""DataSource Protocol + GapReport + 스키마 정의 (spec §3.1, §3.2).

OHLCV 스키마는 timestamp(UTC tz-aware) + open/high/low/close/volume(Float64).
타임프레임 파싱 + 심볼 sanitize 유틸 포함.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import polars as pl

from backtester.core.errors import DataError

# OHLCV 표준 스키마 (spec §3.1)
OHLCV_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),
    "volume": pl.Float64(),
}


@dataclass(frozen=True)
class GapReport:
    """데이터 갭 리포트 (spec §3.2).

    expected_interval: 타임프레임 간격
    gaps: 각 튜플은 `(missing_first_inclusive, missing_last_inclusive)` —
        해당 갭에서 빠진 첫 봉의 timestamp와 마지막 봉의 timestamp.
        예: 1h봉이 13:00 다음 16:00이면 빠진 봉은 14:00, 15:00이며
            튜플은 `(14:00, 15:00)`.
    total_missing_bars: 모든 갭에서 빠진 봉 개수 합

    `is_significant(threshold=10)`로 임계 초과 여부 판단.
    """

    symbol: str
    timeframe: str
    expected_interval: timedelta
    gaps: list[tuple[datetime, datetime]]
    total_missing_bars: int

    def is_significant(self, threshold: int = 10) -> bool:
        return self.total_missing_bars > threshold


class DataSource(Protocol):
    """봉 데이터 fetch 프로토콜 (spec §3.1).

    fetch는 (DataFrame, GapReport)를 반환한다. DataFrame은 OHLCV_SCHEMA 준수,
    timestamp는 오름차순 정렬 보장.
    """

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pl.DataFrame, GapReport]: ...


# ---------- 유틸 ------------------------------------------------------------

_TIMEFRAME_PATTERN = re.compile(r"^(\d+)([smhdw])$")
_UNIT_TO_DELTA: dict[str, timedelta] = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}


def parse_timeframe(tf: str) -> timedelta:
    """'1h' / '15m' / '4h' / '1d' / '1w' → timedelta."""
    m = _TIMEFRAME_PATTERN.match(tf.lower())
    if not m:
        raise DataError(f"Invalid timeframe: {tf!r}. Expected like '1h', '15m', '1d'.")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise DataError(f"Timeframe count must be positive: {tf!r}")
    return _UNIT_TO_DELTA[unit] * n


def sanitize_symbol(symbol: str) -> str:
    """파일명 안전 심볼 (spec §6.5). 'BTC/USDT' → 'BTC_USDT'."""
    return symbol.replace("/", "_").replace("\\", "_")


def validate_ohlcv_schema(df: pl.DataFrame) -> None:
    """OHLCV_SCHEMA 준수 검증. 위반 시 DataError raise.

    검증 항목:
    1. 필수 컬럼 존재
    2. 컬럼 dtype 일치 (특히 timestamp는 UTC tz-aware Datetime)
    3. 모든 컬럼에 null 값 없음 — Clock/BarsView/Ledger 진입 전에 차단해
       이후 단계에서 NaN 전파·연쇄 오류를 막는다.
    """
    for col, expected_dtype in OHLCV_SCHEMA.items():
        if col not in df.columns:
            raise DataError(f"OHLCV schema missing column: {col!r}")
        actual = df.schema[col]
        if actual != expected_dtype:
            raise DataError(
                f"OHLCV column {col!r}: expected {expected_dtype}, got {actual}"
            )
        null_count = df[col].null_count()
        if null_count > 0:
            raise DataError(
                f"OHLCV column {col!r} has {null_count} null value(s); "
                f"null entries are not allowed in any OHLCV column."
            )


def compute_gap_report(
    df: pl.DataFrame,
    symbol: str,
    timeframe: str,
) -> GapReport:
    """timestamp 컬럼이 오름차순 정렬되었다고 가정. 갭 검출."""
    interval = parse_timeframe(timeframe)
    if df.height < 2:
        return GapReport(
            symbol=symbol,
            timeframe=timeframe,
            expected_interval=interval,
            gaps=[],
            total_missing_bars=0,
        )

    timestamps = df["timestamp"].to_list()
    gaps: list[tuple[datetime, datetime]] = []
    total_missing = 0
    for prev, curr in zip(timestamps[:-1], timestamps[1:], strict=True):
        diff = curr - prev
        if diff > interval:
            missing = diff // interval - 1
            if missing > 0:
                gaps.append((prev + interval, curr - interval))
                total_missing += missing
    return GapReport(
        symbol=symbol,
        timeframe=timeframe,
        expected_interval=interval,
        gaps=gaps,
        total_missing_bars=total_missing,
    )
