"""Tests for persistence (model + meta + report separated)."""
import numpy as np
from xgboost import XGBClassifier

from src.ml.persistence import save_run, load_run, RunArtifact


def _trained_model():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(100, 4))
    y = (X[:, 0] > 0).astype(int)
    model = XGBClassifier(
        n_estimators=10, max_depth=3, verbosity=0,
        use_label_encoder=False,
    )
    model.fit(X, y)
    return model, X


def test_save_and_load_roundtrip(tmp_path):
    model, X = _trained_model()
    meta = {
        "pattern_name": "rsi_divergence",
        "pattern_version": "1.0.0",
        "run_id": "test_001",
        "policy": {
            "threshold": 0.62, "tp_pct": 0.04, "sl_pct": 0.02,
            "max_holding_bars": 24,
        },
    }
    report = {"metrics": {"cv": {"mean": 0.01}}}

    save_run(
        run_dir=tmp_path / "test_001",
        model=model,
        meta=meta,
        report=report,
    )
    artifact = load_run(tmp_path / "test_001")
    assert isinstance(artifact, RunArtifact)
    pred1 = model.predict_proba(X)[:5]
    pred2 = artifact.model.predict_proba(X)[:5]
    assert np.allclose(pred1, pred2)
    assert artifact.meta["pattern_name"] == "rsi_divergence"
    assert artifact.meta["policy"]["threshold"] == 0.62
    assert artifact.report["metrics"]["cv"]["mean"] == 0.01


def test_meta_and_model_are_separate_files(tmp_path):
    model, _ = _trained_model()
    save_run(
        run_dir=tmp_path / "r1",
        model=model,
        meta={"pattern_name": "p", "pattern_version": "0", "policy": {}},
        report={},
    )
    assert (tmp_path / "r1" / "model.joblib").exists()
    assert (tmp_path / "r1" / "meta.json").exists()
    assert (tmp_path / "r1" / "report.json").exists()


def test_load_returns_run_dir(tmp_path):
    model, _ = _trained_model()
    save_run(
        run_dir=tmp_path / "r2",
        model=model,
        meta={"pattern_name": "p", "pattern_version": "0", "policy": {}},
        report={},
    )
    artifact = load_run(tmp_path / "r2")
    assert artifact.run_dir == tmp_path / "r2"
