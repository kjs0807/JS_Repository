"""RSI 지표 (Phase 2.5 PR T).

Wilder smoothing 변형:
- delta = close.diff()
- gain = max(delta, 0); loss = max(-delta, 0)
- avg_gain = ewm_mean(alpha=1/period) of gain (Wilder)
- avg_loss = ewm_mean(alpha=1/period) of loss (Wilder)
- rs = avg_gain / avg_loss
- rsi = 100 - 100/(1 + rs)

폴라스 ``ewm_mean(alpha=1/period, adjust=False)`` 가 Wilder 와 정확히 일치.

워밍업: ``period`` 봉 기준. 첫 ``period`` 봉은 None.
"""

from __future__ import annotations

import polars as pl


class RSI:
    """RSI 지표 (Wilder smoothing)."""

    def __init__(self, *, period: int = 14, source: str = "close") -> None:
        if period <= 0:
            raise ValueError(f"period must be > 0, got {period}")
        self.period = period
        self.source = source
        self.name = f"rsi_{period}"

    def required_warmup_bars(self) -> int:
        return self.period

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        col = self.source
        n = bars.height
        if n == 0:
            return pl.DataFrame({self.name: pl.Series([], dtype=pl.Float64)})
        # delta + gain/loss
        delta = bars.with_columns(
            (pl.col(col) - pl.col(col).shift(1)).alias("__delta")
        )
        gain_loss = delta.with_columns(
            pl.when(pl.col("__delta") > 0)
            .then(pl.col("__delta"))
            .otherwise(0.0)
            .alias("__gain"),
            pl.when(pl.col("__delta") < 0)
            .then(-pl.col("__delta"))
            .otherwise(0.0)
            .alias("__loss"),
        )
        # Wilder smoothing — alpha=1/period.
        smoothed = gain_loss.with_columns(
            pl.col("__gain").ewm_mean(alpha=1.0 / self.period, adjust=False).alias(
                "__avg_gain"
            ),
            pl.col("__loss").ewm_mean(alpha=1.0 / self.period, adjust=False).alias(
                "__avg_loss"
            ),
        )
        # RSI 계산. avg_loss == 0 이면 100, avg_gain == 0 이면 0.
        rsi = smoothed.with_columns(
            pl.when(pl.col("__avg_loss") == 0)
            .then(pl.lit(100.0))
            .otherwise(
                100.0
                - 100.0 / (1.0 + pl.col("__avg_gain") / pl.col("__avg_loss"))
            )
            .alias(self.name)
        )
        # 워밍업: 첫 period 개는 None (Wilder 도 안정 전).
        rsi = rsi.with_columns(
            pl.when(pl.int_range(0, n) < self.period)
            .then(pl.lit(None, dtype=pl.Float64))
            .otherwise(pl.col(self.name))
            .alias(self.name)
        )
        return rsi.select(self.name)
