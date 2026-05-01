"""Order + OrderBook (spec §3.9, PR D 확장).

Order는 mutable dataclass — `state`/`remaining`/`fills` 등이 체결 진행에 따라 변한다.

PR D 활성 lifecycle:
- ``add`` 가 ``intent.type ∈ {market, limit, stop, stop_limit}`` 모두 허용.
- limit/stop_limit 은 ``limit_price`` 필수, stop/stop_limit 은 ``stop_price`` 필수.
- ``tif="GTC"`` 만 (IOC/FOK/DAY 는 별개 후속 PR — 이번엔 ``expires_at`` 기반 만료만).
- ``expires_at`` 활성: ``add`` 가 받아들이고, ``expire_pending(ts)`` 가 ``ts >=
  expires_at`` 인 active 주문을 ``expired`` 로 전이.
- ``modify(order_id, *, limit_price=None, stop_price=None)``: limit/stop/stop_limit
  주문의 가격 필드 변경. market 은 ``ValueError``. 비활성 주문은 ``False`` 반환.
- ``cancel`` 은 PR 4 부터 동작 (active → cancelled).

Risk rejection으로 인한 'rejected' state 진입은 OrderBook이 아니라 Engine 책임.
"""

from __future__ import annotations

import dataclasses as _dc
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from backtester.core.orders import OrderIntent
from backtester.core.types import Fill

OrderState = Literal[
    "pending",
    "partially_filled",
    "filled",
    "cancelled",
    "expired",
    "rejected",
]

_ACTIVE_STATES: frozenset[OrderState] = frozenset({"pending", "partially_filled"})
_TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {"filled", "cancelled", "expired", "rejected"}
)


@dataclass
class Order:
    """주문 추적 객체 (mutable).

    `id`는 OrderBook이 부여한 고유 식별자.
    `state`는 lifecycle 진행에 따라 mutate.
    `remaining`은 sized_quantity에서 누적 체결분을 뺀 잔량.
    """

    id: str
    intent: OrderIntent
    state: OrderState
    submitted_at: datetime
    sized_quantity: Decimal
    remaining: Decimal
    fills: list[Fill] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.state in _ACTIVE_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


class OrderBook:
    """주문 등록·취소·체결 추적 (Phase 1)."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._counter: int = 0

    def add(
        self,
        intent: OrderIntent,
        sized_quantity: Decimal,
        ts: datetime,
    ) -> Order:
        """새 주문을 'pending' 상태로 등록.

        Sizer가 SizeSpec을 실 단위로 변환한 결과(`sized_quantity`)를 받는다.

        Phase 2 PR 15b 정상 처리 범위:
        - ``intent.type ∈ {"market", "limit", "stop", "stop_limit"}``
        - ``intent.tif == "GTC"`` + ``intent.expires_at is None`` (TIF/만료는 후속 PR)
        - 가격 필드 정합성:
            - market: ``limit_price``/``stop_price`` 모두 None 이어야 함
            - limit: ``limit_price`` 필수
            - stop: ``stop_price`` 필수
            - stop_limit: ``limit_price`` + ``stop_price`` 둘 다 필수
        - ``sized_quantity > 0`` (Sizer 가 절대값을 반환)

        그 외 입력은 NotImplementedError 또는 ValueError 로 차단. 특히 ``expire_pending``
        이 항상 []를 반환하므로 ``expires_at`` 이 있는 주문이 통과되면 영원히 active 로
        남는 버그가 된다 — 입구에서 막는다.
        """
        if intent.type not in ("market", "limit", "stop", "stop_limit"):
            raise NotImplementedError(
                f"OrderIntent.type={intent.type!r} is not supported "
                f"(Phase 2 PR 15b supports market / limit / stop / stop_limit)"
            )
        if intent.tif != "GTC":
            raise NotImplementedError(
                f"OrderIntent.tif={intent.tif!r} is not supported "
                f"(현재 'GTC' 만 — IOC/FOK/DAY 는 후속 PR)"
            )
        # PR D: expires_at 활성 — expire_pending(ts) 가 만료 처리.
        if intent.expires_at is not None and intent.expires_at <= ts:
            raise ValueError(
                f"OrderIntent.expires_at={intent.expires_at!r} must be > submit ts={ts!r}"
            )

        # 가격 필드 정합성
        if intent.type == "market":
            if intent.limit_price is not None or intent.stop_price is not None:
                raise ValueError(
                    "market order must not have limit_price or stop_price; got "
                    f"limit_price={intent.limit_price!r}, stop_price={intent.stop_price!r}"
                )
        elif intent.type == "limit":
            if intent.limit_price is None:
                raise ValueError("limit order requires limit_price")
            if intent.stop_price is not None:
                raise ValueError(
                    "limit order must not have stop_price; got "
                    f"stop_price={intent.stop_price!r}"
                )
        elif intent.type == "stop":
            if intent.stop_price is None:
                raise ValueError("stop order requires stop_price")
            if intent.limit_price is not None:
                raise ValueError(
                    "stop order must not have limit_price; got "
                    f"limit_price={intent.limit_price!r}"
                )
        else:  # stop_limit
            if intent.limit_price is None or intent.stop_price is None:
                raise ValueError(
                    "stop_limit order requires both limit_price and stop_price; got "
                    f"limit_price={intent.limit_price!r}, stop_price={intent.stop_price!r}"
                )

        if sized_quantity <= 0:
            raise ValueError(
                f"sized_quantity must be > 0 (Sizer returns absolute size); "
                f"got {sized_quantity}"
            )

        order_id = self._next_id()
        order = Order(
            id=order_id,
            intent=intent,
            state="pending",
            submitted_at=ts,
            sized_quantity=sized_quantity,
            remaining=sized_quantity,
        )
        self._orders[order_id] = order
        return order

    def cancel(self, order_id: str, ts: datetime) -> bool:
        """active(pending/partial) 주문을 cancelled로 전이.

        반환:
        - True: 취소 성공
        - False: 주문 미존재 또는 이미 terminal 상태
        """
        del ts  # Phase 1: 취소 시각은 별도 기록하지 않음 (Phase 2+에서 EventLog 활용)
        order = self._orders.get(order_id)
        if order is None or not order.is_active:
            return False
        order.state = "cancelled"
        return True

    def modify(
        self,
        order_id: str,
        *,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> bool:
        """active 주문의 가격 필드를 변경 (PR D).

        반환:
        - True: 변경 성공
        - False: 주문 미존재 / 이미 terminal / 변경 사항 없음

        Raises:
        - ValueError: market 주문은 가격 필드가 없어 modify 불가. 또는 limit_price 가
          stop/market 에, stop_price 가 limit/market 에 잘못 적용된 경우.
        """
        order = self._orders.get(order_id)
        if order is None or not order.is_active:
            return False
        if limit_price is None and stop_price is None:
            return False  # nothing to modify
        if order.intent.type == "market":
            raise ValueError(
                "market order has no price field to modify — use cancel + new"
            )
        if limit_price is not None and order.intent.type not in ("limit", "stop_limit"):
            raise ValueError(
                f"limit_price modify only valid for limit/stop_limit; got "
                f"{order.intent.type!r}"
            )
        if stop_price is not None and order.intent.type not in ("stop", "stop_limit"):
            raise ValueError(
                f"stop_price modify only valid for stop/stop_limit; got "
                f"{order.intent.type!r}"
            )
        new_limit = (
            limit_price if limit_price is not None else order.intent.limit_price
        )
        new_stop = (
            stop_price if stop_price is not None else order.intent.stop_price
        )
        order.intent = _dc.replace(
            order.intent, limit_price=new_limit, stop_price=new_stop
        )
        return True

    def get_active(self, symbol: str | None = None) -> list[Order]:
        """state ∈ {pending, partially_filled}인 주문만 반환.

        `symbol` 지정 시 해당 심볼만.
        """
        actives = [o for o in self._orders.values() if o.is_active]
        if symbol is not None:
            actives = [o for o in actives if o.intent.symbol == symbol]
        return actives

    def expire_pending(self, ts: datetime) -> list[Order]:
        """``ts >= intent.expires_at`` 인 active 주문을 ``expired`` 상태로 전이 (PR D).

        반환: 만료 처리된 ``Order`` 리스트 (호출자가 EventLog 에 ORDER_EXPIRED 기록 +
        Ledger.on_expired 로 잔여 마진 해제 등 후처리).
        ``expires_at`` 이 None 인 주문은 영원히 active (GTC).
        """
        expired: list[Order] = []
        for order in self._orders.values():
            if not order.is_active:
                continue
            if order.intent.expires_at is None:
                continue
            if order.intent.expires_at <= ts:
                order.state = "expired"
                expired.append(order)
        return expired

    def fill(self, order_id: str, fill: Fill) -> None:
        """체결 결과 반영. 누적 체결량이 sized_quantity 도달 시 'filled'로 전이.

        존재하지 않거나 이미 terminal인 주문에 호출하면 KeyError/RuntimeError.
        Fill 메타(order_id/symbol/side)가 Order와 불일치하면 ValueError.
        """
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Order not found: {order_id!r}")
        if order.is_terminal:
            raise RuntimeError(
                f"Cannot fill order in terminal state: {order_id!r} state={order.state}"
            )
        # Fill ↔ Order 정합성 검증 (실수로 다른 주문의 fill을 넣는 것 방지)
        if fill.order_id != order_id:
            raise ValueError(
                f"Fill.order_id mismatch: order={order_id!r} but fill.order_id={fill.order_id!r}"
            )
        if fill.symbol != order.intent.symbol:
            raise ValueError(
                f"Fill.symbol mismatch for {order_id!r}: "
                f"order.intent.symbol={order.intent.symbol!r}, fill.symbol={fill.symbol!r}"
            )
        if fill.side != order.intent.side:
            raise ValueError(
                f"Fill.side mismatch for {order_id!r}: "
                f"order.intent.side={order.intent.side!r}, fill.side={fill.side!r}"
            )
        if fill.size <= 0:
            raise ValueError(f"Fill size must be positive, got {fill.size}")
        if fill.size > order.remaining:
            raise ValueError(
                f"Fill size {fill.size} exceeds remaining {order.remaining} "
                f"for order {order_id!r}"
            )
        order.fills.append(fill)
        order.remaining = order.remaining - fill.size
        if order.remaining == 0:
            order.state = "filled"
        else:
            order.state = "partially_filled"

    def get(self, order_id: str) -> Order:
        if order_id not in self._orders:
            raise KeyError(f"Order not found: {order_id!r}")
        return self._orders[order_id]

    def _next_id(self) -> str:
        order_id = f"ord_{self._counter}"
        self._counter += 1
        return order_id

    def __len__(self) -> int:
        return len(self._orders)
