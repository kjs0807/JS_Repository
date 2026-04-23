"""Evaluate RSI divergence events as a daily regime signal.

This is intentionally **descriptive**, not predictive:
- No model training.
- No ``predict_proba``.
- No threshold tuning.

We take confirmed divergence events, bucket them by ``div_type``,
compute the regime-class distribution (DOWN / FLAT / UP), and compare
to the *unconditional base rate* (same distribution over all days).
The ``lift`` per regime bucket is the ratio of conditional probability
to base rate:

    lift_{type, regime} =
        P(regime | div_type=type) / P(regime | base rate)

If a divergence type has no regime information, every lift should land
near 1.0 ± noise. If the type biases forward regime, lift > 1.2 or
< 0.8 on a large-enough sample is a real signal.

The IS / OOS split is by time: first 80% of events = IS, last 20% = OOS.
If the lift pattern exists in IS and disappears in OOS, the signal is
fragile (exactly the trade-level RSI failure mode). If it persists,
there is something worth researching further — but **this module
does not connect the signal to any live strategy**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


REGIME_VALUES = ("DOWN", "FLAT", "UP")
DIV_TYPES = ("regular_bull", "regular_bear", "hidden_bull", "hidden_bear")


def _distribution(series: pd.Series) -> Dict[str, float]:
    if series.empty:
        return {r: 0.0 for r in REGIME_VALUES}
    vc = series.value_counts(normalize=True).to_dict()
    return {r: float(vc.get(r, 0.0)) for r in REGIME_VALUES}


def _compute_base_rate_from_close(
    close: np.ndarray, horizon: int,
    k_sigma: float, mu: float, sigma: float,
) -> Dict[str, float]:
    """Expected regime distribution on all days, not just event days."""
    n = len(close)
    if n <= horizon or sigma < 1e-12:
        return {r: 0.0 for r in REGIME_VALUES}
    rets = np.log(close[horizon:] / close[:-horizon])
    rets = rets[np.isfinite(rets)]
    if rets.size == 0:
        return {r: 0.0 for r in REGIME_VALUES}
    lo = mu - k_sigma * sigma
    hi = mu + k_sigma * sigma
    down = float(np.mean(rets < lo))
    up = float(np.mean(rets > hi))
    flat = 1.0 - down - up
    return {"DOWN": down, "FLAT": flat, "UP": up}


@dataclass
class TypeRegimeRow:
    split: str
    horizon: int
    div_type: str
    n_events: int
    down_pct: float
    flat_pct: float
    up_pct: float
    base_down_pct: float
    base_flat_pct: float
    base_up_pct: float
    lift_down: float
    lift_flat: float
    lift_up: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "split": self.split,
            "horizon": self.horizon,
            "div_type": self.div_type,
            "n_events": self.n_events,
            "dist": {
                "DOWN": self.down_pct,
                "FLAT": self.flat_pct,
                "UP": self.up_pct,
            },
            "base": {
                "DOWN": self.base_down_pct,
                "FLAT": self.base_flat_pct,
                "UP": self.base_up_pct,
            },
            "lift": {
                "DOWN": self.lift_down,
                "FLAT": self.lift_flat,
                "UP": self.lift_up,
            },
        }


@dataclass
class EvaluatorReport:
    symbol: str
    horizons: Tuple[int, ...]
    is_ratio: float
    n_events_total: int
    n_events_is: int
    n_events_oos: int
    rows: List[TypeRegimeRow] = field(default_factory=list)
    base_rates: Dict[int, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "horizons": list(self.horizons),
            "is_ratio": self.is_ratio,
            "n_events_total": self.n_events_total,
            "n_events_is": self.n_events_is,
            "n_events_oos": self.n_events_oos,
            "rows": [r.to_dict() for r in self.rows],
            "base_rates": {str(h): b for h, b in self.base_rates.items()},
        }


def _safe_lift(cond: float, base: float) -> float:
    if base < 1e-12:
        return 0.0
    return cond / base


def evaluate_rsi_regime(
    events: pd.DataFrame,
    close: np.ndarray,
    symbol: str,
    horizons: Sequence[int],
    unconditional_stats: Dict[int, Any],
    k_sigma: float,
    is_ratio: float = 0.8,
) -> EvaluatorReport:
    """Compute per-type regime distribution for IS / OOS and assemble
    a report.

    The IS/OOS split is chronological: the first ``is_ratio`` fraction
    of events (sorted by timestamp) is IS, the rest is OOS.
    """
    events = events.sort_values("timestamp_ms").reset_index(drop=True)
    n = len(events)
    n_is = int(n * is_ratio)
    is_df = events.iloc[:n_is]
    oos_df = events.iloc[n_is:]

    # base rates from the FULL series, used for both splits
    base_rates: Dict[int, Dict[str, float]] = {}
    for h in horizons:
        u = unconditional_stats[h]
        base_rates[h] = _compute_base_rate_from_close(
            close, horizon=h, k_sigma=k_sigma, mu=u.mean, sigma=u.std,
        )

    rows: List[TypeRegimeRow] = []
    for split_name, split_df in (("IS", is_df), ("OOS", oos_df)):
        for h in horizons:
            col = f"regime_{h}"
            if col not in split_df.columns:
                continue
            base = base_rates[h]
            for dt in DIV_TYPES:
                mask = split_df["div_type"] == dt
                sub = split_df.loc[mask, col]
                dist = _distribution(sub)
                row = TypeRegimeRow(
                    split=split_name,
                    horizon=h,
                    div_type=dt,
                    n_events=int(len(sub)),
                    down_pct=dist["DOWN"],
                    flat_pct=dist["FLAT"],
                    up_pct=dist["UP"],
                    base_down_pct=base["DOWN"],
                    base_flat_pct=base["FLAT"],
                    base_up_pct=base["UP"],
                    lift_down=_safe_lift(dist["DOWN"], base["DOWN"]),
                    lift_flat=_safe_lift(dist["FLAT"], base["FLAT"]),
                    lift_up=_safe_lift(dist["UP"], base["UP"]),
                )
                rows.append(row)

    return EvaluatorReport(
        symbol=symbol,
        horizons=tuple(horizons),
        is_ratio=is_ratio,
        n_events_total=n,
        n_events_is=int(n_is),
        n_events_oos=int(n - n_is),
        rows=rows,
        base_rates=base_rates,
    )


__all__ = [
    "TypeRegimeRow",
    "EvaluatorReport",
    "evaluate_rsi_regime",
    "REGIME_VALUES",
    "DIV_TYPES",
]
