"""PR 8 KeltnerChannel 단위 테스트 (synthetic OHLCV).

기본값: legacy 호환 (period=20, multiplier=1.0, atr_period=14, use_ema=True).
SMA 모드는 명시적으로 `use_ema=False`로 활성 — 단위 테스트는 SMA 모드(예측 가능 산술)를
주로 사용하고 EMA 모드는 별도 smoke 테스트로 검증.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from backtester.indicators import Indicator
from backtester.indicators.stateless.kc import KeltnerChannel

UTC = timezone.utc


def _make_ohlcv(n: int) -> pl.DataFrame:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n)],
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1.0] * n,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))


# ---------- 기본값 / 파라미터 ----------------------------------------------


def test_kc_default_values_match_legacy() -> None:
    """기본값: period=20, multiplier=1.0, atr_period=14, use_ema=True."""
    kc = KeltnerChannel()
    assert kc.period == 20
    assert kc.multiplier == 1.0
    assert kc.atr_period == 14
    assert kc.use_ema is True


def test_kc_invalid_params() -> None:
    with pytest.raises(ValueError, match="period"):
        KeltnerChannel(period=1)
    with pytest.raises(ValueError, match="atr_period"):
        KeltnerChannel(period=20, atr_period=0)
    with pytest.raises(ValueError, match="multiplier"):
        KeltnerChannel(period=20, multiplier=0)
    with pytest.raises(ValueError, match="multiplier"):
        KeltnerChannel(period=20, multiplier=-0.5)


def test_kc_protocol_compatibility() -> None:
    kc: Indicator = KeltnerChannel()
    assert kc.required_warmup_bars() == max(20, 14)


# ---------- SMA 모드 --------------------------------------------------------


def test_kc_sma_warmup() -> None:
    """SMA: max(period - 1, atr_period). period=10, atr_period=14 → 14."""
    assert KeltnerChannel(period=10, atr_period=14, use_ema=False).required_warmup_bars() == 14
    assert KeltnerChannel(period=20, atr_period=14, use_ema=False).required_warmup_bars() == 19


def test_kc_sma_compute_columns() -> None:
    kc = KeltnerChannel(period=5, multiplier=1.5, atr_period=5, use_ema=False)
    out = kc.compute(_make_ohlcv(20))
    expected = "kc_5_1.5_5_sma"
    assert out.columns == [f"{expected}_mid", f"{expected}_upper", f"{expected}_lower"]
    assert out.height == 20


def test_kc_sma_warmup_nulls_then_valid() -> None:
    kc = KeltnerChannel(period=5, multiplier=1.5, atr_period=5, use_ema=False)
    out = kc.compute(_make_ohlcv(15))
    prefix = "kc_5_1.5_5_sma"
    mid = out[f"{prefix}_mid"]
    upper = out[f"{prefix}_upper"]
    lower = out[f"{prefix}_lower"]
    # warmup = max(4, 5) = 5 → 인덱스 0..4 null, 인덱스 5부터 유효
    for i in range(5):
        assert mid[i] is None or upper[i] is None or lower[i] is None
    for i in range(5, 15):
        assert mid[i] is not None
        assert upper[i] is not None
        assert lower[i] is not None


def test_kc_sma_upper_above_mid_lower_below() -> None:
    kc = KeltnerChannel(period=5, multiplier=1.5, atr_period=5, use_ema=False)
    out = kc.compute(_make_ohlcv(15))
    prefix = "kc_5_1.5_5_sma"
    for i in range(5, 15):
        assert out[f"{prefix}_upper"][i] >= out[f"{prefix}_mid"][i]
        assert out[f"{prefix}_mid"][i] >= out[f"{prefix}_lower"][i]


def test_kc_sma_band_width_proportional_to_multiplier() -> None:
    df = _make_ohlcv(15)
    out_a = KeltnerChannel(
        period=5, multiplier=1.0, atr_period=5, use_ema=False
    ).compute(df)
    out_b = KeltnerChannel(
        period=5, multiplier=2.0, atr_period=5, use_ema=False
    ).compute(df)
    width_a = out_a["kc_5_1.0_5_sma_upper"][10] - out_a["kc_5_1.0_5_sma_lower"][10]
    width_b = out_b["kc_5_2.0_5_sma_upper"][10] - out_b["kc_5_2.0_5_sma_lower"][10]
    assert width_b == pytest.approx(width_a * 2, rel=1e-6)


def test_kc_sma_constant_close_zero_atr_collapsed_band() -> None:
    """모든 OHLC가 같은 값이면 ATR=0 → upper=mid=lower (SMA 모드)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(10)],
            "open": [100.0] * 10,
            "high": [100.0] * 10,
            "low": [100.0] * 10,
            "close": [100.0] * 10,
            "volume": [1.0] * 10,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    kc = KeltnerChannel(period=5, multiplier=1.5, atr_period=5, use_ema=False)
    out = kc.compute(df)
    prefix = "kc_5_1.5_5_sma"
    for i in range(5, 10):
        assert out[f"{prefix}_mid"][i] == 100.0
        assert out[f"{prefix}_upper"][i] == 100.0
        assert out[f"{prefix}_lower"][i] == 100.0


# ---------- EMA 모드 (legacy 호환 기본) -------------------------------------


def test_kc_ema_warmup() -> None:
    """EMA: max(period, atr_period). 안정화 위해 보수적으로 period 사용."""
    assert KeltnerChannel(period=20, atr_period=14, use_ema=True).required_warmup_bars() == 20
    assert KeltnerChannel(period=10, atr_period=14, use_ema=True).required_warmup_bars() == 14


def test_kc_ema_compute_columns() -> None:
    kc = KeltnerChannel(period=5, multiplier=1.0, atr_period=5, use_ema=True)
    out = kc.compute(_make_ohlcv(20))
    expected = "kc_5_1.0_5_ema"
    assert out.columns == [f"{expected}_mid", f"{expected}_upper", f"{expected}_lower"]
    assert out.height == 20


def test_kc_ema_constant_close_zero_atr_collapsed_band() -> None:
    """EMA 모드도 상수 close + 상수 H/L → upper=mid=lower."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(20)],
            "open": [100.0] * 20,
            "high": [100.0] * 20,
            "low": [100.0] * 20,
            "close": [100.0] * 20,
            "volume": [1.0] * 20,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    kc = KeltnerChannel(period=5, multiplier=1.5, atr_period=5, use_ema=True)
    out = kc.compute(df)
    prefix = "kc_5_1.5_5_ema"
    for i in range(5, 20):
        assert out[f"{prefix}_mid"][i] == pytest.approx(100.0)
        assert out[f"{prefix}_upper"][i] == pytest.approx(100.0)
        assert out[f"{prefix}_lower"][i] == pytest.approx(100.0)


def test_kc_ema_smoke_produces_values() -> None:
    """EMA 모드 smoke — 워밍업 이후 값이 비-null이고 mid가 close 근방."""
    kc = KeltnerChannel(period=10, multiplier=1.5, atr_period=10, use_ema=True)
    out = kc.compute(_make_ohlcv(30))
    prefix = "kc_10_1.5_10_ema"
    # 인덱스 10 이후 mid는 비-null, close 근방
    mid_at_20 = out[f"{prefix}_mid"][20]
    assert mid_at_20 is not None
    # 합성 데이터: close[20] = 120.5. EMA가 그 근처여야 함 (지수 가중 평균).
    assert 100.0 < mid_at_20 < 130.0
