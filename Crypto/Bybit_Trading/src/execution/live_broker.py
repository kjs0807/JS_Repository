"""LiveBroker — Bybit API 실거래 브로커."""
from __future__ import annotations
import logging
from typing import Dict, List, Optional
from src.core.config import RiskConfig
from src.core.alert import AlertManager
from src.api.rest_client import BybitRestClient
from src.execution.broker import Broker, Position, Portfolio, Fill, Order
from src.execution.risk import RiskManager

logger = logging.getLogger(__name__)

class LiveBroker:
    def __init__(self, rest_client: BybitRestClient, alert_manager: Optional[AlertManager] = None,
                 risk_config: Optional[RiskConfig] = None, leverage: int = 3,
                 initial_capital: float = 50000.0) -> None:
        self._rest = rest_client
        self._alert = alert_manager
        self._risk = RiskManager(risk_config or RiskConfig(), initial_capital)
        self._leverage = leverage
        self._initial_capital = initial_capital
        self._positions: Dict[str, Position] = {}
        self._equity: float = initial_capital
        self._sync_wallet()

    def buy(self, symbol: str, qty: float, stop_loss: float,
            take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._execute_order(symbol, "Buy", qty, stop_loss, take_profit, "STRATEGY", reason)

    def sell(self, symbol: str, qty: float, stop_loss: float,
             take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._execute_order(symbol, "Sell", qty, stop_loss, take_profit, "STRATEGY", reason)

    def close(self, symbol: str, reason: str = "") -> str:
        pos = self._positions.get(symbol)
        if pos is None: return ""
        side = "Sell" if pos.side == "LONG" else "Buy"
        result = self._rest.place_order(symbol=symbol, side=side, qty=str(pos.qty), order_type="Market")
        order_id = result.get("orderId", "")
        if order_id: self._positions.pop(symbol, None)
        return order_id

    def update_stop(self, symbol: str, new_stop: float) -> None:
        pos = self._positions.get(symbol)
        if pos: pos.stop_loss = new_stop

    def manual_buy(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                   take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._execute_order(symbol, "Buy", qty, stop_loss or 0.0, take_profit, "MANUAL", reason)

    def manual_sell(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None, reason: str = "") -> str:
        return self._execute_order(symbol, "Sell", qty, stop_loss or 0.0, take_profit, "MANUAL", reason)

    def manual_close(self, symbol: str, reason: str = "") -> str:
        return self.close(symbol, reason=f"수동: {reason}")

    def manual_close_all(self, reason: str = "") -> List[str]:
        return [self.close(sym, reason=f"수동 전량: {reason}") for sym in list(self._positions.keys())]

    def manual_update_stop(self, symbol: str, new_stop: float) -> None:
        self.update_stop(symbol, new_stop)

    def manual_update_tp(self, symbol: str, new_tp: float) -> None:
        pos = self._positions.get(symbol)
        if pos: pos.take_profit = new_tp

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_portfolio(self) -> Portfolio:
        return Portfolio(initial_capital=self._initial_capital, equity=self._equity,
            available_margin=self._equity * 0.8,
            used_margin=self._equity * 0.2 if self._positions else 0.0,
            realized_pnl=0.0, daily_pnl=self._risk.daily_pnl,
            positions=list(self._positions.values()))

    def calc_qty(self, symbol: str, risk_pct: float, stop_distance: float) -> float:
        if stop_distance <= 0: return 0.0
        return (self._equity * risk_pct) / stop_distance

    def sync_positions(self) -> None:
        raw_positions = self._rest.get_positions()
        new_positions: Dict[str, Position] = {}
        for raw in raw_positions:
            size = float(raw.get("size", 0))
            if size <= 0: continue
            symbol = raw["symbol"]
            side = "LONG" if raw.get("side") == "Buy" else "SHORT"
            new_positions[symbol] = Position(symbol=symbol, side=side, qty=size,
                entry_price=float(raw.get("avgPrice", 0)), entry_time=0,
                stop_loss=0.0, take_profit=None,
                unrealized_pnl=float(raw.get("unrealisedPnl", 0)), strategy_name="SYNCED")
        self._positions = new_positions

    def _sync_wallet(self) -> None:
        try:
            balance = self._rest.get_wallet_balance()
            self._equity = balance.get("equity", self._initial_capital)
        except Exception:
            pass

    def _execute_order(self, symbol: str, side: str, qty: float, stop_loss: Optional[float],
                       take_profit: Optional[float], source: str, reason: str) -> str:
        order = Order(order_id="", symbol=symbol, side=side, qty=qty,
            order_type="MARKET", stop_loss=stop_loss, take_profit=take_profit,
            strategy_name=source, source=source, reason=reason, created_at=0)
        decision = self._risk.check_order(order, self.get_portfolio())
        if decision.action == "REJECT": return ""
        params: Dict = {"symbol": symbol, "side": side, "qty": str(qty), "order_type": "Market"}
        if stop_loss and stop_loss > 0: params["stop_loss"] = str(stop_loss)
        if take_profit and take_profit > 0: params["take_profit"] = str(take_profit)
        result = self._rest.place_order(**params)
        order_id = result.get("orderId", "")
        if order_id and self._alert:
            pos_side = "LONG" if side == "Buy" else "SHORT"
            self._alert.on_trade_entry(symbol=symbol, side=pos_side, qty=qty, price=0.0, strategy=source)
        return order_id

__all__ = ["LiveBroker"]
