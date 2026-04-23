"""Shared dataclasses for the ML subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from src.core.types import BarSeries


@dataclass(frozen=True)
class MTFData:
    """Multi-timeframe OHLCV bundle for a single symbol."""

    symbol: str
    primary_tf: str
    series: Dict[str, BarSeries]

    def get_primary(self) -> BarSeries:
        return self.series[self.primary_tf]


@dataclass(frozen=True)
class PatternEvent:
    """A pattern trigger occurrence at a specific bar."""

    timestamp_ms: int
    bar_index: int
    symbol: str
    direction: Literal["long", "short"]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:  # frozen + dict needs custom hash
        return hash((self.timestamp_ms, self.bar_index, self.symbol, self.direction))


@dataclass(frozen=True)
class LabelConfig:
    """Triple-barrier label configuration.

    ``label_mode`` selects the barrier formula:
    - ``"pct"``:  upper = entry * (1 + tp_pct),          lower = entry * (1 - sl_pct)
    - ``"atr"``:  upper = entry + tp_atr_mult * ATR(t),  lower = entry - sl_atr_mult * ATR(t)

    In both modes short trades use symmetric barriers (TP below entry, SL above).
    The timeout barrier is ``max_holding_bars`` after the event; in v1,
    timeouts are assigned to the ``timeout_class`` (``"negative"`` by default).
    """

    tp_pct: float
    sl_pct: float
    max_holding_bars: int
    label_type: str = "triple_barrier_binary"
    timeout_class: str = "negative"  # v1: timeout = loss class
    label_mode: Literal["pct", "atr"] = "pct"
    tp_atr_mult: Optional[float] = None
    sl_atr_mult: Optional[float] = None
    atr_period: int = 14
