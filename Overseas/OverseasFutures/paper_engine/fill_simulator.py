"""체결 시뮬레이터 — 시장가/지정가 체결 및 슬리피지 적용."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from config.products import FuturesProduct

if TYPE_CHECKING:
    from paper_engine.order_manager import Fill, Order


class FillSimulator:
    """체결 시뮬레이션.

    시장가 주문은 즉시 체결하고, 지정가 주문은 현재가와 비교하여
    체결 가능 여부를 판단한다. 슬리피지는 tick 단위로 적용된다.

    Attributes:
        slippage_ticks: 체결 시 적용할 슬리피지 (틱 수, 0.0 = 슬리피지 없음)
    """

    def __init__(self, slippage_ticks: float = 0.0) -> None:
        self.slippage_ticks = slippage_ticks

    # ── 공개 메서드 ───────────────────────────────────────────────

    def fill_market(
        self,
        order: "Order",
        current_price: float,
        product: FuturesProduct,
    ) -> "Fill":
        """시장가 주문 즉시 체결.

        Args:
            order: 체결할 시장가 주문.
            current_price: 현재 시장가.
            product: FuturesProduct 스펙 (틱 사이즈 참조).

        Returns:
            Fill 객체.
        """
        from paper_engine.order_manager import Fill

        fill_price = self.apply_slippage(current_price, order.side, product)
        slippage_amount = abs(fill_price - current_price)

        return Fill(
            order=order,
            fill_price=fill_price,
            fill_qty=order.qty,
            timestamp=datetime.now(),
            slippage=slippage_amount,
        )

    def check_limit(
        self,
        order: "Order",
        current_price: float,
        product: FuturesProduct,
    ) -> Optional["Fill"]:
        """지정가 주문 체결 가능 여부 확인 및 체결.

        BUY 지정가: current_price <= order.price 이면 체결
        SELL 지정가: current_price >= order.price 이면 체결

        Args:
            order: 체결 여부를 확인할 지정가 주문.
            current_price: 현재 시장가.
            product: FuturesProduct 스펙.

        Returns:
            체결 가능하면 Fill, 아니면 None.
        """
        from paper_engine.order_manager import Fill

        if order.price is None:
            return None

        triggered = (
            (order.side == "BUY" and current_price <= order.price)
            or (order.side == "SELL" and current_price >= order.price)
        )
        if not triggered:
            return None

        # 지정가 체결은 지정가로 체결 (슬리피지 미적용)
        fill_price = order.price

        return Fill(
            order=order,
            fill_price=fill_price,
            fill_qty=order.qty,
            timestamp=datetime.now(),
            slippage=0.0,
        )

    def apply_slippage(
        self,
        price: float,
        side: str,
        product: FuturesProduct,
    ) -> float:
        """슬리피지 적용 체결가 계산.

        BUY는 가격이 오르고, SELL은 가격이 내려간다.

        Args:
            price: 기준 가격.
            side: "BUY" 또는 "SELL".
            product: FuturesProduct 스펙 (tick_size 참조).

        Returns:
            슬리피지 적용 후 체결가.
        """
        if self.slippage_ticks == 0.0:
            return price

        slippage_amount = self.slippage_ticks * product.tick_size
        if side == "BUY":
            return price + slippage_amount
        else:
            return price - slippage_amount
