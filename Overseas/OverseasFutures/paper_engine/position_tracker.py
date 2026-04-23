"""포지션 추적기 — 가상 포지션의 평가손익 및 실현손익 관리."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.products import PRODUCTS, FuturesProduct


@dataclass
class PaperPosition:
    """가상 포지션.

    Attributes:
        symbol: 상품 루트 심볼 (예: "VG")
        side: 포지션 방향 ("LONG" / "SHORT")
        qty: 계약 수
        avg_price: 평균 진입가
        margin_used: 사용 증거금 (해당 통화)
        currency: 통화 (예: "EUR")
        unrealized_pnl: 평가손익 (해당 통화)
        realized_pnl: 실현손익 누계 (해당 통화)
    """

    symbol: str
    side: str
    qty: int
    avg_price: float
    margin_used: float
    currency: str
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리 반환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PaperPosition":
        """딕셔너리에서 PaperPosition 복원."""
        return cls(**data)


class PositionTracker:
    """가상 포지션 추적기.

    포지션 진입/청산, 평가손익 갱신, 거래 이력 관리를 담당한다.
    """

    def __init__(self) -> None:
        self.positions: Dict[str, PaperPosition] = {}
        self.trade_history: deque = deque(maxlen=1000)

    # ── 포지션 관리 ────────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        product: FuturesProduct,
    ) -> PaperPosition:
        """포지션 진입 또는 기존 포지션에 추가.

        Args:
            symbol: 상품 루트 심볼
            side: "LONG" 또는 "SHORT"
            qty: 계약 수 (양수)
            price: 진입 가격
            product: FuturesProduct 스펙

        Returns:
            생성되거나 업데이트된 PaperPosition.

        Raises:
            ValueError: qty가 0 이하이거나 side가 유효하지 않을 때.
        """
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"side must be 'LONG' or 'SHORT', got {side!r}")

        margin_required = product.margin * qty

        if symbol in self.positions:
            existing = self.positions[symbol]
            if existing.side != side:
                raise ValueError(
                    f"Cannot open {side} on {symbol}: existing position is {existing.side}. "
                    "Close the existing position first."
                )
            # 평균단가 재계산 (VWAP)
            total_qty = existing.qty + qty
            new_avg = (existing.avg_price * existing.qty + price * qty) / total_qty
            existing.qty = total_qty
            existing.avg_price = new_avg
            existing.margin_used += margin_required
            pos = existing
        else:
            pos = PaperPosition(
                symbol=symbol,
                side=side,
                qty=qty,
                avg_price=price,
                margin_used=margin_required,
                currency=product.currency,
            )
            self.positions[symbol] = pos

        self.trade_history.append({
            "action": "OPEN",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "timestamp": datetime.now().isoformat(),
        })
        return pos

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        qty: int,
        product: FuturesProduct,
    ) -> float:
        """포지션 전체 또는 일부 청산.

        PnL = (exit_price - entry_price) * qty * point_value * direction
        direction: LONG=+1, SHORT=-1

        Args:
            symbol: 상품 루트 심볼
            exit_price: 청산 가격
            qty: 청산 계약 수 (양수; 보유 계약 수 이하)
            product: FuturesProduct 스펙

        Returns:
            실현손익 (해당 통화).

        Raises:
            KeyError: 해당 심볼의 포지션이 없을 때.
            ValueError: qty가 보유 수량을 초과할 때.
        """
        if symbol not in self.positions:
            raise KeyError(f"No open position for {symbol}")

        pos = self.positions[symbol]

        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if qty > pos.qty:
            raise ValueError(
                f"Cannot close {qty} contracts of {symbol}: only {pos.qty} held"
            )

        direction = 1 if pos.side == "LONG" else -1
        pnl = (exit_price - pos.avg_price) * qty * product.point_value * direction

        # 증거금 비례 해제
        margin_released = product.margin * qty
        pos.margin_used = max(0.0, pos.margin_used - margin_released)
        pos.realized_pnl += pnl
        pos.qty -= qty

        self.trade_history.append({
            "action": "CLOSE",
            "symbol": symbol,
            "side": pos.side,
            "qty": qty,
            "entry_price": pos.avg_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "timestamp": datetime.now().isoformat(),
        })

        if pos.qty == 0:
            del self.positions[symbol]

        return pnl

    def update_unrealized_pnl(self, current_prices: Dict[str, float]) -> None:
        """보유 포지션의 평가손익을 현재가로 갱신.

        Args:
            current_prices: {symbol: current_price} 딕셔너리.
        """
        for symbol, pos in self.positions.items():
            price = current_prices.get(symbol)
            if price is None:
                continue
            product = PRODUCTS.get(symbol)
            if product is None:
                continue
            direction = 1 if pos.side == "LONG" else -1
            pos.unrealized_pnl = (
                (price - pos.avg_price) * pos.qty * product.point_value * direction
            )

    def get_position(self, symbol: str) -> Optional[PaperPosition]:
        """심볼의 현재 포지션 반환. 없으면 None."""
        return self.positions.get(symbol)

    # ── 직렬화 ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리 반환."""
        return {
            "positions": {s: p.to_dict() for s, p in self.positions.items()},
            "trade_history": list(self.trade_history),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PositionTracker":
        """딕셔너리에서 PositionTracker 복원."""
        tracker = cls()
        for symbol, pos_data in data.get("positions", {}).items():
            tracker.positions[symbol] = PaperPosition.from_dict(pos_data)
        tracker.trade_history = list(data.get("trade_history", []))
        return tracker
