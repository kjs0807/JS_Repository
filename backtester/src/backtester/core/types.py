"""공통 도메인 타입 (spec §3.10, §3.11, §3.13).

BarPathModel: 한 봉 안의 high/low 도달 순서 가정.
Fill: 체결 결과.
to_decimal: Decimal 가드 헬퍼.

Phase 1 범위:
- BarPathModel은 enum **정의만** 둔다. ExecutionModel은 next_bar_open만 구현하므로
  Phase 1에서는 어느 멤버도 사용되지 않는다 (사용은 Phase 2).
- Fill은 next_bar_open 체결 결과 기록용.
- to_decimal은 회계 진입 가드 — Ledger 등이 외부 값을 받을 때 강제 사용.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal


class BarPathModel(Enum):
    """한 봉 내 가격 도달 순서 가정. Phase 1에서는 정의만 두고 사용하지 않는다."""

    PESSIMISTIC = "pessimistic"
    OPTIMISTIC = "optimistic"
    OPEN_TO_CLOSE = "linear"
    OHLC_ORDER = "ohlc"


def to_decimal(x: Decimal | int | str | float) -> Decimal:
    """Decimal 변환 가드 (spec §3.13).

    회계 진입 시 외부에서 들어온 값(설정·전략·체결가 등)을 항상 이 함수로 통과시킨다.
    `float`은 `Decimal(str(x))`로 변환해 부동소수 정확성 문제(repr 차이)를 피한다.
    """
    if isinstance(x, Decimal):
        return x
    if isinstance(x, (int, str)):
        return Decimal(str(x))
    if isinstance(x, float):
        return Decimal(str(x))
    raise TypeError(f"Cannot convert {type(x).__name__} to Decimal: {x!r}")


@dataclass(frozen=True)
class Fill:
    """체결 결과 (spec §3.10).

    ExecutionModel이 OrderIntent를 체결할 때 생성. EventLog에 FILL 이벤트로 기록되며
    Ledger.on_fill의 입력이 된다.
    """

    timestamp: datetime
    symbol: str
    price: Decimal
    size: Decimal
    side: Literal["buy", "sell"]
    fee: Decimal
    fee_currency: str
    order_id: str
    intent_reason: str
    indicators_snapshot: dict[str, Any] = field(default_factory=dict)
