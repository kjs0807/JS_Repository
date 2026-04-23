"""MTF alignment — single entry point for confirmed higher-TF bars.

Critical lookahead prevention rule:
  At primary timestamp t, a higher-TF bar is "confirmed" iff its CLOSE time
  is strictly less than t. Bar close time = timestamp + tf_duration.

All pattern code MUST go through get_confirmed() to read 4h/1d data.
Direct indexing into mtf.series["4h"] is forbidden.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from src.ml.types import MTFData

_TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 4 * 3_600_000,
    "1d": 24 * 3_600_000,
}


def tf_to_ms(tf: str) -> int:
    if tf not in _TF_MS:
        raise KeyError(f"Unsupported timeframe: {tf}")
    return _TF_MS[tf]


def get_confirmed(
    timestamp_ms: int,
    target_tf: str,
    mtf: MTFData,
) -> Optional[pd.Series]:
    """Return the most recent target_tf bar whose close time is strictly < timestamp_ms.

    Returns None if no such bar exists (e.g., before warmup of the higher TF).
    The returned value is a pandas Series (one row of the BarSeries DataFrame).
    """
    if target_tf not in mtf.series:
        raise KeyError(f"target_tf={target_tf} not present in mtf.series")
    series = mtf.series[target_tf]
    duration = tf_to_ms(target_tf)

    # Search forward: bars are timestamp-sorted, so iterate and remember the
    # last bar whose close strictly precedes the requested timestamp.
    last = None
    for _, row in series.bars.iterrows():
        close_time = row["timestamp"] + duration
        if close_time < timestamp_ms:
            last = row
        else:
            break
    return last
