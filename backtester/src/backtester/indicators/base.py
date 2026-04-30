"""Indicator Protocol (spec §3.8).

각 지표는 두 가지를 제공한다:
- `required_warmup_bars()`: 첫 유효 출력이 나오는 봉 인덱스 (= 스킵해야 하는 봉 수)
- `compute(bars)`: OHLCV DataFrame 입력 → 새 지표 컬럼만 담은 DataFrame 반환 (행 수 동일)

IndicatorEngine이 모든 지표 결과를 timestamp와 함께 horizontal concat한다.
"""

from __future__ import annotations

from typing import Protocol

import polars as pl


class Indicator(Protocol):
    """지표 계산 단위 (Phase 1: stateless만)."""

    def required_warmup_bars(self) -> int:
        """첫 유효 지표값이 나오는 0-based 인덱스.

        예시:
        - SMA(20): 인덱스 19에서 첫 값 → return 19
        - ATR(14): TR_0가 null이므로 인덱스 14에서 첫 값 → return 14
        """
        ...

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        """OHLCV DataFrame을 받아 지표 컬럼만 담은 DataFrame을 반환.

        반환 DataFrame은 입력과 행 수가 동일해야 하며, 워밍업 구간은 null이다.
        """
        ...
