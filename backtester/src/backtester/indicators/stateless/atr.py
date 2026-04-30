"""Average True Range — `period`-기간 단순 평균(SMA of TR).

True Range:
    TR_t = max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)

`TR_0`는 `C_{-1}`이 없어 null이다. 따라서 `TR.rolling_mean(period)`의 첫 유효값은
인덱스 `period`에서 발생 → warmup = period.

Phase 1은 단순 SMA. Wilder smoothing 등 고급 변종은 Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class ATR:
    """Average True Range 지표 (단순 평균 변종).

    출력 컬럼: `atr_{period}` (그리고 보조로 `tr_{period}`는 출력하지 않음).
    """

    period: int = 14

    def __post_init__(self) -> None:
        if self.period < 1:
            raise ValueError(f"period must be >= 1, got {self.period}")

    @property
    def name(self) -> str:
        return f"atr_{self.period}"

    def required_warmup_bars(self) -> int:
        # TR_0가 null이므로 SMA(period)의 첫 유효값은 인덱스 period
        return self.period

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        prev_close = pl.col("close").shift(1)
        # max_horizontal는 null을 건너뛰므로 prev_close가 null일 때
        # 명시적으로 TR도 null이 되도록 마스킹 (TR_0가 H-L만으로 계산되는 것 방지)
        tr_raw = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )
        tr = pl.when(prev_close.is_null()).then(None).otherwise(tr_raw)
        atr = tr.rolling_mean(self.period)
        return bars.select(atr.alias(self.name))
