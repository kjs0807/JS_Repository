"""Dual Bollinger Band Breakout Strategy — Events and Data Types."""

from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple


class EventType(Enum):
    """Strategy event types."""
    ENTRY_1ST = "entry_1st"
    ENTRY_2ND = "entry_2nd"
    STOP_LOSS = "stop_loss"
    PARTIAL_EXIT = "partial_exit"
    FULL_EXIT = "full_exit"
    BAND_EXIT = "band_exit"
    OUTER_RSI_EXIT = "outer_rsi_exit"
    TRAILING_STOP_EXIT = "trailing_stop_exit"
    EMERGENCY_EXIT = "emergency_exit"


class State(Enum):
    """FSM states for the Dual Bollinger Band strategy."""
    FLAT = "FLAT"
    LONG_1ST = "LONG_1ST"
    LONG_2ND = "LONG_2ND"
    LONG_PARTIAL = "LONG_PARTIAL"
    SHORT_1ST = "SHORT_1ST"
    SHORT_2ND = "SHORT_2ND"
    SHORT_PARTIAL = "SHORT_PARTIAL"


@dataclass
class StrategyEvent:
    """Represents a strategy state transition event."""
    timestamp: datetime
    event_type: EventType
    symbol: str
    side: str           # 'BUY' or 'SELL'
    qty: int
    price: float
    reason: str
    state_before: str   # State.value
    state_after: str    # State.value


@dataclass
class Position:
    """Current position state."""
    symbol: str
    side: str           # 'LONG' or 'SHORT'
    entries: List[Tuple[float, int, datetime]]  # [(price, qty, timestamp), ...]
    total_qty: int
    avg_price: float
    stop_loss_level: float = 0.0  # ATR 기반 동적 스탑
    trailing_stop_level: float = 0.0  # 트레일링 스탑 (활성화 전 = 0)
    trailing_active: bool = False  # 트레일링 활성화 여부
    max_favorable_price: float = 0.0  # 포지션 중 최고/최저 유리가
    unrealized_pnl: float = 0.0


@dataclass
class TradeRecord:
    """Completed trade record with statistics."""
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str    # EventType.value
    had_2nd_entry: bool
    holding_bars: int
    mae: float          # Maximum Adverse Excursion
    mfe: float          # Maximum Favorable Excursion
