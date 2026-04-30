"""ATR-proportional slippage execution (Phase 2 PR 15a, spec §3.10, §16).

설계 — PR 15a minimum testable interface:
- ``AtrSlippageExecution`` 는 ``ExecutionModel`` 프로토콜 구현.
- 외부에서 ``atr_provider: Callable[[datetime], Decimal]`` 을 주입받아 fill 시점의 ATR
  값을 조회. PR 15a 에서는 별도 IndicatorEngine 자동 wiring 을 하지 않는다 — 사용자가
  명시적으로 atr_provider 를 만들어 주입한다.
- fill price = next_bar.open ± ``atr_multiplier × atr_value``
  - buy → +(불리)
  - sell → -(불리)
- market order 만 처리 (limit/stop 은 PR 15b).

후속 작업 (PR 15+ / PR 16):
- IndicatorEngine 의 ATR 캐시를 자동 lookup 하는 helper 제공
- BarPathModel / random slippage 와의 결합
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from backtester.core.orderbook import Order
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill
from backtester.instruments.base import Instrument

AtrProvider = Callable[[datetime], Decimal]


class AtrSlippageExecution:
    """ATR-비례 슬리피지 + 다음 봉 open 체결.

    market order 만 처리. ``atr_multiplier`` 는 비율 (예: ``Decimal("0.5")`` = 0.5 ATR
    만큼 불리 슬리피지). ``atr_provider`` 는 fill 시점 (``snapshot.timestamp``) 의 ATR 값을
    반환하는 callable.
    """

    def __init__(
        self,
        *,
        atr_multiplier: Decimal,
        atr_provider: AtrProvider,
    ) -> None:
        if atr_multiplier < 0:
            raise ValueError(
                f"atr_multiplier must be >= 0, got {atr_multiplier}"
            )
        self.atr_multiplier = atr_multiplier
        self.atr_provider = atr_provider

    def try_fill(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        if intent.type != "market":
            raise NotImplementedError(
                f"AtrSlippageExecution supports type='market' only "
                f"(got {intent.type!r}); limit/stop/stop_limit are PR 15b"
            )
        atr_value = self.atr_provider(snapshot.timestamp)
        if atr_value < 0:
            raise ValueError(
                f"atr_provider returned negative atr_value={atr_value} at "
                f"{snapshot.timestamp}"
            )
        slip = self.atr_multiplier * atr_value
        if intent.side == "buy":
            price = snapshot.open + slip
        elif intent.side == "sell":
            price = snapshot.open - slip
        else:  # pragma: no cover — Sizer 단계에서 차단됨
            raise ValueError(f"Unexpected intent.side: {intent.side!r}")

        size = order.remaining
        notional = size * price
        # market order = taker (PR 15a). limit maker 는 PR 15b 에서.
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
