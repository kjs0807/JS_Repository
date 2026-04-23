"""Tests for Trainer."""
import numpy as np
import pandas as pd

from src.ml.event_dataset_builder import EventDataset
from src.ml.trainer import Trainer, HPOConfig, TrainResult


def _synthetic_dataset(n: int = 200, seed: int = 0) -> EventDataset:
    rng = np.random.default_rng(seed)
    feats = pd.DataFrame({
        "f1": rng.normal(size=n),
        "f2": rng.normal(size=n),
        "f3": rng.normal(size=n),
    })
    # Learnable label: sign of f1+f2
    feats["label"] = ((feats["f1"] + feats["f2"]) > 0).astype(int)
    feats["symbol_id_BTCUSDT"] = 1.0
    feats["sample_weight"] = 1.0
    feats["timestamp_ms"] = (np.arange(n) * 3_600_000).astype(int)
    feats["direction"] = "long"
    feats["symbol_id"] = "BTCUSDT"
    return EventDataset(
        df=feats,
        feature_columns=["f1", "f2", "f3", "symbol_id_BTCUSDT"],
        meta_columns=["label", "sample_weight", "timestamp_ms", "direction", "symbol_id"],
    )


def test_train_returns_result_with_model_and_params():
    dataset = _synthetic_dataset()
    cfg = HPOConfig(n_trials=3, timeout_seconds=60, n_cv_splits=3)
    trainer = Trainer(hpo_config=cfg)
    result = trainer.train(
        dataset=dataset,
        is_period=(0, int(dataset.df["timestamp_ms"].max())),
        tp_pct=0.04,
        sl_pct=0.02,
    )
    assert isinstance(result, TrainResult)
    assert result.model is not None
    assert "max_depth" in result.best_params
    assert "threshold" in result.best_params
    assert result.cv_objective is not None
    # Sanity: a trained model should be able to predict
    proba = result.model.predict_proba(dataset.df[
        ["f1", "f2", "f3", "symbol_id_BTCUSDT"]
    ].values)
    assert proba.shape == (len(dataset.df), 2)


def test_objective_is_expectancy():
    dataset = _synthetic_dataset()
    cfg = HPOConfig(n_trials=2, n_cv_splits=3)
    trainer = Trainer(hpo_config=cfg)
    result = trainer.train(
        dataset=dataset,
        is_period=(0, int(dataset.df["timestamp_ms"].max())),
        tp_pct=0.04,
        sl_pct=0.02,
    )
    assert result.objective_name == "expectancy"


def test_train_respects_is_period():
    dataset = _synthetic_dataset(n=300)
    cutoff = int(dataset.df["timestamp_ms"].iloc[150])
    cfg = HPOConfig(n_trials=2, n_cv_splits=3)
    trainer = Trainer(hpo_config=cfg)
    result = trainer.train(
        dataset=dataset,
        is_period=(0, cutoff),
        tp_pct=0.04,
        sl_pct=0.02,
    )
    assert result.n_train_samples <= 151  # 0..150 inclusive
