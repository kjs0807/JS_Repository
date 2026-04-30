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
    FILL = "fill"
    SETTLE = "settle"
    SNAPSHOT = "snapshot"


SnapshotReason = Literal["fill", "settlement", "expire", "periodic"]
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
