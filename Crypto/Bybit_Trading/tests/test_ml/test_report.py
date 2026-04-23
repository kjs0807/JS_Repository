"""Tests for Failure Report builder."""
from src.ml.report import build_report
from src.ml.validator import ValidationReport


def _stub_validation_report():
    return ValidationReport(
        n_folds=5,
        n_folds_skipped=1,
        fold_breakdown=[
            {"fold": 1, "skipped": False, "is_trades": 100, "oos_trades": 12,
             "oos_wins": 7, "oos_pnl": 0.18, "oos_sharpe": 1.4, "win_rate": 0.58},
            {"fold": 2, "skipped": True, "reason": "min_trades",
             "oos_trades": 0, "oos_pnl": 0.0},
            {"fold": 3, "skipped": False, "is_trades": 100, "oos_trades": 8,
             "oos_wins": 2, "oos_pnl": -0.12, "oos_sharpe": -0.8, "win_rate": 0.25},
        ],
        cv_summary={
            "oos_sharpe_mean": 0.30,
            "oos_sharpe_std": 1.55,
            "oos_pos_pct": 0.50,
            "oos_total_pnl": 0.06,
            "oos_total_trades": 20,
        },
        per_symbol_oos={
            "BTCUSDT": {"trades": 14, "pnl": 0.21},
            "ETHUSDT": {"trades": 6, "pnl": -0.15},
        },
        overfit={"verdict": "WARNING", "p_value": 0.08},
        feature_importance={
            "rsi_primary": 0.30, "h4_close_above_open": 0.20,
            "candle_body_ratio_primary": 0.12, "f_low_a": 0.02, "f_low_b": 0.01,
        },
    )


def test_build_report_has_all_sections():
    val = _stub_validation_report()
    report = build_report(
        validation=val,
        artifacts={
            "run_id": "2026-04-13_001",
            "model_path": "logs/ml/x/2026-04-13_001/model.joblib",
            "meta_path": "logs/ml/x/2026-04-13_001/meta.json",
            "dataset_hash": "sha256:abc",
            "pattern_name": "rsi_divergence",
            "pattern_version": "1.0.0",
            "git_sha": "deadbeef",
        },
        train_summary={
            "objective": "expectancy",
            "best_params": {"max_depth": 5},
            "n_trials_run": 50,
            "early_stop_reason": None,
        },
    )
    assert "artifacts" in report
    assert "metrics" in report
    assert "facts" in report
    assert "hints" in report
    assert "verdict" in report
    assert report["metrics"]["walk_forward"]["n_folds"] == 5
    assert report["metrics"]["overfit"]["verdict"] == "WARNING"


def test_facts_extract_weakest_features():
    val = _stub_validation_report()
    report = build_report(validation=val, artifacts={}, train_summary={})
    weakest = report["facts"]["weakest_features_by_importance"]
    # Two least-important features should appear first
    assert "f_low_b" in weakest
    assert "f_low_a" in weakest


def test_facts_count_zero_trade_folds():
    val = _stub_validation_report()
    report = build_report(validation=val, artifacts={}, train_summary={})
    # No fold has trades==0 AND skipped==False in our stub
    assert report["facts"]["n_zero_trade_folds"] == 0
    assert report["facts"]["n_skipped_folds"] == 1


def test_facts_high_loss_clusters_picks_negative_pnl_folds():
    val = _stub_validation_report()
    report = build_report(validation=val, artifacts={}, train_summary={})
    clusters = report["facts"]["high_loss_clusters"]
    # Fold 3 has negative PnL with positive trade count → must appear
    assert any(c.get("fold") == 3 for c in clusters)


def test_verdict_warning_when_overfit_warning():
    val = _stub_validation_report()
    report = build_report(validation=val, artifacts={}, train_summary={})
    # cv_summary mean<1 and overfit WARNING → verdict WARNING (not PASS, not FAIL)
    assert report["verdict"] == "WARNING"
