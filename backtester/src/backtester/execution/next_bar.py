"""NextBarOpenExecution — Phase 1 유일한 ExecutionModel (spec §3.10).

전략의 의사결정(봉 마감 시점)에 발행된 주문을 **다음 봉의 open 가격에 즉시 전량 체결**.
- order.intent.type == 'market'만 처리. 그 외 → NotImplementedError("Phase 2").
- slippage = 0 (Phase 1). slippage_bps_/atr 모델은 Phase 2.
- 부분 체결 없음 — order.remaining 전량을 한 번에 fill.

Fill.timestamp는 snapshot.timestamp(= 봉 시작 시각 = open 시각)으로 기록한다.
EventLog Event.ts는 ClockEvent.timestamp(봉 마감 시각)이라 별도 — Engine이 처리.
"""

from __future__ import annotations

from backtester.core.orderbook import Order
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill
from backtester.instruments.base import Instrument


class NextBarOpenExecution:
    """다음 봉 open가에 시장가 주문을 전량 체결."""

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
                f"(got {intent.type!r}); limit/stop/stop_limit are Phase 2"
            )
        size = order.remaining
        price = snapshot.open
        notional = size * price
        fee = instrument.fee_model.compute_fee(notional)
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
