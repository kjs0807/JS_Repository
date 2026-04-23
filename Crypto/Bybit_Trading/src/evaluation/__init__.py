"""Strategy evaluation utilities (holdout verdict + bar-level comparator).

This module centralizes the post-compare_variants evaluation code so the
scripts in ``scripts/`` stay thin. It has two responsibilities:

1. ``holdout`` -- run a strategy factory over a fixed holdout window on a
   multi-symbol universe and compute per-symbol + aggregate metrics. This
   is the rule-based experiment workhorse (holdout-first principle).
2. ``verdict`` -- compare a variant's holdout metrics to a baseline and
   emit a ``PROMOTE / CONDITIONAL_PROMOTE / KILL / NO_EDGE`` verdict.
3. ``bar_level_comparison`` -- D2 filter-type comparator
   (BacktestEngine run on raw baseline vs ML-wrapped variant) producing
   ``BAR_FILTER_{VALUE_ADD,DESTROYS,NEUTRAL,NOT_COMPARABLE}``.

The goal is that every experiment script reuses the same metric
computation and verdict rules, so promote/kill decisions are
reproducible and auditable.
"""
from src.evaluation.holdout import (
    HoldoutSpec,
    compute_metrics_from_trades,
    run_strategy_on_holdout,
    run_strategies_on_holdout,
)
from src.evaluation.verdict import (
    HoldoutVerdict,
    judge_variant_vs_baseline,
    format_verdict_line,
)

__all__ = [
    "HoldoutSpec",
    "compute_metrics_from_trades",
    "run_strategy_on_holdout",
    "run_strategies_on_holdout",
    "HoldoutVerdict",
    "judge_variant_vs_baseline",
    "format_verdict_line",
]
