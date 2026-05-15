"""BbkcBroker - LiveBroker subclass for BBKC trading (demo *or* live).

This is the broker the user actually wanted: a thin layer on top of
``LiveBroker`` that calls the real Bybit REST API for every order and
pulls the real wallet / position state for heartbeat display. It is
the structural equivalent of legacy ``main.py trade`` but scoped to a
single strategy (``BBKCSqueeze``).

Mode-agnostic: the same class talks to demo (``api-demo.bybit.com``) or
mainnet (``api.bybit.com``) - only the supplied :class:`BybitRestClient`'s
``base_url`` differs, and that base_url is derived from ``config.app.mode``
via :mod:`src.core.mode`. There is no demo-specific or live-specific code
path here. The legacy class name ``BbkcDemoBroker`` is kept as an alias at
the bottom for back-compat (deprecated).

Why a subclass rather than raw LiveBroker
-----------------------------------------
1. **Universe guard** - the same safety rail as PaperBroker. A bug in
   strategy code cannot submit orders on SOL/LINK even if it tries.
2. **Qty rounding to instrument lot step** - Bybit rejects orders that
   are not multiples of the instrument's ``qtyStep``. Legacy had this
   in ``TradingEngine._round_qty``; we port the same idea here by
   fetching instrument specs once at construction time.
3. **Order audit log** - every submitted order (and its Bybit response)
   gets appended to ``orders.jsonl`` inside the run directory so the
   14-day run can be audited line-by-line.
4. **Public sync()** - heartbeat code can call ``broker.sync()`` without
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

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Set

from src.api.rest_client import BybitRestClient
from src.core.alert import AlertManager
from src.core.config import RiskConfig
from src.execution.broker import Portfolio
from src.execution.live_broker import LiveBroker
from src.runtime.kill_switch import KillSwitch
from src.runtime.order_logger import (
    OrderLogger,
    RESULT_KILL_SWITCH_BLOCK,
    RESULT_QTY_BELOW_MIN,
    RESULT_UNIVERSE_BLOCK,
)

logger = logging.getLogger(__name__)


class BbkcBroker(LiveBroker):
    """LiveBroker + universe guard + lot-step qty rounding + orders.jsonl audit log.

    Mode-agnostic - talks to demo or mainnet identically. The REST endpoint is
    a property of the injected ``BybitRestClient`` (configured via
    ``config.app.mode``), not of this class.

    Stage B additions:

      * ``per_symbol_max_pos_pct`` (B-2) overrides ``risk.max_position_pct``
        for the specific symbol when computing legacy-style notional qty.
      * ``kill_switch`` (B-3) is consulted before every NEW entry order;
        when engaged the order is logged + alerted + dropped. Existing
        positions remain managed (close / SL / TP updates are unaffected).
      * ``ensure_leverage_set`` (B-1) is intended to be called once at
        runner startup; it sets the leverage per symbol via the REST API
        and verifies the change via a position read-back.
    """

    def __init__(
        self,
        rest_client: BybitRestClient,
        run_dir: Path,
        symbols_allowed: List[str],
        alert_manager: Optional[AlertManager] = None,
        risk_config: Optional[RiskConfig] = None,
        leverage: int = 3,
        initial_capital: float = 10_000.0,
        per_symbol_max_pos_pct: Optional[Dict[str, float]] = None,
        kill_switch: Optional[KillSwitch] = None,
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
        # Stage B-2: optional per-symbol max_position_pct override.
        self._per_symbol_max_pos_pct: Dict[str, float] = (
            dict(per_symbol_max_pos_pct) if per_symbol_max_pos_pct else {}
        )
        # Stage B-3: optional kill switch (env + file-flag toggle).
        self._kill_switch: Optional[KillSwitch] = kill_switch
        # Stage C-1: unified orders.jsonl audit log. Both this broker
        # (pre-flight blocks: universe / kill_switch / qty-below-min) and
        # LiveBroker._execute_order (risk_reject / exchange outcomes)
        # write to the same path with the same schema.
        self._order_logger = OrderLogger(self._orders_path)
        self.set_order_logger(self._order_logger)
        if kill_switch is not None:
            self.set_kill_switch_ref(kill_switch)
        self._fetch_instrument_specs()

    # ------------------------------------------------------------------
    # Instrument spec - qty step / min qty per symbol
    # ------------------------------------------------------------------

    def _fetch_instrument_specs(self) -> None:
        """Populate per-symbol ``qty_step`` and ``min_qty`` from
        Bybit ``get_instruments``.

        On failure we fall back to 3-decimal rounding (0.001) so a
        missing API key does not brick the broker entirely - orders
        may get rejected by Bybit but that's a recoverable error.
        """
        try:
            instruments = self._rest.get_instruments()
        except Exception as exc:
            logger.warning(
                "[BbkcBroker] instruments fetch failed: %s - using 0.001 fallback",
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
                self._qty_step[sym] = float(
                    item.get("qty_step", lot.get("qtyStep", "0.001"))
                )
            except Exception:
                self._qty_step[sym] = 0.001
            try:
                self._min_qty[sym] = float(
                    item.get("min_qty", lot.get("minOrderQty", "0.001"))
                )
            except Exception:
                self._min_qty[sym] = 0.001
            logger.info(
                "[BbkcBroker] %s qty_step=%s min_qty=%s",
                sym, self._qty_step[sym], self._min_qty[sym],
            )
        for sym in self._symbols_allowed:
            if sym not in self._qty_step:
                self._qty_step[sym] = 0.001
                self._min_qty[sym] = 0.001
                logger.warning(
                    "[BbkcBroker] %s missing from instruments response, "
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
        step_text = f"{step:.12f}".rstrip("0").rstrip(".")
        decimals = len(step_text.split(".")[1]) if "." in step_text else 0
        rounded = round(rounded, decimals)
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
                "[BbkcBroker] %s blocked for %s - not in allowed universe %s",
                source, symbol, sorted(self._symbols_allowed),
            )
            self._order_logger.log(
                action=source, symbol=symbol, side="",
                qty=0.0, source=self._source_label(source),
                reason="universe block",
                result=RESULT_UNIVERSE_BLOCK,
                failure_message=(
                    f"symbol {symbol} not in {sorted(self._symbols_allowed)}"
                ),
                breaker_eligible=False,
                circuit_breaker_tripped=self._breaker_tripped(),
                kill_switch_engaged=self._ks_engaged(),
                equity_snapshot=float(self._equity),
            )
            return False
        return True

    def _check_kill_switch(self, symbol: str, source: str) -> bool:
        """Stage B-3: short-circuit NEW entries when the kill switch is engaged.

        Returns True when the order may proceed, False when it must be
        dropped (and a WARN log + an on_error alert have been emitted).
        Close / update_stop / update_tp do NOT call this - existing
        positions stay manageable while new exposure is paused.

        C-1: the block is logged to orders.jsonl with
        ``breaker_eligible=False`` so it never feeds the breaker
        (operator-engaged or breaker-engaged kill switches must not
        snowball into a second-order trip).
        """
        if self._kill_switch is None:
            return True
        if not self._kill_switch.is_new_entry_disabled():
            return True
        reason = self._kill_switch.reason() or "(unknown reason)"
        logger.warning(
            "[BbkcBroker] %s %s BLOCKED by kill switch: %s",
            source, symbol, reason,
        )
        if self._alert is not None:
            try:
                self._alert.on_error(
                    f"new entry blocked (kill switch): {source} {symbol} - {reason}"
                )
            except Exception:
                pass
        self._order_logger.log(
            action=source, symbol=symbol, side="",
            qty=0.0, source=self._source_label(source),
            reason="kill switch engaged",
            result=RESULT_KILL_SWITCH_BLOCK,
            failure_message=reason,
            breaker_eligible=False,
            circuit_breaker_tripped=self._breaker_tripped(),
            kill_switch_engaged=True,
            equity_snapshot=float(self._equity),
        )
        return False

    # ------------------------------------------------------------------
    # Snapshot helpers (Stage C-1)
    # ------------------------------------------------------------------
    def _source_label(self, action: str) -> str:
        return "MANUAL" if action.startswith("manual_") else "STRATEGY"

    def _ks_engaged(self) -> bool:
        if self._kill_switch is None:
            return False
        try:
            return bool(self._kill_switch.is_new_entry_disabled())
        except Exception:
            return False

    def _breaker_tripped(self) -> bool:
        return bool(
            self._circuit_breaker is not None
            and getattr(self._circuit_breaker, "tripped", False)
        )

    def _log_qty_below_min(
        self, action: str, symbol: str, side: str,
        stop_loss: Optional[float], take_profit: Optional[float],
        reason: str,
    ) -> None:
        self._order_logger.log(
            action=action, symbol=symbol, side=side,
            qty=0.0, source=self._source_label(action),
            reason=reason,
            result=RESULT_QTY_BELOW_MIN,
            failure_message=(
                f"qty below min_qty={self._min_qty.get(symbol, 0.0)} "
                f"after rounding to step={self._qty_step.get(symbol, 0.0)}"
            ),
            stop_loss=stop_loss, take_profit=take_profit,
            breaker_eligible=False,
            circuit_breaker_tripped=self._breaker_tripped(),
            kill_switch_engaged=self._ks_engaged(),
            equity_snapshot=float(self._equity),
        )

    def calc_qty(
        self, symbol: str, risk_pct: float, stop_distance: float,
    ) -> float:
        raw = super().calc_qty(symbol, risk_pct, stop_distance)
        rounded = self._round_qty(symbol, raw)
        return rounded

    def calc_legacy_notional_qty(self, symbol: str, entry_price: float) -> float:
        """Legacy live sizing: margin = equity * mpp, notional = margin * leverage.

        Stage B-2: ``mpp`` is taken from ``per_symbol_max_pos_pct`` when
        the symbol has an entry there; otherwise it falls back to the
        uniform ``risk.max_position_pct`` so existing single-weight runs
        are unchanged.

        NOTE on scope: per-symbol weights affect THIS method only, not
        :meth:`LiveBroker.calc_qty` (the ``risk_pct`` + ``stop_distance``
        path). Strategies that size via ``calc_qty`` ignore
        ``trading.weights``. The BBKC live path uses
        ``calc_legacy_notional_qty`` so the weights take effect for the
        intended deployment; extending ``calc_qty`` is a Stage B+ design
        item and intentionally out of scope here.
        """
        if entry_price <= 0:
            return 0.0
        mpp = self._per_symbol_max_pos_pct.get(symbol)
        if mpp is None:
            mpp = self._risk.config.max_position_pct
        margin_alloc = self._equity * float(mpp)
        notional = margin_alloc * self._leverage
        return self._round_qty(symbol, notional / entry_price)

    def buy(
        self, symbol: str, qty: float, stop_loss: float,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "buy"):
            return ""
        if not self._check_kill_switch(symbol, "buy"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            logger.warning(
                "[BbkcBroker] buy %s skipped - qty below min after rounding",
                symbol,
            )
            self._log_qty_below_min(
                "buy", symbol, "Buy", stop_loss, take_profit, reason,
            )
            return ""
        return super().buy(symbol, qty, stop_loss, take_profit, reason)

    def sell(
        self, symbol: str, qty: float, stop_loss: float,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "sell"):
            return ""
        if not self._check_kill_switch(symbol, "sell"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            logger.warning(
                "[BbkcBroker] sell %s skipped - qty below min after rounding",
                symbol,
            )
            self._log_qty_below_min(
                "sell", symbol, "Sell", stop_loss, take_profit, reason,
            )
            return ""
        return super().sell(symbol, qty, stop_loss, take_profit, reason)

    def close(self, symbol: str, reason: str = "") -> str:
        # ``close()`` intentionally bypasses kill_switch / breaker — managing
        # existing positions must keep working. Universe still applies so a
        # stray symbol cannot be touched, but audit logging happens here
        # because LiveBroker.close() does not currently route through
        # ``_execute_order``.
        if not self._check_universe(symbol, "close"):
            return ""
        oid = super().close(symbol, reason)
        self._order_logger.log(
            action="close", symbol=symbol, side="",
            qty=0.0, source="STRATEGY", reason=reason,
            result=("success" if oid else "exchange_fail"),
            failure_message=("" if oid else "close returned no orderId"),
            order_id=oid,
            breaker_eligible=False,
            circuit_breaker_tripped=self._breaker_tripped(),
            kill_switch_engaged=self._ks_engaged(),
            equity_snapshot=float(self._equity),
        )
        return oid

    def manual_buy(
        self, symbol: str, qty: float, stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "manual_buy"):
            return ""
        if not self._check_kill_switch(symbol, "manual_buy"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            self._log_qty_below_min(
                "manual_buy", symbol, "Buy", stop_loss, take_profit, reason,
            )
            return ""
        return super().manual_buy(symbol, qty, stop_loss, take_profit, reason)

    def manual_sell(
        self, symbol: str, qty: float, stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None, reason: str = "",
    ) -> str:
        if not self._check_universe(symbol, "manual_sell"):
            return ""
        if not self._check_kill_switch(symbol, "manual_sell"):
            return ""
        qty = self._round_qty(symbol, qty)
        if qty <= 0:
            self._log_qty_below_min(
                "manual_sell", symbol, "Sell", stop_loss, take_profit, reason,
            )
            return ""
        return super().manual_sell(symbol, qty, stop_loss, take_profit, reason)

    # ------------------------------------------------------------------
    # Stage B-1: set_leverage + read-back
    # ------------------------------------------------------------------
    def ensure_leverage_set(self, symbols: Optional[List[str]] = None) -> None:
        """Set leverage on every symbol and verify via a position read-back.

        Raises ``RuntimeError`` if the exchange-side leverage does not
        match ``self._leverage`` after the set call. The runner aborts
        before any order is dispatched on a mismatch.

        ``rest.set_leverage`` returns ``False`` for both "already at
        target" and "real failure", so we always re-read positions and
        treat the read-back as the single source of truth.
        """
        targets = list(symbols) if symbols is not None else sorted(self._symbols_allowed)
        if not targets:
            return
        target = int(self._leverage)
        try:
            self._rest.set_leverage  # type: ignore[attr-defined]
        except AttributeError as exc:
            raise RuntimeError(
                "rest_client has no set_leverage method - cannot verify leverage"
            ) from exc

        for sym in targets:
            try:
                self._rest.set_leverage(sym, target)
            except Exception as exc:
                logger.warning(
                    "[BbkcBroker] set_leverage(%s, %dx) raised %s; "
                    "proceeding to read-back",
                    sym, target, exc,
                )
            # Read-back: every position row for sym must report leverage==target.
            # Pass symbol= so we get the empty hedge slots too (without it
            # Bybit only returns rows with size > 0, which gives us zero
            # rows on a clean demo account). Defensive: filter on
            # row["symbol"] == sym in case Bybit ever returns extra rows
            # for related products on a wider response.
            try:
                raw_rows = self._rest.get_positions(symbol=sym)
            except TypeError:
                # Older rest_client without the symbol kwarg - filter manually.
                raw_rows = self._rest.get_positions()
            except Exception as exc:
                raise RuntimeError(
                    f"leverage read-back failed: get_positions raised {exc}"
                ) from exc
            sym_rows = [p for p in raw_rows if p.get("symbol") == sym]
            if not sym_rows:
                raise RuntimeError(
                    f"leverage read-back: no position row for {sym} "
                    "(account/symbol mode mismatch?)"
                )
            for p in sym_rows:
                lev_raw = p.get("leverage", "")
                try:
                    lev = int(float(lev_raw))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"leverage read-back: unparseable value "
                        f"{lev_raw!r} for {sym}"
                    ) from exc
                if lev != target:
                    raise RuntimeError(
                        f"leverage mismatch for {sym}: expected {target}x, "
                        f"got {lev}x"
                    )
            logger.info(
                "[BbkcBroker] %s leverage verified = %dx", sym, target,
            )

    # ------------------------------------------------------------------
    # Heartbeat helpers
    # ------------------------------------------------------------------

    def sync(self) -> None:
        """Refresh both wallet and positions from the real REST API.

        LiveBroker already has ``sync_positions`` and a private
        ``_sync_wallet`` - this just exposes a single public call and
        swallows no exceptions (so the caller sees why a sync failed).
        """
        self._sync_wallet()
        self.sync_positions()

    def live_portfolio(self) -> Portfolio:
        """Public alias for get_portfolio - returns a fresh Portfolio
        snapshot without forcing a REST call. Use ``sync()`` first if
        you need up-to-date numbers."""
        return self.get_portfolio()

    # Stage C-1: order audit logging is now delegated to the unified
    # :class:`OrderLogger` (``self._order_logger``). LiveBroker logs every
    # outcome that reaches the exchange; BbkcBroker logs the pre-flight
    # blocks (universe / kill_switch / qty-below-min) and close() above.

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def symbols_allowed(self) -> Set[str]:
        return set(self._symbols_allowed)


# Stage A: back-compat alias. ``BbkcDemoBroker`` was the original name when
# the runner only supported Bybit demo. The class itself is mode-agnostic, so
# it has been renamed to :class:`BbkcBroker`. Existing imports of
# ``BbkcDemoBroker`` continue to work via this alias.
BbkcDemoBroker = BbkcBroker

__all__ = ["BbkcBroker", "BbkcDemoBroker"]
