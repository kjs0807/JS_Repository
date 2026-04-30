"""RiskManager (spec §3.13).

Phase 1 범위 (spec §17.1, §20 PR 5):
- 검사: blacklist_symbols (거부 → REJECTED), max_orders_per_symbol (거부 → REJECTED)
- 정의만 두고 검사하지 않음: max_position_size, max_total_exposure, max_leverage,
  max_drawdown_halt (Phase 2에서 활성화)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from backtester.core.orders import OrderIntent
from backtester.instruments.base import Instrument

if TYPE_CHECKING:
    from backtester.core.orderbook import Order
    from backtester.portfolio.ledger import Ledger


@dataclass(frozen=True)
class RiskCheckResult:
    """RiskManager.check 결과. accept 시 reason은 빈 문자열."""

    accepted: bool
    reason: str = ""

    @classmethod
    def accept(cls) -> RiskCheckResult:
        return cls(accepted=True, reason="")

    @classmethod
    def reject(cls, reason: str) -> RiskCheckResult:
        return cls(accepted=False, reason=reason)


@dataclass(frozen=True)
class RiskLimits:
    """리스크 한도 (spec §3.13).

    Phase 1 활성: max_orders_per_symbol, blacklist_symbols.
    Phase 2 활성: 나머지 (정의는 Phase 1에 있지만 RiskManager가 검사하지 않음).
    """

    max_orders_per_symbol: int = 5  # Phase 1
    blacklist_symbols: frozenset[str] = field(default_factory=frozenset)  # Phase 1
    max_position_size: Decimal | None = None  # Phase 2
    max_total_exposure: Decimal | None = None  # Phase 2
    max_leverage: Decimal | None = None  # Phase 2
    max_drawdown_halt: float | None = None  # Phase 2


class RiskManager:
    """주문 의도 + 사이즈를 받아 통과/거부 판정."""

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def check(
        self,
        intent: OrderIntent,
        sized_quantity: Decimal,
        instrument: Instrument,
        ledger: Ledger,
        active_orders: list[Order],
    ) -> RiskCheckResult:
        del sized_quantity, instrument, ledger  # Phase 1 미사용 (Phase 2 활용)

        # Phase 1 검사 1: blacklist
        if intent.symbol in self.limits.blacklist_symbols:
            return RiskCheckResult.reject(
                f"symbol blacklisted: {intent.symbol!r}"
            )

        # Phase 1 검사 2: max_orders_per_symbol
        symbol_orders = [o for o in active_orders if o.intent.symbol == intent.symbol]
        if len(symbol_orders) >= self.limits.max_orders_per_symbol:
            return RiskCheckResult.reject(
                f"max_orders_per_symbol exceeded for {intent.symbol!r}: "
                f"{len(symbol_orders)} >= {self.limits.max_orders_per_symbol}"
            )

        return RiskCheckResult.accept()
