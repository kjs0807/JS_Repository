"""Tests for EngulfingMTF pattern."""
import pandas as pd

from src.core.types import BarSeries
from src.ml.patterns.engulfing_mtf import EngulfingMTF
from src.ml.types import MTFData


H = 3_600_000
D = 24 * H


def _series(bars_args, tf, step_ms, symbol="BTCUSDT"):
    rows = []
    for i, (o, h, l, c) in enumerate(bars_args):
        rows.append({
            "timestamp": i * step_ms,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": 1.0, "turnover": 1.0,
        })
    return BarSeries(symbol=symbol, timeframe=tf, bars=pd.DataFrame(rows))


def _mtf_with_engulfing_at(idx_1h, total_1h=120):
    # Default neutral 1h candles
    bars_1h = [(100.0, 101.0, 99.0, 100.5)] * total_1h
    # Inject bullish engulfing at idx_1h-1, idx_1h
    bars_1h[idx_1h - 1] = (110.0, 111.0, 104.0, 105.0)  # red
    bars_1h[idx_1h] = (104.0, 116.0, 103.0, 115.0)      # green engulfing
    s_1h = _series(bars_1h, "1h", H)

    bars_4h = [(100.0 + i * 0.5, 102.0 + i * 0.5, 99.5 + i * 0.5, 101.0 + i * 0.5)
               for i in range(total_1h // 4)]
    s_4h = _series(bars_4h, "4h", 4 * H)
    n_d = max(1, total_1h // 24)
    bars_1d = [(100.0 + i * 1.0, 105.0 + i * 1.0, 99.0 + i * 1.0, 103.0 + i * 1.0)
               for i in range(n_d)]
    s_1d = _series(bars_1d, "1d", D)

    return MTFData(symbol="BTCUSDT", primary_tf="1h",
                   series={"1h": s_1h, "4h": s_4h, "1d": s_1d})


def test_detect_at_finds_bullish_engulfing():
    pattern = EngulfingMTF()
    mtf = _mtf_with_engulfing_at(idx_1h=80)
    ev = pattern.detect_at(mtf, i=80)
    assert ev is not None
    assert ev.direction == "long"
    assert ev.bar_index == 80


def test_detect_at_returns_none_when_no_engulfing():
    pattern = EngulfingMTF()
    mtf = _mtf_with_engulfing_at(idx_1h=80)
    ev = pattern.detect_at(mtf, i=50)
    assert ev is None


def test_extract_features():
    pattern = EngulfingMTF()
    mtf = _mtf_with_engulfing_at(idx_1h=80)
    ev = pattern.detect_at(mtf, i=80)
    assert ev is not None
    feats = pattern.extract_features(ev, mtf)
    assert "h4_trend_up" in feats
    assert "d1_trend_up" in feats
    assert "engulf_size_ratio" in feats
    assert isinstance(feats["engulf_size_ratio"], float)


def test_extract_features_schema_locked():
    """Pin the exact feature column set. Catches rename / typo regressions
    like the *_1h -> *_primary fix, and forces new features to be added
    explicitly to the expected set."""
    pattern = EngulfingMTF()
    mtf = _mtf_with_engulfing_at(idx_1h=80)
    ev = pattern.detect_at(mtf, i=80)
    assert ev is not None
    feats = pattern.extract_features(ev, mtf)
    expected = {
        "engulf_size_ratio",
        "cur_body_ratio_primary",
        "prev_body_ratio_primary",
        "is_long",
        "h4_trend_up", "h4_body_ratio",
        "d1_trend_up", "d1_body_ratio",
    }
    assert set(feats.keys()) == expected, (
        f"feature set drift: missing={expected - set(feats.keys())} "
        f"extra={set(feats.keys()) - expected}"
    )
    for k, v in feats.items():
        assert isinstance(v, float), f"{k} is not float: {type(v)}"
        # NaN is not allowed — downstream expects numeric
        assert v == v, f"{k} is NaN"


def _mtf_primary_4h_with_engulfing():
    """Build an MTFData whose primary_tf is '4h', with a bullish engulfing
    at a known 4h bar index. The 1h and 1d series are also present so the
    pattern can look them up via get_confirmed, but only 1d should be
    strictly higher than primary=4h — so h4_* features must be zero."""
    n_4h = 120
    bars_4h = [(100.0, 101.0, 99.0, 100.5)] * n_4h
    # Inject bullish engulfing at idx 80: prev red, cur green engulfing
    bars_4h[79] = (110.0, 111.0, 104.0, 105.0)
    bars_4h[80] = (104.0, 116.0, 103.0, 115.0)
    s_4h = _series(bars_4h, "4h", 4 * H)

    # 1h present but not used as primary. Any shape works — the pattern
    # won't touch it because primary=4h and 1h is not strictly higher.
    bars_1h = [(100.0, 101.0, 99.0, 100.5)] * (n_4h * 4)
    s_1h = _series(bars_1h, "1h", H)

    # 1d is the only strictly-higher TF remaining for primary=4h.
    n_d = max(1, n_4h // 6)  # 1d = 6 x 4h
    bars_1d = [(100.0 + i, 105.0 + i, 99.0 + i, 103.0 + i) for i in range(n_d)]
    s_1d = _series(bars_1d, "1d", D)

    return MTFData(
        symbol="BTCUSDT", primary_tf="4h",
        series={"1h": s_1h, "4h": s_4h, "1d": s_1d},
    )


def test_extract_features_guards_htf_when_primary_is_4h():
    """primary_tf='4h' -> h4_* features must be zero (self-reference
    avoided). 1d is strictly higher than 4h so d1_* can still populate."""
    pattern = EngulfingMTF()
    mtf = _mtf_primary_4h_with_engulfing()
    ev = pattern.detect_at(mtf, i=80)
    assert ev is not None
    feats = pattern.extract_features(ev, mtf)
    # h4_* must be zero-filled (not computed from own primary series)
    assert feats["h4_trend_up"] == 0.0
    assert feats["h4_body_ratio"] == 0.0
    # d1_* is still a strictly-higher TF and should carry real values
    # The synthetic 1d series has close>open so d1_trend_up should be 1.0
    assert feats["d1_trend_up"] == 1.0
    assert feats["d1_body_ratio"] > 0.0


def test_extract_features_guards_htf_when_primary_is_1d():
    """primary_tf='1d' -> both h4_* and d1_* must be zero (nothing is
    strictly higher than 1d in this pattern's TF set)."""
    n_1d = 60
    bars_1d = [(100.0, 101.0, 99.0, 100.5)] * n_1d
    bars_1d[40] = (110.0, 111.0, 104.0, 105.0)
    bars_1d[41] = (104.0, 116.0, 103.0, 115.0)
    s_1d = _series(bars_1d, "1d", D)
    s_4h = _series([(100.0, 101.0, 99.0, 100.5)] * (n_1d * 6), "4h", 4 * H)
    s_1h = _series([(100.0, 101.0, 99.0, 100.5)] * (n_1d * 24), "1h", H)

    mtf = MTFData(
        symbol="BTCUSDT", primary_tf="1d",
        series={"1h": s_1h, "4h": s_4h, "1d": s_1d},
    )
    pattern = EngulfingMTF()
    ev = pattern.detect_at(mtf, i=41)
    assert ev is not None
    feats = pattern.extract_features(ev, mtf)
    assert feats["h4_trend_up"] == 0.0
    assert feats["h4_body_ratio"] == 0.0
    assert feats["d1_trend_up"] == 0.0
    assert feats["d1_body_ratio"] == 0.0
