"""NextBarOpenExecution — 다음 봉 OHLC 기반 체결 (spec §3.10).

Phase 2 PR 15b 까지 누적된 동작:
- ``order.intent.type == "market"`` → 다음 봉 open 가격에 즉시 전량 체결.
  bps slippage 적용 (PR 15a). market = taker.
- ``"limit"`` (PESSIMISTIC, spec §3.10):
  - buy: ``open<=L`` → fill at open (taker); ``low<=L`` → fill at L (maker); else no fill.
  - sell: ``open>=L`` → fill at open (taker); ``high>=L`` → fill at L (maker); else no fill.
- ``"stop"`` (PESSIMISTIC, market on trigger):
  - buy: ``open>=S`` → fill at open (taker); ``high>=S`` → fill at S (taker); else no fill.
  - sell: ``open<=S`` → fill at open (taker); ``low<=S`` → fill at S (taker); else no fill.
- ``"stop_limit"`` (PR 15b minimum — 단일 봉 stop+limit 동시 처리):
  같은 봉 안에서 stop trigger + limit 체결이 둘 다 가능한 경우만 fill. trigger 만 되고
  limit 미도달이면 no fill. 다음 봉에서 stop 이 다시 평가됨 — **trigger state 미보존**
  은 PR 15b 한계로 문서화. 후속 PR 에서 ``Order.triggered`` 상태 도입 예정.

부분 체결 없음 — order.remaining 전량을 한 번에 fill. slippage_bps 는 market 에만 적용
(limit/stop/stop_limit 은 OHLC 기반 정확한 가격에 체결).

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
    """다음 봉 OHLC 기반 체결. market 은 open + slippage, limit/stop/stop_limit 은 OHLC."""

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
        if intent.type == "market":
            return self._fill_market(order, snapshot, instrument)
        if intent.type == "limit":
            return self._fill_limit(order, snapshot, instrument)
        if intent.type == "stop":
            return self._fill_stop(order, snapshot, instrument)
        if intent.type == "stop_limit":
            return self._fill_stop_limit(order, snapshot, instrument)
        raise NotImplementedError(  # pragma: no cover — OrderBook.add 가 차단
            f"NextBarOpenExecution does not support order type {intent.type!r}"
        )

    # ---------- 변종별 분기 -------------------------------------------------

    def _fill_market(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill:
        intent = order.intent
        base_price = snapshot.open
        price = apply_bps_slippage(base_price, intent.side, self.slippage_bps)
        return self._make_fill(order, snapshot, instrument, price=price, is_maker=False)

    def _fill_limit(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        assert intent.limit_price is not None  # OrderBook.add 가 강제
        L = intent.limit_price
        o, h, low_ = snapshot.open, snapshot.high, snapshot.low
        if intent.side == "buy":
            if o <= L:
                # 시가가 limit 이하 → market 처럼 open 에 즉시 체결 (taker)
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if low_ <= L:
                # 봉 안에서 limit 도달 → limit 가격 체결 (maker)
                return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
            return None
        # sell
        if o >= L:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if h >= L:
            return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
        return None

    def _fill_stop(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        assert intent.stop_price is not None
        S = intent.stop_price
        o, h, low_ = snapshot.open, snapshot.high, snapshot.low
        if intent.side == "buy":
            # buy stop: 가격이 S 이상으로 가면 trigger
            if o >= S:
                # 갭업 — open 에서 trigger, market 으로 체결 (taker, 더 비쌈 = 불리)
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if h >= S:
                # 봉 안에서 S 터치 → S 에서 market 체결 (taker)
                return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
            return None
        # sell stop
        if o <= S:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if low_ <= S:
            return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
        return None

    def _fill_stop_limit(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        """PR 15b minimum: 같은 봉 안에서 stop trigger + limit 체결이 가능한 경우만 fill.

        trigger 만 되고 limit 미도달이면 no fill (order 는 active 유지). 다음 봉에서 stop
        이 재평가되는 PR 15b 한계 — Order.triggered 상태 도입은 후속 PR.
        """
        intent = order.intent
        assert intent.limit_price is not None and intent.stop_price is not None
        L = intent.limit_price
        S = intent.stop_price
        o, h, low_ = snapshot.open, snapshot.high, snapshot.low

        if intent.side == "buy":
            # 1. stop trigger 검사 (buy stop: price >= S)
            if not (o >= S or h >= S):
                return None
            # 2. trigger 됐으니 limit BUY 처럼 체결 시도
            if o <= L:
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if low_ <= L:
                return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
            return None

        # sell stop_limit
        if not (o <= S or low_ <= S):
            return None
        if o >= L:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if h >= L:
            return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
        return None

    # ---------- 공통 Fill 생성 ----------------------------------------------

    def _make_fill(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
        *,
        price: Decimal,
        is_maker: bool,
    ) -> Fill:
        intent = order.intent
        size = order.remaining
        notional = size * price
        fee = instrument.fee_model.compute_fee(notional, is_maker=is_maker)
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
