"""ML pattern training CLI.

Usage:
  python scripts/train_ml_pattern.py engulfing_mtf \
      --symbols BTCUSDT,ETHUSDT \
      --is 2022-01-01:2024-01-01 \
      --oos 2024-01-01:2026-01-01 \
      --tp 0.04 --sl 0.02 --max-holding 24 \
      --trials 100 --hpo-timeout 1800

Deterministic. Contains NO LLM/subagent calls. Refinement is a separate
optional script (scripts/refine_pattern.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.core.types import BarSeries
from src.ml.event_dataset_builder import EventDatasetBuilder, EventDataset
from src.ml.patterns.bbkc_filter import BBKCFilterPattern
from src.ml.patterns.engulfing_mtf import EngulfingMTF
from src.ml.patterns.rsi_divergence import RSIDivergence
from src.ml.persistence import save_run
from src.ml.report import build_report
from src.ml.trainer import Trainer, HPOConfig
from src.ml.types import LabelConfig, MTFData
from src.ml.validator import (
    Validator, WFConfig, evaluate_holdout, HoldoutReport,
)


PATTERN_REGISTRY = {
    "engulfing_mtf": EngulfingMTF,
    "rsi_divergence": RSIDivergence,
    "bbkc_filter": BBKCFilterPattern,
}


def _filter_dataset(
    dataset: EventDataset,
    hidden_only: bool = False,
    min_adx: float = 0.0,
) -> EventDataset:
    """Apply subset gating filters to a built EventDataset.

    - ``hidden_only``: keep only rows where the divergence type one-hot is
      ``dt_hidden_bull`` or ``dt_hidden_bear``.
    - ``min_adx``: keep only rows where ``adx_primary`` ≥ the given threshold.

    Sample weights are recomputed after filtering so the inverse-symbol-count
    balance + mean==1 invariant still holds on the reduced set.
    """
    df = dataset.df.copy()
    if hidden_only:
        mask_hidden = (df.get("dt_hidden_bull", 0.0) == 1.0) | (
            df.get("dt_hidden_bear", 0.0) == 1.0
        )
        df = df[mask_hidden]
    if min_adx > 0.0 and "adx_primary" in df.columns:
        df = df[df["adx_primary"] >= float(min_adx)]
    df = df.reset_index(drop=True)

    # Rebuild sample_weight so downstream Trainer/Validator still get a
    # properly-scaled inverse-symbol-count weighting on the filtered set.
    if not df.empty and "symbol_id" in df.columns:
        counts = df["symbol_id"].value_counts().to_dict()
        raw = df["symbol_id"].map(lambda s: 1.0 / counts[s]).astype(float)
        total = float(raw.sum())
        n = len(df)
        if total > 0:
            df["sample_weight"] = raw * (n / total)
        else:
            df["sample_weight"] = 1.0

    return EventDataset(
        df=df,
        feature_columns=dataset.feature_columns,
        meta_columns=dataset.meta_columns,
    )


def _df_from_db(db_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a DBManager.get_bars() DataFrame into the form expected by
    BarSeries.bars (timestamp column + 0-based integer index)."""
    if db_df is None or db_df.empty:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"]
        )
    out = db_df.reset_index(drop=True).copy()
    out = out.rename(columns={"open_time": "timestamp"})
    keep = ["timestamp", "open", "high", "low", "close", "volume"]
    if "turnover" in out.columns:
        keep.append("turnover")
    return out[keep]


def load_mtf_data(
    symbols: List[str],
    timeframes: List[str],
    start_ms: int,
    end_ms: int,
    primary_tf: str = "1h",
) -> Dict[str, MTFData]:
    """Default loader: read OHLCV from DB.

    Tests monkey-patch this with a synthetic generator.
    """
    from src.core.config import load_config
    from src.data_manager.db import DBManager

    cfg = load_config()
    db = DBManager(cfg.app.db_path)
    if primary_tf not in timeframes:
        raise ValueError(
            f"primary_tf={primary_tf!r} must be one of pattern.timeframes={timeframes}"
        )
    out: Dict[str, MTFData] = {}
    for sym in symbols:
        series_map: Dict[str, BarSeries] = {}
        for tf in timeframes:
            raw = db.get_bars(symbol=sym, timeframe=tf,
                              start_time=start_ms, end_time=end_ms)
            normalized = _df_from_db(raw)
            series_map[tf] = BarSeries(symbol=sym, timeframe=tf, bars=normalized)
        out[sym] = MTFData(
            symbol=sym, primary_tf=primary_tf, series=series_map,
        )
    return out


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
        ).strip()
    except Exception:
        return "unknown"


def run_pipeline(
    pattern_name: str,
    symbols: List[str],
    is_start_ms: int, is_end_ms: int,
    oos_start_ms: int, oos_end_ms: int,
    tp_pct: float, sl_pct: float, max_holding_bars: int,
    n_trials: int = 100, hpo_timeout: int = 1800,
    cache_dir: Path = Path("cache/ml"),
    out_root: Path = Path("logs/ml"),
    threshold_min: float = 0.35,
    threshold_max: float = 0.65,
    n_cv_splits: int = 5,
    label_mode: str = "pct",
    tp_atr_mult: Optional[float] = None,
    sl_atr_mult: Optional[float] = None,
    atr_period: int = 14,
    hidden_only: bool = False,
    min_adx: float = 0.0,
    primary_tf: str = "1h",
) -> Path:
    if pattern_name not in PATTERN_REGISTRY:
        raise KeyError(f"Unknown pattern: {pattern_name}")
    pattern = PATTERN_REGISTRY[pattern_name]()

    mtf_per_symbol = load_mtf_data(
        symbols=symbols,
        timeframes=pattern.timeframes,
        start_ms=is_start_ms,
        end_ms=oos_end_ms,
        primary_tf=primary_tf,
    )

    if label_mode not in ("pct", "atr"):
        raise ValueError(f"label_mode must be 'pct' or 'atr', got {label_mode!r}")
    if label_mode == "atr" and (tp_atr_mult is None or sl_atr_mult is None):
        raise ValueError(
            "label_mode='atr' requires both --tp-atr and --sl-atr to be set."
        )

    label_cfg = LabelConfig(
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        max_holding_bars=max_holding_bars,
        label_mode=label_mode,
        tp_atr_mult=tp_atr_mult,
        sl_atr_mult=sl_atr_mult,
        atr_period=atr_period,
    )

    # Scoring reward/risk for Trainer and Validator.
    # In pct mode these are tp_pct / sl_pct (percentage per trade).
    # In atr mode these are tp_atr_mult / sl_atr_mult — equivalent to
    # R-multiples, i.e. "on average how many ATR of reward per trade."
    # Using the right pair keeps the HPO objective and WF PnL consistent
    # with the barrier formula the labels were built from.
    if label_mode == "atr":
        scoring_tp = float(tp_atr_mult)  # type: ignore[arg-type]
        scoring_sl = float(sl_atr_mult)  # type: ignore[arg-type]
    else:
        scoring_tp = float(tp_pct)
        scoring_sl = float(sl_pct)
    builder = EventDatasetBuilder(
        pattern=pattern, label_config=label_cfg, cache_dir=Path(cache_dir),
    )
    dataset = builder.build(mtf_per_symbol)
    if dataset.df.empty:
        raise RuntimeError("EventDatasetBuilder produced an empty dataset.")

    # Apply subset gating (hidden-only / ADX regime filter). These are
    # post-build filters so the full-universe cache is still populated and
    # reusable across experiments; only the training view shrinks.
    n_before = len(dataset.df)
    if hidden_only or min_adx > 0.0:
        dataset = _filter_dataset(dataset, hidden_only=hidden_only, min_adx=min_adx)
    n_after = len(dataset.df)
    if dataset.df.empty:
        raise RuntimeError(
            f"Filter produced an empty dataset "
            f"(hidden_only={hidden_only}, min_adx={min_adx})."
        )
    print(
        f"[train_ml_pattern] filter: hidden_only={hidden_only} min_adx={min_adx} "
        f"→ events {n_before} → {n_after}"
    )

    # Use user-configurable splits: small synthetic tests may need 2, real
    # runs default to 5. threshold_range is also runtime-configurable so the
    # model can actually emit trades when the label distribution is imbalanced.
    effective_splits = max(2, min(n_cv_splits, max(2, len(dataset.df) // 50)))
    trainer = Trainer(hpo_config=HPOConfig(
        n_trials=n_trials,
        timeout_seconds=hpo_timeout,
        n_cv_splits=effective_splits,
        threshold_range=(float(threshold_min), float(threshold_max)),
    ))
    train_result = trainer.train(
        dataset=dataset,
        is_period=(is_start_ms, is_end_ms),
        tp_pct=scoring_tp, sl_pct=scoring_sl,
    )

    validator = Validator(wf_config=WFConfig(
        is_window_bars=max(2, train_result.n_train_samples // 5),
        oos_window_bars=max(1, train_result.n_train_samples // 10),
        min_trades_per_fold=1,
    ))
    val_report = validator.validate(
        dataset=dataset,
        best_params=train_result.best_params,
        tp_pct=scoring_tp, sl_pct=scoring_sl,
        max_holding_bars=max_holding_bars,
    )

    # True-holdout evaluation: run the final trained model on events whose
    # timestamp is inside [oos_start_ms, oos_end_ms). The WF validator only
    # walks sliding windows *inside* IS, so without this step the report
    # has no real "data the model never saw" metric — and a pattern can
    # look fine under WF yet collapse on the holdout. The 4h RSI
    # Divergence case was the motivating example.
    holdout_report: Optional[HoldoutReport] = None
    if oos_start_ms < oos_end_ms:
        try:
            holdout_report = evaluate_holdout(
                dataset=dataset,
                model=train_result.model,
                threshold=float(train_result.best_params.get("threshold", 0.5)),
                oos_period_ms=(oos_start_ms, oos_end_ms),
                tp_pct=scoring_tp,
                sl_pct=scoring_sl,
            )
            print(
                f"[train_ml_pattern] holdout: verdict={holdout_report.verdict} "
                f"events={holdout_report.n_events} trades={holdout_report.n_trades} "
                f"pnl={holdout_report.total_pnl_R:+.1f}R "
                f"win_rate={holdout_report.win_rate:.1%}"
            )
            print(
                f"[train_ml_pattern] filter: verdict={holdout_report.filter_verdict} "
                f"baseline_n={holdout_report.baseline_n_trades} "
                f"baseline_wr={holdout_report.baseline_win_rate:.1%} "
                f"delta_wr={holdout_report.delta_win_rate:+.1%} "
                f"delta_R/tr={holdout_report.delta_pnl_per_trade_R:+.4f} "
                f"eff={holdout_report.filter_efficiency:.1%}"
            )
        except Exception as exc:
            print(f"[train_ml_pattern] holdout evaluation skipped: {exc}")

    run_id = dt.datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    out_dir = Path(out_root) / pattern_name / run_id
    dataset_hash = hashlib.sha256(
        dataset.df.to_csv(index=False).encode()
    ).hexdigest()[:16]

    artifacts = {
        "run_id": run_id,
        "model_path": str(out_dir / "model.joblib"),
        "meta_path": str(out_dir / "meta.json"),
        "dataset_hash": f"sha256:{dataset_hash}",
        "pattern_name": pattern.name,
        "pattern_version": pattern.version,
        "git_sha": _git_sha(),
    }
    train_summary = {
        "objective": train_result.objective_name,
        "best_params": train_result.best_params,
        "cv_objective": train_result.cv_objective,
        "n_trials_run": train_result.n_trials_run,
        "early_stop_reason": train_result.early_stop_reason,
        # Scoring unit makes cv_objective and WF PnL interpretable:
        #   pct mode → values are percentage-per-trade (e.g. 0.012 = 1.2%)
        #   atr mode → values are ATR-multiples-per-trade (R-multiples)
        "scoring_unit": "atr_mult" if label_mode == "atr" else "pct",
        "scoring_tp": scoring_tp,
        "scoring_sl": scoring_sl,
    }
    report = build_report(
        validation=val_report,
        artifacts=artifacts,
        train_summary=train_summary,
        holdout=holdout_report,
    )

    threshold = train_result.best_params.get("threshold", 0.5)
    meta = {
        "pattern_name": pattern.name,
        "pattern_version": pattern.version,
        "run_id": run_id,
        "git_sha": artifacts["git_sha"],
        "trained_at": dt.datetime.utcnow().isoformat(),
        "policy": {
            "threshold": threshold,
            # Only emit pct barriers when they were actually used as the
            # label formula. In atr mode the --tp / --sl CLI values are
            # stale placeholders that never touched the labeler, so
            # carrying them through the artifact would let the wrapper
            # silently run a different strategy than what was trained.
            "tp_pct": tp_pct if label_mode == "pct" else None,
            "sl_pct": sl_pct if label_mode == "pct" else None,
            "max_holding_bars": max_holding_bars,
            "label": {
                "type": "triple_barrier_binary",
                "timeout_class": "negative",
                "mode": label_mode,
                "tp_atr_mult": tp_atr_mult,
                "sl_atr_mult": sl_atr_mult,
                "atr_period": atr_period,
            },
            "weighting_policy": "inverse_symbol_count",
            "dataset_filter": {
                "hidden_only": hidden_only,
                "min_adx": min_adx,
            },
        },
        "data": {
            "symbols": symbols,
            "timeframes": pattern.timeframes,
            "primary_tf": primary_tf,
            "is_period_ms": [is_start_ms, is_end_ms],
            "oos_period_ms": [oos_start_ms, oos_end_ms],
            "n_features": len(dataset.feature_columns),
            "n_samples_is": train_result.n_train_samples,
            "dataset_hash": artifacts["dataset_hash"],
        },
        "training": train_summary,
        "feature_columns": dataset.feature_columns,
    }

    save_run(run_dir=out_dir, model=train_result.model, meta=meta, report=report)
    print(f"[train_ml_pattern] saved artifact: {out_dir}")
    print(f"[train_ml_pattern] verdict: {report['verdict']}")
    return out_dir


def _parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("pattern", choices=list(PATTERN_REGISTRY.keys()))
    p.add_argument("--symbols", required=True, help="Comma-separated or 'all'")
    p.add_argument("--is", dest="is_period", required=True,
                   help="ISO date range, e.g. 2022-01-01:2024-01-01")
    p.add_argument("--oos", dest="oos_period", required=True)
    p.add_argument("--tp", type=float, default=0.04)
    p.add_argument("--sl", type=float, default=0.02)
    p.add_argument("--max-holding", type=int, default=24)
    p.add_argument("--trials", type=int, default=100)
    p.add_argument("--hpo-timeout", type=int, default=1800)
    p.add_argument("--threshold-min", type=float, default=0.35)
    p.add_argument("--threshold-max", type=float, default=0.65)
    p.add_argument("--cv-splits", type=int, default=5)
    p.add_argument(
        "--label-mode",
        choices=("pct", "atr"),
        default="pct",
        help="Triple-barrier formula: 'pct' uses tp/sl percentages of entry; "
             "'atr' uses entry ± k×ATR(t).",
    )
    p.add_argument("--tp-atr", type=float, default=None,
                   help="TP multiple of ATR(t) when --label-mode atr (e.g. 2.0).")
    p.add_argument("--sl-atr", type=float, default=None,
                   help="SL multiple of ATR(t) when --label-mode atr (e.g. 1.0).")
    p.add_argument("--atr-period", type=int, default=14,
                   help="ATR period used by the label barrier in atr mode.")
    p.add_argument("--hidden-only", action="store_true",
                   help="Keep only hidden_bull / hidden_bear events after build.")
    p.add_argument("--min-adx", type=float, default=0.0,
                   help="Drop events where adx_primary < this value (0 = disabled).")
    p.add_argument("--primary-tf", choices=("1h", "4h", "1d"), default="1h",
                   help="Primary timeframe on which the pattern detects "
                        "events. Other pattern.timeframes are still loaded "
                        "and used for confirmed HTF features, but only those "
                        "strictly higher than primary contribute non-zero.")
    return p.parse_args(argv)


def _date_to_ms(s: str) -> int:
    return int(dt.datetime.fromisoformat(s).timestamp() * 1000)


def main(argv=None):
    args = _parse_args(argv)
    if args.symbols == "all":
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]
    else:
        symbols = [s.strip() for s in args.symbols.split(",")]
    is_start, is_end = args.is_period.split(":")
    oos_start, oos_end = args.oos_period.split(":")
    run_pipeline(
        pattern_name=args.pattern,
        symbols=symbols,
        is_start_ms=_date_to_ms(is_start), is_end_ms=_date_to_ms(is_end),
        oos_start_ms=_date_to_ms(oos_start), oos_end_ms=_date_to_ms(oos_end),
        tp_pct=args.tp, sl_pct=args.sl, max_holding_bars=args.max_holding,
        n_trials=args.trials, hpo_timeout=args.hpo_timeout,
        threshold_min=args.threshold_min, threshold_max=args.threshold_max,
        n_cv_splits=args.cv_splits,
        label_mode=args.label_mode,
        tp_atr_mult=args.tp_atr,
        sl_atr_mult=args.sl_atr,
        atr_period=args.atr_period,
        hidden_only=args.hidden_only,
        min_adx=args.min_adx,
        primary_tf=args.primary_tf,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
