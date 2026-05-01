"""NextBarOpenExecution — 다음 봉 OHLC 기반 체결 (spec §3.10).

Phase 2 PR 15c 까지 누적된 동작:
- ``order.intent.type == "market"`` → 다음 봉 open 가격에 즉시 전량 체결.
  bps slippage 적용 (PR 15a). market = taker.
- ``"limit"``/``"stop"``/``"stop_limit"`` (PR 15b): OHLC 기반 PESSIMISTIC 분기.
- ``BarPathModel`` 4종 분기 (PR 15c):
  - ``PESSIMISTIC`` (default): 트레이더에게 불리한 봉 path 가정. PR 15b 동작.
  - ``OPTIMISTIC``: 단일 주문 단일 봉 컨텍스트에서 PESSIMISTIC 와 동일 fill 결과
    (limit/stop 의 spec 규칙 자체가 path 에 무관하게 결정적). 차별화는 PR 16+ 의
    position-level TP/SL coexistence 도입 시 활성.
  - ``OHLC_ORDER``: open→high→low→close 명시 path. 단일 주문에서는 PESSIMISTIC 와
    동일 결과 (path 의 fill 가능 여부가 high/low 도달 여부로 결정되며 spec 규칙과
    일치). 차별화는 PR 16+.
  - ``OPEN_TO_CLOSE``: linear path open→close. **high/low 무시**. limit/stop trigger
    여부를 close 도달로만 판단 → PESSIMISTIC 와 명확히 다른 fill 결과 (PR 15c
    minimum 차별화 포인트).

slippage_bps 는 market 에만 적용 (PR 15b). limit/stop/stop_limit 은 OHLC 기반 정확
가격에 체결.

``Fill.timestamp`` 는 ``snapshot.timestamp`` (= 봉 시작 = open 시각). EventLog Event.ts 는
ClockEvent.timestamp (봉 마감) 이라 별도 — Engine 이 처리.

랜덤 path 정책 (예: monte-carlo intra-bar) 은 ``BarPathModel`` 에 정의되어 있지 않음 →
PR 15c 에서 도입하지 않는다. 향후 추가 시 ``random_seed`` 와의 결합 + 재현성 보장 필요.
"""

from __future__ import annotations

from decimal import Decimal

from backtester.core.orderbook import Order
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import BarPathModel, Fill
from backtester.execution.slippage_bps import apply_bps_slippage
from backtester.instruments.base import Instrument


class NextBarOpenExecution:
    """다음 봉 OHLC 기반 체결. ``bar_path_model`` 로 봉 내 path 가정 선택."""

    def __init__(
        self,
        *,
        slippage_bps: Decimal | float | int = 0,
        bar_path_model: BarPathModel = BarPathModel.PESSIMISTIC,
    ) -> None:
        bps = Decimal(str(slippage_bps))
        if bps < 0:
            raise ValueError(f"slippage_bps must be >= 0, got {bps}")
        if not isinstance(bar_path_model, BarPathModel):
            raise ValueError(
                f"bar_path_model must be a BarPathModel enum member, "
                f"got {type(bar_path_model).__name__}"
            )
        self.slippage_bps = bps
        self.bar_path_model = bar_path_model

    def try_fill(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        if intent.type == "market":
            return self._fill_market(order, snapshot, instrument)
        # PR 15c: BarPathModel 분기 — 단일 주문 컨텍스트에서는 OPEN_TO_CLOSE 만
        # PESSIMISTIC 와 차별화. OPTIMISTIC / OHLC_ORDER 는 PR 16+ TP/SL coexistence
        # 도입 시 차별화.
        if self.bar_path_model == BarPathModel.OPEN_TO_CLOSE:
            dispatch = self._dispatch_open_to_close
        else:
            dispatch = self._dispatch_pessimistic
        if intent.type == "limit":
            return dispatch(order, snapshot, instrument, kind="limit")
        if intent.type == "stop":
            return dispatch(order, snapshot, instrument, kind="stop")
        if intent.type == "stop_limit":
            return dispatch(order, snapshot, instrument, kind="stop_limit")
        raise NotImplementedError(  # pragma: no cover — OrderBook.add 가 차단
            f"NextBarOpenExecution does not support order type {intent.type!r}"
        )

    # ---------- market -----------------------------------------------------

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

    # ---------- PESSIMISTIC (= OPTIMISTIC = OHLC_ORDER, single-order) -------

    def _dispatch_pessimistic(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
        *,
        kind: str,
    ) -> Fill | None:
        if kind == "limit":
            return self._fill_limit_pess(order, snapshot, instrument)
        if kind == "stop":
            return self._fill_stop_pess(order, snapshot, instrument)
        return self._fill_stop_limit_pess(order, snapshot, instrument)

    def _fill_limit_pess(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        assert intent.limit_price is not None
        L = intent.limit_price
        o, h, low_ = snapshot.open, snapshot.high, snapshot.low
        if intent.side == "buy":
            if o <= L:
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if low_ <= L:
                return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
            return None
        if o >= L:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if h >= L:
            return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
        return None

    def _fill_stop_pess(
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
            if o >= S:
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if h >= S:
                return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
            return None
        if o <= S:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if low_ <= S:
            return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
        return None

    def _fill_stop_limit_pess(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        assert intent.limit_price is not None and intent.stop_price is not None
        L = intent.limit_price
        S = intent.stop_price
        o, h, low_ = snapshot.open, snapshot.high, snapshot.low

        if intent.side == "buy":
            if not (o >= S or h >= S):
                return None
            if o <= L:
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if low_ <= L:
                return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
            return None
        if not (o <= S or low_ <= S):
            return None
        if o >= L:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if h >= L:
            return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
        return None

    # ---------- OPEN_TO_CLOSE (high/low 무시, linear path) ------------------

    def _dispatch_open_to_close(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
        *,
        kind: str,
    ) -> Fill | None:
        if kind == "limit":
            return self._fill_limit_otc(order, snapshot, instrument)
        if kind == "stop":
            return self._fill_stop_otc(order, snapshot, instrument)
        return self._fill_stop_limit_otc(order, snapshot, instrument)

    def _fill_limit_otc(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        assert intent.limit_price is not None
        L = intent.limit_price
        o, c = snapshot.open, snapshot.close
        if intent.side == "buy":
            if o <= L:
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if c <= L:
                # path open(>L) → close(<=L) 가 L 을 통과 → limit fill at L
                return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
            return None
        if o >= L:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if c >= L:
            return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
        return None

    def _fill_stop_otc(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        intent = order.intent
        assert intent.stop_price is not None
        S = intent.stop_price
        o, c = snapshot.open, snapshot.close
        if intent.side == "buy":
            if o >= S:
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if c >= S:
                return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
            return None
        if o <= S:
            return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
        if c <= S:
            return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
        return None

    def _fill_stop_limit_otc(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None:
        """OPEN_TO_CLOSE stop_limit — trigger_at_open vs trigger_via_close 경로 분리.

        ``trigger_at_open`` 시: post-trigger 가격이 ``open`` 부터 시작 → limit 비교는
        ``open`` 기반.
        ``trigger_via_close`` 시: stop 이 path 위 어딘가에서 발동 (가격=``S``) →
        post-trigger 가격이 ``S`` 부터 시작. limit 비교는 ``S`` 기반 (``open`` 사용 금지 —
        open 시점에는 아직 발동 전이라 limit 체결 불가).
        """
        intent = order.intent
        assert intent.limit_price is not None and intent.stop_price is not None
        L = intent.limit_price
        S = intent.stop_price
        o, c = snapshot.open, snapshot.close

        if intent.side == "buy":
            trigger_at_open = o >= S
            if trigger_at_open:
                # post-trigger 가격 = open. limit 비교 기준 = open.
                if o <= L:
                    return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
                if c <= L:
                    return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
                return None
            # open 시점 미발동. close 도 S 미도달이면 path 어디에서도 미발동.
            if c < S:
                return None
            # trigger_via_close: post-trigger 가격은 ``S`` 부터 시작 (open 사용 금지).
            if S <= L:
                # 발동 즉시 limit 체결 가능 (S <= L) → market-on-trigger style, taker.
                return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
            # S > L (atypical): post-trigger path S→c 가 L 통과해야 fill.
            if c <= L:
                return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
            return None

        # sell stop_limit
        trigger_at_open = o <= S
        if trigger_at_open:
            if o >= L:
                return self._make_fill(order, snapshot, instrument, price=o, is_maker=False)
            if c >= L:
                return self._make_fill(order, snapshot, instrument, price=L, is_maker=True)
            return None
        if c > S:
            return None
        # trigger_via_close: post-trigger 가격 = ``S``.
        if S >= L:
            return self._make_fill(order, snapshot, instrument, price=S, is_maker=False)
        if c >= L:
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
