"""Divergence detection between price and an oscillator (e.g., RSI).

Four divergence types, all computed on confirmed pivots:
- regular_bull: price makes lower low,  indicator makes higher low
- regular_bear: price makes higher high, indicator makes lower high
- hidden_bull:  price makes higher low,  indicator makes lower low
                (uptrend continuation setup)
- hidden_bear:  price makes lower high,  indicator makes higher high
                (downtrend continuation setup)

Pivot confirmation rule
-----------------------
A local extremum at array index ``p`` is *confirmed* only once
``confirmation_bars`` bars have passed on BOTH sides of ``p``. This matches
what a trader can actually see in real time: at ``end_index``, only pivots
with ``p ≤ end_index - confirmation_bars`` are considered valid.

``detect_divergence`` fires only when a newly confirmed pivot lands exactly at
position ``(end_index - confirmation_bars)``. This turns the function into a
lookahead-safe event emitter: called on every bar, it returns non-None at
most once per pivot confirmation, never retroactively.

Lookahead safety
----------------
The function only reads ``price[:end_index+1]`` and ``indicator[:end_index+1]``.
The ``test_no_lookahead.py`` suite verifies this property on both patterns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

import numpy as np


DivergenceType = Literal[
    "regular_bull",
    "regular_bear",
    "hidden_bull",
    "hidden_bear",
]

_EPS = 1e-9
_SLOPE_RATIO_CLIP = 50.0


@dataclass(frozen=True)
class DivergenceInfo:
    """Result of a successful divergence detection.

    All index fields are absolute positions in the original price array.
    ``first_pivot_idx`` and ``second_pivot_idx`` are the only canonical index
    fields; the ``first_low_idx`` / ``second_low_idx`` / ``first_high_idx`` /
    ``second_high_idx`` properties are backward-compatibility aliases for
    code written against the v1 API.
    """

    div_type: DivergenceType
    first_pivot_idx: int
    second_pivot_idx: int
    pivot_distance_bars: int
    pivot_confirmation_lag: int
    # Raw slope components (kept so consumers can derive alternatives safely)
    price_slope: float
    rsi_slope: float
    price_diff: float
    rsi_diff: float
    price_diff_abs: float
    rsi_diff_abs: float
    # Eps-protected, |ratio|-clipped
    slope_divergence_ratio: float
    # Simple prominence of the second pivot within its confirmation window
    pivot_prominence: float
    # Retracement depth between the two pivots as fraction of the move,
    # or 0.0 if the two pivots are adjacent / degenerate
    intervening_retracement_ratio: float

    # --- backward-compat aliases (v1 names) --------------------------------
    @property
    def mode(self) -> Literal["bull", "bear"]:
        return "bull" if self.div_type in ("regular_bull", "hidden_bull") else "bear"

    @property
    def first_low_idx(self) -> int:
        return self.first_pivot_idx

    @property
    def second_low_idx(self) -> int:
        return self.second_pivot_idx

    @property
    def first_high_idx(self) -> int:
        return self.first_pivot_idx

    @property
    def second_high_idx(self) -> int:
        return self.second_pivot_idx

    @property
    def strength(self) -> float:
        return abs(self.price_slope - self.rsi_slope)


def _is_confirmed_pivot(
    arr: np.ndarray,
    i: int,
    confirmation_bars: int,
    kind: str,
) -> bool:
    """Strict local extremum test with ``confirmation_bars`` bars on both sides."""
    if i - confirmation_bars < 0 or i + confirmation_bars >= len(arr):
        return False
    left = arr[i - confirmation_bars : i]
    right = arr[i + 1 : i + confirmation_bars + 1]
    center = arr[i]
    if kind == "low":
        return bool(np.all(center <= left)) and bool(np.all(center <= right)) and bool(
            np.any(center < left) or np.any(center < right)
        )
    # high
    return bool(np.all(center >= left)) and bool(np.all(center >= right)) and bool(
        np.any(center > left) or np.any(center > right)
    )


def _find_confirmed_pivots(
    arr: np.ndarray,
    up_to: int,
    confirmation_bars: int,
    kind: str,
) -> List[int]:
    """Return confirmed pivot indices in [confirmation_bars, up_to], inclusive."""
    out: List[int] = []
    for p in range(confirmation_bars, up_to + 1):
        if _is_confirmed_pivot(arr, p, confirmation_bars, kind):
            out.append(p)
    return out


def _safe_slope_ratio(num: float, den: float) -> float:
    """num / den with epsilon protection and |ratio| clipping to ±50."""
    if abs(den) < _EPS:
        den = _EPS if den >= 0 else -_EPS
    ratio = num / den
    if ratio > _SLOPE_RATIO_CLIP:
        return _SLOPE_RATIO_CLIP
    if ratio < -_SLOPE_RATIO_CLIP:
        return -_SLOPE_RATIO_CLIP
    return ratio


def _classify(
    mode: Literal["bull", "bear"],
    price_p1: float, price_p2: float,
    ind_p1: float, ind_p2: float,
) -> Optional[DivergenceType]:
    """Classify pivot pair into one of the four divergence types, or None."""
    if mode == "bull":
        # Regular bullish: price made a lower low while the oscillator made a higher low
        if price_p2 < price_p1 and ind_p2 > ind_p1:
            return "regular_bull"
        # Hidden bullish: price made a higher low while the oscillator made a lower low
        if price_p2 > price_p1 and ind_p2 < ind_p1:
            return "hidden_bull"
        return None
    # bear
    # Regular bearish: price made a higher high while the oscillator made a lower high
    if price_p2 > price_p1 and ind_p2 < ind_p1:
        return "regular_bear"
    # Hidden bearish: price made a lower high while the oscillator made a higher high
    if price_p2 < price_p1 and ind_p2 > ind_p1:
        return "hidden_bear"
    return None


def detect_divergence(
    price: np.ndarray,
    indicator: np.ndarray,
    end_index: int,
    mode: Literal["bull", "bear"],
    confirmation_bars: int = 3,
    lookback: int = 30,
) -> Optional[DivergenceInfo]:
    """Detect a divergence whose *second* pivot just got confirmed at ``end_index``.

    The function returns non-None iff position ``end_index - confirmation_bars``
    is a confirmed pivot of the requested kind (``low`` for bull, ``high`` for
    bear) AND some prior confirmed pivot of the same kind within ``lookback``
    bars forms either a regular or hidden divergence with it.

    Parameters
    ----------
    price, indicator
        Full arrays. Only entries up to ``end_index`` are read.
    end_index
        The "now" index. The confirmation event, if any, occurs here.
    mode
        ``"bull"`` searches low pivots, ``"bear"`` searches high pivots.
    confirmation_bars
        Number of bars required on BOTH sides of a candidate pivot.
    lookback
        How many bars back from the second pivot to search for the first pivot.
    """
    n_needed = 2 * confirmation_bars + 1
    if end_index < n_needed:
        return None
    target = end_index - confirmation_bars
    if target < confirmation_bars:
        return None

    kind = "low" if mode == "bull" else "high"
    if not _is_confirmed_pivot(price, target, confirmation_bars, kind):
        return None

    earliest = max(confirmation_bars, target - lookback)
    earlier_candidates = _find_confirmed_pivots(
        price, up_to=target - 1, confirmation_bars=confirmation_bars, kind=kind,
    )
    earlier_candidates = [p for p in earlier_candidates if p >= earliest]
    if not earlier_candidates:
        return None

    # Walk candidates from most-recent to oldest; first pair that actually
    # forms a regular or hidden divergence wins. This way a non-diverging
    # most-recent pair does not cause us to miss a diverging older pair
    # that is still inside the lookback window.
    p2 = target
    p1 = -1
    div_type: Optional[DivergenceType] = None
    for cand in reversed(earlier_candidates):
        t = _classify(
            mode,
            float(price[cand]), float(price[p2]),
            float(indicator[cand]), float(indicator[p2]),
        )
        if t is not None:
            p1 = cand
            div_type = t
            break
    if div_type is None or p1 < 0:
        return None

    span = max(p2 - p1, 1)
    price_diff = float(price[p2] - price[p1])
    rsi_diff = float(indicator[p2] - indicator[p1])
    price_slope = price_diff / span
    rsi_slope = rsi_diff / span

    win = price[p2 - confirmation_bars : p2 + confirmation_bars + 1]
    if kind == "low":
        prominence = float(np.max(win) - price[p2])
    else:
        prominence = float(price[p2] - np.min(win))

    if p2 - p1 > 1:
        between = price[p1 + 1 : p2]
        if kind == "low":
            denom = float(price[p1]) - float(price[p2])
            if len(between) > 0 and denom > _EPS:
                peak = float(np.max(between))
                retrace = (peak - float(price[p2])) / denom
            else:
                retrace = 0.0
        else:
            denom = float(price[p2]) - float(price[p1])
            if len(between) > 0 and denom > _EPS:
                trough = float(np.min(between))
                retrace = (float(price[p2]) - trough) / denom
            else:
                retrace = 0.0
    else:
        retrace = 0.0

    return DivergenceInfo(
        div_type=div_type,
        first_pivot_idx=int(p1),
        second_pivot_idx=int(p2),
        pivot_distance_bars=int(p2 - p1),
        pivot_confirmation_lag=int(confirmation_bars),
        price_slope=price_slope,
        rsi_slope=rsi_slope,
        price_diff=price_diff,
        rsi_diff=rsi_diff,
        price_diff_abs=abs(price_diff),
        rsi_diff_abs=abs(rsi_diff),
        slope_divergence_ratio=_safe_slope_ratio(price_slope, rsi_slope),
        pivot_prominence=prominence,
        intervening_retracement_ratio=float(retrace),
    )


__all__ = [
    "DivergenceInfo",
    "DivergenceType",
    "detect_divergence",
]
