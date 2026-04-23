"""BbkcDemoBroker — LiveBroker subclass for BBKC[BIGTHREE] demo trading.

This is the broker the user actually wanted: a thin layer on top of
``LiveBroker`` that calls the real Bybit Demo API for every order and
pulls the real wallet / position state for heartbeat display. It is
the structural equivalent of legacy ``main.py trade`` but scoped to a
single strategy (``BBKCSqueeze``) and a fixed universe (``BIGTHREE``).

Why a subclass rather than raw LiveBroker
-----------------------------------------
1. **Universe guard** — the same safety rail as PaperBroker. A bug in
   strategy code cannot submit orders on SOL/LINK even if it tries.
2. **Qty rounding to instrument lot step** — Bybit rejects orders that
   are not multiples of the instrument's ``qtyStep``. Legacy had this
   in ``TradingEngine._round_qty``; we port the same idea here by
   fetching instrument specs once at construction time.
3. **Order audit log** — every submitted order (and its Bybit response)
   gets appended to ``orders.jsonl`` inside the run directory so the
   14-day run can be audited line-by-line.
4. **Public sync()** — heartbeat code can call ``broker.sync()`` without
   poking private LiveBroker methods.

What is NOT changed
-------------------
- BBKCSqueeze entry logic (P5: entry unchanged).
- LiveBroker itself (so main_live.py and any other LiveBroker consumer
  is unaffected).
- BacktestBroker or PaperBroker.

Safety
------
- The broker class itself cannot reach mainnet by accident: the caller
  must pass an already-constructed ``BybitRestClient``, and the
  mainnet/demo switch lives in ``cfg.app.base_url``. For demo trading
  ``AppSettings.base_url`` defaults to ``https://api-demo.bybit.com``.
- If the instrument spec lookup fails (no API key, network error), the
  broker falls back to a conservative default (qty rounded to 3
  decimals) and logs a warning so the user notices.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.api.rest_client import BybitRestClient
from src.core.alert import AlertManager
from src.core.config import RiskConfig
from src.execution.broker import Portfolio, Position
from src.execution.live_broker import LiveBroker

logger = logging.getLogger(__name__)


class BbkcDemoBroker(LiveBroker):
    """LiveBroker + BIGTHREE universe guard + lot-step qty rounding +
    orders.jsonl audit log."""

    def __init__(
        self,
        rest_client: BybitRestClient,
        run_dir: Path,
        symbols_allowed: List[str],
        alert_manager: Optional[AlertManager] = None,
        risk_config: Optional[RiskConfig] = None,
        leverage: int = 3,
        initial_capital: float = 10_000.0,
    ) -> None:
        super().__init__(
            rest_client=rest_client,
            alert_manager=alert_manager,
            risk_config=risk_config,
            leverage=leverage,
            initial_capital=initial_capital,
        )
        self._run_dir = Path(run_dir)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._orders_path = self._run_dir / "orders.jsonl"
        self._symbols_allowed: Set[str] = set(symbols_allowed)
        self._qty_step: Dict[str, float] = {}
        self._min_qty: Dict[str, float] = {}
        self._fetch_instrument_specs()

    # ------------------------------------------------------------------
    # Instrument spec — qty step / min qty per symbol
    # ------------------------------------------------------------------

    def _fetch_instrument_specs(self) -> None:
        """Populate per-symbol ``qty_step`` and ``min_qty`` from
        Bybit ``get_instruments``.

        On failure we fall back to 3-decimal rounding (0.001) so a
        missing API key does not brick the broker entirely — orders
        may get rejected by Bybit but that's a recoverable error.
        """
        try:
            instruments = self._rest.get_instruments()
        except Exception as exc:
            logger.warning(
                "[BbkcDemoBroker] instruments fetch failed: %s — using 0.001 fallback",
                exc,
            )
            for sym in self._symbols_allowed:
                self._qty_step[sym] = 0.001
                self._min_qty[sym] = 0.001
            return
        for item in instruments:
            sym = item.get("symbol")
            if sym not in self._symbols_allowed:
                continue
            lot = item.get("lotSizeFilter", {})
            try:
                self._qty_step[sym] = float(lot.get("qtyStep", "0.001"))
            except Exception:
                self._qty_step[sym] = 0.001
            try:
                self._min_qty[sym] = float(lot.get("minOrderQty", "0.001"))
            except Exception:
                self._min_qty[sym] = 0.001
            logger.info(
                "[BbkcDemoBroker] %s qty_step=%s min_qty=%s",
                sym, self._qty_step[sym], self._min_qty[sym],
            )
        for sym in self._symbols_allowed:
            if sym not in self._qty_step:
                self._qty_step[sym] = 0.001
                self._min_qty[sym] = 0.001
                logger.warning(
                    "[BbkcDemoBroker] %s missing from instruments response, "
                    "using fallback qty_step=0.001",
                    sym,
                )

    def _round_qty(self, symbol: str, qty: float) -> float:
        """Floor ``qty`` to the nearest multiple of the symbol's lot step.

        Uses ``floor`` rather than ``round`` to guarantee the final
        notional never exceeds the risk-sized target.
        """
        step = self._qty_step.get(symbol, 0.001)
        if step <= 0:
            return float(qty)
        n_steps = math.floor(qty / step)
        rounded = n_steps * step
        min_q = self._min_qty.get(symbol, 0.0)
        if rounded < min_q:
            return 0.0
        return float(rounded)

    # ------------------------------------------------------------------
    # Universe guard + qty rounding wrappers
    # ------------------------------------------------------------------

    def _check_universe(self, symbol: str, source: str) -> bool:
        if symbol not in self._symbols_allowed:
            logger.warning(
                "[BbkcDemoBroker] %s blocked for %s — not in allowed universe %s",
                source, symbol, sorted(self._symbols_allowed),
            )
            return False
        return True

    def calc_qty(
        self, symbol: str, risk_pct: float, stop_distance: float,
    ) -> float:
        raw = super().calc_qty(symbol, risk_pct, stop_distance)
        rounded = self._round_qty(symbol, raw)
        return rounded

    def buy(
        self, symbol: str, qty: float, stop_loss: float,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "buy"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            logger.warning(
                "[BbkcDemoBroker] buy %s skipped — qty below min after rounding",
                symbol,
            )
            return ""
        oid = super().buy(symbol, qty, stop_loss, take_profit, reason)
        self._log_order("buy", symbol, qty, stop_loss, take_profit, reason, oid)
        return oid

    def sell(
        self, symbol: str, qty: float, stop_loss: float,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "sell"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            logger.warning(
                "[BbkcDemoBroker] sell %s skipped — qty below min after rounding",
                symbol,
            )
            return ""
        oid = super().sell(symbol, qty, stop_loss, take_profit, reason)
        self._log_order("sell", symbol, qty, stop_loss, take_profit, reason, oid)
        return oid

    def close(self, symbol: str, reason: str = "") -> str:
        if not self._check_universe(symbol, "close"):
            return ""
        oid = super().close(symbol, reason)
        self._log_order(
            "close", symbol, 0.0, 0.0, None, reason, oid,
        )
        return oid

    def manual_buy(
        self, symbol: str, qty: float, stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "manual_buy"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            return ""
        oid = super().manual_buy(symbol, qty, stop_loss, take_profit, reason)
        self._log_order("manual_buy", symbol, qty, stop_loss or 0.0, take_profit, reason, oid)
        return oid

    def manual_sell(
        self, symbol: str, qty: float, stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "manual_sell"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            return ""
        oid = super().manual_sell(symbol, qty, stop_loss, take_profit, reason)
        self._log_order("manual_sell", symbol, qty, stop_loss or 0.0, take_profit, reason, oid)
        return oid

    # ------------------------------------------------------------------
    # Heartbeat helpers
    # ------------------------------------------------------------------

    def sync(self) -> None:
        """Refresh both wallet and positions from the real REST API.

        LiveBroker already has ``sync_positions`` and a private
        ``_sync_wallet`` — this just exposes a single public call and
        swallows no exceptions (so the caller sees why a sync failed).
        """
        self._sync_wallet()
        self.sync_positions()

    def live_portfolio(self) -> Portfolio:
        """Public alias for get_portfolio — returns a fresh Portfolio
        snapshot without forcing a REST call. Use ``sync()`` first if
        you need up-to-date numbers."""
        return self.get_portfolio()

    # ------------------------------------------------------------------
    # Order audit log
    # ------------------------------------------------------------------

    def _log_order(
        self, action: str, symbol: str, qty: float,
        stop_loss: Optional[float], take_profit: Optional[float],
        reason: str, order_id: str,
    ) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ts_ms": int(time.time() * 1000),
            "action": action,
            "symbol": symbol,
            "qty": float(qty),
            "stop_loss": float(stop_loss) if stop_loss else None,
            "take_profit": float(take_profit) if take_profit else None,
            "reason": reason,
            "order_id": order_id,
        }
        try:
            with self._orders_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as exc:
            logger.error("[BbkcDemoBroker] failed to write orders.jsonl: %s", exc)

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def symbols_allowed(self) -> Set[str]:
        return set(self._symbols_allowed)


__all__ = ["BbkcDemoBroker"]
