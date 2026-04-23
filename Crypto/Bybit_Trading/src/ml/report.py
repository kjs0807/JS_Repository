"""Failure Report builder.

Sections:
- artifacts: run identification + paths (passed in by caller)
- metrics: hard numbers (CV, WF, overfit, importance, per-symbol, holdout)
- facts: derived stats (no interpretation)
- hints: heuristic interpretations (LLM should be able to filter these out)
- verdict: PASS / WARNING / FAIL summary string, holdout-aware

The WF walk-forward is computed on the IS range (sliding windows). The
holdout, when provided, is the final trained model's evaluation on a
disjoint window of events the model has never seen. final_verdict
combines both — a WARNING/CLEAN WF can still be overridden to FAIL if
the holdout section fails, which is what the 4h RSI Divergence case
exposed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.ml.validator import HoldoutReport, ValidationReport


def build_report(
    validation: ValidationReport,
    artifacts: Dict[str, Any],
    train_summary: Dict[str, Any],
    holdout: Optional[HoldoutReport] = None,
) -> Dict[str, Any]:
    metrics = {
        "cv": {
            "objective": train_summary.get("objective", "expectancy"),
            "best_value": train_summary.get("cv_objective", None),
        },
        "walk_forward": {
            "n_folds": validation.n_folds,
            "n_folds_skipped": validation.n_folds_skipped,
            "fold_breakdown": validation.fold_breakdown,
            **validation.cv_summary,
        },
        "per_symbol_oos": validation.per_symbol_oos,
        "feature_importance": validation.feature_importance,
        "overfit": validation.overfit,
    }
    if holdout is not None:
        metrics["holdout"] = holdout.to_dict()

    sorted_imp = sorted(validation.feature_importance.items(), key=lambda kv: kv[1])
    weakest = [name for name, _ in sorted_imp[:5]]

    facts = {
        "n_zero_trade_folds": sum(
            1 for f in validation.fold_breakdown
            if not f.get("skipped") and f.get("oos_trades", 0) == 0
        ),
        "n_skipped_folds": validation.n_folds_skipped,
        "weakest_features_by_importance": weakest,
        "high_loss_clusters": _find_loss_clusters(validation.fold_breakdown),
        "symbol_imbalance": _symbol_imbalance(validation.per_symbol_oos),
    }

    hints = {
        "possible_failure_modes": _hint_failure_modes(validation),
        "suggested_directions": _hint_directions(validation),
    }

    verdict = _final_verdict(validation, holdout)

    return {
        "artifacts": artifacts,
        "metrics": metrics,
        "facts": facts,
        "hints": hints,
        "verdict": verdict,
    }


def _find_loss_clusters(fold_breakdown: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for f in fold_breakdown:
        if f.get("skipped"):
            continue
        if f.get("oos_pnl", 0.0) < 0 and f.get("oos_trades", 0) > 0:
            clusters.append({
                "fold": f["fold"],
                "trades": f.get("oos_trades", 0),
                "pnl": f.get("oos_pnl", 0.0),
                "win_rate": f.get("win_rate", 0.0),
            })
    return clusters


def _symbol_imbalance(per_symbol: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    if not per_symbol:
        return {}
    totals = {s: d.get("trades", 0) for s, d in per_symbol.items()}
    grand = sum(totals.values()) or 1
    max_sym = max(totals.items(), key=lambda kv: kv[1])
    return {
        "max_share": float(max_sym[1] / grand),
        "symbol": max_sym[0],
    }


def _hint_failure_modes(val: ValidationReport) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    pos_pct = val.cv_summary.get("oos_pos_pct", 0.0)
    if pos_pct < 0.5:
        out.append({
            "type": "low_oos_consistency",
            "evidence": f"oos_pos_pct={pos_pct:.2f} below 0.5",
            "confidence": "medium",
        })
    if val.overfit.get("verdict") == "OVERFIT":
        out.append({
            "type": "permutation_overfit",
            "evidence": f"p_value={val.overfit.get('p_value', 0):.3f}",
            "confidence": "high",
        })
    return out


def _hint_directions(val: ValidationReport) -> List[str]:
    out: List[str] = []
    pos_pct = val.cv_summary.get("oos_pos_pct", 0.0)
    if pos_pct < 0.5:
        out.append("Consider adding regime filter (ADX or volatility-based)")
    if val.overfit.get("verdict") == "OVERFIT":
        out.append("Reduce HPO trial count or constrain parameter ranges")
    if val.cv_summary.get("oos_sharpe_mean", 0) < 1.0:
        out.append("Re-examine label config (tp/sl/max_holding)")
    return out


def _final_verdict(
    val: ValidationReport, holdout: Optional[HoldoutReport] = None
) -> str:
    """Combined WF + holdout + filter verdict.

    Priority order:
    1. WF permutation test overfit -> FAIL (unchanged from legacy)
    2. Holdout explicitly failed -> FAIL (overrides WF WARNING/PASS)
    3. Holdout had too few trades -> WARNING_HOLDOUT_NO_TRADES
    4. Holdout passed BUT filter_verdict=FILTER_DESTROYS ->
       FAIL_FILTER_DESTROYS (the BBKC filter case: absolutely
       profitable but destroys per-trade quality vs the no-filter
       baseline, which for filter-type patterns means the ML is
       subtracting value from the underlying strategy)
    5. Holdout passed AND filter_verdict=FILTER_NEUTRAL ->
       WARNING_FILTER_NEUTRAL (one axis improved, the other worsened)
    6. Holdout passed AND filter_verdict=FILTER_VALUE_ADD AND WF strong
       -> PASS
    7. Holdout passed AND filter_verdict=FILTER_VALUE_ADD AND WF mixed
       -> WARNING
    8. Holdout passed AND filter_verdict=FILTER_NOT_APPLICABLE (baseline
       too few trades to compare) -> fall back to absolute-only path
       (legacy behavior)
    9. No holdout provided -> legacy WF-only logic
    """
    overfit_v = val.overfit.get("verdict", "WARNING")
    if overfit_v == "OVERFIT":
        return "FAIL"
    sharpe = val.cv_summary.get("oos_sharpe_mean", 0)
    pos_pct = val.cv_summary.get("oos_pos_pct", 0)

    if holdout is not None:
        if holdout.verdict == "HOLDOUT_FAIL":
            return "FAIL"
        if holdout.verdict == "HOLDOUT_NO_TRADES":
            return "WARNING_HOLDOUT_NO_TRADES"
        # HOLDOUT_PASS from here down; filter_verdict can still downgrade.
        filter_v = getattr(holdout, "filter_verdict", "FILTER_NOT_APPLICABLE")
        if filter_v == "FILTER_DESTROYS":
            return "FAIL_FILTER_DESTROYS"
        if filter_v == "FILTER_NEUTRAL":
            return "WARNING_FILTER_NEUTRAL"
        # FILTER_VALUE_ADD or FILTER_NOT_APPLICABLE (baseline too small).
        if sharpe >= 1.0 and pos_pct >= 0.6 and overfit_v == "CLEAN":
            return "PASS"
        return "WARNING"

    # Legacy WF-only path (no holdout provided)
    if sharpe >= 1.0 and pos_pct >= 0.6 and overfit_v == "CLEAN":
        return "PASS"
    return "WARNING"


__all__ = ["build_report"]
