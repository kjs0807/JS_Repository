"""RiskManager (spec §3.13, PR I 활성).

활성 검사:
- blacklist_symbols (Phase 1): symbol 차단.
- max_orders_per_symbol (Phase 1): per-symbol 활성 주문 한도.
- max_position_size (PR I): 사이즈 적용 후 ``abs(new_position_size) > max_position_size``
  → reject.
- max_total_exposure (PR I): 사이즈 적용 후 모든 심볼의 |size × mark| 합이 한도 초과 →
  reject.
- max_leverage (PR I): 사이즈 적용 후 ``total_notional / equity > max_leverage`` →
  reject.
- max_drawdown_halt (후속 PR): equity drawdown 한도 도달 시 신규 주문 정지.
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
    max_position_size: Decimal | None = None  # PR I (활성)
    max_total_exposure: Decimal | None = None  # PR I (활성)
    max_leverage: Decimal | None = None  # PR I (활성)
    max_drawdown_halt: float | None = None  # 후속 PR


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
        market_close: Decimal | None = None,
    ) -> RiskCheckResult:
        """주문 통과 여부.

        ``market_close`` 가 None 이면 max_total_exposure / max_leverage 등 가격 기반
        체크는 스킵. Engine 은 ``current_snapshots[symbol].close`` 를 넘긴다.
        """
        del instrument

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

        # PR I: position-size / exposure / leverage 검사.
        # 사이즈 적용 후 새로운 포지션 추정.
        delta = sized_quantity if intent.side == "buy" else -sized_quantity
        current_pos = ledger.positions.get(intent.symbol)
        current_size = current_pos.size if current_pos is not None else Decimal("0")
        new_size = current_size + delta

        if self.limits.max_position_size is not None:
            if abs(new_size) > self.limits.max_position_size:
                return RiskCheckResult.reject(
                    f"max_position_size exceeded for {intent.symbol!r}: "
                    f"|{new_size}| > {self.limits.max_position_size}"
                )

        if (
            market_close is not None
            and market_close > 0
            and (
                self.limits.max_total_exposure is not None
                or self.limits.max_leverage is not None
            )
        ):
            # 새 심볼의 notional 추정
            new_symbol_notional = abs(new_size) * market_close
            # 다른 심볼들의 notional 합 (mark = avg_price 근사 — 정확한 mark 는
            # 호출자가 별도 마크 사전을 넘기는 후속 PR 에서 도입)
            other_notional = Decimal("0")
            for sym, pos in ledger.positions.items():
                if sym == intent.symbol:
                    continue
                if pos.size == 0:
                    continue
                # 보수적으로 avg_price 사용 — 진입가 기준 누적
                other_notional += abs(pos.size) * pos.avg_price
            total_notional = other_notional + new_symbol_notional

            if (
                self.limits.max_total_exposure is not None
                and total_notional > self.limits.max_total_exposure
            ):
                return RiskCheckResult.reject(
                    f"max_total_exposure exceeded: {total_notional} > "
                    f"{self.limits.max_total_exposure}"
                )

            if self.limits.max_leverage is not None:
                equity = ledger.equity
                if equity > 0:
                    leverage = total_notional / equity
                    if leverage > self.limits.max_leverage:
                        return RiskCheckResult.reject(
                            f"max_leverage exceeded for {intent.symbol!r}: "
                            f"notional/equity={leverage} > {self.limits.max_leverage}"
                        )

        return RiskCheckResult.accept()
