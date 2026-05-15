"""Append-only fill audit log (Stage C-2b).

A *separate* file from ``orders.jsonl``. Per C-2b design contract:

  * ``fills.jsonl`` is append-only.
  * ``orders.jsonl`` rows are NEVER mutated after the fact, so we do
    not need to map fill -> order row update; we just append a parallel
    record keyed by orderId.

Schema (per JSON line)
----------------------
ts             : RFC3339 UTC timestamp at write time
ts_ms          : ms epoch at write time
order_id       : Bybit orderId (joins with ``orders.jsonl.order_id``)
symbol         : Bybit symbol
side           : ``Buy`` | ``Sell``
intent_qty     : qty submitted on the order
fill_qty       : cumExecQty observed on the order at reconciliation
intent_price   : last bar close at submit time (best available "intent")
fill_price     : avgPrice from Bybit order history
slippage_abs   : signed adverse-or-not in price units
slippage_bps   : signed basis points (slippage_abs / intent_price * 10000)
submit_ts_ms   : ms epoch at which place_order succeeded
fill_ts_ms     : ms epoch at which reconciliation observed the fill
fill_lag_ms    : fill_ts_ms - submit_ts_ms
status         : ``filled`` | ``partial`` | ``timeout`` | ``missing_intent``

Sign convention
---------------
For a *Buy*, paying more than intent is *adverse*: ``slippage_abs > 0``.
For a *Sell*, receiving less than intent is *adverse*: ``slippage_abs > 0``.
So a positive ``slippage_bps`` always means "the live execution moved
*against* the strategy versus the bar-close reference".

Best-effort: write failures are logged at ERROR but never raised — the
runner heartbeat must keep ticking even when the disk is full.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STATUS_FILLED = "filled"
STATUS_PARTIAL = "partial"
STATUS_TIMEOUT = "timeout"
STATUS_MISSING_INTENT = "missing_intent"

ALL_STATUSES = (
    STATUS_FILLED, STATUS_PARTIAL, STATUS_TIMEOUT, STATUS_MISSING_INTENT,
)


class FillLogger:
    """Append a fill record to ``fills.jsonl``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def log(
        self,
        *,
        order_id: str,
        symbol: str,
        side: str,
        intent_qty: float,
        fill_qty: float,
        intent_price: Optional[float],
        fill_price: float,
        submit_ts_ms: int,
        fill_ts_ms: int,
        status: str,
    ) -> None:
        """Append one fill row. Never raises."""
        if status not in ALL_STATUSES:
            logger.warning(
                "[fill_logger] unknown status %r — writing anyway", status,
            )
        slippage_abs: Optional[float]
        slippage_bps: Optional[float]
        if (
            intent_price is not None and intent_price > 0
            and fill_price > 0 and status in (STATUS_FILLED, STATUS_PARTIAL)
        ):
            raw = fill_price - intent_price
            slippage_abs = raw if side == "Buy" else -raw
            slippage_bps = (slippage_abs / intent_price) * 10_000
        else:
            slippage_abs = None
            slippage_bps = None

        row: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ts_ms": int(time.time() * 1000),
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "intent_qty": float(intent_qty),
            "fill_qty": float(fill_qty),
            "intent_price": (
                float(intent_price) if intent_price is not None else None
            ),
            "fill_price": float(fill_price),
            "slippage_abs": slippage_abs,
            "slippage_bps": slippage_bps,
            "submit_ts_ms": int(submit_ts_ms),
            "fill_ts_ms": int(fill_ts_ms),
            "fill_lag_ms": int(fill_ts_ms - submit_ts_ms),
            "status": status,
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("[fill_logger] failed to append %s: %s",
                         self._path, exc)


__all__ = [
    "FillLogger",
    "STATUS_FILLED",
    "STATUS_PARTIAL",
    "STATUS_TIMEOUT",
    "STATUS_MISSING_INTENT",
    "ALL_STATUSES",
]
