"""PR 3 IndicatorEngine + BB + ATR 테스트 (spec §20 PR 3 acceptance)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from backtester.core.errors import DataError
from backtester.indicators import ATR, BollingerBands, Indicator, IndicatorEngine

UTC = timezone.utc


def _make_ohlcv_df(n: int) -> pl.DataFrame:
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


# ---------- BollingerBands --------------------------------------------------


def test_bb_warmup_period_minus_one() -> None:
    """spec §20: BB 20 → 19."""
    assert BollingerBands(period=20, num_std=2.0).required_warmup_bars() == 19
    assert BollingerBands(period=10).required_warmup_bars() == 9


def test_bb_compute_columns() -> None:
    bb = BollingerBands(period=5, num_std=2.0)
    out = bb.compute(_make_ohlcv_df(20))
    assert out.columns == ["bb_5_2.0_mid", "bb_5_2.0_upper", "bb_5_2.0_lower"]
    assert out.height == 20


def test_bb_warmup_nulls_then_valid() -> None:
    bb = BollingerBands(period=5, num_std=2.0)
    out = bb.compute(_make_ohlcv_df(10))
    mid = out["bb_5_2.0_mid"]
    # 인덱스 0..3 (4개)는 null, 인덱스 4부터 유효
    assert mid[0] is None
    assert mid[3] is None
    assert mid[4] is not None
    assert mid[9] is not None


def test_bb_invalid_params() -> None:
    with pytest.raises(ValueError, match="period"):
        BollingerBands(period=1)
    with pytest.raises(ValueError, match="num_std"):
        BollingerBands(period=20, num_std=0)


def test_bb_protocol_compatibility() -> None:
    """structural Protocol 만족 확인."""
    bb: Indicator = BollingerBands(period=20)
    assert bb.required_warmup_bars() == 19


# ---------- ATR -------------------------------------------------------------


def test_atr_warmup_equals_period() -> None:
    """spec §20: ATR 14 → 14 (TR_0가 null이라 SMA 첫 유효는 인덱스 period)."""
    assert ATR(period=14).required_warmup_bars() == 14
    assert ATR(period=20).required_warmup_bars() == 20


def test_atr_compute_column() -> None:
    atr = ATR(period=5)
    out = atr.compute(_make_ohlcv_df(15))
    assert out.columns == ["atr_5"]
    assert out.height == 15


def test_atr_warmup_nulls_then_valid() -> None:
    atr = ATR(period=5)
    out = atr.compute(_make_ohlcv_df(15))
    series = out["atr_5"]
    # period=5 → warmup=5 → 인덱스 0..4 null, 인덱스 5부터 유효
    for i in range(5):
        assert series[i] is None, f"atr[{i}] should be null"
    for i in range(5, 15):
        assert series[i] is not None, f"atr[{i}] should be valid"


def test_atr_invalid_period() -> None:
    with pytest.raises(ValueError, match="period"):
        ATR(period=0)


# ---------- IndicatorEngine -------------------------------------------------


def test_engine_required_warmup_max() -> None:
    engine = IndicatorEngine()
    bb = BollingerBands(period=20)
    atr = ATR(period=14)
    assert engine.required_warmup([bb, atr]) == 19  # max(19, 14)
    assert engine.required_warmup([atr]) == 14
    assert engine.required_warmup([]) == 0


def test_engine_precompute_no_persist() -> None:
    engine = IndicatorEngine()
    df = _make_ohlcv_df(30)
    bars = {"BTCUSDT": {"1h": df}}
    bb = BollingerBands(period=5)
    engine.precompute(bars, [bb])
    assert engine.has("BTCUSDT", "1h")
    out = engine.get("BTCUSDT", "1h")
    # timestamp + 3 bb columns
    assert "timestamp" in out.columns
    assert "bb_5_2.0_mid" in out.columns
    assert out.height == 30


def test_engine_precompute_persists(tmp_path: Path) -> None:
    """spec §20: persist_to 지정 시 {symbol}_{tf}.parquet 생성."""
    engine = IndicatorEngine()
    df = _make_ohlcv_df(30)
    bars = {"BTCUSDT": {"1h": df}}
    bb = BollingerBands(period=5)
    persist_dir = tmp_path / "indicators"
    engine.precompute(bars, [bb], persist_to=persist_dir)

    out_path = persist_dir / "BTCUSDT_1h.parquet"
    assert out_path.exists()

    # 읽어서 컬럼 확인
    persisted = pl.read_parquet(out_path)
    assert "timestamp" in persisted.columns
    assert "bb_5_2.0_mid" in persisted.columns
    assert persisted.height == 30


def test_engine_precompute_sanitizes_symbol(tmp_path: Path) -> None:
    """심볼에 슬래시가 있으면 파일명은 sanitized (BTC/USDT → BTC_USDT)."""
    engine = IndicatorEngine()
    df = _make_ohlcv_df(10)
    bars = {"BTC/USDT": {"1h": df}}
    bb = BollingerBands(period=5)
    persist_dir = tmp_path / "indicators"
    engine.precompute(bars, [bb], persist_to=persist_dir)
    assert (persist_dir / "BTC_USDT_1h.parquet").exists()


def test_engine_precompute_multi_indicators(tmp_path: Path) -> None:
    engine = IndicatorEngine()
    df = _make_ohlcv_df(30)
    bars = {"BTCUSDT": {"1h": df}}
    indicators: list[Indicator] = [BollingerBands(period=5), ATR(period=5)]
    engine.precompute(bars, indicators)
    out = engine.get("BTCUSDT", "1h")
    for col in ["timestamp", "bb_5_2.0_mid", "bb_5_2.0_upper", "bb_5_2.0_lower", "atr_5"]:
        assert col in out.columns


def test_engine_get_unknown_raises() -> None:
    engine = IndicatorEngine()
    with pytest.raises(DataError, match="not precomputed"):
        engine.get("UNKNOWN", "1h")


def test_engine_creates_persist_dir_if_missing(tmp_path: Path) -> None:
    engine = IndicatorEngine()
    df = _make_ohlcv_df(10)
    bars = {"BTCUSDT": {"1h": df}}
    bb = BollingerBands(period=5)
    persist_dir = tmp_path / "deeply" / "nested" / "indicators"
    assert not persist_dir.exists()
    engine.precompute(bars, [bb], persist_to=persist_dir)
    assert persist_dir.exists()
    assert (persist_dir / "BTCUSDT_1h.parquet").exists()
