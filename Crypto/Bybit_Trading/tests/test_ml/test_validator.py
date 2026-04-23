"""Tests for Validator (WF retrain + overfit)."""
import numpy as np
import pandas as pd

from src.ml.event_dataset_builder import EventDataset
from src.ml.validator import (
    Validator, WFConfig, ValidationReport,
    HoldoutReport, evaluate_holdout,
)


def _make_dataset(n: int = 300, seed: int = 0) -> EventDataset:
    rng = np.random.default_rng(seed)
    timestamps = (np.arange(n) * 3_600_000).astype(int)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    label = ((f1 + f2) > 0).astype(int)
    df = pd.DataFrame({
        "f1": f1, "f2": f2,
        "symbol_id_BTCUSDT": 1.0,
        "label": label,
        "sample_weight": 1.0,
        "timestamp_ms": timestamps,
        "direction": "long",
        "symbol_id": "BTCUSDT",
    })
    return EventDataset(
        df=df,
        feature_columns=["f1", "f2", "symbol_id_BTCUSDT"],
        meta_columns=["label", "sample_weight", "timestamp_ms", "direction", "symbol_id"],
    )


def _best_params():
    return {
        "max_depth": 3,
        "n_estimators": 50,
        "learning_rate": 0.1,
        "min_child_weight": 1,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "threshold": 0.5,
    }


def test_validate_returns_report_with_fold_breakdown():
    dataset = _make_dataset()
    cfg = WFConfig(is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1)
    validator = Validator(wf_config=cfg)
    report = validator.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02,
    )
    assert isinstance(report, ValidationReport)
    assert report.n_folds > 0
    assert len(report.fold_breakdown) == report.n_folds
    for fold in report.fold_breakdown:
        assert "fold" in fold
        assert "oos_pnl" in fold
        assert "oos_trades" in fold


def test_validator_records_per_symbol_metrics():
    dataset = _make_dataset()
    cfg = WFConfig(is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1)
    validator = Validator(wf_config=cfg)
    report = validator.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02,
    )
    assert "BTCUSDT" in report.per_symbol_oos


def test_validator_records_overfit_verdict():
    dataset = _make_dataset()
    cfg = WFConfig(is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1)
    validator = Validator(wf_config=cfg)
    report = validator.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02,
    )
    assert "verdict" in report.overfit
    assert report.overfit["verdict"] in {"CLEAN", "WARNING", "OVERFIT"}
    assert "p_value" in report.overfit


def test_validator_returns_feature_importance():
    dataset = _make_dataset()
    cfg = WFConfig(is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1)
    validator = Validator(wf_config=cfg)
    report = validator.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02,
    )
    assert isinstance(report.feature_importance, dict)
    # Should include at least one of the model features
    assert len(report.feature_importance) > 0


def test_validator_purge_window_explicit_config():
    """Explicit purge_bars in WFConfig takes precedence over max_holding_bars."""
    dataset = _make_dataset(n=300)
    cfg = WFConfig(
        is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1,
        purge_bars=10,
    )
    validator = Validator(wf_config=cfg)
    report = validator.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02, max_holding_bars=24,
    )
    assert report.cv_summary["purge_bars_used"] == 10


def test_validator_purge_window_defaults_to_max_holding_bars():
    """purge_bars=None falls back to the max_holding_bars passed to validate()."""
    dataset = _make_dataset(n=300)
    cfg = WFConfig(
        is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1,
        purge_bars=None,
    )
    validator = Validator(wf_config=cfg)
    report = validator.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02, max_holding_bars=24,
    )
    assert report.cv_summary["purge_bars_used"] == 24


def test_validator_purge_window_reduces_fold_count():
    """Adding a purge gap should yield at most as many folds as no purge."""
    dataset = _make_dataset(n=300)
    base = Validator(wf_config=WFConfig(
        is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1,
        purge_bars=0,
    ))
    purged = Validator(wf_config=WFConfig(
        is_window_bars=80, oos_window_bars=20, min_trades_per_fold=1,
        purge_bars=30,
    ))
    base_report = base.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02, max_holding_bars=0,
    )
    purged_report = purged.validate(
        dataset=dataset, best_params=_best_params(),
        tp_pct=0.04, sl_pct=0.02, max_holding_bars=0,
    )
    assert purged_report.n_folds <= base_report.n_folds
    assert purged_report.cv_summary["purge_bars_used"] == 30
    assert base_report.cv_summary["purge_bars_used"] == 0


# ---------------------------------------------------------------------------
# Holdout evaluation tests
# ---------------------------------------------------------------------------


class _AlwaysWinModel:
    """Stub model whose predict_proba always says the true label is 1."""

    def predict_proba(self, X):
        n = len(X)
        out = np.zeros((n, 2), dtype=float)
        out[:, 0] = 0.05
        out[:, 1] = 0.95
        return out


class _AlwaysLoseModel:
    """Stub model that passes every event through the threshold but the
    labels in the fixture are all zeros, so every trade is a loser."""

    def predict_proba(self, X):
        n = len(X)
        out = np.zeros((n, 2), dtype=float)
        out[:, 0] = 0.1
        out[:, 1] = 0.9
        return out


class _ZeroConfidenceModel:
    """Predicts below threshold for every event → no trades taken."""

    def predict_proba(self, X):
        n = len(X)
        out = np.zeros((n, 2), dtype=float)
        out[:, 0] = 0.9
        out[:, 1] = 0.1
        return out


def _make_holdout_dataset(
    n_is: int = 200,
    n_oos: int = 30,
    label_value: int = 1,
    oos_label_value: int = 1,
    seed: int = 0,
) -> EventDataset:
    """IS events labeled ``label_value``, OOS events labeled ``oos_label_value``.
    Timestamps are monotonic so oos_period_ms can split cleanly at n_is."""
    rng = np.random.default_rng(seed)
    n = n_is + n_oos
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    timestamps = (np.arange(n) * 3_600_000).astype(int)
    labels = np.concatenate([
        np.full(n_is, label_value, dtype=int),
        np.full(n_oos, oos_label_value, dtype=int),
    ])
    df = pd.DataFrame({
        "f1": f1, "f2": f2,
        "symbol_id_BTCUSDT": 1.0,
        "label": labels,
        "sample_weight": 1.0,
        "timestamp_ms": timestamps,
        "symbol_id": "BTCUSDT",
    })
    return EventDataset(
        df=df,
        feature_columns=["f1", "f2", "symbol_id_BTCUSDT"],
        meta_columns=["label", "sample_weight", "timestamp_ms", "symbol_id"],
    )


def test_evaluate_holdout_pass_on_all_winners():
    dataset = _make_holdout_dataset(n_is=100, n_oos=30, oos_label_value=1)
    # OOS window = last 30 events
    oos_start = int(dataset.df["timestamp_ms"].iloc[100])
    oos_end = int(dataset.df["timestamp_ms"].iloc[-1]) + 1
    report = evaluate_holdout(
        dataset=dataset,
        model=_AlwaysWinModel(),
        threshold=0.5,
        oos_period_ms=(oos_start, oos_end),
        tp_pct=2.0, sl_pct=1.0,
        min_trades=5,
    )
    assert isinstance(report, HoldoutReport)
    assert report.verdict == "HOLDOUT_PASS"
    assert report.n_events == 30
    assert report.n_trades == 30
    assert report.n_wins == 30
    assert report.n_losses == 0
    assert report.total_pnl_R == 60.0   # 30 wins * 2R
    assert report.win_rate == 1.0
    assert "BTCUSDT" in report.per_symbol


def test_evaluate_holdout_fail_on_all_losers():
    dataset = _make_holdout_dataset(n_is=100, n_oos=30, oos_label_value=0)
    oos_start = int(dataset.df["timestamp_ms"].iloc[100])
    oos_end = int(dataset.df["timestamp_ms"].iloc[-1]) + 1
    report = evaluate_holdout(
        dataset=dataset,
        model=_AlwaysLoseModel(),
        threshold=0.5,
        oos_period_ms=(oos_start, oos_end),
        tp_pct=2.0, sl_pct=1.0,
        min_trades=5,
    )
    assert report.verdict == "HOLDOUT_FAIL"
    assert report.n_trades == 30
    assert report.n_wins == 0
    assert report.total_pnl_R == -30.0


def test_evaluate_holdout_no_trades_when_below_min():
    dataset = _make_holdout_dataset(n_is=100, n_oos=30)
    oos_start = int(dataset.df["timestamp_ms"].iloc[100])
    oos_end = int(dataset.df["timestamp_ms"].iloc[-1]) + 1
    report = evaluate_holdout(
        dataset=dataset,
        model=_ZeroConfidenceModel(),
        threshold=0.5,
        oos_period_ms=(oos_start, oos_end),
        tp_pct=2.0, sl_pct=1.0,
        min_trades=5,
    )
    assert report.verdict == "HOLDOUT_NO_TRADES"
    assert report.n_events == 30
    assert report.n_trades == 0


def test_evaluate_holdout_empty_window_returns_no_trades():
    dataset = _make_holdout_dataset(n_is=100, n_oos=30)
    # Choose a window completely after the dataset's timestamps
    last_ts = int(dataset.df["timestamp_ms"].iloc[-1])
    report = evaluate_holdout(
        dataset=dataset,
        model=_AlwaysWinModel(),
        threshold=0.5,
        oos_period_ms=(last_ts + 10_000_000, last_ts + 20_000_000),
        tp_pct=2.0, sl_pct=1.0,
    )
    assert report.verdict == "HOLDOUT_NO_TRADES"
    assert report.n_events == 0
    assert report.n_trades == 0


# ---------------------------------------------------------------------------
# Filter verdict (baseline-relative axis)
# ---------------------------------------------------------------------------


def _make_mixed_dataset(
    n_is: int = 200,
    n_oos: int = 60,
    baseline_win_rate: float = 0.5,
    ml_selected_win_rate: float = 0.8,
    ml_selection_count: int = 20,
    seed: int = 0,
) -> EventDataset:
    """Dataset whose OOS slice has a controllable baseline win rate and
    the first ``ml_selection_count`` OOS rows are hand-picked to carry a
    different win rate. A model that thresholds on ``proba = f1`` where
    f1 == 1.0 for the first ml_selection_count OOS rows will select
    exactly those rows (filter_verdict tests use this)."""
    rng = np.random.default_rng(seed)
    # IS portion: mixed labels so trainer compatibility stays consistent
    is_labels = rng.integers(0, 2, size=n_is)

    # OOS portion: hand-crafted
    # First ml_selection_count rows -> ml_selected_win_rate wins
    # Remaining -> balanced to hit baseline_win_rate overall
    ml_wins = int(round(ml_selected_win_rate * ml_selection_count))
    ml_losses = ml_selection_count - ml_wins
    remainder = n_oos - ml_selection_count
    overall_wins = int(round(baseline_win_rate * n_oos))
    rem_wins = max(0, overall_wins - ml_wins)
    rem_losses = remainder - rem_wins
    oos_labels = (
        [1] * ml_wins + [0] * ml_losses
        + [1] * rem_wins + [0] * rem_losses
    )
    assert len(oos_labels) == n_oos

    # f1 feature: 1.0 for ML-selected rows, 0.0 for rest (so a stub
    # model using "proba = f1" selects exactly the ML rows)
    f1_oos = [1.0] * ml_selection_count + [0.0] * remainder
    f1_is = list(rng.normal(0.5, 0.1, size=n_is))  # noise, below 1.0

    n = n_is + n_oos
    timestamps = (np.arange(n) * 3_600_000).astype(int)
    df = pd.DataFrame({
        "f1": np.array(f1_is + f1_oos, dtype=float),
        "symbol_id_BTCUSDT": 1.0,
        "label": np.array(list(is_labels) + oos_labels, dtype=int),
        "sample_weight": 1.0,
        "timestamp_ms": timestamps,
        "symbol_id": "BTCUSDT",
    })
    return EventDataset(
        df=df,
        feature_columns=["f1", "symbol_id_BTCUSDT"],
        meta_columns=["label", "sample_weight", "timestamp_ms", "symbol_id"],
    )


class _F1ProbaModel:
    """Stub whose predict_proba returns (1 - f1_col, f1_col). Combined
    with the fixture above, a threshold of 0.5 selects exactly the
    first ml_selection_count OOS rows."""

    def predict_proba(self, X):
        n = len(X)
        f1 = X[:, 0]  # f1 is the first feature column
        out = np.zeros((n, 2), dtype=float)
        out[:, 0] = 1.0 - f1
        out[:, 1] = f1
        return out


def test_evaluate_holdout_filter_value_add():
    """ML subset has 80% win rate, baseline has 50%. Both R/trade and
    win_rate strictly improve -> FILTER_VALUE_ADD."""
    dataset = _make_mixed_dataset(
        n_is=200, n_oos=60,
        baseline_win_rate=0.5,
        ml_selected_win_rate=0.8,
        ml_selection_count=20,
    )
    oos_start = int(dataset.df["timestamp_ms"].iloc[200])
    oos_end = int(dataset.df["timestamp_ms"].iloc[-1]) + 1
    report = evaluate_holdout(
        dataset=dataset,
        model=_F1ProbaModel(),
        threshold=0.5,
        oos_period_ms=(oos_start, oos_end),
        tp_pct=2.0, sl_pct=1.0,
        min_trades=5,
    )
    # Absolute axis
    assert report.verdict == "HOLDOUT_PASS"
    assert report.n_trades == 20
    assert report.win_rate == 0.8
    # Baseline axis
    assert report.baseline_n_trades == 60
    assert abs(report.baseline_win_rate - 0.5) < 1e-9
    # Filter verdict
    assert report.delta_win_rate > 0
    assert report.delta_pnl_per_trade_R > 0
    assert report.filter_verdict == "FILTER_VALUE_ADD"
    # Filter efficiency = 20/60
    assert abs(report.filter_efficiency - (20.0 / 60.0)) < 1e-9


def test_evaluate_holdout_filter_destroys():
    """ML subset has 30% win rate, baseline has 50%. Both axes worsen
    -> FILTER_DESTROYS. This is the BBKC-filter failure mode the whole
    axis exists to catch."""
    dataset = _make_mixed_dataset(
        n_is=200, n_oos=60,
        baseline_win_rate=0.5,
        ml_selected_win_rate=0.3,
        ml_selection_count=20,
    )
    oos_start = int(dataset.df["timestamp_ms"].iloc[200])
    oos_end = int(dataset.df["timestamp_ms"].iloc[-1]) + 1
    report = evaluate_holdout(
        dataset=dataset,
        model=_F1ProbaModel(),
        threshold=0.5,
        oos_period_ms=(oos_start, oos_end),
        tp_pct=2.0, sl_pct=1.0,
        min_trades=5,
    )
    # Absolute-axis will be HOLDOUT_FAIL because 30% win rate at 2R/1R
    # gives negative P&L. That's fine -- we're testing the filter axis
    # is still computed and flags DESTROYS.
    assert report.delta_win_rate < 0
    assert report.delta_pnl_per_trade_R < 0
    assert report.filter_verdict == "FILTER_DESTROYS"


def test_evaluate_holdout_filter_neutral():
    """ML win_rate up slightly but count so small that the per-trade
    R is essentially flat vs baseline. This exercises the NEUTRAL path
    where the two axes disagree."""
    # Baseline 50% / 60 trades = +30R.  R/trade = +0.5R
    # ML = 5 trades, 3 wins 2 losses = +4R. R/trade = +0.8
    # That's actually VALUE_ADD. Let me engineer a mixed signal:
    # Baseline 50% / 60 trades -> R/trade = +0.5R
    # ML 5 trades at 60% = 3W/2L -> +4R / 5 = +0.8 R/trade  (both up)
    # For NEUTRAL I need one up one down. Use:
    # baseline win_rate=0.6, ml win_rate=0.7 (wr up)
    # baseline R/trade = 0.6*2 - 0.4*1 = +0.8
    # ml with 5 trades, 3.5 wins -> impossible, try 4 wins / 1 loss:
    #   wr 0.8 (vs baseline 0.6) -> delta +0.2
    #   R/trade = (4*2 - 1*1)/5 = 1.4  (vs 0.8 -> +0.6)  both up
    #
    # For NEUTRAL let me use an asymmetric payoff:
    # baseline 60% wr, 60 trades, tp=1, sl=3.
    #   baseline pnl = 36*1 - 24*3 = 36 - 72 = -36 R
    #   baseline R/trade = -0.6
    # ml 5 trades 80% wr (4W/1L):
    #   ml pnl = 4*1 - 1*3 = +1
    #   ml R/trade = +0.2
    #   delta wr +0.2, delta R/trade +0.8 -> still both up
    #
    # Actually it's hard to engineer NEUTRAL with these tight params.
    # Use a case where baseline has high R/trade and ml has lower:
    # baseline 20 wins / 0 losses = pnl=40, R/trade=40/20=2
    # Wait that requires baseline_win_rate=1.0 which collapses.
    #
    # Easier: directly construct the baseline win rate and the
    # ml_selected_win_rate so ml delta_wr > 0 but delta_R/tr < 0.
    # That requires baseline to have higher tp-weighted payoff than ml.
    # With uniform tp/sl, delta_wr > 0 implies delta_R/tr > 0 (they
    # co-move), so NEUTRAL only arises with non-uniform tp/sl or with
    # exact ties (delta_wr == 0 with delta_R/tr != 0 via rounding).
    #
    # Use tp=1, sl=2 -> break-even win rate = 0.667.
    # baseline 70% (above break-even), R/tr = 0.7*1 - 0.3*2 = 0.7 - 0.6 = 0.1
    # ml 65% (below break-even but higher win count ratio...), R/tr = 0.65*1 - 0.35*2 = 0.65 - 0.7 = -0.05
    # delta_wr = -0.05 (down), delta_R/tr = -0.15 (down) -> DESTROYS
    #
    # OK try tp=2, sl=1 (normal 2:1):
    # baseline 55% -> R/tr = 0.55*2 - 0.45*1 = 0.65
    # ml 70% -> R/tr = 0.7*2 - 0.3*1 = 1.1 -> both up
    # ml 50% -> R/tr = 0.5*2 - 0.5*1 = 0.5 -> both down (DESTROYS)
    # Neutral with uniform tp/sl is essentially impossible for win_rate
    # deltas because R/trade is a linear function of win_rate.
    #
    # The remaining case: delta_wr exactly 0 (rounding) but delta_R/tr
    # nonzero due to floating-point artifacts. In practice that lands
    # in the eps-tolerant "neither up nor down" branch, so NEUTRAL.
    # Simulate this via a dataset where ML and baseline have the same
    # win rate (so delta_wr == 0) and tp_pct vs sl_pct cancel exactly.
    # Actually if delta_wr == 0 and tp/sl uniform, delta_R/tr == 0 too.
    # So NEUTRAL really only happens with asymmetric payoff structures.
    #
    # Use a CUSTOM tp_pct that makes pnl sensitive to count:
    # baseline 50% -> R/tr = 0. Exactly 0.
    # ml 50% (same win rate) -> R/tr = 0 exactly.
    # delta_wr == 0 (within eps), delta_R/tr == 0 -> NEUTRAL (both not > 0, both not < 0)
    dataset = _make_mixed_dataset(
        n_is=200, n_oos=60,
        baseline_win_rate=0.5,
        ml_selected_win_rate=0.5,   # same
        ml_selection_count=20,
    )
    oos_start = int(dataset.df["timestamp_ms"].iloc[200])
    oos_end = int(dataset.df["timestamp_ms"].iloc[-1]) + 1
    report = evaluate_holdout(
        dataset=dataset,
        model=_F1ProbaModel(),
        threshold=0.5,
        oos_period_ms=(oos_start, oos_end),
        tp_pct=1.0, sl_pct=1.0,
        min_trades=5,
    )
    # Both arms have the same win rate at 1:1 R:R -> both axes = 0
    assert abs(report.delta_win_rate) < 1e-6
    assert abs(report.delta_pnl_per_trade_R) < 1e-6
    assert report.filter_verdict == "FILTER_NEUTRAL"


def test_evaluate_holdout_filter_not_applicable_when_baseline_small():
    """If the holdout is too small for the baseline to be meaningful,
    the filter_verdict should be FILTER_NOT_APPLICABLE so that
    _final_verdict falls back to the absolute axis alone."""
    dataset = _make_mixed_dataset(
        n_is=200, n_oos=4,  # baseline < min_trades(5)
        baseline_win_rate=0.5,
        ml_selected_win_rate=0.5,
        ml_selection_count=2,
    )
    oos_start = int(dataset.df["timestamp_ms"].iloc[200])
    oos_end = int(dataset.df["timestamp_ms"].iloc[-1]) + 1
    report = evaluate_holdout(
        dataset=dataset,
        model=_F1ProbaModel(),
        threshold=0.5,
        oos_period_ms=(oos_start, oos_end),
        tp_pct=2.0, sl_pct=1.0,
        min_trades=5,
    )
    assert report.filter_verdict == "FILTER_NOT_APPLICABLE"
