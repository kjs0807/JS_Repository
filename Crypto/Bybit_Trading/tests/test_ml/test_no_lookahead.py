"""Critical lookahead-prevention test for ML patterns.

Strategy:
- Build full MTF data with N bars.
- For each pattern, walk i from warmup to N-1.
  - At each i, if the pattern fires, compute features twice:
      a) using the full-length MTF
      b) using an MTF sliced to include only bars whose close time is <= t
  - The pattern's event and feature dict MUST be identical in both cases.
- Any mismatch indicates the pattern accessed data beyond index i.
"""
import numpy as np
import pandas as pd
import pytest

from src.core.types import BarSeries
from src.ml.patterns.bbkc_filter import BBKCFilterPattern
from src.ml.patterns.engulfing_mtf import EngulfingMTF
from src.ml.patterns.rsi_divergence import RSIDivergence
from src.ml.types import MTFData


H = 3_600_000  # 1h in ms


def _series_from_records(records, tf, symbol):
    return BarSeries(
        symbol=symbol,
        timeframe=tf,
        bars=pd.DataFrame(records),
    )


def _build_full_mtf(symbol="BTCUSDT", n=240, seed=123):
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 0.5, n))
    # Inject a few engulfing pairs so EngulfingMTF actually fires
    for k in (60, 120, 180):
        # red then green engulfing
        closes[k - 1] = closes[k - 1] + 6.0  # push open/high/close profile
        closes[k] = closes[k - 1] + 8.0
    bars_1h = []
    for i in range(n):
        c = float(closes[i])
        # Build OHLC so that k-1 is red and k is engulfing green at the injected points
        if i in (60 - 1, 120 - 1, 180 - 1):
            o, h, l = c + 5.0, c + 6.0, c - 1.0
        elif i in (60, 120, 180):
            prev_open = float(closes[i - 1]) + 5.0
            prev_close = float(closes[i - 1])
            o = prev_close - 1.0
            h = prev_open + 10.0
            l = prev_close - 2.0
            c = prev_open + 9.0
        else:
            o = c - 0.5
            h = c + 1.0
            l = c - 1.0
        bars_1h.append({
            "timestamp": i * H,
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": 1.0, "turnover": 1.0,
        })
    s_1h = _series_from_records(bars_1h, "1h", symbol)

    bars_4h = []
    for i in range(n // 4):
        seg_closes = [float(b["close"]) for b in bars_1h[i * 4 : (i + 1) * 4]]
        if not seg_closes:
            continue
        o = seg_closes[0]
        c = seg_closes[-1]
        h = max(seg_closes)
        low = min(seg_closes)
        bars_4h.append({
            "timestamp": i * 4 * H,
            "open": o, "high": h + 0.5, "low": low - 0.5, "close": c,
            "volume": 1.0, "turnover": 1.0,
        })
    s_4h = _series_from_records(bars_4h, "4h", symbol)

    bars_1d = []
    for i in range(max(1, n // 24)):
        seg_closes = [float(b["close"]) for b in bars_1h[i * 24 : (i + 1) * 24]]
        if not seg_closes:
            continue
        o = seg_closes[0]
        c = seg_closes[-1]
        h = max(seg_closes)
        low = min(seg_closes)
        bars_1d.append({
            "timestamp": i * 24 * H,
            "open": o, "high": h + 1.0, "low": low - 1.0, "close": c,
            "volume": 1.0, "turnover": 1.0,
        })
    s_1d = _series_from_records(bars_1d, "1d", symbol)

    return MTFData(symbol=symbol, primary_tf="1h",
                   series={"1h": s_1h, "4h": s_4h, "1d": s_1d})


def _slice_mtf_upto_primary_index(mtf: MTFData, max_primary_idx: int) -> MTFData:
    """Truncate every TF in mtf to bars whose close time is at or before the
    primary bar indexed at max_primary_idx (inclusive on close of bar max_primary_idx).

    That is: all bars with open_time < primary[max_primary_idx].open_time + 1h duration."""
    primary = mtf.get_primary()
    if max_primary_idx >= len(primary):
        return mtf
    tf_duration = {"1h": H, "4h": 4 * H, "1d": 24 * H}
    max_primary_close = int(primary.bars["timestamp"].iloc[max_primary_idx]) + H
    new_series = {}
    for tf, s in mtf.series.items():
        dur = tf_duration[tf]
        # Keep bars whose close time <= max_primary_close
        mask = s.bars["timestamp"] + dur <= max_primary_close
        new_df = s.bars[mask].reset_index(drop=True)
        new_series[tf] = BarSeries(symbol=s.symbol, timeframe=tf, bars=new_df)
    return MTFData(symbol=mtf.symbol, primary_tf=mtf.primary_tf, series=new_series)


@pytest.mark.parametrize("pattern_cls", [EngulfingMTF, RSIDivergence])
def test_pattern_features_have_no_lookahead(pattern_cls):
    full_mtf = _build_full_mtf()
    primary = full_mtf.get_primary()
    n = len(primary)

    pattern_full = pattern_cls()
    checked = 0

    for i in range(pattern_full.warmup_bars, n):
        ev_full = pattern_full.detect_at(full_mtf, i)
        if ev_full is None:
            continue

        # Build a fresh instance so any pattern-internal cache is clean
        pattern_sliced = pattern_cls()
        sliced_mtf = _slice_mtf_upto_primary_index(full_mtf, i)

        # The same local index `i` may not be valid in the sliced MTF iff the
        # slice truncates the primary — but by construction the primary is kept
        # up to and including index i, so i is still valid.
        ev_sliced = pattern_sliced.detect_at(sliced_mtf, i)
        assert ev_sliced is not None, (
            f"[{pattern_cls.__name__}] At i={i}, pattern fired on FULL data "
            "but NOT on sliced-to-i data → lookahead leakage."
        )

        feats_full = pattern_full.extract_features(ev_full, full_mtf)
        feats_sliced = pattern_sliced.extract_features(ev_sliced, sliced_mtf)
        for key, a in feats_full.items():
            b = feats_sliced[key]
            # Allow tiny floating-point drift from pandas reindexing
            assert abs(a - b) < 1e-9, (
                f"[{pattern_cls.__name__}] i={i} feature '{key}' differs: "
                f"full={a} sliced={b} → lookahead."
            )
        checked += 1

    assert checked > 0, (
        f"No events detected for {pattern_cls.__name__} in the synthetic fixture; "
        "the test did not actually verify anything."
    )


def _build_bbkc_friendly_mtf(symbol: str = "BTCUSDT", seed: int = 7) -> MTFData:
    """Squeeze/expansion fixture for BBKCFilterPattern no-lookahead test.

    The fixture in _build_full_mtf is engulfing/divergence oriented and
    does not produce BBKC squeeze releases, so BBKC needs its own.
    Close is nearly flat during the quiet phase (BB contracts) while
    high/low remain wide (KC stays moderate) -> squeeze ON. During
    expansion phases close jumps directionally so BB overtakes KC and
    the release edge fires.
    """
    rng = np.random.default_rng(seed)
    n = 700
    closes = []
    highs = []
    lows = []
    opens = []
    price = 100.0
    for i in range(n):
        cycle_pos = i % 45
        if cycle_pos < 30:
            price += rng.normal(0.0, 0.01)
            c = float(price)
            intraday = 0.5 + rng.uniform(0.0, 0.2)
            o = c + rng.normal(0.0, 0.05)
            h = c + intraday
            low = c - intraday
        else:
            direction = 1.0 if (i // 45) % 2 == 0 else -1.0
            step = rng.normal(direction * 1.5, 0.3)
            price += step
            c = float(price)
            o = c - step * 0.5
            h = max(c, o) + 0.2
            low = min(c, o) - 0.2
        closes.append(c)
        opens.append(float(o))
        highs.append(float(h))
        lows.append(float(low))

    bars_1h = [{
        "timestamp": i * H,
        "open": float(opens[i]),
        "high": float(highs[i]),
        "low": float(lows[i]),
        "close": float(closes[i]),
        "volume": 1.0,
        "turnover": 1.0,
    } for i in range(n)]
    s_1h = _series_from_records(bars_1h, "1h", symbol)

    # Coarse resamples so MTFData is well-formed
    def _resample(step):
        out = []
        for j in range(n // step):
            seg_c = closes[j * step : (j + 1) * step]
            seg_h = highs[j * step : (j + 1) * step]
            seg_l = lows[j * step : (j + 1) * step]
            if not seg_c:
                continue
            out.append({
                "timestamp": j * step * H,
                "open": float(opens[j * step]),
                "high": float(max(seg_h)),
                "low": float(min(seg_l)),
                "close": float(seg_c[-1]),
                "volume": 1.0,
                "turnover": 1.0,
            })
        return out

    s_4h = _series_from_records(_resample(4), "4h", symbol)
    s_1d = _series_from_records(_resample(24), "1d", symbol)

    return MTFData(
        symbol=symbol, primary_tf="1h",
        series={"1h": s_1h, "4h": s_4h, "1d": s_1d},
    )


def test_bbkc_filter_pattern_features_have_no_lookahead():
    """Dedicated no-lookahead check for BBKCFilterPattern using a
    squeeze-friendly fixture."""
    full_mtf = _build_bbkc_friendly_mtf()
    primary = full_mtf.get_primary()
    n = len(primary)

    pattern_full = BBKCFilterPattern()
    checked = 0

    for i in range(pattern_full.warmup_bars, n):
        ev_full = pattern_full.detect_at(full_mtf, i)
        if ev_full is None:
            continue

        pattern_sliced = BBKCFilterPattern()
        sliced_mtf = _slice_mtf_upto_primary_index(full_mtf, i)

        ev_sliced = pattern_sliced.detect_at(sliced_mtf, i)
        assert ev_sliced is not None, (
            f"[BBKCFilterPattern] At i={i}, pattern fired on FULL data "
            "but NOT on sliced-to-i data → lookahead leakage."
        )

        feats_full = pattern_full.extract_features(ev_full, full_mtf)
        feats_sliced = pattern_sliced.extract_features(ev_sliced, sliced_mtf)
        for key, a in feats_full.items():
            b = feats_sliced[key]
            assert abs(a - b) < 1e-9, (
                f"[BBKCFilterPattern] i={i} feature '{key}' differs: "
                f"full={a} sliced={b} → lookahead."
            )
        checked += 1

    assert checked > 0, (
        "No events detected for BBKCFilterPattern in the squeeze fixture; "
        "test did not verify anything."
    )
