"""OrderIntent + SizeSpec 6종 (spec §3.7).

SizeSpec은 6종 모두 정의하지만 Phase 1 Sizer는 TargetUnits / TargetNotional /
ClosePosition 3종만 처리한다. 나머지(TargetWeight / FullPosition / ScaleIn)는
Phase 2에서 활성화 — Sizer.resolve()가 NotImplementedError 발생.

OrderIntent.type / tif도 모든 값을 정의하지만 Phase 1 OrderBook/ExecutionModel은
type='market' + tif='GTC' + expires_at=None만 정상 처리한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

# ---------- SizeSpec 변종 ---------------------------------------------------


@dataclass(frozen=True)
class TargetWeight:
    """포트폴리오 가중치 목표 (예: 0.5 = equity의 50%). Phase 2."""

    weight: Decimal


@dataclass(frozen=True)
class TargetNotional:
    """명목 금액 목표 (quote currency 단위). Phase 1."""

    notional: Decimal


@dataclass(frozen=True)
class TargetUnits:
    """단위 수 직접 지정 (size_unit 기준). Phase 1."""

    units: Decimal


@dataclass(frozen=True)
class FullPosition:
    """가용 마진/equity로 가능한 최대 포지션. Phase 2."""


@dataclass(frozen=True)
class ClosePosition:
    """현재 포지션 전량 청산. Phase 1."""


@dataclass(frozen=True)
class ScaleIn:
    """기존 포지션에 비례 추가. Phase 2."""

    by: Decimal


# ---------- Futures sizing (PR I) -------------------------------------------


@dataclass(frozen=True)
class TargetMarginPct:
    """``equity × margin_pct × leverage`` notional 을 mark price 로 단위 환산.

    Phase 2.5 PR I — crypto perp 표준. 예: equity=100k, margin_pct=0.1 (initial
    margin 10% 사용), leverage=5 → notional 50k → 가격 100 → 500 units.
    """

    margin_pct: Decimal
    leverage: Decimal


@dataclass(frozen=True)
class TargetNotionalPct:
    """``equity × notional_pct`` 직접 notional. leverage 별도 표현 안 함.

    예: equity=100k, notional_pct=0.5 → notional 50k. RiskManager 가
    max_total_exposure 로 별도 검증.
    """

    notional_pct: Decimal


@dataclass(frozen=True)
class FullEquityNotional:
    """``equity × leverage`` notional. 가용 마진 전부를 사용 — risk 강함.

    RiskLimits.max_leverage 가 이 변종을 가장 적극적으로 차단.
    """

    leverage: Decimal


# ---------- Bracket / OCO (PR K) --------------------------------------------


@dataclass(frozen=True)
class BracketSpec:
    """Entry intent 에 부착하는 TP/SL/time_stop 후속 child order 명세 (PR K).

    Engine 이 entry fill 직후 다음 child 를 자동 생성:
    - ``take_profit_price``: reduce-only limit (long entry → sell limit, short entry → buy limit).
    - ``stop_loss_price``: reduce-only stop (long entry → sell stop, short entry → buy stop).
    - ``time_stop_bars``: PR N 에서 활성. PR K 는 필드만 보존.

    Children 은 같은 ``oco_group_id`` 를 갖고, 한쪽이 체결되면 sibling 자동 cancel
    (PR L). ``parent_order_id`` 는 entry 주문 id.
    """

    take_profit_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    time_stop_bars: int | None = None

    def has_any(self) -> bool:
        return (
            self.take_profit_price is not None
            or self.stop_loss_price is not None
            or self.time_stop_bars is not None
        )


# ---------- Multi-leg bracket (Phase 3) -------------------------------------


@dataclass(frozen=True)
class TakeProfitLeg:
    """One TP leg in a :class:`MultiBracketSpec` — partial reduce-only limit.

    ``size_fraction`` is the share of the parent entry fill that this leg
    closes (e.g. ``Decimal("0.3333")`` for a 1/3 split). ``label`` is an
    opaque tag preserved on the child order's ``intent.reason`` for downstream
    filtering / replay (e.g. ``"tp1"``).
    """

    price: Decimal
    size_fraction: Decimal
    label: str = ""

    def __post_init__(self) -> None:
        if self.size_fraction <= Decimal(0) or self.size_fraction > Decimal(1):
            raise ValueError(
                f"size_fraction must be in (0, 1], got {self.size_fraction}"
            )
        if self.price <= Decimal(0):
            raise ValueError(f"price must be > 0, got {self.price}")


@dataclass(frozen=True)
class MultiBracketSpec:
    """Multi-leg bracket — N partial TP legs + one protective SL stop.

    Phase 3 alternative to :class:`BracketSpec` when the strategy wants to
    scale out across multiple price targets. Engine spawns ``len(take_profits)``
    reduce-only limits + (optionally) one reduce-only SL stop, all sharing a
    single ``bracket_group_id``. Each TP fill triggers an SL resize event
    (``ORDER_RESIZED``) that shrinks the SL by the leg's size fraction. SL
    fill cancels every remaining TP. Last TP fill (when fractions sum to 1)
    cancels the SL.

    Notes:

    - At least one TP leg is required; if you only need one TP / SL pair use
      :class:`BracketSpec`.
    - ``size_fraction`` sum must be in ``(0, 1]``. Sums below 1 leave a
      "tail" of position that stays exposed under the SL — the strategy is
      responsible for closing it (e.g. via opposite signal or time stop).
    - ``time_stop_bars`` is intentionally omitted: the engine has never
      auto-processed it on ``BracketSpec`` either, and bundling it here would
      reintroduce the dual source of truth (cf. SATS strategy doc Section 6).
      Strategies should run their own ``ctx.bars_held()`` + ``ClosePosition()``.
    - Tuple order ("TP1 first, TP3 last") encodes proximity to the entry —
      the engine treats ``take_profits[0]`` as the closest TP regardless of
      long/short side. Side-aware monotonic-distance validation runs at spawn
      time when the engine knows the entry's actual side.
    """

    take_profits: tuple[TakeProfitLeg, ...]
    stop_loss_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.take_profits:
            raise ValueError(
                "MultiBracketSpec requires >= 1 TP legs; use BracketSpec for "
                "SL-only or single-TP setups"
            )
        total = sum(
            (leg.size_fraction for leg in self.take_profits), Decimal(0)
        )
        if total <= Decimal(0) or total > Decimal(1):
            raise ValueError(
                f"sum of TP size_fractions must be in (0, 1], got {total}"
            )
        # Distinct prices — Pine doesn't enforce this strictly but two TPs at
        # the same price would mean one of them never adds informational value
        # to the strategy and complicates same-bar tie-breaking. Reject early.
        prices = [leg.price for leg in self.take_profits]
        if len(set(prices)) != len(prices):
            raise ValueError(
                f"TP legs must have distinct prices, got {prices}"
            )
        if self.stop_loss_price is not None and self.stop_loss_price <= Decimal(0):
            raise ValueError(
                f"stop_loss_price must be > 0 if set, got {self.stop_loss_price}"
            )

    @property
    def total_fraction(self) -> Decimal:
        return sum(
            (leg.size_fraction for leg in self.take_profits), Decimal(0)
        )

    def has_any(self) -> bool:
        # MultiBracketSpec always has at least one TP leg by invariant.
        return True


BracketLike = BracketSpec | MultiBracketSpec
"""Type alias for the union accepted by ``OrderIntent.bracket``.

Engine branches on ``isinstance(intent.bracket, MultiBracketSpec)`` to pick
the multi-leg spawn path; ``BracketSpec`` keeps the original single-TP / SL
behavior for backwards compatibility (PR K)."""


SizeSpec = (
    TargetWeight
    | TargetNotional
    | TargetUnits
    | FullPosition
    | ClosePosition
    | ScaleIn
    | TargetMarginPct
    | TargetNotionalPct
    | FullEquityNotional
)
"""Sizer.resolve()의 입력. Phase 1 지원: TargetUnits, TargetNotional, ClosePosition.
PR I 추가: TargetMarginPct, TargetNotionalPct, FullEquityNotional (futures sizing)."""


# ---------- OrderIntent ------------------------------------------------------


@dataclass(frozen=True)
class OrderIntent:
    """전략이 발행하는 주문 의도 (spec §3.7).

    Phase 1 정상 처리 범위:
    - type: 'market'만
    - tif: 'GTC'만, expires_at: None만
    - size_spec: TargetUnits / TargetNotional / ClosePosition만 (Sizer가 강제)

    그 외 입력은 OrderBook.add 또는 ExecutionModel/Sizer 단계에서
    NotImplementedError("Phase 2") raise.
    """

    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop", "stop_limit"]
    size_spec: SizeSpec
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    reason: str = ""
    tif: Literal["GTC", "IOC", "FOK", "DAY"] = "GTC"
    client_order_id: str | None = None
    expires_at: datetime | None = None
    # PR J — reduce-only flag. True 면 새 반대 방향 포지션을 절대 열지 않는다.
    # flat 또는 같은 방향 (extend) 시도는 Sizer 가 reject. ClosePosition 은 본질적으로
    # reduce-only — 별도 표기 없이도 동일 의미로 처리.
    reduce_only: bool = False
    # PR K — entry intent 가 채워지면 Engine 이 reduce-only TP/SL child 자동 생성.
    # Phase 3: ``MultiBracketSpec`` 도 허용 — engine 이 isinstance 분기로 multi-leg
    # spawn 경로를 탄다 (한 entry 에 N TP legs + 1 protective SL).
    bracket: BracketSpec | MultiBracketSpec | None = None


# ---------- OrderAction -----------------------------------------------------


@dataclass(frozen=True)
class OrderAction:
    """전략의 OrderBook 변경 요청 (spec §4.2, PR D 확장).

    Engine 이 strategy.on_bar 에서 받은 OrderIntent 들을 ``OrderAction(type='new',
    intent=...)`` 로 감싸고, 추가로 strategy.on_pending_orders 가 반환한 cancel/modify
    액션과 합쳐 처리한다.

    PR D 활성:
    - ``type='new'``: ``intent`` 필수.
    - ``type='cancel'``: ``order_id`` 필수. 활성 주문이면 cancelled 상태로 전이.
    - ``type='modify'``: ``order_id`` + ``modify_limit_price`` 또는 ``modify_stop_price``
      중 하나 이상 필요. limit/stop/stop_limit 만 modify 가능 (market 은 ValueError).
    """

    type: Literal["new", "cancel", "modify"]
    intent: OrderIntent | None = None  # type='new'에서 필수
    order_id: str | None = None  # type='cancel'/'modify'에서 필수
    modify_limit_price: Decimal | None = None  # type='modify' 에서 사용
    modify_stop_price: Decimal | None = None  # type='modify' 에서 사용
