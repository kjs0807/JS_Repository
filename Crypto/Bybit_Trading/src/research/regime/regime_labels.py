"""Forward-horizon regime labels for daily divergence events.

For each event at bar index ``p``, we compute:
- ``fwd_log_return_N`` = ln(close[p+N] / close[p]) for horizons in
  ``horizons``
- ``regime_N`` = one of {"DOWN", "FLAT", "UP"} based on thresholds
  expressed in units of the *unconditional* forward-return distribution
  standard deviation.

The threshold being "unconditional sigma" is deliberate: the question
the research is trying to answer is

    "is a divergence event's forward distribution materially different
    from the base rate?"

so the class boundary must be the base rate's natural scale, not a
round number like ±1%. This keeps the comparison fair across symbols
and windows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LabelConfig:
    horizons: Tuple[int, ...] = (20, 40, 60)
    # Each regime boundary is ±k_sigma * unconditional_std
    k_sigma: float = 0.5


@dataclass
class UnconditionalStats:
    horizon: int
    mean: float
    std: float
    n: int

    def regime_for(self, fwd_ret: float, k_sigma: float) -> str:
        lo = self.mean - k_sigma * self.std
        hi = self.mean + k_sigma * self.std
        if fwd_ret < lo:
            return "DOWN"
        if fwd_ret > hi:
            return "UP"
        return "FLAT"


def compute_unconditional_stats(
    close: np.ndarray, horizons: Tuple[int, ...],
) -> Dict[int, UnconditionalStats]:
    """Unconditional forward log-return stats over the entire series.

    Used as the regime class boundary. This is the base rate — the
    thing the research is checking for deviation.
    """
    n = len(close)
    out: Dict[int, UnconditionalStats] = {}
    for h in horizons:
        if n <= h:
            out[h] = UnconditionalStats(horizon=h, mean=0.0, std=1e-9, n=0)
            continue
        rets = np.log(close[h:] / close[:-h])
        rets = rets[np.isfinite(rets)]
        if len(rets) < 10:
            out[h] = UnconditionalStats(horizon=h, mean=0.0, std=1e-9, n=int(len(rets)))
            continue
        out[h] = UnconditionalStats(
            horizon=h,
            mean=float(np.mean(rets)),
            std=float(np.std(rets, ddof=1)),
            n=int(len(rets)),
        )
    return out


def attach_forward_labels(
    events: pd.DataFrame,
    close: np.ndarray,
    cfg: Optional[LabelConfig] = None,
    uncond: Optional[Dict[int, UnconditionalStats]] = None,
) -> Tuple[pd.DataFrame, Dict[int, UnconditionalStats]]:
    """Add ``fwd_log_return_N`` and ``regime_N`` columns for each horizon.

    Events that fall within ``max(horizons)`` bars of the series end are
    dropped (no future left). This is the only row-reducing step.
    """
    if cfg is None:
        cfg = LabelConfig()
    if events.empty:
        return events, uncond or {}
    if uncond is None:
        uncond = compute_unconditional_stats(close, cfg.horizons)

    n = len(close)
    events = events.copy()
    max_h = max(cfg.horizons)
    keep_mask = events["bar_index"].to_numpy() + max_h < n
    events = events.loc[keep_mask].reset_index(drop=True)
    if events.empty:
        return events, uncond

    idxs = events["bar_index"].to_numpy(dtype=int)
    for h in cfg.horizons:
        fut = close[idxs + h]
        cur = close[idxs]
        with np.errstate(divide="ignore", invalid="ignore"):
            lr = np.log(fut / cur)
        events[f"fwd_log_return_{h}"] = lr.astype(float)
        u = uncond[h]
        regimes: List[str] = []
        for r in lr:
            if not np.isfinite(r):
                regimes.append("FLAT")
                continue
            regimes.append(u.regime_for(float(r), cfg.k_sigma))
        events[f"regime_{h}"] = regimes
    return events, uncond


__all__ = [
    "LabelConfig",
    "UnconditionalStats",
    "compute_unconditional_stats",
    "attach_forward_labels",
]
