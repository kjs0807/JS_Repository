"""BBKCSqueeze + 4h EMA(50) trend filter (Variant BBKC+HTF).

BBKCSqueeze with a 4h EMA(50) higher-timeframe alignment gate added
on top of the existing squeeze release + bb_mid + RSI entry conditions.
The baseline BBKCSqueeze reads only its 1h primary feed and has no
higher-timeframe context, so counter-trend squeeze releases get taken
at the same weight as trend-aligned ones. Adding a 1-bit "1h close vs
confirmed 4h EMA(50)" gate is the cheapest rule-based way to bring HTF
trend awareness into the strategy without changing any existing
behavior.

Key design decisions:

1. Self-contained 4h aggregation from the 1h feed.
   The BacktestEngine feeds strategies a single-timeframe BarSeries.
   This class resamples 1h -> 4h internally inside ``prepare`` and
   caches an aligned 4h EMA array with one value per 1h bar. No new
   feed infrastructure, no cross-symbol pollution, no MTFData plumbing.

2. Lookahead-safe "confirmed 4h" alignment.
   At 1h bar index ``i`` with open timestamp ``ts[i]``, the aligned 4h
   EMA is the value from the MOST RECENT 4h bucket that has already
   closed BEFORE or AT ``ts[i]``. Concretely: if ``ts[i]`` falls inside
   4h bucket ``k``, the confirmed 4h bar is bucket ``k - 1`` (the last
   one that finished). At the exact start of a new bucket the same
   rule holds -- we still use bucket ``k - 1`` because bucket ``k``
   has only just opened.

3. Inherits from BBKCSqueeze.
   Overrides ``prepare`` / ``on_bar`` / ``on_bar_fast`` only. All
   indicators, the RSI filter, the TP/SL math, and the position lock
   are inherited unchanged. This keeps baseline parity intact.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from src.core.types import Bar, BarSeries
from src.execution.broker import Broker
from src.strategies.base import IndicatorCache
from src.strategies.bbkc_squeeze import BBKCSqueeze
from src.strategies.indicators.momentum import atr, bollinger, keltner
from src.strategies.indicators.oscillator import rsi
from src.strategies.indicators.trend import ema


_H4_MS = 4 * 60 * 60 * 1000  # 4 hours in milliseconds


def _aggregate_1h_to_4h(
    full_series: BarSeries,
) -> "tuple[pd.DataFrame, np.ndarray]":
    """Resample a 1h BarSeries into 4h OHLC buckets.

    Returns ``(agg_df, bucket_per_1h_bar)`` where:
      - ``agg_df`` is a DataFrame with columns timestamp/open/high/low/
        close/volume, one row per 4h bucket that has at least one 1h
        bar. ``timestamp`` is the bucket start (``bucket_index * 4h``).
        Bars within a bucket are grouped by ``timestamp // 4h``.
      - ``bucket_per_1h_bar`` is a numpy int array of length
        ``len(full_series.bars)`` mapping each 1h bar to its 4h
        bucket index.

    Buckets are referenced by the absolute bucket index (``ts // 4h``)
    so callers can align them with 1h bars via subtraction.
    """
    bars = full_series.bars
    if len(bars) == 0:
        return pd.DataFrame(columns=[
            "timestamp", "open", "high", "low", "close", "volume",
        ]), np.array([], dtype=int)

    ts = bars["timestamp"].to_numpy().astype(np.int64)
    bucket = (ts // _H4_MS).astype(np.int64)

    df = bars.copy()
    df["_bucket"] = bucket
    agg = (
        df.groupby("_bucket", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
    )
    agg["timestamp"] = agg["_bucket"].astype(np.int64) * _H4_MS
    # turnover optional -- keep column shape stable if upstream needs it
    if "turnover" in bars.columns:
        agg["turnover"] = 1.0
    # Reorder to match BarSeries convention
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    if "turnover" in agg.columns:
        cols.append("turnover")
    cols.append("_bucket")
    agg = agg[cols]
    return agg, bucket


def _build_confirmed_4h_ema(
    full_series: BarSeries,
    period: int,
) -> np.ndarray:
    """Compute an EMA(period) on the 4h-aggregated version of a 1h
    BarSeries, then align each 1h index to the MOST RECENT CONFIRMED
    4h EMA value (``close_time <= current 1h open_time``).

    Returns a numpy array of length ``len(full_series.bars)``. NaN for
    any 1h bar for which no confirmed 4h EMA exists yet (early warmup
    or missing 4h data).

    The alignment rule "confirmed bucket = current_bucket - 1" is
    conservative: at the exact opening bar of a new 4h bucket, the new
    bucket has not closed yet, so we still look at the previous
    bucket's EMA. This avoids any lookahead, including the "peek at
    the very last second of the just-closed bucket" case.
    """
    bars = full_series.bars
    n = len(bars)
    if n == 0:
        return np.array([], dtype=float)

    agg, bucket_per_1h = _aggregate_1h_to_4h(full_series)
    if len(agg) == 0:
        return np.full(n, np.nan, dtype=float)

    # Build a 4h BarSeries from the aggregation and run the shared EMA
    # implementation for bit-for-bit parity with other 4h EMA callers.
    agg_for_series = agg.drop(columns=["_bucket"], errors="ignore")
    s_4h = BarSeries(
        symbol=full_series.symbol, timeframe="4h", bars=agg_for_series,
    )
    ema_4h_vals = ema(s_4h, period=period).values  # length == len(agg)

    # Map bucket index -> position in ema_4h array
    bucket_to_pos = {
        int(b): pos for pos, b in enumerate(agg["_bucket"].to_numpy())
    }

    aligned = np.full(n, np.nan, dtype=float)
    for i in range(n):
        # The 4h bucket that contains the current 1h bar is bucket_per_1h[i].
        # The most recent CONFIRMED 4h bar is one bucket earlier.
        confirmed_bucket = int(bucket_per_1h[i]) - 1
        pos = bucket_to_pos.get(confirmed_bucket)
        if pos is None:
            continue
        val = ema_4h_vals[pos]
        if np.isnan(val):
            continue
        aligned[i] = float(val)
    return aligned


class BBKCSqueezeHTFTrend(BBKCSqueeze):
    """BBKCSqueeze with a confirmed 4h EMA(htf_ema_period) trend gate.

    Adds a single 1-bit condition to the baseline BBKCSqueeze entry:
        LONG requires  1h_close > confirmed_4h_ema
        SHORT requires 1h_close < confirmed_4h_ema

    All other logic (squeeze release edge, close vs bb_mid direction,
    RSI overextension filter, fixed pct TP/SL, leverage-adjusted
    barriers, position lock) is inherited unchanged from BBKCSqueeze.
    """

    name: str = "BBKCSqueeze_HTF_Trend"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 1.5,
        kc_period: int = 20,
        kc_mult: float = 1.0,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_filter: float = 70.0,
        tp_pct: float = 0.06,
        sl_pct: float = 0.07,
        leverage: int = 3,
        htf_ema_period: int = 50,
        timeframe: str = "1h",
    ) -> None:
        super().__init__(
            bb_period=bb_period,
            bb_std=bb_std,
            kc_period=kc_period,
            kc_mult=kc_mult,
            atr_period=atr_period,
            rsi_period=rsi_period,
            rsi_filter=rsi_filter,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            leverage=leverage,
            timeframe=timeframe,
        )
        self.htf_ema_period = htf_ema_period

    @property
    def warmup_bars(self) -> int:
        base = super().warmup_bars
        # Need enough 4h bars for EMA(htf_ema_period) to have a value.
        # 4h EMA(50) needs 50 4h bars = 200 1h bars; the alignment also
        # waits one bucket, so +4. Round up to a safe multiple of 4.
        htf_min = self.htf_ema_period * 4 + 4
        return max(base, htf_min)

    def prepare(self, full_series: BarSeries) -> IndicatorCache:
        cache = super().prepare(full_series)
        cache.arrays["htf_ema_4h"] = _build_confirmed_4h_ema(
            full_series, period=self.htf_ema_period,
        )
        return cache

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        """Legacy slow path -- matches BBKCSqueeze.on_bar shape by
        calling prepare + on_bar_fast. Kept for unit-test compatibility."""
        if len(series) < self.warmup_bars:
            return
        cache = self.prepare(series)
        idx = len(series) - 1
        self.on_bar_fast(bar, idx, cache, broker)

    def on_bar_fast(self, bar: Bar, i: int, cache, broker) -> None:
        # HTF gate applies only to NEW entries, not to managing existing
        # positions. The parent already skips early-return when
        # ``pos is not None``, so we pre-check the gate only on the
        # no-position path.
        pos = broker.get_position(bar.symbol)
        if pos is None:
            htf_arr = cache.arrays.get("htf_ema_4h")
            if htf_arr is None or i >= len(htf_arr):
                return
            htf_val = htf_arr[i]
            if np.isnan(htf_val):
                return  # No confirmed 4h EMA yet -> no entry
            close = bar.close
            # We do NOT know yet which direction the parent would pick
            # (long vs short depends on close vs bb_mid, computed inside
            # the parent). So apply a directional gate consistent with
            # BBKCSqueeze's own direction logic: squeeze releases above
            # the HTF EMA only go long, below only go short. Any close
            # that would contradict the HTF direction is blocked.
            #
            # This is implemented by short-circuit: if close > htf_ema
            # only allow the long branch to fire, if close < htf_ema
            # only allow short. We cannot cheaply intercept the parent
            # per-branch, so we call the parent and rely on the fact
            # that its direction logic already uses the same ``close``
            # value -- after the parent fires, the wrapper checks if
            # the emitted order matches the HTF direction and, if not,
            # the parent itself would not have chosen that direction.
            #
            # Practical implementation: the parent's direction check is
            # ``close > bb_mid -> long`` and ``close < bb_mid -> short``.
            # The HTF gate is applied as an AND with ``close > htf``
            # for long and ``close < htf`` for short. We apply the gate
            # by checking whether the parent WOULD fire a long (close
            # > bb_mid) and whether that aligns with ``close > htf``.
            # If it does not align, we simply return before delegating.
            bb_mid_arr = cache.arrays.get("bb_mid")
            if bb_mid_arr is None or i >= len(bb_mid_arr):
                return
            bb_mid_i = bb_mid_arr[i]
            if np.isnan(bb_mid_i):
                return
            long_leaning = close > bb_mid_i
            short_leaning = close < bb_mid_i
            long_aligned = close > htf_val
            short_aligned = close < htf_val
            if long_leaning and not long_aligned:
                return  # parent would go long but HTF rejects it
            if short_leaning and not short_aligned:
                return  # parent would go short but HTF rejects it
            # Otherwise direction and HTF are consistent (or no
            # direction yet -- parent will handle all other filters).
        # Delegate to parent for the actual entry / exit mechanics.
        super().on_bar_fast(bar, i, cache, broker)

    def get_params(self) -> dict:
        params = super().get_params()
        params["htf_ema_period"] = self.htf_ema_period
        return params


__all__ = ["BBKCSqueezeHTFTrend", "_build_confirmed_4h_ema"]
