"""PatternMLFilterStrategy — wraps a trained ML model + pattern as a Strategy.

Conforms to the Strategy Protocol used by BacktestEngine and LiveBroker.
Role: pattern trigger detection + ML probability filter + triple-barrier
TP/SL/timeout. Position sizing and order routing are delegated to the broker;
this wrapper only emits buy/sell intents and tracks an open-position record
for timeout exit.

Label parity: the wrapper supports both label modes the training pipeline
produces and applies the SAME barrier formula at execution time.

- ``label_mode="pct"``: tp = entry*(1±tp_pct), sl = entry*(1∓sl_pct)
- ``label_mode="atr"``: tp = entry ± tp_atr_mult * ATR(t),
                        sl = entry ∓ sl_atr_mult * ATR(t)

Picking the right mode is not optional — an ATR-trained artifact run with
pct barriers is a different strategy than the one the walk-forward report
validated.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import numpy as np

from src.core.types import Bar, BarSeries
from src.ml.helpers.indicators_mtf import compute_atr
from src.ml.patterns.base import BasePattern
from src.ml.persistence import load_run
from src.ml.types import MTFData
from src.strategies.base import IndicatorCache


@dataclass
class _OpenPosition:
    direction: str
    entry_price: float
    entry_index: int
    tp_price: float
    sl_price: float


class PatternMLFilterStrategy:
    name: str = "PatternMLFilter"

    def __init__(
        self,
        pattern: BasePattern,
        model: Any,
        feature_columns: List[str],
        threshold: float,
        max_holding_bars: int,
        label_mode: Literal["pct", "atr"] = "pct",
        tp_pct: Optional[float] = None,
        sl_pct: Optional[float] = None,
        tp_atr_mult: Optional[float] = None,
        sl_atr_mult: Optional[float] = None,
        atr_period: int = 14,
        timeframe: str = "1h",
        mtf_data: Optional[MTFData] = None,
    ):
        self.pattern = pattern
        self.model = model
        self.feature_columns = list(feature_columns)
        self.threshold = float(threshold)
        self.max_holding_bars = int(max_holding_bars)
        self.timeframe = str(timeframe)
        self.label_mode = label_mode
        self.atr_period = int(atr_period)

        if label_mode == "pct":
            if tp_pct is None or sl_pct is None:
                raise ValueError(
                    "label_mode='pct' requires both tp_pct and sl_pct."
                )
            self.tp_pct = float(tp_pct)
            self.sl_pct = float(sl_pct)
            self.tp_atr_mult: Optional[float] = None
            self.sl_atr_mult: Optional[float] = None
        elif label_mode == "atr":
            if tp_atr_mult is None or sl_atr_mult is None:
                raise ValueError(
                    "label_mode='atr' requires both tp_atr_mult and sl_atr_mult."
                )
            self.tp_pct = None
            self.sl_pct = None
            self.tp_atr_mult = float(tp_atr_mult)
            self.sl_atr_mult = float(sl_atr_mult)
        else:
            raise ValueError(
                f"Unsupported label_mode={label_mode!r} (expected 'pct' or 'atr')."
            )

        self._mtf: Optional[MTFData] = mtf_data
        self._open: Optional[_OpenPosition] = None

    @property
    def warmup_bars(self) -> int:
        return int(self.pattern.warmup_bars)

    @classmethod
    def from_artifact(
        cls,
        run_dir: Path,
        pattern_factory: Callable[[], BasePattern],
        mtf_data: Optional[MTFData] = None,
    ) -> "PatternMLFilterStrategy":
        """Load a trained artifact and construct the wrapper with execution
        rules that match the label mode the model was trained against.

        Reads ``policy.label.mode`` to decide between pct and atr execution
        and pulls ``data.primary_tf`` as the wrapper's Strategy timeframe.
        Older artifacts without these fields fall back to pct + "1h" for
        backward compatibility.
        """
        artifact = load_run(run_dir)
        meta = artifact.meta
        policy = meta["policy"]
        feature_columns = meta.get("feature_columns", [])

        label_policy: Dict[str, Any] = policy.get("label", {}) or {}
        label_mode: str = label_policy.get("mode") or "pct"
        primary_tf: str = meta.get("data", {}).get("primary_tf", "1h")

        base_kwargs: Dict[str, Any] = dict(
            pattern=pattern_factory(),
            model=artifact.model,
            feature_columns=feature_columns,
            threshold=float(policy["threshold"]),
            max_holding_bars=int(policy["max_holding_bars"]),
            timeframe=primary_tf,
            label_mode=label_mode,
            mtf_data=mtf_data,
        )

        if label_mode == "pct":
            tp_pct = policy.get("tp_pct")
            sl_pct = policy.get("sl_pct")
            if tp_pct is None or sl_pct is None:
                raise ValueError(
                    f"Artifact at {run_dir} declares label mode=pct but is "
                    f"missing policy.tp_pct / policy.sl_pct."
                )
            base_kwargs["tp_pct"] = float(tp_pct)
            base_kwargs["sl_pct"] = float(sl_pct)
        elif label_mode == "atr":
            tp_atr_mult = label_policy.get("tp_atr_mult")
            sl_atr_mult = label_policy.get("sl_atr_mult")
            atr_period = label_policy.get("atr_period", 14)
            if tp_atr_mult is None or sl_atr_mult is None:
                raise ValueError(
                    f"Artifact at {run_dir} declares label mode=atr but is "
                    f"missing policy.label.tp_atr_mult / sl_atr_mult."
                )
            base_kwargs["tp_atr_mult"] = float(tp_atr_mult)
            base_kwargs["sl_atr_mult"] = float(sl_atr_mult)
            base_kwargs["atr_period"] = int(atr_period)
        else:
            raise ValueError(
                f"Artifact at {run_dir} declares unsupported label mode "
                f"{label_mode!r}."
            )

        return cls(**base_kwargs)

    def set_mtf_data(self, mtf: MTFData) -> None:
        self._mtf = mtf

    def prepare(self, full_series: BarSeries) -> IndicatorCache:
        cache = IndicatorCache(arrays={})
        # Attach the (already known) MTF context as an ad-hoc attribute on the
        # cache. IndicatorCache is a regular dataclass, not frozen, so this is
        # allowed. In atr mode we also cache the primary-TF ATR array so
        # on_bar_fast can read ATR at index i in O(1) — matching exactly the
        # ATR the event_dataset_builder used for label barriers.
        cache.mtf = self._mtf  # type: ignore[attr-defined]
        if self.label_mode == "atr" and self._mtf is not None:
            primary = self._mtf.get_primary()
            cache.atr_arr = compute_atr(  # type: ignore[attr-defined]
                primary, period=self.atr_period
            )
        return cache

    def _compute_barriers(
        self,
        entry: float,
        direction: str,
        i: int,
        cache: IndicatorCache,
    ) -> Optional[tuple]:
        """Return (tp, sl) pair based on label_mode, or None if ATR is
        unavailable at bar i. The same barrier formula is used as the
        training label (pct or atr) so execution parity is guaranteed.
        """
        if self.label_mode == "atr":
            atr_arr = getattr(cache, "atr_arr", None)
            if atr_arr is None or i >= len(atr_arr):
                return None
            atr_i = float(atr_arr[i])
            if not np.isfinite(atr_i) or atr_i <= 0:
                return None
            tp_dist = float(self.tp_atr_mult) * atr_i  # type: ignore[arg-type]
            sl_dist = float(self.sl_atr_mult) * atr_i  # type: ignore[arg-type]
        else:  # pct
            tp_dist = entry * float(self.tp_pct)  # type: ignore[arg-type]
            sl_dist = entry * float(self.sl_pct)  # type: ignore[arg-type]
        if direction == "long":
            return entry + tp_dist, entry - sl_dist
        else:
            return entry - tp_dist, entry + sl_dist

    def on_bar(self, bar: Bar, series: BarSeries, broker) -> None:
        # Slow path: lazy-build the cache once per call. Not used in the fast
        # path test, but provided so the Strategy Protocol contract is met.
        cache = self.prepare(series)
        i = len(series) - 1
        self.on_bar_fast(bar=bar, i=i, cache=cache, broker=broker)

    def on_bar_fast(self, bar: Bar, i: int, cache, broker) -> None:
        mtf: Optional[MTFData] = getattr(cache, "mtf", None)
        if mtf is None:
            return  # nothing to do without MTF context

        # 1) Manage open position
        if self._open is not None:
            held = i - self._open.entry_index
            high = float(bar.high)
            low = float(bar.low)
            if self._open.direction == "long":
                tp_hit = high >= self._open.tp_price
                sl_hit = low <= self._open.sl_price
            else:
                tp_hit = low <= self._open.tp_price
                sl_hit = high >= self._open.sl_price
            if held >= self.max_holding_bars or tp_hit or sl_hit:
                try:
                    broker.close_position(symbol=bar.symbol)
                except Exception:
                    pass
                self._open = None
                return
            return  # holding — do not stack

        # 2) Pattern trigger?
        event = self.pattern.detect_at(mtf, i)
        if event is None:
            return

        # 3) ML filter
        feats = self.pattern.extract_features(event, mtf)
        vec = np.array(
            [[feats.get(col, 0.0) for col in self.feature_columns]],
            dtype=float,
        )
        proba = float(self.model.predict_proba(vec)[0, 1])
        if proba < self.threshold:
            return

        # 4) Barriers
        entry = float(bar.close)
        barriers = self._compute_barriers(entry, event.direction, i, cache)
        if barriers is None:
            return
        tp, sl = barriers

        stop_distance = abs(entry - sl)
        if event.direction == "long":
            try:
                qty = broker.calc_qty(
                    bar.symbol, risk_pct=0.02, stop_distance=stop_distance
                )
            except Exception:
                qty = 1.0
            if qty and qty > 0:
                broker.buy(
                    bar.symbol, qty,
                    stop_loss=sl, take_profit=tp,
                    reason=f"ml:{self.pattern.name} score={proba:.3f}",
                )
                self._open = _OpenPosition(
                    direction="long", entry_price=entry, entry_index=i,
                    tp_price=tp, sl_price=sl,
                )
        else:  # short
            try:
                qty = broker.calc_qty(
                    bar.symbol, risk_pct=0.02, stop_distance=stop_distance
                )
            except Exception:
                qty = 1.0
            if qty and qty > 0:
                broker.sell(
                    bar.symbol, qty,
                    stop_loss=sl, take_profit=tp,
                    reason=f"ml:{self.pattern.name} score={proba:.3f}",
                )
                self._open = _OpenPosition(
                    direction="short", entry_price=entry, entry_index=i,
                    tp_price=tp, sl_price=sl,
                )

    def on_fill(self, fill) -> None:
        return None

    def get_params(self) -> dict:
        params: Dict[str, Any] = {
            "threshold": self.threshold,
            "max_holding_bars": self.max_holding_bars,
            "label_mode": self.label_mode,
            "timeframe": self.timeframe,
        }
        if self.label_mode == "pct":
            params["tp_pct"] = self.tp_pct
            params["sl_pct"] = self.sl_pct
        else:
            params["tp_atr_mult"] = self.tp_atr_mult
            params["sl_atr_mult"] = self.sl_atr_mult
            params["atr_period"] = self.atr_period
        return params

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)


__all__ = ["PatternMLFilterStrategy"]
