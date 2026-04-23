"""BacktestBroker — 시뮬레이션 체결 브로커.
Pending order → 다음 봉 open 체결 (Lookahead Bias 방지).
기존 포지션 스톱/TP: 봉 high/low로 intra-bar 체크.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from src.core.types import Bar
from src.core.config import BacktestConfig, RiskConfig
from src.execution.broker import Position, Portfolio, Fill, Order
from src.execution.position_tracker import PositionTracker
from src.execution.order_manager import OrderManager
from src.execution.risk import RiskManager

logger = logging.getLogger(__name__)

@dataclass
class TradeRecord:
    symbol: str
    strategy_name: str
    side: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    fee: float
    exit_reason: str
    source: str


class BacktestBroker:
    def __init__(self, config: BacktestConfig, risk_config: Optional[RiskConfig] = None) -> None:
        self._config = config
        self._positions = PositionTracker()
        self._orders = OrderManager()
        self._risk = RiskManager(risk_config or RiskConfig(), config.initial_capital)
        self._equity = config.initial_capital
        self._realized_pnl = 0.0
        self._equity_curve: List[float] = [config.initial_capital]
        self._trades: List[TradeRecord] = []
        self._last_bar: Dict[str, Bar] = {}
        self._close_requests: List[Tuple[str, str, str]] = []

    # ── Strategy orders ──────────────────────────────────────────────────────

    def buy(self, symbol: str, qty: float, stop_loss: float,
            take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._orders.create(symbol, "BUY", qty, "MARKET", stop_loss, take_profit,
                                   "STRATEGY", "STRATEGY", reason)

    def sell(self, symbol: str, qty: float, stop_loss: float,
             take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._orders.create(symbol, "SELL", qty, "MARKET", stop_loss, take_profit,
                                   "STRATEGY", "STRATEGY", reason)

    def close(self, symbol: str, reason: str = "") -> str:
        self._close_requests.append((symbol, reason, "STRATEGY"))
        return f"close_{symbol}"

    def update_stop(self, symbol: str, new_stop: float) -> None:
        self._positions.update_stop(symbol, new_stop)

    # ── Manual orders ────────────────────────────────────────────────────────

    def manual_buy(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                   take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._orders.create(symbol, "BUY", qty, "MARKET", stop_loss or 0.0,
                                   take_profit, "MANUAL", "MANUAL", reason)

    def manual_sell(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._orders.create(symbol, "SELL", qty, "MARKET", stop_loss or 0.0,
                                   take_profit, "MANUAL", "MANUAL", reason)

    def manual_close(self, symbol: str, reason: str = "") -> str:
        self._close_requests.append((symbol, reason, "MANUAL"))
        return f"manual_close_{symbol}"

    def manual_close_all(self, reason: str = "") -> List[str]:
        return [self.manual_close(p.symbol, reason) for p in self._positions.get_all()]

    def manual_update_stop(self, symbol: str, new_stop: float) -> None:
        self._positions.update_stop(symbol, new_stop)

    def manual_update_tp(self, symbol: str, new_tp: float) -> None:
        self._positions.update_tp(symbol, new_tp)

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_positions(self) -> List[Position]:
        return self._positions.get_all()

    def get_portfolio(self) -> Portfolio:
        return Portfolio(
            initial_capital=self._config.initial_capital,
            equity=self._equity,
            available_margin=self._equity * 0.8,
            used_margin=self._equity * 0.2 if self._positions.count > 0 else 0.0,
            realized_pnl=self._realized_pnl,
            daily_pnl=self._risk.daily_pnl,
            positions=self._positions.get_all(),
        )

    def calc_qty(self, symbol: str, risk_pct: float, stop_distance: float) -> float:
        if stop_distance <= 0:
            return 0.0
        return (self._equity * risk_pct) / stop_distance

    # ── Bar processing ────────────────────────────────────────────────────────

    def process_bar(self, bar: Bar) -> None:
        self._last_bar[bar.symbol] = bar

        # 1. Process close requests for this symbol
        remaining: List[Tuple[str, str, str]] = []
        for sym, reason, source in self._close_requests:
            if sym == bar.symbol:
                self._execute_close(sym, bar.open, bar.timestamp, reason, source)
            else:
                remaining.append((sym, reason, source))
        self._close_requests = remaining

        # 2. Fill pending orders for this symbol at bar open
        for order in list(self._orders.get_pending()):
            if order.symbol != bar.symbol:
                continue
            decision = self._risk.check_order(order, self.get_portfolio())
            if decision.action == "REJECT":
                logger.warning("주문 거부 [%s]: %s", order.order_id, decision.reason)
                self._orders.cancel(order.order_id)
                continue
            entry_price = bar.open
            slippage = self._config.slippage_pct * entry_price
            entry_price = entry_price + slippage if order.side == "BUY" else entry_price - slippage
            fee = order.qty * entry_price * self._config.taker_fee_pct
            self._equity -= fee
            fill = self._orders.fill(order.order_id, entry_price, fee, bar.timestamp, "ENTRY")
            if fill is None:
                continue
            side = "LONG" if order.side == "BUY" else "SHORT"
            self._positions.open(
                bar.symbol, side, order.qty, entry_price,
                bar.timestamp, order.stop_loss, order.take_profit,
                order.strategy_name,
            )

        # 3. Check intra-bar stop/TP for existing position
        pos = self._positions.get(bar.symbol)
        if pos is None:
            self._equity_curve.append(
                self._equity + sum(p.unrealized_pnl for p in self._positions.get_all())
            )
            return

        exit_price, exit_reason = self._check_exit(
            pos.side, pos.stop_loss, pos.take_profit,
            bar.open, bar.high, bar.low,
        )
        if exit_reason:
            self._execute_exit(pos, exit_price, bar.timestamp, exit_reason)
        elif self._positions.has_position(bar.symbol):
            self._positions.update_unrealized(bar.symbol, bar.close)

        self._equity_curve.append(
            self._equity + sum(p.unrealized_pnl for p in self._positions.get_all())
        )

    def close_all(self, reason: str = "END") -> None:
        for pos in list(self._positions.get_all()):
            last_bar = self._last_bar.get(pos.symbol)
            if last_bar:
                self._execute_exit(pos, last_bar.close, last_bar.timestamp, reason)
            else:
                self._positions.close(pos.symbol)

    def get_trades(self) -> List[TradeRecord]:
        return list(self._trades)

    def get_equity_curve(self) -> List[float]:
        return list(self._equity_curve)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _execute_close(self, symbol: str, price: float, timestamp: int,
                       reason: str, source: str) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            return
        fee = pos.qty * price * self._config.taker_fee_pct
        pnl = self._calc_pnl(pos.side, pos.entry_price, price, pos.qty) - fee
        self._equity += pnl
        self._realized_pnl += pnl
        self._risk.record_trade(pnl, pnl > 0)
        self._trades.append(TradeRecord(
            symbol=symbol, strategy_name=pos.strategy_name, side=pos.side,
            entry_time=pos.entry_time, exit_time=timestamp,
            entry_price=pos.entry_price, exit_price=price,
            qty=pos.qty, pnl=pnl, fee=fee, exit_reason=reason, source=source,
        ))
        self._positions.close(symbol)

    def _execute_exit(self, pos: Position, exit_price: float,
                      timestamp: int, exit_reason: str) -> None:
        fee = pos.qty * exit_price * self._config.taker_fee_pct
        pnl = self._calc_pnl(pos.side, pos.entry_price, exit_price, pos.qty) - fee
        self._equity += pnl
        self._realized_pnl += pnl
        self._risk.record_trade(pnl, pnl > 0)
        self._trades.append(TradeRecord(
            symbol=pos.symbol, strategy_name=pos.strategy_name, side=pos.side,
            entry_time=pos.entry_time, exit_time=timestamp,
            entry_price=pos.entry_price, exit_price=exit_price,
            qty=pos.qty, pnl=pnl, fee=fee, exit_reason=exit_reason, source="STRATEGY",
        ))
        self._positions.close(pos.symbol)

    @staticmethod
    def _check_exit(
        side: str,
        stop: Optional[float],
        tp: Optional[float],
        cur_open: float,
        cur_high: float,
        cur_low: float,
    ) -> Tuple[Optional[float], Optional[str]]:
        if side == "LONG":
            if stop is not None:
                if cur_open <= stop:
                    return cur_open, "STOP"
                if cur_low <= stop:
                    return stop, "STOP"
            if tp is not None:
                if cur_open >= tp:
                    return cur_open, "TP"
                if cur_high >= tp:
                    return tp, "TP"
        else:  # SHORT
            if stop is not None:
                if cur_open >= stop:
                    return cur_open, "STOP"
                if cur_high >= stop:
                    return stop, "STOP"
            if tp is not None:
                if cur_open <= tp:
                    return cur_open, "TP"
                if cur_low <= tp:
                    return tp, "TP"
        return None, None

    @staticmethod
    def _calc_pnl(side: str, entry: float, exit_: float, qty: float) -> float:
        return (exit_ - entry) * qty if side == "LONG" else (entry - exit_) * qty


__all__ = ["BacktestBroker", "TradeRecord"]
