"""Bollinger Bands — `period`-기간 SMA ± `num_std`·rolling_std.

Warmup: `period - 1` (SMA의 첫 유효값은 인덱스 period-1).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class BollingerBands:
    """Bollinger Bands 지표.

    출력 컬럼: `{name}_mid`, `{name}_upper`, `{name}_lower`.
    예: BollingerBands(period=20, num_std=2.0) → bb_20_2.0_mid / _upper / _lower
    """

    period: int = 20
    num_std: float = 2.0

    def __post_init__(self) -> None:
        if self.period < 2:
            raise ValueError(f"period must be >= 2, got {self.period}")
        if self.num_std <= 0:
            raise ValueError(f"num_std must be > 0, got {self.num_std}")

    @property
    def name(self) -> str:
        return f"bb_{self.period}_{self.num_std}"

    def required_warmup_bars(self) -> int:
        # SMA(period)의 첫 유효값은 인덱스 period-1
        return self.period - 1

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        prefix = self.name
        mid = pl.col("close").rolling_mean(self.period)
        std = pl.col("close").rolling_std(self.period)
        return bars.select(
            [
                mid.alias(f"{prefix}_mid"),
                (mid + self.num_std * std).alias(f"{prefix}_upper"),
                (mid - self.num_std * std).alias(f"{prefix}_lower"),
            ]
        )
