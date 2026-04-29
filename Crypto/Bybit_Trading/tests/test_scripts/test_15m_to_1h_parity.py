"""Test synth_1h_from_15m (round 5 §6)."""
from scripts.check_15m_to_1h_parity import synth_1h_from_15m


def test_synth_one_window_4_bars():
    bars_15m = [
        {"open_time": 0,        "open": 100, "high": 105, "low":  99, "close": 102, "volume": 10},
        {"open_time":  900_000, "open": 102, "high": 108, "low": 101, "close": 107, "volume": 12},
        {"open_time": 1_800_000, "open": 107, "high": 110, "low": 105, "close": 109, "volume":  8},
        {"open_time": 2_700_000, "open": 109, "high": 112, "low": 106, "close": 111, "volume": 15},
    ]
    out = synth_1h_from_15m(bars_15m)
    assert len(out) == 1
    o = out[0]
    assert o["open_time"] == 0
    assert o["open"] == 100
    assert o["high"] == 112
    assert o["low"] == 99
    assert o["close"] == 111
    assert o["volume"] == 45


def test_synth_skips_partial_first_bar():
    """첫 15m 봉이 정각 경계 아니면 skip."""
    bars_15m = [
        {"open_time":  900_000, "open": 102, "high": 108, "low": 101, "close": 107, "volume": 12},
        {"open_time": 1_800_000, "open": 107, "high": 110, "low": 105, "close": 109, "volume":  8},
        {"open_time": 2_700_000, "open": 109, "high": 112, "low": 106, "close": 111, "volume": 15},
        # 다음 정각 봉은 3_600_000
        {"open_time": 3_600_000, "open": 111, "high": 113, "low": 110, "close": 112, "volume":  9},
    ]
    out = synth_1h_from_15m(bars_15m)
    assert len(out) == 0   # 정각 시작 봉이 1개뿐이라 4개 채우지 못함


def test_synth_two_windows():
    bars_15m = [
        # window 1
        {"open_time":         0, "open": 100, "high": 105, "low":  99, "close": 102, "volume": 10},
        {"open_time":   900_000, "open": 102, "high": 108, "low": 101, "close": 107, "volume": 12},
        {"open_time": 1_800_000, "open": 107, "high": 110, "low": 105, "close": 109, "volume":  8},
        {"open_time": 2_700_000, "open": 109, "high": 112, "low": 106, "close": 111, "volume": 15},
        # window 2
        {"open_time": 3_600_000, "open": 111, "high": 113, "low": 110, "close": 112, "volume":  9},
        {"open_time": 4_500_000, "open": 112, "high": 115, "low": 111, "close": 114, "volume": 11},
        {"open_time": 5_400_000, "open": 114, "high": 116, "low": 113, "close": 115, "volume": 13},
        {"open_time": 6_300_000, "open": 115, "high": 117, "low": 113, "close": 116, "volume":  7},
    ]
    out = synth_1h_from_15m(bars_15m)
    assert len(out) == 2
    assert out[0]["open_time"] == 0
    assert out[1]["open_time"] == 3_600_000
    assert out[1]["close"] == 116
    assert out[1]["high"] == 117
