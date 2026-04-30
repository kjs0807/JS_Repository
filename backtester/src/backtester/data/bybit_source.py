"""Bybit OHLCV DataSource with local parquet cache (Phase 2 PR 14, spec §3.1, §16).

설계:
- 로컬 parquet cache 가 단일 진실 소스. ``{cache_dir}/{symbol}_{timeframe}.parquet``.
- ``fetch(symbol, tf, start, end)`` 흐름:
    1. cache hit (요청 범위가 cache 안에 들어옴) → cache slice 만 반환, 네트워크 미사용.
    2. cache miss / partial miss → ``KlineFetcher`` 로 누락 범위 fetch → cache 머지 → 영속화.
- ``KlineFetcher`` 는 외부 네트워크 호출 추상화. 테스트는 mock fetcher 주입.
- default fetcher (``_default_kline_fetcher``) 는 stdlib ``urllib`` 으로 Bybit v5 REST
  ``GET /v5/market/kline`` 에 단발 호출. 외부 SDK (pybit 등) 의존성 추가 안 함.

DataSource 인터페이스 (``fetch`` → ``(pl.DataFrame, GapReport)``) 는 ``ParquetDataSource``
와 동일. ``ParquetDataSource`` 가 정적 cache, ``BybitDataSource`` 가 incremental cache.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import polars as pl

from backtester.core.errors import DataError
from backtester.data.base import (
    GapReport,
    compute_gap_report,
    parse_timeframe,
    sanitize_symbol,
    validate_ohlcv_schema,
)

BYBIT_REST_BASE = "https://api.bybit.com"
BYBIT_KLINE_PATH = "/v5/market/kline"

# Bybit v5 interval encoding (spec: numeric minutes for sub-day, "D"/"W"/"M" for higher).
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "D",
}


@dataclass(frozen=True)
class BybitKlineRow:
    """Bybit v5 kline 한 줄. open_time epoch ms (Bybit 기본 단위)."""

    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


# 외부 네트워크 호출 추상화 시그니처.
# (symbol, interval_code, start_dt, end_dt, category) → list[BybitKlineRow]
KlineFetcher = Callable[[str, str, datetime, datetime, str], list[BybitKlineRow]]


def _to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _default_kline_fetcher(
    symbol: str,
    interval_code: str,
    start: datetime,
    end: datetime,
    category: str,
) -> list[BybitKlineRow]:
    """Bybit v5 REST ``GET /v5/market/kline`` 단발 호출.

    한 번에 최대 1000 봉. 더 긴 범위가 필요하면 caller 가 페이지네이션 책임 (PR 14 범위 외).
    네트워크 / JSON / Bybit retCode 오류는 ``DataError`` 로 wrap.
    """
    params = {
        "category": category,
        "symbol": symbol,
        "interval": interval_code,
        "start": str(_to_epoch_ms(start)),
        "end": str(_to_epoch_ms(end)),
        "limit": "1000",
    }
    url = f"{BYBIT_REST_BASE}{BYBIT_KLINE_PATH}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 — Bybit 공식 호스트
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as e:
        raise DataError(f"Bybit REST request failed: {url}: {e}") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise DataError(f"Bybit REST returned non-JSON body: {body[:200]!r}") from e

    if payload.get("retCode") != 0:
        raise DataError(
            f"Bybit REST retCode={payload.get('retCode')}, "
            f"retMsg={payload.get('retMsg')!r}"
        )
    raw_list = (payload.get("result") or {}).get("list") or []

    rows: list[BybitKlineRow] = []
    for entry in raw_list:
        if len(entry) < 6:
            raise DataError(f"Bybit kline row too short: {entry!r}")
        rows.append(
            BybitKlineRow(
                open_time_ms=int(entry[0]),
                open=float(entry[1]),
                high=float(entry[2]),
                low=float(entry[3]),
                close=float(entry[4]),
                volume=float(entry[5]),
            )
        )
    # Bybit 응답은 최신 → 과거 순서. 사용처가 오름차순 기대 → 역정렬.
    rows.sort(key=lambda r: r.open_time_ms)
    return rows


def _rows_to_dataframe(rows: list[BybitKlineRow]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(
            schema={
                "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            }
        )
    timestamps = [
        datetime.fromtimestamp(r.open_time_ms / 1000, tz=timezone.utc) for r in rows
    ]
    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
    )


class BybitDataSource:
    """Bybit perpetual/spot OHLCV DataSource with parquet cache (Phase 2)."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        category: Literal["linear", "spot", "inverse"] = "linear",
        fetcher: KlineFetcher | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        if self.cache_dir.exists() and not self.cache_dir.is_dir():
            raise DataError(
                f"BybitDataSource cache_dir is not a directory: {self.cache_dir}"
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.category = category
        self._fetcher: KlineFetcher = fetcher or _default_kline_fetcher

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        return self.cache_dir / f"{sanitize_symbol(symbol)}_{timeframe}.parquet"

    def _load_cache(self, symbol: str, timeframe: str) -> pl.DataFrame | None:
        path = self._cache_path(symbol, timeframe)
        if not path.exists():
            return None
        df = pl.read_parquet(path)
        validate_ohlcv_schema(df)
        return df

    def _persist_cache(
        self, symbol: str, timeframe: str, df: pl.DataFrame
    ) -> None:
        df.write_parquet(self._cache_path(symbol, timeframe))

    def _interval_code(self, timeframe: str) -> str:
        if timeframe not in _INTERVAL_MAP:
            raise DataError(
                f"BybitDataSource does not support timeframe {timeframe!r}. "
                f"Supported: {sorted(_INTERVAL_MAP)}"
            )
        return _INTERVAL_MAP[timeframe]

    def _ensure_utc(self, name: str, dt: datetime) -> None:
        if dt.tzinfo is None:
            raise DataError(
                f"{name} must be timezone-aware (UTC), got naive: {dt!r}"
            )
        if dt.utcoffset() != timedelta(0):
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
        interval_code = self._interval_code(timeframe)

        cached = self._load_cache(symbol, timeframe)
        cache_min: datetime | None = None
        cache_max: datetime | None = None
        if cached is not None and cached.height > 0:
            cache_min = cached["timestamp"][0]
            cache_max = cached["timestamp"][-1]

        # 누락 범위 계산:
        # - 헤드 갭: requested.start < cache_min  →  [start, cache_min) 조회
        # - 테일 갭: cache_max < requested.end  →  (cache_max, end] 조회
        new_pieces: list[pl.DataFrame] = []
        if cached is None:
            new_pieces.append(
                _rows_to_dataframe(
                    self._fetcher(symbol, interval_code, start, end, self.category)
                )
            )
        else:
            assert cache_min is not None and cache_max is not None
            if start < cache_min:
                new_pieces.append(
                    _rows_to_dataframe(
                        self._fetcher(
                            symbol, interval_code, start, cache_min, self.category
                        )
                    )
                )
            if cache_max < end:
                new_pieces.append(
                    _rows_to_dataframe(
                        self._fetcher(
                            symbol, interval_code, cache_max, end, self.category
                        )
                    )
                )

        # 머지 + dedup + sort
        merged_pieces: list[pl.DataFrame] = []
        if cached is not None and cached.height > 0:
            merged_pieces.append(cached)
        for piece in new_pieces:
            if piece.height > 0:
                merged_pieces.append(piece)

        if merged_pieces:
            merged = (
                pl.concat(merged_pieces, how="vertical_relaxed")
                .unique(subset=["timestamp"])
                .sort("timestamp")
            )
        else:
            merged = _rows_to_dataframe([])

        # 새로 받은 데이터가 있으면 cache 영속화
        if new_pieces and merged.height > 0:
            self._persist_cache(symbol, timeframe, merged)

        validate_ohlcv_schema(merged)

        # 요청 범위 inclusive 슬라이스
        out = merged.filter(
            (pl.col("timestamp") >= start) & (pl.col("timestamp") <= end)
        )

        # strictly increasing 검증 (cache 무결성)
        if out.height >= 2:
            ts = out["timestamp"]
            if ts.n_unique() != out.height:
                raise DataError(
                    f"BybitDataSource cache has duplicate timestamps for "
                    f"{symbol}/{timeframe}"
                )
            if not ts.is_sorted():
                raise DataError(
                    f"BybitDataSource cache is not sorted for {symbol}/{timeframe}"
                )

        # Phase 2 timeframe 다양성 — gap_report 가 1m/3m/2h/6h/12h 등 모든 지원 TF 에서
        # 작동해야 한다. compute_gap_report 가 parse_timeframe 를 사용하므로 신뢰.
        _ = parse_timeframe(timeframe)
        gap_report = compute_gap_report(out, symbol=symbol, timeframe=timeframe)
        return out, gap_report
