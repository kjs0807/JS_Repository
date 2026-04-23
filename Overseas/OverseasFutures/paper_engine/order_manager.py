"""주문 관리자 — 가상 주문의 생성, 취소, 체결 관리."""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Order:
    """가상 주문.

    Attributes:
        order_id: UUID 기반 고유 주문 ID
        symbol: 상품 루트 심볼 (예: "VG")
        side: 매수/매도 방향 ("BUY" / "SELL")
        qty: 계약 수
        order_type: 주문 유형 ("MARKET" / "LIMIT")
        price: 지정가 (시장가 주문은 None)
        status: 주문 상태 ("PENDING" / "FILLED" / "CANCELLED")
        created_at: 주문 생성 시각
        filled_at: 체결 시각
        fill_price: 체결가
        strategy: 주문을 발생시킨 전략 이름
        event_type: 전략 이벤트 유형 (예: "ENTRY", "EXIT")
    """

    order_id: str
    symbol: str
    side: str
    qty: int
    order_type: str
    price: Optional[float]
    status: str
    created_at: datetime
    filled_at: Optional[datetime] = None
    fill_price: Optional[float] = None
    strategy: str = ""
    event_type: str = ""

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리 반환."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "order_type": self.order_type,
            "price": self.price,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "fill_price": self.fill_price,
            "strategy": self.strategy,
            "event_type": self.event_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Order":
        """딕셔너리에서 Order 복원."""
        return cls(
            order_id=data["order_id"],
            symbol=data["symbol"],
            side=data["side"],
            qty=data["qty"],
            order_type=data["order_type"],
            price=data.get("price"),
            status=data["status"],
            created_at=datetime.fromisoformat(data["created_at"]),
            filled_at=(
                datetime.fromisoformat(data["filled_at"])
                if data.get("filled_at")
                else None
            ),
            fill_price=data.get("fill_price"),
            strategy=data.get("strategy", ""),
            event_type=data.get("event_type", ""),
        )


@dataclass
class Fill:
    """체결 결과.

    Attributes:
        order: 체결된 주문
        fill_price: 체결가
        fill_qty: 체결 수량
        timestamp: 체결 시각
        slippage: 슬리피지 (가격 단위)
    """

    order: Order
    fill_price: float
    fill_qty: int
    timestamp: datetime
    slippage: float = 0.0


class OrderManager:
    """가상 주문 관리자.

    주문 생성, 취소, 지정가 체결 확인, 이력 관리를 담당한다.
    """

    def __init__(self) -> None:
        self.pending_orders: List[Order] = []
        self.filled_orders: deque = deque(maxlen=1000)
        self.order_history: deque = deque(maxlen=2000)

    # ── 주문 생성 ─────────────────────────────────────────────────

    def submit_market_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        strategy: str = "",
        event_type: str = "",
    ) -> Order:
        """시장가 주문 제출.

        Args:
            symbol: 상품 루트 심볼
            side: "BUY" 또는 "SELL"
            qty: 계약 수 (양수)
            strategy: 주문 발생 전략 이름
            event_type: 전략 이벤트 유형

        Returns:
            생성된 Order (status="PENDING").

        Raises:
            ValueError: qty가 0 이하이거나 side가 유효하지 않을 때.
        """
        self._validate_order_params(side, qty)
        order = Order(
            order_id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="MARKET",
            price=None,
            status="PENDING",
            created_at=datetime.now(),
            strategy=strategy,
            event_type=event_type,
        )
        self.pending_orders.append(order)
        self.order_history.append(order)
        return order

    def submit_limit_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        strategy: str = "",
        event_type: str = "",
    ) -> Order:
        """지정가 주문 제출.

        Args:
            symbol: 상품 루트 심볼
            side: "BUY" 또는 "SELL"
            qty: 계약 수 (양수)
            price: 지정 가격
            strategy: 주문 발생 전략 이름
            event_type: 전략 이벤트 유형

        Returns:
            생성된 Order (status="PENDING").

        Raises:
            ValueError: qty가 0 이하, side가 유효하지 않거나 price가 0 이하일 때.
        """
        self._validate_order_params(side, qty)
        if price <= 0:
            raise ValueError(f"Limit price must be positive, got {price}")
        order = Order(
            order_id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="LIMIT",
            price=price,
            status="PENDING",
            created_at=datetime.now(),
            strategy=strategy,
            event_type=event_type,
        )
        self.pending_orders.append(order)
        self.order_history.append(order)
        return order

    # ── 주문 취소 ─────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        """지정한 주문 취소.

        Args:
            order_id: 취소할 주문의 UUID

        Returns:
            취소 성공이면 True, 해당 주문이 없으면 False.
        """
        for i, order in enumerate(self.pending_orders):
            if order.order_id == order_id:
                order.status = "CANCELLED"
                self.pending_orders.pop(i)
                return True
        return False

    # ── 체결 확인 ─────────────────────────────────────────────────

    def check_fills(self, current_prices: Dict[str, float]) -> List[Fill]:
        """대기 중인 지정가 주문의 체결 가능 여부 확인.

        시장가 주문도 PENDING 상태로 존재할 수 있으므로 함께 처리한다.
        체결된 주문은 pending_orders에서 제거되고 filled_orders로 이동한다.

        Args:
            current_prices: {symbol: current_price} 딕셔너리

        Returns:
            이번 호출에서 체결된 Fill 목록.
        """
        from paper_engine.fill_simulator import FillSimulator
        from config.products import PRODUCTS

        simulator = FillSimulator(slippage_ticks=0.0)
        fills: List[Fill] = []
        remaining: List[Order] = []

        for order in self.pending_orders:
            price = current_prices.get(order.symbol)
            product = PRODUCTS.get(order.symbol)

            if price is None or product is None:
                remaining.append(order)
                continue

            fill: Optional[Fill] = None
            if order.order_type == "MARKET":
                fill = simulator.fill_market(order, price, product)
            elif order.order_type == "LIMIT":
                fill = simulator.check_limit(order, price, product)

            if fill is not None:
                order.status = "FILLED"
                order.filled_at = fill.timestamp
                order.fill_price = fill.fill_price
                self.filled_orders.append(order)
                fills.append(fill)
            else:
                remaining.append(order)

        self.pending_orders = remaining
        return fills

    # ── 조회 ─────────────────────────────────────────────────────

    def get_pending_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """대기 중인 주문 목록 반환.

        Args:
            symbol: 필터할 심볼. None이면 전체 반환.

        Returns:
            PENDING 상태 주문 목록.
        """
        if symbol is None:
            return list(self.pending_orders)
        return [o for o in self.pending_orders if o.symbol == symbol]

    # ── 직렬화 ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리 반환."""
        return {
            "pending_orders": [o.to_dict() for o in self.pending_orders],
            "filled_orders": [o.to_dict() for o in self.filled_orders],
            "order_history": [o.to_dict() for o in self.order_history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OrderManager":
        """딕셔너리에서 OrderManager 복원."""
        manager = cls()
        manager.pending_orders = [
            Order.from_dict(o) for o in data.get("pending_orders", [])
        ]
        manager.filled_orders = [
            Order.from_dict(o) for o in data.get("filled_orders", [])
        ]
        manager.order_history = [
            Order.from_dict(o) for o in data.get("order_history", [])
        ]
        return manager

    # ── 내부 헬퍼 ────────────────────────────────────────────────

    @staticmethod
    def _validate_order_params(side: str, qty: int) -> None:
        """주문 파라미터 기본 검증."""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
