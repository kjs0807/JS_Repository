"""Beda Band v2.4 indicator port.

The TradingView script treats RSI as the source series, builds an RSI-range
RMA, then trails two RSI bands.  The strategy layer consumes the state/event
columns rather than plot colors.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from backtester.indicators.stateless.rsi import RSI


@dataclass(frozen=True)
class BedaBand:
    """RSI-based Beda Band state indicator.

    Output columns:
    - ``{name}_rsi``
    - ``{name}_trend_slow`` / ``{name}_trend_fast``
    - ``{name}_bull`` / ``{name}_bear``
    - ``{name}_bull_start`` / ``{name}_bear_start``
    """

    rsi_length: int = 13
    atr_period: int = 14
    slow_mult: float = 2.0
    fast_mult: float = 1.0
    source: str = "close"

    def __post_init__(self) -> None:
        if self.rsi_length <= 0:
            raise ValueError(f"rsi_length must be > 0, got {self.rsi_length}")
        if self.atr_period <= 0:
            raise ValueError(f"atr_period must be > 0, got {self.atr_period}")
        if self.slow_mult <= 0:
            raise ValueError(f"slow_mult must be > 0, got {self.slow_mult}")
        if self.fast_mult <= 0:
            raise ValueError(f"fast_mult must be > 0, got {self.fast_mult}")

    @property
    def name(self) -> str:
        return (
            f"beda_{self.rsi_length}_{self.atr_period}_"
            f"{self.slow_mult}_{self.fast_mult}"
        )

    def required_warmup_bars(self) -> int:
        return self.rsi_length + self.atr_period

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        prefix = self.name
        n = bars.height
        if n == 0:
            return pl.DataFrame(
                {
                    f"{prefix}_rsi": pl.Series([], dtype=pl.Float64),
                    f"{prefix}_trend_slow": pl.Series([], dtype=pl.Float64),
                    f"{prefix}_trend_fast": pl.Series([], dtype=pl.Float64),
                    f"{prefix}_bull": pl.Series([], dtype=pl.Boolean),
                    f"{prefix}_bear": pl.Series([], dtype=pl.Boolean),
                    f"{prefix}_bull_start": pl.Series([], dtype=pl.Boolean),
                    f"{prefix}_bear_start": pl.Series([], dtype=pl.Boolean),
                }
            )

        rsi_col = RSI(period=self.rsi_length, source=self.source).compute(bars)
        rsi_values = rsi_col[f"rsi_{self.rsi_length}"].to_list()

        ranges: list[float | None] = []
        for i, rsi in enumerate(rsi_values):
            if rsi is None:
                ranges.append(None)
                continue
            prev = rsi_values[i - 1] if i > 0 else None
            prev_rsi = rsi if prev is None else prev
            ranges.append(abs(float(rsi) - float(prev_rsi)))

        rsi_atr = self._rma(ranges, self.atr_period)

        slow: list[float | None] = []
        fast: list[float | None] = []
        bull: list[bool | None] = []
        bear: list[bool | None] = []
        bull_start: list[bool | None] = []
        bear_start: list[bool | None] = []

        prev_slow: float | None = None
        prev_fast: float | None = None
        prev_bull = False
        prev_bear = False

        for i, rsi in enumerate(rsi_values):
            if rsi is None:
                slow.append(None)
                fast.append(None)
                bull.append(None)
                bear.append(None)
                bull_start.append(None)
                bear_start.append(None)
                continue

            r = float(rsi)
            prev_r = rsi_values[i - 1] if i > 0 else None
            prev_r = r if prev_r is None else float(prev_r)
            atr = rsi_atr[i]

            tr_slow = self._next_trend(
                rsi=r,
                prev_rsi=prev_r,
                prev_trend=prev_slow,
                band=None if atr is None else self.slow_mult * atr,
            )
            tr_fast = self._next_trend(
                rsi=r,
                prev_rsi=prev_r,
                prev_trend=prev_fast,
                band=None if atr is None else self.fast_mult * atr,
            )

            is_bull = r > tr_slow and r > tr_fast
            is_bear = r < tr_slow and r < tr_fast

            slow.append(tr_slow)
            fast.append(tr_fast)
            bull.append(is_bull)
            bear.append(is_bear)
            bull_start.append(is_bull and not prev_bull)
            bear_start.append(is_bear and not prev_bear)

            prev_slow = tr_slow
            prev_fast = tr_fast
            prev_bull = is_bull
            prev_bear = is_bear

        return pl.DataFrame(
            {
                f"{prefix}_rsi": rsi_values,
                f"{prefix}_trend_slow": slow,
                f"{prefix}_trend_fast": fast,
                f"{prefix}_bull": bull,
                f"{prefix}_bear": bear,
                f"{prefix}_bull_start": bull_start,
                f"{prefix}_bear_start": bear_start,
            }
        )

    @staticmethod
    def _rma(values: list[float | None], period: int) -> list[float | None]:
        out: list[float | None] = []
        seed: list[float] = []
        acc: float | None = None
        for value in values:
            if value is None:
                out.append(None)
                continue
            if acc is None:
                seed.append(value)
                if len(seed) < period:
                    out.append(None)
                    continue
                acc = sum(seed) / period
                out.append(acc)
                continue
            acc = (acc * (period - 1) + value) / period
            out.append(acc)
        return out

    @staticmethod
    def _next_trend(
        *,
        rsi: float,
        prev_rsi: float,
        prev_trend: float | None,
        band: float | None,
    ) -> float:
        tr = rsi if prev_trend is None else prev_trend
        dv = tr
        if band is None:
            return tr

        if rsi < tr:
            tr = rsi + band
            if prev_rsi < dv and tr > dv:
                tr = dv

        if rsi > tr:
            tr = rsi - band
            if prev_rsi > dv and tr < dv:
                tr = dv

        return tr


__all__ = ["BedaBand"]
