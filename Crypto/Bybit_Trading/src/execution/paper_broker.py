"""PaperBroker — persistent, logged, universe-bounded extension of BacktestBroker.

This is the "third broker" that sits between ``BacktestBroker`` (pure
simulation, no state) and ``LiveBroker`` (real Bybit API).

Why a separate class
--------------------
For the BBKC[BIGTHREE] staged-promote work we need to run the strategy
against replay (or, later, against a live feed) and:

1. Never call the real Bybit order API.
2. Persist the full portfolio state between runs so start / stop /
   resume works without losing position context.
3. Emit append-only logs (signals, fills, equity snapshots) so the
   paper run can be audited later without re-running the strategy.
4. Enforce a fixed universe (BIGTHREE = BTC + ETH + AVAX) at the
   broker boundary so a bug in strategy code cannot accidentally
   submit orders on SOL/LINK.

Separation from LiveBroker
--------------------------
PaperBroker does NOT import ``BybitRestClient`` or any network-facing
module. A live paper loop can later delegate "place this order" to a
Bybit demo endpoint, but that wiring lives in ``scripts/run_bbkc_paper.py``
or a thin LivePaperBroker subclass — not here. This module stays
deterministic and offline-testable.

Subclass, not compose
---------------------
``BacktestBroker.process_bar`` already handles the full fill / exit /
pnl pipeline with intra-bar TP/SL. Re-implementing that for paper
would duplicate about 200 lines. Subclassing means paper inherits all
existing tests implicitly and any bug fix in backtest_broker.py
benefits paper for free.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from src.core.config import BacktestConfig, RiskConfig
from src.core.types import Bar
from src.execution.backtest_broker import BacktestBroker, TradeRecord
from src.execution.broker import Position

logger = logging.getLogger(__name__)


class PaperBroker(BacktestBroker):
    """BacktestBroker + universe guard + state persistence + jsonl logs."""

    def __init__(
        self,
        config: BacktestConfig,
        risk_config: Optional[RiskConfig],
        run_dir: Path,
        symbols_allowed: Iterable[str],
        run_id: Optional[str] = None,
    ) -> None:
        super().__init__(config, risk_config)
        self._run_dir = Path(run_dir)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._symbols_allowed: Set[str] = set(symbols_allowed)
        self._run_id = run_id or self._run_dir.name
        self._signals_path = self._run_dir / "signals.jsonl"
        self._fills_path = self._run_dir / "fills.jsonl"
        self._equity_path = self._run_dir / "equity_curve.csv"
        self._state_path = self._run_dir / "paper_state.json"
        self._fills_seen: int = 0
        self._last_bar_ts: Dict[str, int] = {}
        # Write CSV header if file is new.
        if not self._equity_path.exists():
            self._equity_path.write_text(
                "ts_ms,equity,realized_pnl,n_open_positions\n",
                encoding="utf-8",
            )

    # ------------------------------------------------------------------
    # Universe guard — reject order attempts outside allowed symbols.
    # Both buy() and sell() are routed through this guard so any strategy
    # code calling them cannot leak orders to unsupported symbols.
    # ------------------------------------------------------------------

    def _check_universe(self, symbol: str, source: str) -> bool:
        if symbol not in self._symbols_allowed:
            logger.warning(
                "[PaperBroker] %s blocked for %s — not in allowed universe %s",
                source, symbol, sorted(self._symbols_allowed),
            )
            return False
        return True

    def buy(self, symbol: str, qty: float, stop_loss: float,
            take_profit: Optional[float] = None, reason: str = "") -> str:
        if not self._check_universe(symbol, "buy"):
            return ""
        return super().buy(symbol, qty, stop_loss, take_profit, reason)

    def sell(self, symbol: str, qty: float, stop_loss: float,
             take_profit: Optional[float] = None, reason: str = "") -> str:
        if not self._check_universe(symbol, "sell"):
            return ""
        return super().sell(symbol, qty, stop_loss, take_profit, reason)

    def manual_buy(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                   take_profit: Optional[float] = None, reason: str = "") -> str:
        if not self._check_universe(symbol, "manual_buy"):
            return ""
        return super().manual_buy(symbol, qty, stop_loss, take_profit, reason)

    def manual_sell(self, symbol: str, qty: float, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None, reason: str = "") -> str:
        if not self._check_universe(symbol, "manual_sell"):
            return ""
        return super().manual_sell(symbol, qty, stop_loss, take_profit, reason)

    # ------------------------------------------------------------------
    # Bar processing hooks — flush new fills and equity snapshot.
    # ------------------------------------------------------------------

    def process_bar(self, bar: Bar) -> None:
        super().process_bar(bar)
        self._last_bar_ts[bar.symbol] = int(bar.timestamp)
        # Append any newly created trades to fills.jsonl. Uses the
        # BacktestBroker._trades length as a cursor so we only emit
        # rows that appeared since the last call.
        n_trades = len(self._trades)
        if n_trades > self._fills_seen:
            new_trades = self._trades[self._fills_seen:]
            with self._fills_path.open("a", encoding="utf-8") as f:
                for t in new_trades:
                    f.write(json.dumps(_trade_row(t)) + "\n")
            self._fills_seen = n_trades
        # Append an equity snapshot.
        eq = self._equity + sum(
            p.unrealized_pnl for p in self._positions.get_all()
        )
        with self._equity_path.open("a", encoding="utf-8") as f:
            f.write(
                f"{int(bar.timestamp)},{eq:.4f},"
                f"{self._realized_pnl:.4f},"
                f"{self._positions.count}\n"
            )

    # ------------------------------------------------------------------
    # Signal log — strategies can call this to leave a breadcrumb even
    # if the order is rejected downstream (universe block, risk, etc.).
    # ------------------------------------------------------------------

    def log_signal(
        self, bar: Bar, action: str, reason: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "ts_ms": int(bar.timestamp),
            "symbol": bar.symbol,
            "action": action,
            "reason": reason,
            "close": float(bar.close),
            "meta": meta or {},
        }
        with self._signals_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------------
    # State persistence — called by the runner at checkpoint boundaries.
    # Pending orders are deliberately NOT persisted: the backtest
    # semantics create pending orders inside a single bar iteration
    # and fill them on the next bar, so checkpointing between full
    # bars means zero pending orders are ever live. If a paper run is
    # killed mid-bar the user loses the order, which is accepted.
    # ------------------------------------------------------------------

    def save_state(self, extra: Optional[Dict[str, Any]] = None) -> None:
        positions = [_position_row(p) for p in self._positions.get_all()]
        eq = self._equity + sum(
            p.unrealized_pnl for p in self._positions.get_all()
        )
        state: Dict[str, Any] = {
            "run_id": self._run_id,
            "run_dir": str(self._run_dir),
            "symbols_allowed": sorted(self._symbols_allowed),
            "equity": float(self._equity),
            "equity_incl_unrealized": float(eq),
            "realized_pnl": float(self._realized_pnl),
            "n_open_positions": int(self._positions.count),
            "positions": positions,
            "trades_total": len(self._trades),
            "last_bar_ts": {
                k: int(v) for k, v in self._last_bar_ts.items()
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            state["extra"] = extra
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state, indent=2, default=str), encoding="utf-8",
        )
        os.replace(tmp, self._state_path)

    def load_state(self) -> Optional[Dict[str, Any]]:
        """Read the persisted state snapshot without mutating the broker.

        Restoration of pending positions is intentionally left to the
        caller (the runner) so the semantics stay explicit — the broker
        here only tells you *what* was saved.
        """
        if not self._state_path.exists():
            return None
        return json.loads(self._state_path.read_text(encoding="utf-8"))

    def restore_from_state(self, state: Dict[str, Any]) -> None:
        """Apply a previously saved state back into this broker.

        Only positions + equity + realized_pnl are restored. Pending
        orders are discarded (see save_state docstring). The idea is
        that the runner is responsible for the next feed position, so
        we do not rebuild the price history here.
        """
        self._equity = float(state.get("equity", self._equity))
        self._realized_pnl = float(state.get("realized_pnl", 0.0))
        self._last_bar_ts = {
            str(k): int(v) for k, v in state.get("last_bar_ts", {}).items()
        }
        # Positions — rebuild PositionTracker entries.
        for row in state.get("positions", []):
            self._positions.open(
                symbol=row["symbol"],
                side=row["side"],
                qty=float(row["qty"]),
                entry_price=float(row["entry_price"]),
                entry_time=int(row.get("entry_time", 0)),
                stop_loss=float(row.get("stop_loss", 0.0) or 0.0),
                take_profit=(
                    float(row["take_profit"])
                    if row.get("take_profit") is not None
                    else None
                ),
                strategy_name=row.get("strategy_name", "RESTORED"),
            )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def symbols_allowed(self) -> Set[str]:
        return set(self._symbols_allowed)

    @property
    def realized_pnl(self) -> float:
        return float(self._realized_pnl)


def _position_row(pos: Position) -> Dict[str, Any]:
    return {
        "symbol": pos.symbol,
        "side": pos.side,
        "qty": float(pos.qty),
        "entry_price": float(pos.entry_price),
        "entry_time": int(pos.entry_time),
        "stop_loss": float(pos.stop_loss or 0.0),
        "take_profit": (
            float(pos.take_profit) if pos.take_profit is not None else None
        ),
        "unrealized_pnl": float(pos.unrealized_pnl),
        "strategy_name": pos.strategy_name,
    }


def _trade_row(t: TradeRecord) -> Dict[str, Any]:
    return {
        "symbol": t.symbol,
        "strategy_name": t.strategy_name,
        "side": t.side,
        "entry_time": int(t.entry_time),
        "exit_time": int(t.exit_time),
        "entry_price": float(t.entry_price),
        "exit_price": float(t.exit_price),
        "qty": float(t.qty),
        "pnl": float(t.pnl),
        "fee": float(t.fee),
        "exit_reason": t.exit_reason,
        "source": t.source,
    }


__all__ = ["PaperBroker"]
