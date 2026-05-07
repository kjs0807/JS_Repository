"""Event types (spec §3.15).

EventType은 (str, Enum) 패턴으로 Python 3.10 호환 (3.11+의 StrEnum 대체).
이렇게 하면 `event.type.value`도 동작하고 동시에 EventType.FILL == "fill"도 참.

IntentCreatedPayload는 의사결정 컨텍스트(decision_ts, bar_timestamp, bar_close_price)를
명시적으로 보존 — Phase 1.5+ 디버깅·재현·시각화 모두에서 사용.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from backtester.core.orders import OrderIntent


class EventType(str, Enum):
    """EventLog에 기록되는 이벤트 종류 (spec §3.15)."""

    BAR_CLOSE = "bar_close"
    DATA_GAP = "data_gap"
    INTENT_CREATED = "intent_created"
    ORDER_ADDED = "order_added"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_MODIFIED = "order_modified"
    ORDER_EXPIRED = "order_expired"
    ORDER_REJECTED = "order_rejected"
    ORDER_RESIZED = "order_resized"  # Phase 3 — multi-leg SL auto-shrink on TP fill
    FILL = "fill"
    SETTLE = "settle"
    LIQUIDATION = "liquidation"  # PR P
    SNAPSHOT = "snapshot"


SnapshotReason = Literal["fill", "settlement", "expire", "periodic", "liquidation"]
"""SNAPSHOT 이벤트 발행 사유 (spec §3.15).

Phase 1 활성: fill, periodic, expire(만료 케이스 자체가 안 발생하지만 ref).
Phase 1.5+: settlement.
"""


@dataclass(frozen=True)
class Event:
    """EventLog 레코드 단위.

    `payload`는 dataclass / dict / 기본 타입 등 무엇이든 가능. serialize_event_payload가
    JSON-친화 형태로 변환한다.
    """

    ts: datetime
    type: EventType
    payload: Any


@dataclass(frozen=True)
class IntentCreatedPayload:
    """INTENT_CREATED 이벤트 payload (spec §3.15).

    `decision_ts`는 ClockEvent.timestamp(= 봉 마감), `bar_timestamp`는 의사결정에 사용된
    가장 최근 마감 봉의 OHLCV timestamp(= 봉 시작). 두 ts를 모두 보존해 시간 모델
    혼동을 차단한다 (spec §2).
    """

    intent: OrderIntent
    decision_ts: datetime
    bar_timestamp: datetime
    bar_close_price: Decimal


@dataclass(frozen=True)
class OrderResizedPayload:
    """ORDER_RESIZED 이벤트 payload (Phase 3 multi-leg bracket).

    Engine 이 TP leg 체결 직후 같은 ``bracket_group_id`` 의 protector SL 의
    ``sized_quantity`` 를 leg 의 ``size_fraction`` 만큼 줄일 때 emit. resize 는
    ``OrderBook.resize`` 가 강제하는 invariant ``bracket_role == "protector_sl"`` 에
    의해 SL child 에만 허용 — TP leg 이나 non-bracket 주문에 대한 resize 는
    ``ValueError`` 로 차단된다.

    payload 필드:

    - ``order_id``: 줄어든 SL child 의 OrderBook id.
    - ``bracket_group_id``: SL 가 속한 multi-leg bracket group.
    - ``trigger_order_id``: 이번 resize 를 유발한 TP leg 의 order id (downstream
      replay/audit 용 — ``Fill`` 자체에는 별도 id 가 없고 ``(order_id, timestamp)``
      로 식별 가능).
    - ``old_sized_quantity`` / ``new_sized_quantity``: SL 의 ``sized_quantity``
      변경 전후. ``old - new`` 가 이번 cycle 에서 청산된 단위 수 (= TP leg 의
      ``size_fraction × parent_fill.size``).
    - ``old_remaining`` / ``new_remaining``: 이미 partial fill 이 있었던 경우
      적용 전후 의 ``remaining``. resize 는 ``new_remaining = new_sized -
      already_filled`` 로 재계산 — caller 가 partial-fill 이력을 직접 읽지 않아도
      되도록 함께 기록.
    - ``reason``: 자유형 문자열 (``"tp_leg_filled"`` 기본).
    """

    order_id: str
    bracket_group_id: str
    trigger_order_id: str
    old_sized_quantity: Decimal
    new_sized_quantity: Decimal
    old_remaining: Decimal
    new_remaining: Decimal
    reason: str = "tp_leg_filled"
