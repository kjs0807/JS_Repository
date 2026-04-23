"""리스크 관리자. Broker 내부에서 주문을 검증하는 필터."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional
from src.core.config import RiskConfig
from src.execution.broker import Order, Portfolio

logger = logging.getLogger(__name__)

@dataclass
class RiskDecision:
    action: str
    reason: str = ""
    adjusted_qty: Optional[float] = None

class RiskManager:
    def __init__(self, config: RiskConfig, initial_capital: float = 50000.0) -> None:
        self.config = config
        self._initial_capital = initial_capital
        self._peak_equity = initial_capital
        self._current_equity = initial_capital
        self._daily_pnl = 0.0

    def check_order(self, order: Order, portfolio: Portfolio) -> RiskDecision:
        is_manual = order.source == "MANUAL"
        # MDD 한도: 수동 주문도 거부
        if self.drawdown_pct >= self.config.max_drawdown_pct:
            reason = f"MDD 한도 초과: {self.drawdown_pct:.1%} >= {self.config.max_drawdown_pct:.1%}"
            return RiskDecision("REJECT", reason)
        # 일일 손실 한도: 수동 주문도 거부
        daily_limit = portfolio.initial_capital * self.config.daily_loss_limit_pct
        if portfolio.daily_pnl <= -daily_limit:
            reason = f"일일 손실 한도 초과: {portfolio.daily_pnl:.0f} <= -{daily_limit:.0f}"
            return RiskDecision("REJECT", reason)
        # 동시 포지션 한도: 수동 주문은 경고만
        if len(portfolio.positions) >= self.config.max_concurrent:
            if is_manual:
                logger.warning("수동 주문: 동시 포지션 한도 초과 (경고만)")
            else:
                reason = f"동시 포지션 한도 초과: {len(portfolio.positions)}/{self.config.max_concurrent}"
                return RiskDecision("REJECT", reason)
        return RiskDecision("ALLOW")

    def update_equity(self, equity: float) -> None:
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    def record_trade(self, pnl: float, is_win: bool) -> None:
        self._daily_pnl += pnl
        self._current_equity += pnl
        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return max(0.0, (self._peak_equity - self._current_equity) / self._peak_equity)

    @property
    def current_equity(self) -> float:
        return self._current_equity

__all__ = ["RiskManager", "RiskDecision"]
