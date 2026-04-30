"""NextBarOpenExecution — 다음 봉 open 가격 체결 (spec §3.10).

전략의 의사결정(봉 마감 시점)에 발행된 주문을 **다음 봉의 open 가격에 즉시 전량 체결**.
- ``order.intent.type == 'market'`` 만 처리. 그 외 → ``NotImplementedError`` (limit/stop 은 PR 15b).
- 부분 체결 없음 — order.remaining 전량을 한 번에 fill.

Phase 2 PR 15a:
- ``slippage_bps`` 파라미터 추가. 기본 0 → Phase 1 동작 그대로 유지.
- market order 는 caller 가 ``is_maker=False`` (= taker) 로 fee 계산. limit maker 판단은
  PR 15b 에서 활성.

``Fill.timestamp`` 는 ``snapshot.timestamp`` (= 봉 시작 = open 시각). EventLog Event.ts 는
ClockEvent.timestamp (봉 마감) 이라 별도 — Engine 이 처리.
"""

from __future__ import annotations

from decimal import Decimal

from backtester.core.orderbook import Order
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill
from backtester.execution.slippage_bps import apply_bps_slippage
from backtester.instruments.base import Instrument


class NextBarOpenExecution:
    """다음 봉 open 가에 시장가 주문을 전량 체결. 옵션으로 bps 슬리피지 적용."""

    def __init__(self, *, slippage_bps: Decimal | float | int = 0) -> None:
        bps = Decimal(str(slippage_bps))
        if bps < 0:
            raise ValueError(f"slippage_bps must be >= 0, got {bps}")
        self.slippage_bps = bps

    def try_fill(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        if intent.type != "market":
            raise NotImplementedError(
                f"NextBarOpenExecution supports type='market' only "
                f"(got {intent.type!r}); limit/stop/stop_limit are PR 15b"
            )
        size = order.remaining
        base_price = snapshot.open
        price = apply_bps_slippage(base_price, intent.side, self.slippage_bps)
        notional = size * price
        # market order = taker. limit maker 는 PR 15b ExecutionModel 에서.
        fee = instrument.fee_model.compute_fee(notional, is_maker=False)
        return Fill(
            timestamp=snapshot.timestamp,
            symbol=intent.symbol,
            price=price,
            size=size,
            side=intent.side,
            fee=fee,
            fee_currency=instrument.quote_currency,
            order_id=order.id,
            intent_reason=intent.reason,
        )
