"""BaseStrategy (spec §3.5).

전략은 `on_bar`만 구현하면 동작. 나머지 메서드는 기본 noop이며 필요 시 override.

명시적으로 NotImplementedError를 raise하는 패턴(@abstractmethod 아님) — Engine은 인스턴스
생성 시점에 실패하지 않고 첫 호출 시점에 실패한다 (spec §3.5 그대로).
"""

from __future__ import annotations

from datetime import datetime

from backtester.core.context import StrategyContext
from backtester.core.orderbook import Order
from backtester.core.orders import OrderAction, OrderIntent
from backtester.indicators.base import Indicator
from backtester.instruments.base import Instrument


class BaseStrategy:
    """전략 베이스 클래스.

    필수 override: `on_bar`.
    선택 override: `on_init`, `required_indicators`, `on_pending_orders`, `on_data_gap`.
    """

    def on_init(self, instruments: list[Instrument]) -> None:
        """백테스트 시작 시 1회 호출. 등록된 instrument 목록을 받아 내부 상태 초기화."""
        del instruments

    def required_indicators(self) -> list[Indicator]:
        """전략이 필요로 하는 지표 리스트. 빈 리스트면 워밍업 0."""
        return []

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: list[Order],
    ) -> list[OrderAction]:
        """봉 마감 시 활성 주문 목록을 받아 cancel/modify 액션 반환.

        Phase 1: 기본 빈 리스트 (취소/수정 미사용).
        """
        del ctx, pending
        return []

    def on_data_gap(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[OrderIntent]:
        """데이터 갭 발생 시 호출. 기본은 noop."""
        del symbol, start, end
        return []

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        """봉 마감 시 호출. 발행할 OrderIntent 리스트 반환.

        반드시 서브클래스에서 override. Phase 1 시그니처는 단일 timeframe 가정.
        """
        del ctx
        raise NotImplementedError("on_bar must be implemented by subclass")
