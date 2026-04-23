"""가상 계좌 — 통화별 잔고 및 증거금 관리."""

from __future__ import annotations

from typing import Any, Dict, Optional

from config.products import PRODUCTS, FuturesProduct
from paper_engine.position_tracker import PaperPosition, PositionTracker


# 기본 통화별 초기 잔고
_DEFAULT_BALANCES: Dict[str, float] = {
    "EUR": 100_000.0,
    "JPY": 15_000_000.0,
    "HKD": 1_000_000.0,
    "AUD": 150_000.0,
    "TWD": 5_000_000.0,
}


class InsufficientMarginError(Exception):
    """증거금 부족 오류."""
    pass


class VirtualAccount:
    """가상 계좌 관리.

    통화별 가용 현금과 사용 증거금을 추적하고, 포지션의 평가 자산을 계산한다.

    Attributes:
        initial_balances: 계좌 초기 통화별 잔고
        cash: 통화별 가용 현금 (증거금 제외 후 잔액)
        margin_used: 통화별 사용 중인 증거금
        positions: 보유 포지션 딕셔너리 (PositionTracker와 공유)
    """

    def __init__(
        self, initial_balances: Optional[Dict[str, float]] = None
    ) -> None:
        self.initial_balances: Dict[str, float] = (
            dict(initial_balances) if initial_balances else dict(_DEFAULT_BALANCES)
        )
        self.cash: Dict[str, float] = dict(self.initial_balances)
        self.margin_used: Dict[str, float] = {
            ccy: 0.0 for ccy in self.initial_balances
        }
        # PositionTracker와 연동하기 위해 외부에서 주입하거나 직접 사용한다.
        self.positions: Dict[str, PaperPosition] = {}

    # ── 증거금 관리 ───────────────────────────────────────────────

    def check_margin(self, symbol: str, qty: int, product: FuturesProduct) -> bool:
        """증거금 충족 여부 확인.

        Args:
            symbol: 상품 루트 심볼 (현재 미사용, 확장성 위해 유지)
            qty: 계약 수
            product: FuturesProduct 스펙

        Returns:
            가용 현금이 필요 증거금 이상이면 True.
        """
        required = product.margin * qty
        available = self.cash.get(product.currency, 0.0)
        return available >= required

    def reserve_margin(self, symbol: str, qty: int, product: FuturesProduct) -> None:
        """증거금 예약 — 가용 현금에서 차감하고 사용 증거금에 추가.

        Args:
            symbol: 상품 루트 심볼 (로깅용)
            qty: 계약 수
            product: FuturesProduct 스펙

        Raises:
            InsufficientMarginError: 가용 현금이 부족할 때.
        """
        if not self.check_margin(symbol, qty, product):
            required = product.margin * qty
            available = self.cash.get(product.currency, 0.0)
            raise InsufficientMarginError(
                f"Insufficient margin for {symbol} x{qty}: "
                f"required {required:.2f} {product.currency}, "
                f"available {available:.2f} {product.currency}"
            )

        required = product.margin * qty
        ccy = product.currency
        self.cash[ccy] = self.cash.get(ccy, 0.0) - required
        self.margin_used[ccy] = self.margin_used.get(ccy, 0.0) + required

    def release_margin(self, symbol: str, qty: int, product: FuturesProduct) -> None:
        """증거금 해제 — 사용 증거금에서 차감하고 가용 현금에 반환.

        Args:
            symbol: 상품 루트 심볼 (로깅용)
            qty: 청산 계약 수
            product: FuturesProduct 스펙
        """
        released = product.margin * qty
        ccy = product.currency
        self.margin_used[ccy] = max(0.0, self.margin_used.get(ccy, 0.0) - released)
        self.cash[ccy] = self.cash.get(ccy, 0.0) + released

    # ── 자산 평가 ─────────────────────────────────────────────────

    def update_equity(self, current_prices: Dict[str, float]) -> Dict[str, float]:
        """현재가 기준 통화별 총 자산(Equity) 계산.

        Equity = cash + margin_used + unrealized_pnl (모두 해당 통화 기준)

        Args:
            current_prices: {symbol: current_price} 딕셔너리

        Returns:
            통화별 총 자산 딕셔너리.
        """
        # 평가손익을 통화별로 집계
        unrealized_by_ccy: Dict[str, float] = {}
        for symbol, pos in self.positions.items():
            product = PRODUCTS.get(symbol)
            if product is None or symbol not in current_prices:
                # 현재가 없으면 기존 unrealized_pnl 사용
                unrealized_by_ccy[pos.currency] = (
                    unrealized_by_ccy.get(pos.currency, 0.0) + pos.unrealized_pnl
                )
                continue

            price = current_prices[symbol]
            direction = 1 if pos.side == "LONG" else -1
            upnl = (price - pos.avg_price) * pos.qty * product.point_value * direction
            unrealized_by_ccy[pos.currency] = (
                unrealized_by_ccy.get(pos.currency, 0.0) + upnl
            )

        equity: Dict[str, float] = {}
        all_currencies = set(self.cash) | set(self.margin_used) | set(unrealized_by_ccy)
        for ccy in all_currencies:
            equity[ccy] = (
                self.cash.get(ccy, 0.0)
                + self.margin_used.get(ccy, 0.0)
                + unrealized_by_ccy.get(ccy, 0.0)
            )
        return equity

    def apply_realized_pnl(self, currency: str, pnl: float) -> None:
        """실현손익을 가용 현금에 반영.

        Args:
            currency: 손익 통화
            pnl: 실현손익 (양수=이익, 음수=손실)
        """
        self.cash[currency] = self.cash.get(currency, 0.0) + pnl

    # ── 요약 / 직렬화 ─────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """계좌 현황 요약 딕셔너리 반환."""
        return {
            "cash": dict(self.cash),
            "margin_used": dict(self.margin_used),
            "initial_balances": dict(self.initial_balances),
            "open_positions": list(self.positions.keys()),
        }

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리 반환."""
        return {
            "initial_balances": dict(self.initial_balances),
            "cash": dict(self.cash),
            "margin_used": dict(self.margin_used),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VirtualAccount":
        """딕셔너리에서 VirtualAccount 복원."""
        account = cls(initial_balances=data.get("initial_balances"))
        account.cash = dict(data.get("cash", account.cash))
        account.margin_used = dict(data.get("margin_used", account.margin_used))
        return account
