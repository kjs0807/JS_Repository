"""FundingRateSource (PR Q) — symbol 별 funding rate 시계열 조회.

PR Q 1차 범위:
- ``FundingRateSource`` Protocol: ``get_rate(symbol, ts) -> Decimal | None``.
- ``ParquetFundingRateSource``: ``{base_dir}/funding_{symbol}.parquet`` 형식의
  시계열에서 ts 기준 rate 조회. 정확 매칭 ts 만 반환 — 보간 안 함 (보수적).
- missing rate → ``None`` 반환. caller (FundingProcessor / Engine) 가 strict reject.

후속 (별도 PR):
- ``BybitFundingFetcher`` (REST API → parquet cache).
- ``funding_gap_policy="skip" | "ffill"`` 옵션.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import polars as pl

from backtester.data.base import sanitize_symbol


class FundingRateSource(Protocol):
    """심볼별 funding rate 시계열 조회 인터페이스 (PR Q).

    구현체는 외부 데이터 (parquet / DB / REST cache) 에서 ts 기준 rate 조회.
    정확 매칭 ts 가 없으면 ``None`` 을 반환 — caller 가 strict reject 결정.
    """

    def get_rate(self, symbol: str, ts: datetime) -> Decimal | None:
        ...

    def known_timestamps(self, symbol: str) -> list[datetime]:
        """``symbol`` 에 대해 알려진 funding ts 리스트 (sorted, 디버깅 / persist 용)."""
        ...


class ParquetFundingRateSource:
    """``{base_dir}/funding_{symbol}.parquet`` 시계열에서 funding rate 조회.

    parquet 스키마: ``timestamp`` (UTC tz-aware Datetime) + ``rate`` (Float64 또는 Utf8).
    Decimal 보존이 필요하면 rate 를 str 로 저장 (Float 정밀도 회피).
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self._cache: dict[str, dict[datetime, Decimal]] = {}

    def _load(self, symbol: str) -> dict[datetime, Decimal]:
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached
        path = self.base_dir / f"funding_{sanitize_symbol(symbol)}.parquet"
        if not path.exists():
            self._cache[symbol] = {}
            return self._cache[symbol]
        df = pl.read_parquet(path)
        out: dict[datetime, Decimal] = {}
        for row in df.iter_rows(named=True):
            ts = row["timestamp"]
            raw = row["rate"]
            out[ts] = Decimal(str(raw))
        self._cache[symbol] = out
        return out

    def get_rate(self, symbol: str, ts: datetime) -> Decimal | None:
        return self._load(symbol).get(ts)

    def known_timestamps(self, symbol: str) -> list[datetime]:
        return sorted(self._load(symbol).keys())
