"""Walk-Forward retraining validator on the event cache.

Reuses EventDataset (no detect_at re-execution per fold). Each fold retrains
XGBoost on the IS slice and simulates trades on OOS by treating the cached
triple-barrier label as ground truth (1 = TP-first, 0 = SL/timeout).

In addition to the sliding-window walk-forward, this module also exposes
``evaluate_holdout`` which takes the final trained model and evaluates it
on events whose timestamp falls inside a true held-out window (by
``oos_period_ms``). The WF sliding-window and the holdout measure
different things: WF quantifies stability across retrain folds *inside*
the IS range, whereas the holdout tells you what the deployed single
model does on data it has never seen. A pattern can look robust under
WF yet fail on the real holdout — that divergence is the failure mode
the holdout evaluation is specifically designed to catch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from src.ml.event_dataset_builder import EventDataset


@dataclass
class WFConfig:
    is_window_bars: int = 1000      # event count, not bar count
    oos_window_bars: int = 200
    min_trades_per_fold: int = 10
    retrain_per_fold: bool = True
    # Number of events to skip between IS end and OOS start ("purge window").
    # Labels of events near the IS boundary may still be unresolved at the OOS
    # start because their triple-barrier outcome depends on bars up to
    # event_bar + max_holding_bars. Skipping these prevents leakage.
    # None → resolved at validate() time to max_holding_bars.
    purge_bars: Optional[int] = None


@dataclass
class ValidationReport:
    n_folds: int
    n_folds_skipped: int
    fold_breakdown: List[Dict[str, Any]]
    cv_summary: Dict[str, float]
    per_symbol_oos: Dict[str, Dict[str, float]]
    overfit: Dict[str, Any]
    feature_importance: Dict[str, float]


_FIXED_XGB_KWARGS = dict(
    objective="binary:logistic",
    eval_metric="logloss",
    verbosity=0,
    use_label_encoder=False,
    random_state=42,
)


def _fit_xgb(xgb_params: Dict[str, Any], X, y, w) -> XGBClassifier:
    model = XGBClassifier(**xgb_params, **_FIXED_XGB_KWARGS)
    model.fit(X, y, sample_weight=w)
    return model


class Validator:
    def __init__(self, wf_config: WFConfig):
        self.wf_config = wf_config

    def validate(
        self,
        dataset: EventDataset,
        best_params: Dict[str, Any],
        tp_pct: float,
        sl_pct: float,
        max_holding_bars: int = 0,
    ) -> ValidationReport:
        df = dataset.df.sort_values("timestamp_ms").reset_index(drop=True)
        threshold = float(best_params.get("threshold", 0.5))
        xgb_params = {k: v for k, v in best_params.items() if k != "threshold"}
        feat_cols = [c for c in dataset.feature_columns if c in df.columns]

        is_window = self.wf_config.is_window_bars
        oos_window = self.wf_config.oos_window_bars
        # Resolve purge window: explicit config wins, else fall back to
        # max_holding_bars (events near the IS boundary whose labels depend
        # on bars up to event_bar + max_holding_bars are still unresolved).
        if self.wf_config.purge_bars is None:
            purge = int(max_holding_bars)
        else:
            purge = int(self.wf_config.purge_bars)
        purge = max(0, purge)

        fold_breakdown: List[Dict[str, Any]] = []
        per_symbol_pnl: Dict[str, float] = {}
        per_symbol_trades: Dict[str, int] = {}
        skipped = 0
        last_model: XGBClassifier = None  # type: ignore

        i = 0
        fold_idx = 0
        while i + is_window + purge + oos_window <= len(df):
            is_slice = df.iloc[i : i + is_window]
            oos_start = i + is_window + purge
            oos_slice = df.iloc[oos_start : oos_start + oos_window]
            i += oos_window
            fold_idx += 1

            if len(oos_slice) < self.wf_config.min_trades_per_fold:
                skipped += 1
                fold_breakdown.append({
                    "fold": fold_idx,
                    "skipped": True,
                    "reason": "min_trades",
                    "oos_trades": 0,
                    "oos_pnl": 0.0,
                })
                continue

            Xt = is_slice[feat_cols].values
            yt = is_slice["label"].values
            wt = is_slice["sample_weight"].values

            # Single-class IS slice would crash XGBoost. Skip the fold
            # (treat as no trades) instead of failing the whole validation.
            if len(np.unique(yt)) < 2:
                skipped += 1
                fold_breakdown.append({
                    "fold": fold_idx,
                    "skipped": True,
                    "reason": "single_class_is",
                    "oos_trades": 0,
                    "oos_pnl": 0.0,
                })
                continue

            model = _fit_xgb(xgb_params, Xt, yt, wt)
            last_model = model

            Xv = oos_slice[feat_cols].values
            yv = oos_slice["label"].values
            proba = model.predict_proba(Xv)[:, 1]
            taken = proba >= threshold
            n_taken = int(taken.sum())
            wins = int(yv[taken].sum())
            losses = n_taken - wins
            pnl = wins * tp_pct - losses * sl_pct
            if n_taken > 0:
                per_trade_pnl = np.where(yv[taken] > 0, tp_pct, -sl_pct)
                std = float(np.std(per_trade_pnl)) if n_taken > 1 else 0.0
                sharpe = float(np.mean(per_trade_pnl) / std) if std > 1e-12 else 0.0
            else:
                sharpe = 0.0

            fold_breakdown.append({
                "fold": fold_idx,
                "skipped": False,
                "is_trades": int(len(is_slice)),
                "oos_trades": n_taken,
                "oos_wins": wins,
                "oos_pnl": float(pnl),
                "oos_sharpe": float(sharpe),
                "win_rate": float(wins / n_taken) if n_taken else 0.0,
            })

            for sym in oos_slice["symbol_id"].unique():
                m = (oos_slice["symbol_id"] == sym).values & taken
                if m.any():
                    sym_wins = int(yv[m].sum())
                    sym_losses = int(m.sum() - sym_wins)
                    sym_pnl = sym_wins * tp_pct - sym_losses * sl_pct
                    per_symbol_pnl[sym] = per_symbol_pnl.get(sym, 0.0) + float(sym_pnl)
                    per_symbol_trades[sym] = per_symbol_trades.get(sym, 0) + int(m.sum())

        scored_folds = [f for f in fold_breakdown if not f.get("skipped")]
        oos_sharpes = [f["oos_sharpe"] for f in scored_folds]
        cv_summary = {
            "oos_sharpe_mean": float(np.mean(oos_sharpes)) if oos_sharpes else 0.0,
            "oos_sharpe_std": float(np.std(oos_sharpes)) if oos_sharpes else 0.0,
            "oos_pos_pct": float(
                sum(1 for f in scored_folds if f["oos_pnl"] > 0)
                / max(len(scored_folds), 1)
            ),
            "oos_total_pnl": float(sum(f["oos_pnl"] for f in scored_folds)),
            "oos_total_trades": int(sum(f["oos_trades"] for f in scored_folds)),
            "purge_bars_used": int(purge),
        }
        per_symbol_oos = {
            sym: {
                "trades": per_symbol_trades.get(sym, 0),
                "pnl": float(per_symbol_pnl.get(sym, 0.0)),
            }
            for sym in per_symbol_trades.keys()
        }

        # Permutation overfit test
        rng = np.random.default_rng(42)
        permuted_pnls: List[float] = []
        X_all = df[feat_cols].values
        y_all = df["label"].values
        w_all = df["sample_weight"].values
        half = len(df) // 2
        if half >= 2:
            for _ in range(10):
                shuffled = rng.permutation(y_all)
                if len(np.unique(shuffled[:half])) < 2:
                    permuted_pnls.append(0.0)
                    continue
                model_p = _fit_xgb(xgb_params, X_all[:half], shuffled[:half], w_all[:half])
                proba_p = model_p.predict_proba(X_all[half:])[:, 1]
                taken_p = proba_p >= threshold
                if taken_p.sum() == 0:
                    permuted_pnls.append(0.0)
                    continue
                yp = shuffled[half:][taken_p]
                pnl_p = float(yp.sum() * tp_pct - (taken_p.sum() - yp.sum()) * sl_pct)
                permuted_pnls.append(pnl_p)
        real_pnl = cv_summary["oos_total_pnl"]
        if permuted_pnls:
            p_value = float(sum(1 for p in permuted_pnls if p >= real_pnl) / len(permuted_pnls))
        else:
            p_value = 1.0
        if p_value < 0.05:
            verdict = "CLEAN"
        elif p_value < 0.2:
            verdict = "WARNING"
        else:
            verdict = "OVERFIT"

        feat_imp: Dict[str, float] = {}
        if last_model is not None and hasattr(last_model, "feature_importances_"):
            for name, imp in zip(feat_cols, last_model.feature_importances_):
                feat_imp[name] = float(imp)

        return ValidationReport(
            n_folds=fold_idx,
            n_folds_skipped=skipped,
            fold_breakdown=fold_breakdown,
            cv_summary=cv_summary,
            per_symbol_oos=per_symbol_oos,
            overfit={"verdict": verdict, "p_value": p_value},
            feature_importance=feat_imp,
        )


@dataclass
class HoldoutReport:
    """True out-of-sample evaluation of the final trained model.

    Two verdicts are emitted on orthogonal axes:

    1. ``verdict`` is the **absolute** check: does the ML-filtered subset
       make money on its own? One of:
           HOLDOUT_PASS        -- n_trades >= min_trades and total_pnl_R > 0
                                  and win_rate >= 0.35
           HOLDOUT_FAIL        -- n_trades >= min_trades but not passing
           HOLDOUT_NO_TRADES   -- n_trades < min_trades (not enough evidence)

    2. ``filter_verdict`` is the **relative** check: does the ML threshold
       add value over taking every holdout event at threshold=0? One of:
           FILTER_VALUE_ADD       -- both win_rate and R/trade improved
           FILTER_DESTROYS        -- both win_rate and R/trade worsened
           FILTER_NEUTRAL         -- one axis up, the other down
           FILTER_NOT_APPLICABLE  -- baseline had < min_trades to compare

    The BBKC filter case (docs/superpowers/specs/ml/2026-04-14_bbkc_filter_*)
    showed why both axes are needed. A filter-type pattern can pass the
    absolute check while destroying the baseline strategy's edge (93% P&L
    loss on BBKC). ``verdict`` alone would have called BBKC deployable; the
    ``filter_verdict`` axis catches it as FILTER_DESTROYS so downstream
    ``_final_verdict`` can escalate to FAIL_FILTER_DESTROYS.
    """

    oos_period_ms: Tuple[int, int]
    n_events: int
    n_trades: int
    n_wins: int
    n_losses: int
    total_pnl_R: float
    win_rate: float
    per_symbol: Dict[str, Dict[str, float]]
    verdict: str

    # Baseline (all holdout events, no threshold filter) -- used by
    # filter_verdict to measure ML value-add vs "take everything".
    baseline_n_trades: int = 0
    baseline_n_wins: int = 0
    baseline_pnl_R: float = 0.0
    baseline_win_rate: float = 0.0
    # Deltas (ml - baseline)
    delta_win_rate: float = 0.0
    delta_pnl_per_trade_R: float = 0.0
    # n_trades / baseline_n_trades (0..1). Rejection rate = 1 - this.
    filter_efficiency: float = 0.0
    filter_verdict: str = "FILTER_NOT_APPLICABLE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "oos_period_ms": list(self.oos_period_ms),
            "n_events": int(self.n_events),
            "n_trades": int(self.n_trades),
            "n_wins": int(self.n_wins),
            "n_losses": int(self.n_losses),
            "total_pnl_R": float(self.total_pnl_R),
            "win_rate": float(self.win_rate),
            "per_symbol": self.per_symbol,
            "verdict": self.verdict,
            "baseline": {
                "n_trades": int(self.baseline_n_trades),
                "n_wins": int(self.baseline_n_wins),
                "pnl_R": float(self.baseline_pnl_R),
                "win_rate": float(self.baseline_win_rate),
            },
            "delta": {
                "win_rate": float(self.delta_win_rate),
                "pnl_per_trade_R": float(self.delta_pnl_per_trade_R),
                "filter_efficiency": float(self.filter_efficiency),
            },
            "filter_verdict": self.filter_verdict,
        }


def _derive_filter_verdict(
    ml_n_trades: int,
    ml_win_rate: float,
    ml_pnl_R: float,
    baseline_n_trades: int,
    baseline_win_rate: float,
    baseline_pnl_R: float,
    min_trades: int,
    eps: float = 1e-9,
) -> Tuple[str, float, float]:
    """Compute filter_verdict + (delta_win_rate, delta_pnl_per_trade_R).

    ``filter_verdict`` says whether applying the ML threshold improves
    *per-trade quality* compared to taking every event at threshold=0.
    The comparison is **per-trade**, not absolute: a good filter can
    legitimately reduce total trades while improving R/trade, and that
    is the value add we care about. Raw total P&L comparison would
    punish any filter that rejects anything, even a perfectly correct
    one, which is not what we want.

    Eligibility: both arms must have >= min_trades for the deltas to
    be statistically meaningful. Otherwise we return
    ``FILTER_NOT_APPLICABLE`` and leave the caller to rely on the
    absolute ``verdict`` axis alone.
    """
    if ml_n_trades < min_trades or baseline_n_trades < min_trades:
        return "FILTER_NOT_APPLICABLE", 0.0, 0.0

    baseline_r_per_trade = baseline_pnl_R / baseline_n_trades
    ml_r_per_trade = ml_pnl_R / ml_n_trades
    delta_win_rate = ml_win_rate - baseline_win_rate
    delta_r_per_trade = ml_r_per_trade - baseline_r_per_trade

    wr_up = delta_win_rate > eps
    wr_down = delta_win_rate < -eps
    r_up = delta_r_per_trade > eps
    r_down = delta_r_per_trade < -eps

    if wr_up and r_up:
        verdict = "FILTER_VALUE_ADD"
    elif wr_down and r_down:
        verdict = "FILTER_DESTROYS"
    else:
        verdict = "FILTER_NEUTRAL"
    return verdict, delta_win_rate, delta_r_per_trade


def evaluate_holdout(
    dataset: EventDataset,
    model: Any,
    threshold: float,
    oos_period_ms: Tuple[int, int],
    tp_pct: float,
    sl_pct: float,
    min_trades: int = 5,
    min_win_rate: float = 0.35,
) -> HoldoutReport:
    """Evaluate the final trained model on true holdout events.

    Two axes are measured:

    1. **ML-filtered subset**: events whose model proba >= ``threshold``.
       This is the absolute "does the deployed model make money" check.
    2. **Baseline**: every holdout event (no threshold filter). The
       filter_verdict compares the ML subset's per-trade quality
       (win_rate + R/trade) to this baseline, answering "does the
       threshold actually add value or just discard signal?".

    Both comparisons use the cached triple-barrier labels as ground
    truth; the baseline is event-level too, so it is apples-to-apples
    with the ML subset. For filter-type patterns that wrap an existing
    strategy (e.g. BBKCFilterPattern wrapping BBKCSqueeze), this
    baseline is a faithful proxy for the raw strategy's holdout
    performance *at the event level*.

    This function is deliberately cheap: no refitting, no bar-level
    backtest simulation. The point is to answer "does the deployed
    model produce a positive-expectancy subset on unseen data, AND is
    that subset better than just taking everything?"
    """
    df = dataset.df
    if df.empty:
        raise ValueError("Cannot evaluate holdout on an empty dataset.")

    start_ms, end_ms = int(oos_period_ms[0]), int(oos_period_ms[1])
    mask = (df["timestamp_ms"] >= start_ms) & (df["timestamp_ms"] < end_ms)
    oos_df = df[mask].reset_index(drop=True)

    feat_cols = [c for c in dataset.feature_columns if c in oos_df.columns]
    n_events = int(len(oos_df))

    if n_events == 0:
        return HoldoutReport(
            oos_period_ms=(start_ms, end_ms),
            n_events=0, n_trades=0, n_wins=0, n_losses=0,
            total_pnl_R=0.0, win_rate=0.0,
            per_symbol={},
            verdict="HOLDOUT_NO_TRADES",
        )

    X = oos_df[feat_cols].values
    y = oos_df["label"].values
    proba = model.predict_proba(X)[:, 1]
    taken = proba >= threshold

    # --- ML-filtered subset (absolute axis) ---
    n_trades = int(taken.sum())
    wins = int(y[taken].sum()) if n_trades > 0 else 0
    losses = n_trades - wins
    total_pnl = wins * float(tp_pct) - losses * float(sl_pct)
    win_rate = float(wins / n_trades) if n_trades > 0 else 0.0

    per_symbol: Dict[str, Dict[str, float]] = {}
    if "symbol_id" in oos_df.columns and n_trades > 0:
        sym_df = oos_df.loc[taken]
        for sym in sym_df["symbol_id"].unique():
            sym_mask = (sym_df["symbol_id"] == sym).values
            sym_wins = int(sym_df.loc[sym_mask, "label"].sum())
            sym_n = int(sym_mask.sum())
            sym_losses = sym_n - sym_wins
            per_symbol[str(sym)] = {
                "trades": sym_n,
                "wins": sym_wins,
                "losses": sym_losses,
                "pnl_R": float(sym_wins * tp_pct - sym_losses * sl_pct),
            }

    if n_trades < min_trades:
        verdict = "HOLDOUT_NO_TRADES"
    elif total_pnl > 0 and win_rate >= min_win_rate:
        verdict = "HOLDOUT_PASS"
    else:
        verdict = "HOLDOUT_FAIL"

    # --- Baseline: every holdout event, no threshold filter ---
    baseline_n_trades = int(n_events)
    baseline_wins = int(y.sum())
    baseline_losses = baseline_n_trades - baseline_wins
    baseline_pnl = baseline_wins * float(tp_pct) - baseline_losses * float(sl_pct)
    baseline_win_rate = (
        float(baseline_wins / baseline_n_trades)
        if baseline_n_trades > 0 else 0.0
    )

    filter_efficiency = (
        float(n_trades / baseline_n_trades) if baseline_n_trades > 0 else 0.0
    )

    filter_verdict, delta_wr, delta_r = _derive_filter_verdict(
        ml_n_trades=n_trades,
        ml_win_rate=win_rate,
        ml_pnl_R=total_pnl,
        baseline_n_trades=baseline_n_trades,
        baseline_win_rate=baseline_win_rate,
        baseline_pnl_R=baseline_pnl,
        min_trades=min_trades,
    )

    return HoldoutReport(
        oos_period_ms=(start_ms, end_ms),
        n_events=n_events,
        n_trades=n_trades,
        n_wins=wins,
        n_losses=losses,
        total_pnl_R=float(total_pnl),
        win_rate=win_rate,
        per_symbol=per_symbol,
        verdict=verdict,
        baseline_n_trades=baseline_n_trades,
        baseline_n_wins=baseline_wins,
        baseline_pnl_R=float(baseline_pnl),
        baseline_win_rate=baseline_win_rate,
        delta_win_rate=delta_wr,
        delta_pnl_per_trade_R=delta_r,
        filter_efficiency=filter_efficiency,
        filter_verdict=filter_verdict,
    )


__all__ = [
    "Validator", "WFConfig", "ValidationReport",
    "HoldoutReport", "evaluate_holdout",
]
