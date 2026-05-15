"""Unified order audit log (Stage C-1).

Both :class:`BbkcBroker` and :class:`LiveBroker` write to the SAME
``orders.jsonl`` with the SAME schema so a 14-day forward run can be
audited line-by-line regardless of which surface failed.

Schema (per JSON line)
----------------------
ts                : RFC3339 UTC timestamp (ISO-8601, ``+00:00``)
ts_ms             : integer ms epoch
event_type        : ``order_attempt`` (current) — reserved for future event kinds
action            : ``buy`` | ``sell`` | ``close`` | ``manual_buy`` | ``manual_sell``
symbol            : Bybit symbol (``BTCUSDT``...)
side              : ``Buy`` | ``Sell`` | ``""`` for close
qty               : float (post-rounding when applicable)
source            : ``STRATEGY`` | ``MANUAL`` | ``""``
reason            : strategy reason string (free text)
result            : ``success`` | ``exchange_reject`` | ``exchange_fail`` |
                    ``risk_reject`` | ``kill_switch_block`` | ``universe_block`` |
                    ``qty_below_min``
failure_category  : :class:`OrderFailureCategory` value, or ``""``
failure_message   : raw exchange message (or local block reason)
order_id          : Bybit orderId, ``""`` on failure
stop_loss         : optional float
take_profit       : optional float
breaker_eligible  : bool — whether this outcome feeds the circuit breaker
circuit_breaker_tripped : bool — breaker state AT THE TIME of this attempt
kill_switch_engaged     : bool — kill switch state AT THE TIME of this attempt
equity_snapshot   : float — broker equity at attempt time (best-effort)

File handling
-------------
Append-mode, UTF-8, newline-delimited. Best-effort: a failure to write
the audit row is logged at WARNING but never raised — the broker has
to keep functioning even if the disk is full.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


EVENT_ORDER_ATTEMPT = "order_attempt"

# Result enum — kept as plain strings so it serialises naturally.
RESULT_SUCCESS = "success"
RESULT_EXCHANGE_REJECT = "exchange_reject"   # pybit raised retCode != 0
RESULT_EXCHANGE_FAIL = "exchange_fail"       # success-shaped reply, no orderId
RESULT_RISK_REJECT = "risk_reject"
RESULT_KILL_SWITCH_BLOCK = "kill_switch_block"
RESULT_UNIVERSE_BLOCK = "universe_block"
RESULT_QTY_BELOW_MIN = "qty_below_min"

ALL_RESULTS = (
    RESULT_SUCCESS,
    RESULT_EXCHANGE_REJECT,
    RESULT_EXCHANGE_FAIL,
    RESULT_RISK_REJECT,
    RESULT_KILL_SWITCH_BLOCK,
    RESULT_UNIVERSE_BLOCK,
    RESULT_QTY_BELOW_MIN,
)


class OrderLogger:
    """Appends order outcomes to ``run_dir/orders.jsonl``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def log(
        self,
        *,
        action: str,
        symbol: str,
        side: str = "",
        qty: float = 0.0,
        source: str = "",
        reason: str = "",
        result: str,
        failure_category: str = "",
        failure_message: str = "",
        order_id: str = "",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        breaker_eligible: bool = True,
        circuit_breaker_tripped: bool = False,
        kill_switch_engaged: bool = False,
        equity_snapshot: Optional[float] = None,
    ) -> None:
        """Append one order-outcome row. Never raises."""
        if result not in ALL_RESULTS:
            # Be permissive — log unknown results too, but warn so we
            # notice schema drift.
            logger.warning(
                "[order_logger] unknown result %r — writing anyway", result,
            )
        row: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ts_ms": int(time.time() * 1000),
            "event_type": EVENT_ORDER_ATTEMPT,
            "action": action,
            "symbol": symbol,
            "side": side,
            "qty": float(qty),
            "source": source,
            "reason": reason,
            "result": result,
            "failure_category": failure_category or "",
            "failure_message": failure_message or "",
            "order_id": order_id or "",
            "stop_loss": float(stop_loss) if stop_loss else None,
            "take_profit": float(take_profit) if take_profit else None,
            "breaker_eligible": bool(breaker_eligible),
            "circuit_breaker_tripped": bool(circuit_breaker_tripped),
            "kill_switch_engaged": bool(kill_switch_engaged),
            "equity_snapshot": (
                float(equity_snapshot) if equity_snapshot is not None else None
            ),
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("[order_logger] failed to append %s: %s",
                         self._path, exc)


__all__ = [
    "OrderLogger",
    "EVENT_ORDER_ATTEMPT",
    "RESULT_SUCCESS",
    "RESULT_EXCHANGE_REJECT",
    "RESULT_EXCHANGE_FAIL",
    "RESULT_RISK_REJECT",
    "RESULT_KILL_SWITCH_BLOCK",
    "RESULT_UNIVERSE_BLOCK",
    "RESULT_QTY_BELOW_MIN",
    "ALL_RESULTS",
]
