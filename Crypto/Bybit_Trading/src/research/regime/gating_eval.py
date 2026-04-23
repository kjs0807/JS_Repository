"""Research-only regime gating simulation.

**Not for production.** This module simulates what *would* happen to a
reference strategy's forward returns if entries were gated by the RSI
regime state. The point is to answer the question:

    "If we treated the RSI regime artifact as an entry filter,
    does the reference strategy's forward-horizon return profile
    improve materially?"

This is deliberately NOT a backtest. It runs on the same daily bars,
with the same forward-horizon returns we already computed for the
regime labels. The gating experiment measures how many forward
returns remain, what their mean/std are, and the implied Sharpe of
the gated subset versus the unconditional set.

Why separate from ``src/evaluation``
------------------------------------
- ``src/evaluation/`` is the trade-level verdict layer; it operates
  on BacktestEngine outputs and per-variant comparisons for live
  strategies.
- This file is a paper sketch. It never touches BacktestEngine, never
  calls a broker, never produces a TradeRecord. It operates purely on
  forward-log-return series.
- Keeping them separate enforces protocol §P9: research cannot silently
  become a production comparator.

Inputs
------
- ``events`` — events DataFrame with ``timestamp_ms``, ``bar_index``,
  ``div_type``, ``fwd_log_return_N`` columns. Produced by
  ``scripts/train_rsi_regime.py``.
- ``gating_rules`` — dict mapping ``(div_type, horizon)`` to an allow
  direction: ``"long"`` / ``"short"`` / ``"block"``. Missing entries
  are treated as ``"allow"`` (passthrough).

Output
------
``GatingSimulationResult`` — per-rule and aggregate stats with
sign-flipped returns for short directions so everything aggregates as
"realized forward return under the gating policy".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import numpy as np
import pandas as pd


GatingDirection = str  # one of "long", "short", "block", "allow"


@dataclass
class GatingRule:
    div_type: str
    horizon: int
    direction: GatingDirection  # long | short | block | allow


@dataclass
class GatingStats:
    label: str
    n_events: int
    mean_fwd: float
    std_fwd: float
    sharpe: float
    win_rate: float  # P(fwd > 0)
    median_fwd: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "n_events": self.n_events,
            "mean_fwd": self.mean_fwd,
            "std_fwd": self.std_fwd,
            "sharpe": self.sharpe,
            "win_rate": self.win_rate,
            "median_fwd": self.median_fwd,
        }


@dataclass
class GatingSimulationResult:
    horizon: int
    unconditional: GatingStats
    gated: GatingStats
    per_rule: Dict[str, GatingStats] = field(default_factory=dict)
    rules_used: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "horizon": self.horizon,
            "unconditional": self.unconditional.to_dict(),
            "gated": self.gated.to_dict(),
            "per_rule": {k: v.to_dict() for k, v in self.per_rule.items()},
            "rules_used": self.rules_used,
        }


def _stats(returns: np.ndarray, label: str) -> GatingStats:
    n = int(len(returns))
    if n == 0:
        return GatingStats(
            label=label, n_events=0, mean_fwd=0.0, std_fwd=0.0,
            sharpe=0.0, win_rate=0.0, median_fwd=0.0,
        )
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1)) if n > 1 else 0.0
    sharpe = float(mean / std) if std > 1e-12 else 0.0
    win = float(np.mean(returns > 0.0))
    median = float(np.median(returns))
    return GatingStats(
        label=label, n_events=n, mean_fwd=mean, std_fwd=std,
        sharpe=sharpe, win_rate=win, median_fwd=median,
    )


def simulate_gating(
    events: pd.DataFrame,
    horizon: int,
    rules: Iterable[GatingRule],
    baseline_close: np.ndarray,
) -> GatingSimulationResult:
    """Apply a gating policy to the event-level forward returns.

    Semantics:
    - A rule maps ``(div_type, horizon)`` to a direction. At an event:
      - ``long``  → keep the forward return as-is
      - ``short`` → flip the sign (shorting is profitable when price falls)
      - ``block`` → drop the event entirely
      - ``allow`` → same as ``long`` (default)
    - The **gated** bucket is the union of kept returns across all rules.
    - The **unconditional** bucket is the full population of daily
      forward returns computed from ``baseline_close``, NOT only event
      days. This is the fair comparison: "gating vs doing nothing".
    """
    fwd_col = f"fwd_log_return_{horizon}"
    if fwd_col not in events.columns:
        raise ValueError(f"events missing column {fwd_col}")

    # Build a fast lookup for rules
    rule_map: Dict[Tuple[str, int], GatingDirection] = {
        (r.div_type, int(r.horizon)): r.direction for r in rules
    }

    # Unconditional distribution: every daily forward return, all days
    n_close = len(baseline_close)
    if n_close <= horizon:
        uncond_returns = np.array([], dtype=float)
    else:
        uncond_returns = np.log(
            baseline_close[horizon:] / baseline_close[:-horizon]
        )
        uncond_returns = uncond_returns[np.isfinite(uncond_returns)]

    # Gated subset — iterate events, apply rule per div_type
    kept: List[float] = []
    per_rule_rows: Dict[str, List[float]] = {}
    rules_used: List[Dict[str, Any]] = []
    for row in events.itertuples(index=False):
        direction = rule_map.get(
            (str(row.div_type), int(horizon)), "allow",
        )
        if direction == "block":
            continue
        fwd = float(getattr(row, fwd_col))
        if not np.isfinite(fwd):
            continue
        if direction == "short":
            fwd = -fwd
        kept.append(fwd)
        per_rule_rows.setdefault(
            f"{row.div_type}[{direction}]", [],
        ).append(fwd)

    # Record the actual rules applied (for audit)
    for (dt, h), direction in rule_map.items():
        rules_used.append({
            "div_type": dt,
            "horizon": h,
            "direction": direction,
        })

    gated = _stats(np.asarray(kept, dtype=float), label="gated")
    uncond = _stats(uncond_returns, label="unconditional")
    per_rule_stats = {
        k: _stats(np.asarray(v, dtype=float), label=k)
        for k, v in per_rule_rows.items()
    }
    return GatingSimulationResult(
        horizon=horizon,
        unconditional=uncond,
        gated=gated,
        per_rule=per_rule_stats,
        rules_used=rules_used,
    )


# Default gating rules derived from BTCUSDT 2021-2026 cross-window
# strong lift analysis. Documented in:
#   docs/.../2026-04-14_rsi_regime_go_decision.md §2
#
# THIS IS A RESEARCH PRIOR, NOT A DEPLOYMENT POLICY. Modify freely; the
# simulator reports results per-rule so drift in the prior is visible.
DEFAULT_GATING_RULES_BTC_RESEARCH: List[GatingRule] = [
    # regular bear → forward UP regime suppressed → short bias short-TF
    GatingRule("regular_bear", 20, "short"),
    GatingRule("regular_bear", 40, "short"),
    # hidden bull → DOWN regime suppressed → long continuation
    GatingRule("hidden_bull", 20, "long"),
    GatingRule("hidden_bull", 40, "long"),
    # regular bull longer horizon → DOWN regime elevated → mean reversion short
    GatingRule("regular_bull", 60, "short"),
    # hidden bear → block (weak signal, keep out)
    GatingRule("hidden_bear", 20, "block"),
    GatingRule("hidden_bear", 40, "block"),
    GatingRule("hidden_bear", 60, "block"),
]


__all__ = [
    "GatingDirection",
    "GatingRule",
    "GatingStats",
    "GatingSimulationResult",
    "simulate_gating",
    "DEFAULT_GATING_RULES_BTC_RESEARCH",
]
