"""Regime output contract — what a strategy *would* consume later.

**Research-only.** No strategy in ``src/strategies/`` is allowed to
import from this module until the GO/NO-GO 합류 조건이 충족된다
(``docs/.../2026-04-14_parallel_workflow.md §B``).

The point of defining the contract now, before any strategy connection,
is to lock down what a regime artifact looks like. Once the contract
is stable, a future gating implementation has a fixed schema to write
against and this module's producers / consumers can be swapped
independently.

Shape
-----
``RegimeState``: one of ``UP`` / ``FLAT`` / ``DOWN`` / ``UNKNOWN``.
``RegimeOutput``: a timestamped, lookahead-safe record with:
- ``asof_ms``      — the bar timestamp on which this regime is declared
- ``valid_from_ms``— first bar at which a consumer may act on this state
- ``valid_until_ms``— last bar at which this state is still binding
- ``symbol``       — which market this regime applies to
- ``state``        — RegimeState
- ``score``        — continuous strength in [-1, +1]; sign matches state
- ``confidence``   — [0, 1]; reserved for future calibration
- ``horizon_bars`` — N-bar forward horizon the state was measured on
- ``source``       — "rsi_divergence_daily_v1" etc., for audit

Lookahead safety invariants
---------------------------
Producers MUST satisfy:

1. ``valid_from_ms > asof_ms`` — a regime can't take effect on the same
   bar whose close it was computed from (would be lookahead).
2. ``valid_until_ms = valid_from_ms + horizon_bars * bar_duration_ms``.
3. Regime is produced on confirmed pivots only; for RSI divergence the
   confirmation lag is ``confirmation_bars`` daily bars, so
   ``valid_from_ms = asof_ms + confirmation_bars * 86_400_000``.

Consumers MUST:
- Only look up ``state`` for a query timestamp ``t`` where
  ``valid_from_ms <= t <= valid_until_ms``.
- Treat absent records as ``UNKNOWN`` and fall back to their own logic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class RegimeState(str, Enum):
    UP = "UP"
    FLAT = "FLAT"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeOutput:
    asof_ms: int
    valid_from_ms: int
    valid_until_ms: int
    symbol: str
    state: RegimeState
    score: float
    confidence: float
    horizon_bars: int
    source: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RegimeOutput":
        return cls(
            asof_ms=int(d["asof_ms"]),
            valid_from_ms=int(d["valid_from_ms"]),
            valid_until_ms=int(d["valid_until_ms"]),
            symbol=str(d["symbol"]),
            state=RegimeState(d["state"]),
            score=float(d["score"]),
            confidence=float(d.get("confidence", 0.0)),
            horizon_bars=int(d["horizon_bars"]),
            source=str(d.get("source", "unknown")),
        )


# -----------------------------------------------------------------------
# Utility: build a RegimeOutput from a divergence event.
# -----------------------------------------------------------------------

_DAY_MS = 86_400_000


def make_rsi_regime_output(
    event_row: Dict[str, Any],
    horizon_bars: int,
    confirmation_bars: int,
    lift_score: float,
    confidence: float = 0.0,
) -> RegimeOutput:
    """Convert a single divergence event into a RegimeOutput record.

    ``event_row`` is one row from the event DataFrame emitted by
    ``src/research/regime/divergence_events.py``. ``lift_score`` is a
    caller-supplied continuous signal in [-1, +1] that the gating
    experiment decides based on the div_type's historical lift pattern
    (e.g. regular_bear → negative score, hidden_bull → positive).

    The state is derived from the sign of ``lift_score`` only —
    consumers should not infer magnitude from the boundary itself.
    """
    div_type = str(event_row["div_type"])
    asof = int(event_row["timestamp_ms"])
    valid_from = asof + confirmation_bars * _DAY_MS
    valid_until = valid_from + horizon_bars * _DAY_MS

    if lift_score > 0.1:
        state = RegimeState.UP
    elif lift_score < -0.1:
        state = RegimeState.DOWN
    else:
        state = RegimeState.FLAT

    return RegimeOutput(
        asof_ms=asof,
        valid_from_ms=valid_from,
        valid_until_ms=valid_until,
        symbol=str(event_row.get("symbol", "UNKNOWN")),
        state=state,
        score=float(lift_score),
        confidence=float(confidence),
        horizon_bars=int(horizon_bars),
        source=f"rsi_divergence_daily_v1:{div_type}",
    )


def serialize_outputs(outputs: List[RegimeOutput]) -> List[Dict[str, Any]]:
    return [o.to_dict() for o in outputs]


__all__ = [
    "RegimeState",
    "RegimeOutput",
    "make_rsi_regime_output",
    "serialize_outputs",
]
