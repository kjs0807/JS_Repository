"""LiveBroker - Bybit API live/demo broker.

Stage B-4 adds per-category failure classification on every order
attempt that flows through :meth:`_execute_order`; the counter is
exposed via :meth:`get_failure_counters`. Stage B-5 lets the runner
attach a :class:`CircuitBreaker` via :meth:`set_circuit_breaker` so the
breaker sees every outcome and can trip the on-disk kill switch when
the failure rate climbs.

``close()`` is intentionally NOT wrapped: managing already-open
positions has to keep working even when the circuit breaker has
paused new entries.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from src.core.config import RiskConfig
from src.core.alert import AlertManager
from src.api.rest_client import BybitRestClient
from src.execution.broker import Broker, Position, Portfolio, Fill, Order
from src.execution.risk import RiskManager
from src.runtime.order_failure import (
    ALL_CATEGORIES,
    OrderFailureCategory,
    classify_order_failure,
)

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
        # Stage B-4: counter[category] = N failures since process start.
        self._failure_counters: Dict[str, int] = {c: 0 for c in ALL_CATEGORIES}
        self._success_count: int = 0
        # Stage B-5: optional circuit breaker (CircuitBreaker instance).
        # set_circuit_breaker() wires it in after construction so the
        # broker stays decoupled from the circuit-breaker module.
        self._circuit_breaker: Optional[Any] = None
        self._sync_wallet()

    # ------------------------------------------------------------------
    # Stage B-4/B-5 wiring
    # ------------------------------------------------------------------
    def set_circuit_breaker(self, breaker: Any) -> None:
        """Attach a circuit breaker. ``breaker`` must implement
        ``record(success: bool, category: str)``."""
        self._circuit_breaker = breaker

    def get_failure_counters(self) -> Dict[str, int]:
        """Snapshot of per-category failure counts since process start."""
        return dict(self._failure_counters)

    def get_order_success_count(self) -> int:
        return self._success_count

    def _record_outcome(self, success: bool, category: str = "") -> None:
        if success:
            self._success_count += 1
        else:
            cat = category or OrderFailureCategory.OTHER
            self._failure_counters[cat] = self._failure_counters.get(cat, 0) + 1
        if self._circuit_breaker is not None:
            try:
                self._circuit_breaker.record(success=success, category=category)
            except Exception as exc:
                logger.warning(
                    "[LiveBroker] circuit_breaker.record raised: %s", exc,
                )

    def _position_idx_for_side(self, side: str) -> int:
        """Hedge mode 가정: LONG=1, SHORT=2.

        NOTE: 계정이 one-way mode이면 0을 반환해야 함. 현재 src 경로는
        rest_client.place_order의 자동 도출 로직(side='Buy' → 1)과 동일하게
        hedge를 가정. one-way 전환 시 이 헬퍼 + place_order 모두 수정 필요.
        Round 5 §5.2 참조.
        """
        return 1 if side == "LONG" else 2

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
        """API 경유 SL 갱신 (round 5 §5.2). 성공 시에만 로컬 갱신."""
        pos = self._positions.get(symbol)
        if pos is None:
            return
        pos_idx = self._position_idx_for_side(pos.side)
        try:
            self._rest.set_trading_stop(
                symbol=symbol, stop_loss=new_stop, position_idx=pos_idx,
            )
            pos.stop_loss = new_stop   # 성공 시에만 (서버 ↔ 로컬 일치)
        except Exception as exc:
            logger.warning(
                "set_trading_stop(stop_loss) failed for %s: %s "
                "- local stop_loss not updated to keep server/local consistent",
                symbol, exc,
            )

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

    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None:
        """API 경유 TP 갱신 (round 5 §5.3). 성공 시에만 로컬 갱신."""
        pos = self._positions.get(symbol)
        if pos is None:
            return
        pos_idx = self._position_idx_for_side(pos.side)
        try:
            self._rest.set_trading_stop(
                symbol=symbol, take_profit=new_tp, position_idx=pos_idx,
            )
            pos.take_profit = new_tp
        except Exception as exc:
            logger.warning(
                "set_trading_stop(take_profit) failed for %s: %s "
                "- local take_profit not updated",
                symbol, exc,
            )

    def manual_update_tp(self, symbol: str, new_tp: float) -> None:
        """Round 5: 로컬만 변경하던 기존 동작 → API 경유로 변경."""
        self.update_tp(symbol, new_tp)

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
        """Bybit get_positions 응답에서 SL/TP 파싱 (round 5 §5.4).

        재시작 후 BE/trail로 이동된 stop_loss/take_profit을 잃지 않도록 함.
        빈 문자열 / "0" / None은 해당 필드 미설정으로 취급.
        """
        raw_positions = self._rest.get_positions()
        new_positions: Dict[str, Position] = {}
        for raw in raw_positions:
            size = float(raw.get("size", 0))
            if size <= 0:
                continue
            symbol = raw["symbol"]
            side = "LONG" if raw.get("side") == "Buy" else "SHORT"

            sl_raw = raw.get("stopLoss")
            tp_raw = raw.get("takeProfit")
            sl_value = float(sl_raw) if sl_raw not in (None, "", "0") else 0.0
            tp_value = float(tp_raw) if tp_raw not in (None, "", "0") else None

            new_positions[symbol] = Position(
                symbol=symbol, side=side, qty=size,
                entry_price=float(raw.get("avgPrice", 0)),
                entry_time=0,
                stop_loss=sl_value,
                take_profit=tp_value,
                unrealized_pnl=float(raw.get("unrealisedPnl", 0)),
                strategy_name="SYNCED",
            )
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
        if decision.action == "REJECT":
            self._record_outcome(False, OrderFailureCategory.RISK_REJECT)
            return ""
        params: Dict = {"symbol": symbol, "side": side, "qty": str(qty), "order_type": "Market"}
        if stop_loss and stop_loss > 0: params["stop_loss"] = str(stop_loss)
        if take_profit and take_profit > 0: params["take_profit"] = str(take_profit)
        try:
            result = self._rest.place_order(**params)
        except Exception as exc:
            # pybit raises InvalidRequestError for retCode != 0 on most
            # paths. Classify, alert, and let the strategy see "" so it
            # cannot proceed assuming the order is open.
            category = classify_order_failure(exc)
            logger.warning(
                "[LiveBroker] place_order raised for %s %s qty=%s: %s [%s]",
                symbol, side, qty, exc, category,
            )
            if self._alert is not None:
                try:
                    self._alert.on_error(
                        f"order failed [{category}]: {symbol} {side} "
                        f"qty={qty}: {exc}"
                    )
                except Exception:
                    pass
            self._record_outcome(False, category)
            return ""
        # rest_client.place_order swallows retCode != 0 in some
        # versions and returns ``{"error": retMsg}``. Detect and
        # classify identically.
        if isinstance(result, dict) and "error" in result and "orderId" not in result:
            ret_msg = result.get("error", "")
            category = classify_order_failure(result)
            logger.warning(
                "[LiveBroker] place_order rejected for %s %s: %s [%s]",
                symbol, side, ret_msg, category,
            )
            if self._alert is not None:
                try:
                    self._alert.on_error(
                        f"order rejected [{category}]: {symbol} {side}: {ret_msg}"
                    )
                except Exception:
                    pass
            self._record_outcome(False, category)
            return ""
        order_id = result.get("orderId", "")
        if order_id:
            if self._alert is not None:
                pos_side = "LONG" if side == "Buy" else "SHORT"
                try:
                    self._alert.on_trade_entry(
                        symbol=symbol, side=pos_side, qty=qty,
                        price=0.0, strategy=source,
                    )
                except Exception:
                    pass
            self._record_outcome(True, "")
        else:
            # pybit returned success-shaped dict but no orderId. Treat
            # defensively so the strategy never thinks an order is open.
            logger.warning(
                "[LiveBroker] place_order returned no orderId for %s %s: %s",
                symbol, side, result,
            )
            self._record_outcome(False, OrderFailureCategory.OTHER)
        return order_id

__all__ = ["LiveBroker"]
