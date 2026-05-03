"""Exponential moving average indicator."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class EMA:
    """Close-price EMA with a stable ``ema_{period}`` output column."""

    period: int = 200
    source: str = "close"

    def __post_init__(self) -> None:
        if self.period < 1:
            raise ValueError(f"period must be >= 1, got {self.period}")
        if not self.source:
            raise ValueError("source must be non-empty")

    @property
    def name(self) -> str:
        return f"ema_{self.period}"

    def required_warmup_bars(self) -> int:
        return self.period - 1

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        if bars.height == 0:
            return pl.DataFrame(schema={self.name: pl.Float64})
        if self.source not in bars.columns:
            raise ValueError(f"EMA source column not found: {self.source!r}")
        return bars.select(
            pl.col(self.source)
            .ewm_mean(span=self.period, adjust=False)
            .alias(self.name)
        )


__all__ = ["EMA"]
