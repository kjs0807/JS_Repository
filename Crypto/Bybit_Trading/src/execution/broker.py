"""Broker Protocol + 핵심 데이터 타입."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_time: int
    stop_loss: float
    take_profit: Optional[float]
    unrealized_pnl: float
    strategy_name: str
    max_favorable: float = 0.0

@dataclass
class Portfolio:
    initial_capital: float
    equity: float
    available_margin: float
    used_margin: float
    realized_pnl: float
    daily_pnl: float
    positions: List[Position] = field(default_factory=list)

@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    timestamp: int
    fill_type: str

@dataclass(frozen=True)
class Order:
    order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str
    stop_loss: Optional[float]
    take_profit: Optional[float]
    strategy_name: str
    source: str
    reason: str
    created_at: int

@runtime_checkable
class Broker(Protocol):
    def buy(self, symbol: str, qty: float, stop_loss: float,
            take_profit: Optional[float] = None, reason: str = "") -> str: ...
    def sell(self, symbol: str, qty: float, stop_loss: float,
             take_profit: Optional[float] = None, reason: str = "") -> str: ...
    def close(self, symbol: str, reason: str = "") -> str: ...
    def update_stop(self, symbol: str, new_stop: float) -> None: ...
    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None: ...
    def manual_buy(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                   take_profit: Optional[float] = None, reason: str = "") -> str: ...
    def manual_sell(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None, reason: str = "") -> str: ...
    def manual_close(self, symbol: str, reason: str = "") -> str: ...
    def manual_close_all(self, reason: str = "") -> List[str]: ...
    def manual_update_stop(self, symbol: str, new_stop: float) -> None: ...
    def manual_update_tp(self, symbol: str, new_tp: float) -> None: ...
    def get_position(self, symbol: str) -> Optional[Position]: ...
    def get_positions(self) -> List[Position]: ...
    def get_portfolio(self) -> Portfolio: ...
    def calc_qty(self, symbol: str, risk_pct: float, stop_distance: float) -> float: ...

__all__ = ["Position", "Portfolio", "Fill", "Order", "Broker"]
