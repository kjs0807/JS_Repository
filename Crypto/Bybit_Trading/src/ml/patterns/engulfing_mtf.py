"""Bullish/Bearish Engulfing pattern with MTF context features.

Regime/body ratio features are suffixed ``_primary`` — they are computed
on whatever timeframe the caller sets as ``mtf.primary_tf`` (1h, 4h, or
1d). HTF features keep the ``h4_`` / ``d1_`` prefix but are zero-filled
when the referenced TF is not strictly higher than primary (avoids
self-reference bugs when running with primary_tf="4h" or "1d").
"""
from __future__ import annotations

from typing import Dict, Optional

from src.ml.helpers.candle import (
    is_bullish_engulfing, is_bearish_engulfing,
    candle_body_ratio,
)
from src.ml.helpers.mtf_align import get_confirmed
from src.ml.patterns.base import BasePattern
from src.ml.types import MTFData, PatternEvent


# NOTE: duplicated from src/ml/patterns/rsi_divergence.py on purpose —
# refactor to src/ml/helpers/tf_order.py if a third pattern ever needs it.
_TF_ORDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]


def _is_higher_tf(target: str, primary: str) -> bool:
    if target not in _TF_ORDER or primary not in _TF_ORDER:
        return False
    return _TF_ORDER.index(target) > _TF_ORDER.index(primary)


class EngulfingMTF(BasePattern):
    name = "engulfing_mtf"
    version = "1.0.0"
    timeframes = ["1h", "4h", "1d"]
    direction = "both"
    warmup_bars = 5

    def detect_at(self, mtf: MTFData, i: int) -> Optional[PatternEvent]:
        if i < self.warmup_bars:
            return None
        primary = mtf.get_primary()
        if i >= len(primary):
            return None
        if is_bullish_engulfing(primary, i):
            ts = int(primary.bars["timestamp"].iloc[i])
            return PatternEvent(
                timestamp_ms=ts,
                bar_index=i, symbol=mtf.symbol, direction="long",
                metadata={"variant": "bullish_engulfing"},
            )
        if is_bearish_engulfing(primary, i):
            ts = int(primary.bars["timestamp"].iloc[i])
            return PatternEvent(
                timestamp_ms=ts,
                bar_index=i, symbol=mtf.symbol, direction="short",
                metadata={"variant": "bearish_engulfing"},
            )
        return None

    def extract_features(self, event: PatternEvent, mtf: MTFData) -> Dict[str, float]:
        primary = mtf.get_primary()
        i = event.bar_index
        cur = primary.bars.iloc[i]
        prev = primary.bars.iloc[i - 1]
        cur_body = abs(float(cur["close"]) - float(cur["open"]))
        prev_body = max(abs(float(prev["close"]) - float(prev["open"])), 1e-12)
        engulf_ratio = cur_body / prev_body

        feats: Dict[str, float] = {
            "engulf_size_ratio": float(engulf_ratio),
            "cur_body_ratio_primary": float(candle_body_ratio(primary, i)),
            "prev_body_ratio_primary": float(candle_body_ratio(primary, i - 1)),
            "is_long": 1.0 if event.direction == "long" else 0.0,
        }
        ts = int(cur["timestamp"])
        primary_tf = mtf.primary_tf

        # HTF features: only compute on TFs strictly higher than primary.
        # Otherwise we'd emit self-reference (4h close>open when primary=4h)
        # that looks like a real trend signal but is effectively noise.
        if _is_higher_tf("4h", primary_tf) and "4h" in mtf.series:
            h4 = get_confirmed(ts, "4h", mtf)
        else:
            h4 = None
        if h4 is not None:
            feats["h4_trend_up"] = 1.0 if float(h4["close"]) > float(h4["open"]) else 0.0
            feats["h4_body_ratio"] = float(
                abs(float(h4["close"]) - float(h4["open"])) /
                max(float(h4["high"]) - float(h4["low"]), 1e-12)
            )
        else:
            feats["h4_trend_up"] = 0.0
            feats["h4_body_ratio"] = 0.0

        if _is_higher_tf("1d", primary_tf) and "1d" in mtf.series:
            d1 = get_confirmed(ts, "1d", mtf)
        else:
            d1 = None
        if d1 is not None:
            feats["d1_trend_up"] = 1.0 if float(d1["close"]) > float(d1["open"]) else 0.0
            feats["d1_body_ratio"] = float(
                abs(float(d1["close"]) - float(d1["open"])) /
                max(float(d1["high"]) - float(d1["low"]), 1e-12)
            )
        else:
            feats["d1_trend_up"] = 0.0
            feats["d1_body_ratio"] = 0.0
        return feats
