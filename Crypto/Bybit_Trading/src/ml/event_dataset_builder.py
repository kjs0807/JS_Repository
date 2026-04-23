"""Build a pooled ML dataset from a pattern + multi-symbol MTF data.

Workflow:
  for each symbol:
    for each i in [warmup, len(primary)):
      event = pattern.detect_at(mtf, i)
      if event:
        features = pattern.extract_features(event, mtf)
        label = triple_barrier_label(event, primary, label_config)
        record (features ∪ {symbol_id, timestamp, label})
  concat → DataFrame → cached parquet
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.ml.helpers.indicators_mtf import compute_atr
from src.ml.patterns.base import BasePattern
from src.ml.types import LabelConfig, MTFData, PatternEvent


@dataclass(frozen=True)
class EventDataset:
    df: pd.DataFrame
    feature_columns: List[str]
    meta_columns: List[str]

    def to_dataframe(self) -> pd.DataFrame:
        return self.df


class EventDatasetBuilder:
    def __init__(
        self,
        pattern: BasePattern,
        label_config: LabelConfig,
        cache_dir: Optional[Path] = None,
        weighting_policy: str = "inverse_symbol_count",
    ):
        self.pattern = pattern
        self.label_config = label_config
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.weighting_policy = weighting_policy

    def _data_fingerprint(
        self, mtf_per_symbol: Dict[str, MTFData]
    ) -> Dict[str, Dict[str, Dict[str, int]]]:
        """Build a compact "(tf, first_ts, last_ts, n_bars)" fingerprint of
        the input OHLCV so the cache key invalidates automatically when:
          - the date range widens or shrinks
          - a new bar is appended (live DB update)
          - a symbol's timeframe set changes
        Using the actual timestamps instead of a full data hash keeps cache
        lookups fast while still catching the realistic sources of drift.
        """
        out: Dict[str, Dict[str, Dict[str, int]]] = {}
        for sym in sorted(mtf_per_symbol.keys()):
            mtf = mtf_per_symbol[sym]
            per_tf: Dict[str, Dict[str, int]] = {}
            for tf in sorted(mtf.series.keys()):
                bars = mtf.series[tf].bars
                n = int(len(bars))
                if n == 0:
                    per_tf[tf] = {"n": 0, "first_ts": 0, "last_ts": 0}
                else:
                    per_tf[tf] = {
                        "n": n,
                        "first_ts": int(bars["timestamp"].iloc[0]),
                        "last_ts": int(bars["timestamp"].iloc[-1]),
                    }
            out[sym] = per_tf
        return out

    def _cache_path(
        self, mtf_per_symbol: Dict[str, MTFData]
    ) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        # Derive primary_tf from the first MTFData — all symbols share it
        # because run_pipeline constructs MTFData with a single primary_tf.
        first_mtf = next(iter(mtf_per_symbol.values()), None)
        primary_tf = first_mtf.primary_tf if first_mtf is not None else "unknown"
        key_obj = {
            "pattern": self.pattern.name,
            "version": self.pattern.version,
            "symbols": sorted(mtf_per_symbol.keys()),
            "primary_tf": primary_tf,
            "data_fingerprint": self._data_fingerprint(mtf_per_symbol),
            "label": {
                "tp_pct": self.label_config.tp_pct,
                "sl_pct": self.label_config.sl_pct,
                "max_holding_bars": self.label_config.max_holding_bars,
                "label_type": self.label_config.label_type,
                "timeout_class": self.label_config.timeout_class,
                "label_mode": self.label_config.label_mode,
                "tp_atr_mult": self.label_config.tp_atr_mult,
                "sl_atr_mult": self.label_config.sl_atr_mult,
                "atr_period": self.label_config.atr_period,
            },
            "weighting_policy": self.weighting_policy,
        }
        key = hashlib.sha256(
            json.dumps(key_obj, sort_keys=True).encode()
        ).hexdigest()[:16]
        d = self.cache_dir / self.pattern.name
        d.mkdir(parents=True, exist_ok=True)
        return d / f"events_{key}.parquet"

    def build(self, mtf_per_symbol: Dict[str, MTFData]) -> EventDataset:
        cache = self._cache_path(mtf_per_symbol)
        if cache is not None and cache.exists():
            df = pd.read_parquet(cache)
            return self._wrap(df)

        records: List[dict] = []
        for symbol, mtf in mtf_per_symbol.items():
            primary = mtf.get_primary()
            n = len(primary)
            # ATR is computed once per symbol when label_mode == "atr" so the
            # label function does not re-derive it on every event.
            atr_array: Optional[np.ndarray] = None
            if self.label_config.label_mode == "atr":
                atr_array = compute_atr(primary, period=self.label_config.atr_period)
            for i in range(self.pattern.warmup_bars, n):
                event = self.pattern.detect_at(mtf, i)
                if event is None:
                    continue
                features = self.pattern.extract_features(event, mtf)
                label = self._triple_barrier_label(event, primary, atr_array)
                rec = dict(features)
                rec["symbol_id"] = symbol
                rec["timestamp_ms"] = int(event.timestamp_ms)
                rec["direction"] = event.direction
                rec["label"] = int(label)
                records.append(rec)

        if not records:
            df = pd.DataFrame()
        else:
            df = pd.DataFrame(records)
            # One-hot symbol_id while keeping the raw column too
            one_hot = pd.get_dummies(df["symbol_id"], prefix="symbol_id").astype(float)
            df = pd.concat([df, one_hot], axis=1)
            if self.weighting_policy == "inverse_symbol_count":
                counts = df["symbol_id"].value_counts().to_dict()
                raw = df["symbol_id"].map(lambda s: 1.0 / counts[s])
                # Normalize so that mean(sample_weight) == 1.0. Without this,
                # the raw weights sum to n_symbols (each symbol contributes 1),
                # making individual weights ~1/N. Tree learners with
                # min_child_weight >= 1 then fail to find any valid split and
                # collapse to a constant-output degenerate model. Rescaling
                # keeps the relative (inverse-count) balance while giving
                # absolute weights on the usual ~1 scale.
                total = float(raw.sum())
                n = len(df)
                if total > 0:
                    df["sample_weight"] = raw * (n / total)
                else:
                    df["sample_weight"] = 1.0
            else:
                df["sample_weight"] = 1.0

        if cache is not None and not df.empty:
            df.to_parquet(cache, index=False)

        return self._wrap(df)

    def _triple_barrier_label(
        self,
        event: PatternEvent,
        primary,
        atr_array: Optional[np.ndarray] = None,
    ) -> int:
        i = event.bar_index
        if i >= len(primary):
            return 0
        entry = float(primary.bars["close"].iloc[i])
        cfg = self.label_config
        if cfg.label_mode == "atr":
            if atr_array is None or cfg.tp_atr_mult is None or cfg.sl_atr_mult is None:
                return 0
            atr_val = float(atr_array[i]) if i < len(atr_array) else float("nan")
            if not np.isfinite(atr_val) or atr_val <= 0:
                return 0
            tp_delta = float(cfg.tp_atr_mult) * atr_val
            sl_delta = float(cfg.sl_atr_mult) * atr_val
            if event.direction == "long":
                tp = entry + tp_delta
                sl = entry - sl_delta
            else:
                tp = entry - tp_delta
                sl = entry + sl_delta
        else:
            if event.direction == "long":
                tp = entry * (1.0 + cfg.tp_pct)
                sl = entry * (1.0 - cfg.sl_pct)
            else:
                tp = entry * (1.0 - cfg.tp_pct)
                sl = entry * (1.0 + cfg.sl_pct)
        end_idx = min(i + cfg.max_holding_bars, len(primary) - 1)
        highs = primary.bars["high"]
        lows = primary.bars["low"]
        for k in range(i + 1, end_idx + 1):
            high = float(highs.iloc[k])
            low = float(lows.iloc[k])
            if event.direction == "long":
                if low <= sl:
                    return 0
                if high >= tp:
                    return 1
            else:
                if high >= sl:
                    return 0
                if low <= tp:
                    return 1
        return 0  # timeout = negative

    def _wrap(self, df: pd.DataFrame) -> EventDataset:
        meta_cols = [c for c in [
            "symbol_id", "timestamp_ms", "direction", "label", "sample_weight",
        ] if c in df.columns]
        feat_cols = [c for c in df.columns if c not in meta_cols]
        return EventDataset(df=df, feature_columns=feat_cols, meta_columns=meta_cols)
