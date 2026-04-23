"""주문 관리자. Broker 내부 컴포넌트."""
from __future__ import annotations
import logging
import uuid
from typing import Dict, List, Optional
from src.execution.broker import Order, Fill

logger = logging.getLogger(__name__)

class OrderManager:
    def __init__(self) -> None:
        self._pending: Dict[str, Order] = {}
        self._fills: List[Fill] = []

    def create(self, symbol: str, side: str, qty: float, order_type: str,
               stop_loss: Optional[float], take_profit: Optional[float],
               strategy_name: str, source: str, reason: str) -> str:
        order_id = str(uuid.uuid4())[:8]
        order = Order(order_id=order_id, symbol=symbol, side=side, qty=qty,
                      order_type=order_type, stop_loss=stop_loss, take_profit=take_profit,
                      strategy_name=strategy_name, source=source, reason=reason, created_at=0)
        self._pending[order_id] = order
        return order_id

    def fill(self, order_id: str, price: float, fee: float,
             timestamp: int, fill_type: str) -> Optional[Fill]:
        order = self._pending.pop(order_id, None)
        if order is None:
            return None
        fill = Fill(order_id=order_id, symbol=order.symbol, side=order.side,
                    qty=order.qty, price=price, fee=fee, timestamp=timestamp, fill_type=fill_type)
        self._fills.append(fill)
        return fill

    def cancel(self, order_id: str) -> bool:
        return self._pending.pop(order_id, None) is not None

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._pending.get(order_id)

    def get_pending(self) -> List[Order]:
        return list(self._pending.values())

    def get_fills(self) -> List[Fill]:
        return list(self._fills)

    def clear_pending(self) -> None:
        self._pending.clear()

__all__ = ["OrderManager"]
