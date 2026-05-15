"""Pending-fill reconciliation (Stage C-2b).

Bybit V5 market orders fill within a few hundred ms, but the
``place_order`` response itself only returns ``orderId`` — not
``avgPrice`` / ``cumExecQty``. So the broker registers the order as
*pending* the moment ``_execute_order`` succeeds, and the runner
heartbeat reconciles the pending list against
``rest_client.get_order(order_id, symbol)`` later.

This decoupling matters because:

  * The order path stays fast: no second REST call inside
    ``_execute_order`` blocking on the network.
  * Reconciliation failures live in their own swim-lane and CANNOT
    feed the circuit breaker — fill telemetry is informational, not
    an order-result signal.
  * If the runner restarts mid-pending, only the in-memory entries
    are lost; the ``orders.jsonl`` audit row already captured the
    fact of submission with success.

Lifecycle for one order:

    submit  ──▶  register(order_id, intent, submit_ts)
                       │
    heartbeat ─▶  reconcile_all(rest, fill_logger)
                       │
                       ├── filled            → emit 'filled' row,    remove
                       ├── partial (>0 qty)  → keep pending unless aged out
                       ├── api error         → log WARNING,          keep
                       └── aged > max_age_ms → emit 'timeout' row,   remove

The "missing_intent" status (no bar close seeded for the symbol when
the order was submitted) is decided at registration time, not here:
the broker emits the row directly and never registers a pending
entry, since reconciliation would have nothing to compare against.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.runtime.fill_logger import (
    FillLogger, STATUS_FILLED, STATUS_PARTIAL, STATUS_TIMEOUT,
)

logger = logging.getLogger(__name__)

# A pending order older than this gets a "timeout" fill row and is
# evicted. 10 minutes is generous for a market order; partial fills
# that genuinely take longer than 10 min on linear perps are a sign of
# something else wrong upstream anyway.
DEFAULT_TIMEOUT_MS = 10 * 60 * 1000

# Reconciliation considers a fill "good enough" to emit a filled row
# when cumExecQty / intent_qty >= this ratio. Below that we treat it
# as partial fill.
FULL_FILL_RATIO = 0.99


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class PendingFill:
    order_id: str
    symbol: str
    side: str
    intent_qty: float
    intent_price: Optional[float]
    submit_ts_ms: int
    attempts: int = 0
    # C-2b hotfix: track the cumExecQty value we have already emitted
    # a row for. Re-observing the same value during the next heartbeat
    # must NOT produce a duplicate partial row.
    last_emitted_qty: float = 0.0


class FillTracker:
    """In-memory pending registry + reconcile driver."""

    def __init__(
        self, *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        clock: Optional[Any] = None,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._timeout_ms = int(timeout_ms)
        self._clock = clock or _now_ms
        self._pending: Dict[str, PendingFill] = {}

    # ------------------------------------------------------------------
    # registration / inspection
    # ------------------------------------------------------------------
    def register(
        self, *, order_id: str, symbol: str, side: str,
        intent_qty: float, intent_price: Optional[float],
        submit_ts_ms: Optional[int] = None,
    ) -> None:
        if not order_id:
            # Nothing to reconcile against.
            return
        self._pending[order_id] = PendingFill(
            order_id=order_id, symbol=symbol, side=side,
            intent_qty=float(intent_qty),
            intent_price=(
                float(intent_price) if intent_price is not None else None
            ),
            submit_ts_ms=int(submit_ts_ms) if submit_ts_ms else self._clock(),
        )

    def pending_count(self) -> int:
        return len(self._pending)

    def pending_snapshot(self) -> Tuple[PendingFill, ...]:
        return tuple(self._pending.values())

    # ------------------------------------------------------------------
    # reconciliation
    # ------------------------------------------------------------------
    def reconcile_all(
        self, rest_client: Any, fill_logger: FillLogger,
    ) -> int:
        """Walk the pending dict; emit filled / partial / timeout rows
        as appropriate. Returns the number of rows emitted on this
        pass.

        NEVER raises — caller is the runner heartbeat which must keep
        ticking even if a single reconcile probe blows up.
        """
        if not self._pending:
            return 0
        emitted = 0
        # Snapshot the keys so we can mutate the dict in-flight.
        for order_id in list(self._pending.keys()):
            try:
                if self._reconcile_one(order_id, rest_client, fill_logger):
                    emitted += 1
            except Exception as exc:
                # An exception here is a reconcile bug or a transient
                # REST hiccup — never propagate to the runner.
                logger.warning(
                    "[fill_tracker] reconcile_one(%s) raised: %s",
                    order_id, exc,
                )
        return emitted

    def _reconcile_one(
        self, order_id: str, rest_client: Any, fill_logger: FillLogger,
    ) -> bool:
        entry = self._pending.get(order_id)
        if entry is None:
            return False
        entry.attempts += 1
        now = self._clock()
        # Aging timeout — emit a timeout row and evict so we don't
        # keep polling indefinitely.
        if now - entry.submit_ts_ms > self._timeout_ms:
            fill_logger.log(
                order_id=order_id, symbol=entry.symbol, side=entry.side,
                intent_qty=entry.intent_qty, fill_qty=0.0,
                intent_price=entry.intent_price, fill_price=0.0,
                submit_ts_ms=entry.submit_ts_ms, fill_ts_ms=now,
                status=STATUS_TIMEOUT,
            )
            del self._pending[order_id]
            return True
        # REST probe.
        try:
            data = rest_client.get_order(
                order_id=order_id, symbol=entry.symbol,
            )
        except Exception as exc:
            # Keep pending for the next heartbeat; warn but don't
            # propagate.
            logger.warning(
                "[fill_tracker] get_order(%s) raised: %s — keep pending",
                order_id, exc,
            )
            return False
        if not isinstance(data, dict) or not data:
            return False
        # Bybit returns avgPrice / cumExecQty as strings; empty or "0"
        # means "not yet observable".
        try:
            avg_price = float(data.get("avgPrice", "") or 0)
        except (TypeError, ValueError):
            avg_price = 0.0
        try:
            cum_qty = float(data.get("cumExecQty", "") or 0)
        except (TypeError, ValueError):
            cum_qty = 0.0
        if avg_price <= 0 or cum_qty <= 0:
            return False
        # Decide filled vs partial. Intent qty zero (defensive) -> treat
        # as filled to avoid an unsolvable divide-by-zero in the ratio.
        if entry.intent_qty <= 0:
            ratio = 1.0
        else:
            ratio = cum_qty / entry.intent_qty
        status = STATUS_FILLED if ratio >= FULL_FILL_RATIO else STATUS_PARTIAL
        # Hotfix #2: dedup partial rows on a re-observed cumExecQty.
        # If cum_qty did not grow since the last emit, do not write
        # another row. Full fills always emit (the eviction below
        # ensures we never emit them twice anyway).
        if status == STATUS_PARTIAL and cum_qty <= entry.last_emitted_qty:
            return False
        # Hotfix #1: prefer Bybit's own updatedTime / createdTime as
        # the fill timestamp so fill_lag_ms measures real execution
        # latency, not observation lag. Fall back to tracker clock if
        # the response did not include a usable timestamp.
        fill_ts_ms = _parse_bybit_ts(
            data.get("updatedTime"),
            data.get("createdTime"),
        )
        if fill_ts_ms is None:
            fill_ts_ms = now
        # Defensive: do not allow fill_ts_ms < submit_ts_ms (clock skew
        # or stale timestamp would otherwise produce negative lag).
        if fill_ts_ms < entry.submit_ts_ms:
            fill_ts_ms = entry.submit_ts_ms
        fill_logger.log(
            order_id=order_id, symbol=entry.symbol, side=entry.side,
            intent_qty=entry.intent_qty, fill_qty=cum_qty,
            intent_price=entry.intent_price, fill_price=avg_price,
            submit_ts_ms=entry.submit_ts_ms, fill_ts_ms=fill_ts_ms,
            status=status,
        )
        entry.last_emitted_qty = cum_qty
        # Only evict on a full(-enough) fill. Partial fills stay in the
        # pending dict so a later cumExecQty bump produces another row.
        # Note: Bybit's order history may freeze cumExecQty after the
        # final fill; the aging timeout will eventually clean these up.
        if status == STATUS_FILLED:
            del self._pending[order_id]
        return True


def _parse_bybit_ts(*candidates: Any) -> Optional[int]:
    """Return the first parseable ms-epoch from Bybit's response.

    Bybit V5 timestamps come as strings of digits (ms epoch). Empty
    string, ``"0"``, or anything non-numeric is treated as missing so
    the caller can fall back to the tracker clock.
    """
    for raw in candidates:
        if raw in (None, "", "0"):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                continue
        if value > 0:
            return value
    return None


__all__ = ["FillTracker", "PendingFill", "DEFAULT_TIMEOUT_MS"]
