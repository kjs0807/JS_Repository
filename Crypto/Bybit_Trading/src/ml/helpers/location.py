"""Location helpers for ML pattern features.

All functions operate on a primary-TF BarSeries and return numpy arrays of
length == len(series). Every value at index ``i`` uses only bars at indices
``< i`` (strict look-back), so these helpers are lookahead-safe and can
be cached once per series and indexed in O(1) from ``on_bar_fast``.

The location helpers currently implemented:

- ``rolling_nbar_extremes(series, n)`` — for each bar i, returns
  ``(rolling_high, rolling_low)`` over bars ``[i-n, i-1]`` inclusive.
- ``confirmed_swing_highs_lows(series, confirmation_bars)`` — for each bar
  i, returns ``(last_confirmed_swing_high, last_confirmed_swing_low)``,
  where the swing at pivot index ``p`` is only "known" at ``i >= p + confirmation_bars``.
  This matches the detector's strict left+right pivot confirmation rule.

Both are used to build ATR-normalized "distance to location" features such
as ``distance_to_prev_swing_low_atr`` or ``distance_to_rolling_20_high_atr``.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

from src.core.types import BarSeries


def rolling_nbar_extremes(
    series: BarSeries, n: int
) -> Tuple[np.ndarray, np.ndarray]:
    """Rolling N-bar high/low over the PRIOR ``n`` bars.

    ``rolling_high[i] = max(high[max(0, i-n) : i])``
    ``rolling_low[i]  = min(low[max(0, i-n) : i])``

    Index 0 is NaN because there is no history at all. If ``i - n < 0``
    the available (smaller) window is used. The current bar ``i`` is NOT
    included in its own rolling extreme — this is the "what is the recent
    range I could see before trading this bar" definition.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    highs = series.bars["high"].to_numpy()
    lows = series.bars["low"].to_numpy()
    nb = len(highs)
    out_h = np.full(nb, np.nan, dtype=float)
    out_l = np.full(nb, np.nan, dtype=float)
    for i in range(1, nb):
        lo = max(0, i - n)
        window_h = highs[lo:i]
        window_l = lows[lo:i]
        if len(window_h) > 0:
            out_h[i] = float(np.max(window_h))
            out_l[i] = float(np.min(window_l))
    return out_h, out_l


def confirmed_swing_highs_lows(
    series: BarSeries, confirmation_bars: int = 3
) -> Tuple[np.ndarray, np.ndarray]:
    """For each bar i, return (prev_confirmed_swing_high, prev_confirmed_swing_low).

    A swing at pivot index ``p`` is confirmed once ``confirmation_bars`` bars
    have passed on both sides. From the perspective of a trader at bar ``i``,
    only pivots with ``p + confirmation_bars <= i`` are known. This function
    walks the series forward and maintains running "last confirmed swing"
    prices so that ``extract_features`` can read them in O(1).

    Strict extremum rule: pivot at ``p`` must be >= (or <=) every bar in both
    its left and right confirmation windows, with at least one strict
    inequality (no duplicate-value bars confirming).
    """
    if confirmation_bars <= 0:
        raise ValueError("confirmation_bars must be positive")
    highs = series.bars["high"].to_numpy()
    lows = series.bars["low"].to_numpy()
    nb = len(highs)
    out_h = np.full(nb, np.nan, dtype=float)
    out_l = np.full(nb, np.nan, dtype=float)
    last_h = np.nan
    last_l = np.nan
    cb = int(confirmation_bars)
    for i in range(nb):
        # A pivot at p becomes confirmed when i >= p + cb. On bar i, the
        # newly-confirmed pivot is at p = i - cb. We need p - cb >= 0 for
        # a full left window and the right window runs from p+1 .. p+cb
        # which fits inside [0, i] since p + cb == i.
        p = i - cb
        if p >= cb:
            left = slice(p - cb, p)
            right = slice(p + 1, p + cb + 1)
            # High pivot check
            lh = highs[left]
            rh = highs[right]
            if len(lh) == cb and len(rh) == cb:
                center = highs[p]
                if (
                    np.all(center >= lh)
                    and np.all(center >= rh)
                    and (np.any(center > lh) or np.any(center > rh))
                ):
                    last_h = float(center)
            # Low pivot check
            ll = lows[left]
            rl = lows[right]
            if len(ll) == cb and len(rl) == cb:
                center = lows[p]
                if (
                    np.all(center <= ll)
                    and np.all(center <= rl)
                    and (np.any(center < ll) or np.any(center < rl))
                ):
                    last_l = float(center)
        out_h[i] = last_h
        out_l[i] = last_l
    return out_h, out_l


__all__ = [
    "rolling_nbar_extremes",
    "confirmed_swing_highs_lows",
]
