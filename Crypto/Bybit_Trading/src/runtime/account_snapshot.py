"""Account heartbeat snapshot (Stage C-1).

The runner appends one snapshot per heartbeat tick to
``run_dir/account.jsonl``. After a 14-day forward run we can replay
``equity / pnl / failure_counters / breaker_stats`` line by line to
reconstruct what happened without scraping console logs.

Schema (per JSON line)
----------------------
ts                : RFC3339 UTC timestamp
ts_ms             : ms epoch
mode              : ``demo`` | ``live``
strategy          : strategy name
universe          : list of symbols
timeframe         : strategy timeframe (e.g. ``1h``)
equity            : float
available_margin  : float
used_margin       : float
daily_pnl         : float
realized_pnl      : float
positions         : list of {symbol, side, qty, entry_price, unrealized_pnl,
                             stop_loss, take_profit}
failure_counters  : {category: count} since process start
breaker_stats     : ``CircuitBreaker.stats()`` snapshot (or ``None``)
kill_switch_engaged : bool
kill_switch_reason  : str — non-empty only when engaged
bars_seen         : int
ws_connected      : bool

Best-effort: write failures are logged at WARNING but never raise.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AccountSnapshotWriter:
    """Append heartbeat snapshots to ``account.jsonl``."""

    def __init__(
        self,
        path: Path,
        *,
        mode: str,
        strategy: str,
        universe: List[str],
        timeframe: str,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._mode = mode
        self._strategy = strategy
        self._universe = list(universe)
        self._timeframe = timeframe

    @property
    def path(self) -> Path:
        return self._path

    def write(
        self,
        *,
        portfolio: Any,
        failure_counters: Optional[Dict[str, int]] = None,
        breaker_stats: Optional[Dict[str, Any]] = None,
        kill_switch_engaged: bool = False,
        kill_switch_reason: str = "",
        bars_seen: int = 0,
        ws_connected: bool = True,
    ) -> None:
        """Write one heartbeat row. Best-effort (never raises)."""
        positions_serialised: List[Dict[str, Any]] = []
        try:
            for p in (portfolio.positions or []):
                positions_serialised.append({
                    "symbol": getattr(p, "symbol", ""),
                    "side": getattr(p, "side", ""),
                    "qty": float(getattr(p, "qty", 0.0) or 0.0),
                    "entry_price": float(getattr(p, "entry_price", 0.0) or 0.0),
                    "unrealized_pnl": float(
                        getattr(p, "unrealized_pnl", 0.0) or 0.0
                    ),
                    "stop_loss": (
                        float(getattr(p, "stop_loss", 0.0))
                        if getattr(p, "stop_loss", None) else None
                    ),
                    "take_profit": (
                        float(getattr(p, "take_profit", 0.0))
                        if getattr(p, "take_profit", None) else None
                    ),
                })
        except Exception as exc:
            logger.warning("[account_snapshot] positions serialise failed: %s", exc)

        row: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ts_ms": int(time.time() * 1000),
            "mode": self._mode,
            "strategy": self._strategy,
            "universe": list(self._universe),
            "timeframe": self._timeframe,
            "equity": float(getattr(portfolio, "equity", 0.0) or 0.0),
            "available_margin": float(
                getattr(portfolio, "available_margin", 0.0) or 0.0
            ),
            "used_margin": float(getattr(portfolio, "used_margin", 0.0) or 0.0),
            "daily_pnl": float(getattr(portfolio, "daily_pnl", 0.0) or 0.0),
            "realized_pnl": float(
                getattr(portfolio, "realized_pnl", 0.0) or 0.0
            ),
            "positions": positions_serialised,
            "failure_counters": dict(failure_counters or {}),
            "breaker_stats": dict(breaker_stats) if breaker_stats else None,
            "kill_switch_engaged": bool(kill_switch_engaged),
            "kill_switch_reason": kill_switch_reason or "",
            "bars_seen": int(bars_seen),
            "ws_connected": bool(ws_connected),
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("[account_snapshot] failed to append %s: %s",
                         self._path, exc)


__all__ = ["AccountSnapshotWriter"]
