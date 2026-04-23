"""XGBoost + Optuna HPO trainer with a light expectancy objective.

Per spec:
- Trial objective is expectancy (label-based, no full backtest in HPO loop).
- threshold is HPO-tuned but stored alongside best_params (it is a policy
  parameter, not a model parameter — downstream code persists it in meta.json).
- TimeSeriesSplit on event timestamps for CV.
- Final fit on full IS with best params.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np
import optuna
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBClassifier

from src.ml.event_dataset_builder import EventDataset


@dataclass
class HPOConfig:
    objective: str = "expectancy"
    n_trials: int = 100
    timeout_seconds: int = 1800
    early_stop_no_improvement: int = 20
    target_objective: Optional[float] = None
    n_cv_splits: int = 5
    threshold_range: Tuple[float, float] = (0.5, 0.8)
    seed: int = 42


@dataclass
class TrainResult:
    model: Any
    best_params: Dict[str, Any]
    cv_objective: float
    objective_name: str
    n_trials_run: int
    early_stop_reason: Optional[str]
    n_train_samples: int


_FIXED_XGB_KWARGS = dict(
    objective="binary:logistic",
    eval_metric="logloss",
    verbosity=0,
    use_label_encoder=False,
)


class Trainer:
    def __init__(self, hpo_config: HPOConfig):
        self.hpo_config = hpo_config

    def train(
        self,
        dataset: EventDataset,
        is_period: Tuple[int, int],
        tp_pct: float,
        sl_pct: float,
    ) -> TrainResult:
        """Run the HPO study and return the best-fit model + params.

        ``tp_pct`` / ``sl_pct`` are the per-trade *reward* and *risk*
        used to compute the expectancy objective. They are named ``*_pct``
        for historical reasons but semantically represent the reward/risk
        in the **label's natural unit**:

        - When the dataset was labeled with ``LabelConfig.label_mode='pct'``
          these are percentages of entry price (e.g. 0.04, 0.02).
        - When ``label_mode='atr'`` these should be the ATR multiples
          (``tp_atr_mult`` / ``sl_atr_mult``) so the expectancy is expressed
          in R-multiples consistent with the barriers that produced the labels.

        The CLI (``scripts/train_ml_pattern.py``) handles this routing.
        Callers that bypass the CLI must pass the unit-consistent values
        themselves.
        """
        df = dataset.df.copy()
        df = df.sort_values("timestamp_ms").reset_index(drop=True)
        in_is = (df["timestamp_ms"] >= is_period[0]) & (df["timestamp_ms"] <= is_period[1])
        df_is = df[in_is].reset_index(drop=True)
        if len(df_is) < self.hpo_config.n_cv_splits + 1:
            raise ValueError(
                f"Not enough events ({len(df_is)}) for "
                f"{self.hpo_config.n_cv_splits}-split CV."
            )

        feature_cols = [c for c in dataset.feature_columns if c in df_is.columns]
        X = df_is[feature_cols].values
        y = df_is["label"].values
        if "sample_weight" in df_is.columns:
            w = df_is["sample_weight"].values
        else:
            w = np.ones(len(df_is))

        sampler = optuna.samplers.TPESampler(seed=self.hpo_config.seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)

        no_improve = {"count": 0, "best": -np.inf}

        def objective(trial: optuna.Trial) -> float:
            params = {
                "max_depth": trial.suggest_int("max_depth", 3, 8),
                "n_estimators": trial.suggest_categorical("n_estimators", [100, 200, 400]),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
                "min_child_weight": trial.suggest_int("min_child_weight", 1, 5),
                "subsample": trial.suggest_float("subsample", 0.7, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.7, 1.0),
            }
            threshold = trial.suggest_float(
                "threshold",
                self.hpo_config.threshold_range[0],
                self.hpo_config.threshold_range[1],
            )
            tscv = TimeSeriesSplit(n_splits=self.hpo_config.n_cv_splits)
            fold_objs = []
            for train_idx, val_idx in tscv.split(X):
                Xt, Xv = X[train_idx], X[val_idx]
                yt, yv = y[train_idx], y[val_idx]
                wt = w[train_idx]
                # Single-class fold: XGBoost classifier rejects this with
                # "Invalid classes inferred from unique values of `y`". Skip
                # the fold (counts as zero expectancy) instead of crashing.
                if len(np.unique(yt)) < 2:
                    fold_objs.append(0.0)
                    continue
                model = XGBClassifier(
                    **params,
                    **_FIXED_XGB_KWARGS,
                    random_state=self.hpo_config.seed,
                )
                model.fit(Xt, yt, sample_weight=wt)
                proba = model.predict_proba(Xv)[:, 1]
                taken = proba >= threshold
                if taken.sum() == 0:
                    fold_objs.append(0.0)
                    continue
                wins = int(yv[taken].sum())
                losses = int(taken.sum()) - wins
                expectancy = (wins * tp_pct - losses * sl_pct) / int(taken.sum())
                fold_objs.append(float(expectancy))
            mean_obj = float(np.mean(fold_objs))

            if mean_obj > no_improve["best"] + 1e-9:
                no_improve["best"] = mean_obj
                no_improve["count"] = 0
            else:
                no_improve["count"] += 1
            if no_improve["count"] >= self.hpo_config.early_stop_no_improvement:
                trial.study.stop()
            if (
                self.hpo_config.target_objective is not None
                and mean_obj >= self.hpo_config.target_objective
            ):
                trial.study.stop()
            return mean_obj

        study.optimize(
            objective,
            n_trials=self.hpo_config.n_trials,
            timeout=self.hpo_config.timeout_seconds,
            show_progress_bar=False,
        )

        best_params = dict(study.best_params)
        threshold = best_params.pop("threshold")
        final_model = XGBClassifier(
            **best_params,
            **_FIXED_XGB_KWARGS,
            random_state=self.hpo_config.seed,
        )
        final_model.fit(X, y, sample_weight=w)
        # Re-attach threshold as a policy parameter alongside model params
        best_params["threshold"] = threshold

        early_stop_reason: Optional[str] = None
        if no_improve["count"] >= self.hpo_config.early_stop_no_improvement:
            early_stop_reason = "no_improvement"
        elif (
            self.hpo_config.target_objective is not None
            and study.best_value >= self.hpo_config.target_objective
        ):
            early_stop_reason = "target_reached"

        return TrainResult(
            model=final_model,
            best_params=best_params,
            cv_objective=float(study.best_value),
            objective_name=self.hpo_config.objective,
            n_trials_run=len(study.trials),
            early_stop_reason=early_stop_reason,
            n_train_samples=len(df_is),
        )


__all__ = ["Trainer", "HPOConfig", "TrainResult"]
