"""Tests for BBKCFilterPattern.

The critical test is ``test_parity_with_raw_bbkc_squeeze``: it builds a
synthetic 1h MTF fixture, runs the ML pattern's ``detect_at`` over every
post-warmup bar, runs ``BBKCSqueeze.on_bar_fast`` over the same bars with
a recording broker, and asserts the set of (bar_index, side) pairs is
exactly equal between the two paths. That equality is the parity
contract the filter design hinges on: if it drifts the ML model learns
labels from bars that won't be traded at deployment, or misses bars
that will.

Additional tests:
- ``test_feature_schema_locked`` pins the 11 P0 feature keys (P1).
- ``test_extract_features_guards_h4_when_primary_is_4h`` confirms the
  ``_is_higher_tf`` gate zero-fills h4_* when the pattern runs with
  primary_tf="4h" (P2). 4h is NOT an officially supported runtime --
  this test just protects against feature drift if the pattern is
  ever used on a 4h MTFData fixture.
- ``test_ml_filter_never_emits_more_events_than_raw_bbkc`` is a looser
  version of the P0 parity test useful as a smoke check (P5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.core.types import Bar, BarSeries
from src.ml.patterns.bbkc_filter import BBKCFilterPattern
from src.ml.types import MTFData
from src.strategies.bbkc_squeeze import BBKCSqueeze


H = 3_600_000
D = 24 * H


# ---------------------------------------------------------------------------
# Synthetic fixture: alternates "squeeze" and "expansion" phases. For BB to
# contract inside KC we need close-std to be SMALLER than the high-low
# range (BB uses close std, KC uses ATR of OHLC). The quiet phase holds
# close near a flat level while letting intra-bar high/low span a wider
# range, so BB collapses while KC stays moderate -> squeeze ON. The
# expansion phase injects a directional jump in close so BB widens past
# KC -> squeeze release edge fires.
# ---------------------------------------------------------------------------

def _make_bbkc_fixture(symbol: str = "BTCUSDT", seed: int = 7) -> MTFData:
    rng = np.random.default_rng(seed)
    # Longer series so we clear the pattern's 119-bar warmup with several
    # full 45-bar cycles afterwards.
    n = 600

    closes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    opens: List[float] = []

    price = 100.0
    for i in range(n):
        cycle_pos = i % 45
        if cycle_pos < 30:
            # Quiet phase: close nearly flat, intra-bar range wide.
            # close std ~ 0.01 -> BB contracts
            # high/low span ~ 1.0 -> KC stays wide via ATR
            price = price + rng.normal(0.0, 0.01)
            c = float(price)
            intraday = 0.5 + rng.uniform(0.0, 0.2)  # ~0.5-0.7 half-range
            o = c + rng.normal(0.0, 0.05)
            h = c + intraday
            low = c - intraday
        else:
            # Expansion phase: close jumps directionally with ATR-scale
            # moves so BB widens past KC. Direction alternates across
            # cycles to generate both long and short releases.
            direction = 1.0 if (i // 45) % 2 == 0 else -1.0
            step = rng.normal(direction * 1.5, 0.3)
            price = price + step
            c = float(price)
            o = c - step * 0.5
            # Intra-bar range on expansion bars still modest so ATR
            # grows slowly relative to BB (which grows fast on close
            # std).
            h = max(c, o) + 0.2
            low = min(c, o) - 0.2
        closes.append(c)
        opens.append(float(o))
        highs.append(float(h))
        lows.append(float(low))

    bars_1h = []
    for i in range(n):
        bars_1h.append({
            "timestamp": i * H,
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": 1.0,
            "turnover": 1.0,
        })
    s_1h = BarSeries(symbol=symbol, timeframe="1h", bars=pd.DataFrame(bars_1h))

    # Coarse 4h and 1d series so MTFData is well-formed. Pattern will not
    # read them at primary_tf="1h" beyond the HTF features (which test
    # primary=1h can tolerate any values).
    bars_4h: List[Dict[str, float]] = []
    for j in range(n // 4):
        seg = closes[j * 4 : (j + 1) * 4]
        if not seg:
            continue
        bars_4h.append({
            "timestamp": j * 4 * H,
            "open": float(seg[0]),
            "high": float(max(seg) + 0.1),
            "low": float(min(seg) - 0.1),
            "close": float(seg[-1]),
            "volume": 1.0,
            "turnover": 1.0,
        })
    s_4h = BarSeries(symbol=symbol, timeframe="4h", bars=pd.DataFrame(bars_4h))

    bars_1d: List[Dict[str, float]] = []
    n_d = max(1, n // 24)
    for j in range(n_d):
        seg = closes[j * 24 : (j + 1) * 24]
        if not seg:
            continue
        bars_1d.append({
            "timestamp": j * D,
            "open": float(seg[0]),
            "high": float(max(seg) + 0.2),
            "low": float(min(seg) - 0.2),
            "close": float(seg[-1]),
            "volume": 1.0,
            "turnover": 1.0,
        })
    s_1d = BarSeries(symbol=symbol, timeframe="1d", bars=pd.DataFrame(bars_1d))

    return MTFData(
        symbol=symbol, primary_tf="1h",
        series={"1h": s_1h, "4h": s_4h, "1d": s_1d},
    )


# ---------------------------------------------------------------------------
# Minimal broker stub that records every entry call and skips subsequent
# entries while a position is open. This mirrors the position-state
# semantics BBKCSqueeze relies on (``if pos is not None: return``) so the
# parity test can fairly compare ML pattern events to raw strategy entries.
# ---------------------------------------------------------------------------


@dataclass
class _OpenPos:
    side: str
    entry_index: int
    tp: float
    sl: float


class _RecordingBroker:
    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []
        self._open: Optional[_OpenPos] = None

    # Position state mirror ---------------------------------------------------
    def get_position(self, symbol: str):
        return self._open

    def close_position(self, symbol: str, **kwargs) -> None:
        self._open = None

    # BBKCSqueeze uses calc_qty + buy/sell --------------------------------------
    def calc_qty(self, symbol: str, risk_pct: float, stop_distance: float) -> float:
        return 1.0

    def buy(self, symbol, qty, stop_loss, take_profit, reason, **kwargs):
        self.entries.append({
            "side": "buy", "symbol": symbol, "qty": qty,
            "stop_loss": stop_loss, "take_profit": take_profit,
        })
        self._open = _OpenPos(
            side="long", entry_index=len(self.entries) - 1,
            tp=take_profit, sl=stop_loss,
        )

    def sell(self, symbol, qty, stop_loss, take_profit, reason, **kwargs):
        self.entries.append({
            "side": "sell", "symbol": symbol, "qty": qty,
            "stop_loss": stop_loss, "take_profit": take_profit,
        })
        self._open = _OpenPos(
            side="short", entry_index=len(self.entries) - 1,
            tp=take_profit, sl=stop_loss,
        )

    # Release the position immediately after recording it. This simulates
    # "instant exit" so the next squeeze release bar is not blocked by the
    # pos != None check in BBKCSqueeze. Without this the raw strategy will
    # only emit one entry per fixture, which defeats the point of the test.
    def release_after_record(self) -> None:
        self._open = None


def _bar_from_row(series: BarSeries, i: int) -> Bar:
    row = series.bars.iloc[i]
    return Bar(
        symbol=series.symbol,
        timestamp=int(row["timestamp"]),
        timeframe=series.timeframe,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        turnover=float(row["turnover"]),
    )


# ---------------------------------------------------------------------------
# P0: parity test between ML pattern and raw BBKCSqueeze entries
# ---------------------------------------------------------------------------


def test_parity_with_raw_bbkc_squeeze():
    """The bar indices where BBKCFilterPattern.detect_at fires must equal
    the bar indices where BBKCSqueeze.on_bar_fast would call buy/sell,
    and the direction (long/short vs buy/sell) must match. This is the
    hard contract the filter design depends on -- if it drifts the ML
    label distribution no longer corresponds to what the wrapper will
    actually execute at deployment.
    """
    mtf = _make_bbkc_fixture()
    primary = mtf.get_primary()
    n = len(primary)

    # --- Raw strategy path ---
    raw = BBKCSqueeze()
    broker = _RecordingBroker()
    cache = raw.prepare(primary)
    raw_entries: List[tuple] = []  # (bar_index, side)
    for i in range(max(raw.warmup_bars, 1), n):
        bar = _bar_from_row(primary, i)
        before = len(broker.entries)
        raw.on_bar_fast(bar, i, cache, broker)
        if len(broker.entries) > before:
            raw_entries.append((i, broker.entries[-1]["side"]))
            # Simulate instant exit so the position lock does not block
            # subsequent releases. BBKCFilterPattern is stateless, so it
            # does not have this lock at all -- this keeps the two event
            # sets comparable.
            broker.release_after_record()

    # --- ML pattern path ---
    pattern = BBKCFilterPattern()
    ml_entries: List[tuple] = []
    for i in range(pattern.warmup_bars, n):
        ev = pattern.detect_at(mtf, i)
        if ev is None:
            continue
        side = "buy" if ev.direction == "long" else "sell"
        ml_entries.append((i, side))

    # Warmup alignment: BBKCSqueeze.warmup_bars (max of BB/KC/ATR/RSI + 10
    # = 30) is smaller than BBKCFilterPattern.warmup_bars (119, because
    # of percentile_lookback). The pattern intentionally needs more bars
    # for its regime features. Restrict raw_entries to the same i >= 119
    # range so the sets are comparable.
    raw_entries_gated = [e for e in raw_entries if e[0] >= pattern.warmup_bars]

    assert len(ml_entries) > 0, (
        "synthetic fixture must produce at least one squeeze release; "
        "if this fails the fixture design is wrong, not the pattern"
    )
    assert ml_entries == raw_entries_gated, (
        f"parity violation:\n"
        f"  raw={raw_entries_gated}\n"
        f"  ml ={ml_entries}\n"
        f"  only in raw: {set(raw_entries_gated) - set(ml_entries)}\n"
        f"  only in ml:  {set(ml_entries) - set(raw_entries_gated)}"
    )


def test_ml_filter_never_emits_more_events_than_raw_bbkc():
    """Sanity: the filter can only REJECT entries that raw BBKCSqueeze
    would have taken. It must never fire on a bar the raw strategy
    would skip. This is a weaker version of the parity test above and
    also acts as a guard against the opposite class of bugs (pattern
    firing without the underlying strategy)."""
    mtf = _make_bbkc_fixture()
    primary = mtf.get_primary()
    n = len(primary)

    raw = BBKCSqueeze()
    broker = _RecordingBroker()
    cache = raw.prepare(primary)
    raw_entries: List[tuple] = []
    for i in range(max(raw.warmup_bars, 1), n):
        bar = _bar_from_row(primary, i)
        before = len(broker.entries)
        raw.on_bar_fast(bar, i, cache, broker)
        if len(broker.entries) > before:
            raw_entries.append((i, broker.entries[-1]["side"]))
            broker.release_after_record()

    pattern = BBKCFilterPattern()
    ml_count = 0
    for i in range(pattern.warmup_bars, n):
        if pattern.detect_at(mtf, i) is not None:
            ml_count += 1

    raw_count_gated = sum(1 for e in raw_entries if e[0] >= pattern.warmup_bars)
    assert ml_count <= raw_count_gated, (
        f"ml emitted {ml_count} events but raw only emitted {raw_count_gated} "
        "-- filter cannot fire where raw does not"
    )


# ---------------------------------------------------------------------------
# P1: feature schema locked
# ---------------------------------------------------------------------------


def test_feature_schema_locked():
    """Pin the exact P0 feature set. Rename regressions fail loudly and
    new features have to be added explicitly."""
    mtf = _make_bbkc_fixture()
    pattern = BBKCFilterPattern()
    primary = mtf.get_primary()

    ev = None
    for i in range(pattern.warmup_bars, len(primary)):
        ev = pattern.detect_at(mtf, i)
        if ev is not None:
            break
    assert ev is not None, "fixture must produce at least one event"

    feats = pattern.extract_features(ev, mtf)
    expected = {
        # Squeeze quality
        "squeeze_duration_bars",
        "bb_kc_width_ratio",
        "breakout_magnitude_atr",
        # Volatility regime
        "atr_primary_pct",
        "bb_width_pct_primary",
        # Trend regime
        "adx_primary",
        # Location
        "dist_roll_high_atr",
        "dist_roll_low_atr",
        # HTF context
        "h4_ema_slope_atr_norm",
        "h4_trend_alignment",
        # Meta
        "is_long",
    }
    assert set(feats.keys()) == expected, (
        f"feature drift: missing={expected - set(feats.keys())} "
        f"extra={set(feats.keys()) - expected}"
    )
    for k, v in feats.items():
        assert isinstance(v, float), f"{k} is not float: {type(v)}"
        assert v == v, f"{k} is NaN"  # NaN check
        assert np.isfinite(v), f"{k} is not finite: {v}"


def test_squeeze_duration_is_nonzero_at_release():
    """When an event fires, squeeze_duration_bars must be > 0 -- the
    feature reads duration[i-1] which is the length of the run that
    just ended at the release bar."""
    mtf = _make_bbkc_fixture()
    pattern = BBKCFilterPattern()
    primary = mtf.get_primary()
    for i in range(pattern.warmup_bars, len(primary)):
        ev = pattern.detect_at(mtf, i)
        if ev is None:
            continue
        feats = pattern.extract_features(ev, mtf)
        assert feats["squeeze_duration_bars"] >= 1.0, (
            f"event at i={i} but squeeze_duration_bars={feats['squeeze_duration_bars']}"
        )
        return
    raise AssertionError("no event found in fixture")


# ---------------------------------------------------------------------------
# P2: HTF gate (primary_tf="4h" test only -- production is 1h only)
# ---------------------------------------------------------------------------


def test_extract_features_guards_h4_when_primary_is_4h():
    """If someone runs BBKCFilterPattern with primary_tf='4h', h4_*
    features must zero-fill (self-reference avoided). 4h is NOT an
    officially supported runtime, but the gate must exist or the
    features silently become noise."""
    # Build an MTFData whose primary_tf is 4h, containing a synthetic
    # squeeze release on the 4h series. We reuse the 1h fixture logic
    # but label it as 4h primary.
    mtf_1h = _make_bbkc_fixture(symbol="BTCUSDT", seed=7)
    s_4h_raw = mtf_1h.series["1h"]
    # Rename the series TF so mtf.primary_tf == "4h" can route to it
    s_as_4h = BarSeries(
        symbol=s_4h_raw.symbol, timeframe="4h", bars=s_4h_raw.bars.copy(),
    )
    # Provide a 1d fallback (also synthetic, coarser)
    n = len(s_4h_raw)
    closes = s_4h_raw.bars["close"].to_numpy()
    n_d = max(1, n // 6)  # 1d = 6 x 4h
    bars_1d = []
    for j in range(n_d):
        seg = closes[j * 6 : (j + 1) * 6]
        if len(seg) == 0:
            continue
        bars_1d.append({
            "timestamp": j * D,
            "open": float(seg[0]),
            "high": float(float(seg.max()) + 0.5),
            "low": float(float(seg.min()) - 0.5),
            "close": float(seg[-1]),
            "volume": 1.0,
            "turnover": 1.0,
        })
    s_1d = BarSeries(
        symbol="BTCUSDT", timeframe="1d", bars=pd.DataFrame(bars_1d),
    )
    mtf = MTFData(
        symbol="BTCUSDT", primary_tf="4h",
        series={"4h": s_as_4h, "1d": s_1d},
    )
    pattern = BBKCFilterPattern()

    ev = None
    for i in range(pattern.warmup_bars, len(s_as_4h)):
        ev = pattern.detect_at(mtf, i)
        if ev is not None:
            break
    assert ev is not None, "fixture must produce at least one 4h-primary event"
    feats = pattern.extract_features(ev, mtf)
    # h4_* must be zero because primary itself is 4h -- not strictly higher
    assert feats["h4_ema_slope_atr_norm"] == 0.0
    assert feats["h4_trend_alignment"] == 0.0
