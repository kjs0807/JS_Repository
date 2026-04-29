"""포지션 추적기. Broker 내부 컴포넌트."""
from __future__ import annotations
import logging
from typing import Dict, List, Optional
from src.execution.broker import Position

logger = logging.getLogger(__name__)

class PositionTracker:
    def __init__(self) -> None:
        self._positions: Dict[str, Position] = {}

    def open(self, symbol: str, side: str, qty: float, entry_price: float,
             entry_time: int, stop_loss: float, take_profit: Optional[float],
             strategy_name: str) -> Position:
        pos = Position(symbol=symbol, side=side, qty=qty, entry_price=entry_price,
                       entry_time=entry_time, stop_loss=stop_loss, take_profit=take_profit,
                       unrealized_pnl=0.0, strategy_name=strategy_name)
        self._positions[symbol] = pos
        return pos

    def close(self, symbol: str) -> Optional[Position]:
        return self._positions.pop(symbol, None)

    def close_all(self) -> List[Position]:
        closed = list(self._positions.values())
        self._positions.clear()
        return closed

    def get(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_all(self) -> List[Position]:
        return list(self._positions.values())

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def update_stop(self, symbol: str, new_stop: float) -> None:
        pos = self._positions.get(symbol)
        if pos:
            pos.stop_loss = new_stop

    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None:
        """Optional[float] 허용 — None은 TP 제거 의미 (drop_tp / 운영자 manual close 등)."""
        pos = self._positions.get(symbol)
        if pos:
            pos.take_profit = new_tp

    def update_unrealized(self, symbol: str, current_price: float) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            return
        if pos.side == "LONG":
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.qty
        else:
            pos.unrealized_pnl = (pos.entry_price - current_price) * pos.qty

    @property
    def count(self) -> int:
        return len(self._positions)

__all__ = ["PositionTracker"]
