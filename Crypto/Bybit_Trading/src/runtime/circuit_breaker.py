"""Stage B-5: order-failure circuit breaker.

Watches a sliding window of recent order outcomes. When the failure
rate within the window exceeds a threshold AND a minimum sample size
has been reached, the breaker trips:

  * the supplied :class:`KillSwitch` is engaged via the file-flag path,
    so subsequent NEW entries are blocked even if this process is
    restarted (the flag persists on disk).
  * an ``alert.on_error`` is fired.
  * subsequent calls to :meth:`record` keep updating stats but cannot
    re-trip (idempotent).

The breaker only touches NEW-entry outcomes - the broker wires it into
``_execute_order`` (which covers both strategy and manual entries) but
NOT into ``close()`` so existing positions stay manageable.

Clearing the tripped state is an explicit operator action: remove the
file flag in ``run_dir`` and call :meth:`reset` (or restart the bot).
"""
from __future__ import annotations

import logging
import time
from collections import Counter, deque
from typing import Callable, Deque, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Sliding-window failure-rate guard."""

    def __init__(
        self,
        kill_switch: Optional[object] = None,
        alert_manager: Optional[object] = None,
        window_seconds: float = 3600.0,
        failure_rate_threshold: float = 0.10,
        min_sample: int = 5,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if not (0.0 < failure_rate_threshold <= 1.0):
            raise ValueError("failure_rate_threshold must be in (0, 1]")
        if min_sample < 1:
            raise ValueError("min_sample must be >= 1")
        self._ks = kill_switch
        self._alert = alert_manager
        self._window_seconds = float(window_seconds)
        self._threshold = float(failure_rate_threshold)
        self._min_sample = int(min_sample)
        self._clock = clock or time.time
        # (ts_seconds, success, category)
        self._events: Deque[Tuple[float, bool, str]] = deque()
        self._tripped = False

    # ------------------------------------------------------------------
    # recording / introspection
    # ------------------------------------------------------------------
    def record(self, success: bool, category: str = "") -> None:
        now = self._clock()
        self._events.append((now, bool(success), str(category)))
        self._evict(now)
        if not self._tripped:
            self._maybe_trip(now)

    def stats(self) -> dict:
        total = len(self._events)
        failures = sum(1 for _, ok, _ in self._events if not ok)
        rate = (failures / total) if total else 0.0
        return {
            "total": total,
            "failures": failures,
            "rate": rate,
            "tripped": self._tripped,
            "window_seconds": self._window_seconds,
            "threshold": self._threshold,
            "min_sample": self._min_sample,
        }

    @property
    def tripped(self) -> bool:
        return self._tripped

    def reset(self) -> None:
        """Operator action: clear tripped state and forget history.

        The on-disk kill-switch flag is NOT removed by this method - the
        operator must delete ``run_dir/disable_new_entry.flag`` manually
        so that flipping the breaker requires a deliberate touch.
        """
        self._tripped = False
        self._events.clear()
        logger.info("[circuit_breaker] reset by operator")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _evict(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def _top_category(self) -> str:
        counter: Counter[str] = Counter(
            cat for _, ok, cat in self._events if not ok and cat
        )
        if not counter:
            return "other"
        return counter.most_common(1)[0][0]

    def _maybe_trip(self, now: float) -> None:
        total = len(self._events)
        if total < self._min_sample:
            return
        failures = sum(1 for _, ok, _ in self._events if not ok)
        rate = failures / total
        if rate < self._threshold:
            return
        # Trip.
        self._tripped = True
        top_cat = self._top_category()
        window_min = int(round(self._window_seconds / 60))
        msg = (
            f"order failure rate {rate * 100:.1f}% ({failures}/{total}) "
            f"over last {window_min}min; top category={top_cat}"
        )
        logger.critical("[circuit_breaker] TRIPPED - %s", msg)
        if self._ks is not None:
            try:
                self._ks.engage_via_file(message=f"circuit_breaker: {msg}")
            except Exception as exc:
                logger.error(
                    "[circuit_breaker] failed to engage kill switch: %s", exc,
                )
        if self._alert is not None:
            try:
                self._alert.on_error(f"circuit breaker tripped - {msg}")
            except Exception as exc:
                logger.warning(
                    "[circuit_breaker] alert delivery failed: %s", exc,
                )


__all__ = ["CircuitBreaker"]
