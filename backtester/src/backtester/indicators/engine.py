"""IndicatorEngine — 사전계산 + 자동 영속화 (spec §3.8).

`precompute(bars, indicators, persist_to=...)`:
- bars는 `{symbol: {timeframe: DataFrame}}` 중첩 매핑
- 각 (symbol, tf) DataFrame에 모든 지표를 horizontal concat (timestamp 포함)
- `persist_to` 지정 시 `{persist_to}/{symbol_sanitized}_{tf}.parquet`로 저장

`required_warmup(indicators)`:
- 모든 지표의 required_warmup_bars 중 최댓값. 빈 리스트면 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import polars as pl

from backtester.core.errors import DataError
from backtester.data.base import sanitize_symbol
from backtester.indicators.base import Indicator


class IndicatorEngine:
    """지표 사전계산 + 결과 캐시 + 자동 영속화."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir
        self._cache: dict[tuple[str, str], pl.DataFrame] = {}

    def required_warmup(self, indicators: Sequence[Indicator]) -> int:
        return max((ind.required_warmup_bars() for ind in indicators), default=0)

    def precompute(
        self,
        bars: Mapping[str, Mapping[str, pl.DataFrame]],
        indicators: Sequence[Indicator],
        persist_to: Path | None = None,
    ) -> None:
        if persist_to is not None:
            persist_to.mkdir(parents=True, exist_ok=True)
        for symbol, tfs in bars.items():
            for tf, df in tfs.items():
                pieces: list[pl.DataFrame] = [df.select("timestamp")]
                for ind in indicators:
                    out = ind.compute(df)
                    if out.height != df.height:
                        raise DataError(
                            f"Indicator output height mismatch for {symbol}/{tf}: "
                            f"expected {df.height}, got {out.height}"
                        )
                    pieces.append(out)
                merged = pl.concat(pieces, how="horizontal")
                self._cache[(symbol, tf)] = merged
                if persist_to is not None:
                    out_path = persist_to / f"{sanitize_symbol(symbol)}_{tf}.parquet"
                    merged.write_parquet(out_path)

    def get(self, symbol: str, timeframe: str) -> pl.DataFrame:
        key = (symbol, timeframe)
        if key not in self._cache:
            raise DataError(
                f"Indicators not precomputed for symbol={symbol!r} tf={timeframe!r}. "
                f"Available: {sorted(self._cache.keys())}"
            )
        return self._cache[key]

    def has(self, symbol: str, timeframe: str) -> bool:
        return (symbol, timeframe) in self._cache
