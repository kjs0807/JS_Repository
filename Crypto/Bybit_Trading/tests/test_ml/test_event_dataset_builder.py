"""Tests for EventDatasetBuilder."""
import pandas as pd

from src.core.types import BarSeries
from src.ml.event_dataset_builder import EventDatasetBuilder, EventDataset
from src.ml.patterns.engulfing_mtf import EngulfingMTF
from src.ml.types import MTFData, LabelConfig


H = 3_600_000


def _series(bars_args, tf, step_ms, symbol):
    rows = []
    for i, (o, h, l, c) in enumerate(bars_args):
        rows.append({
            "timestamp": i * step_ms,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": 1.0, "turnover": 1.0,
        })
    return BarSeries(symbol=symbol, timeframe=tf, bars=pd.DataFrame(rows))


def _make_mtf(symbol):
    n = 200
    bars_1h = [(100.0 + i * 0.05, 100.5 + i * 0.05,
                99.5 + i * 0.05, 100.2 + i * 0.05) for i in range(n)]
    # Inject bullish engulfing at idx 50 and 120
    bars_1h[49] = (110.0, 111.0, 104.0, 105.0)
    bars_1h[50] = (104.0, 116.0, 103.0, 115.0)
    bars_1h[119] = (130.0, 131.0, 124.0, 125.0)
    bars_1h[120] = (124.0, 136.0, 123.0, 135.0)
    s_1h = _series(bars_1h, "1h", H, symbol)
    bars_4h = [(100.0, 102.0, 99.0, 101.0)] * (n // 4)
    s_4h = _series(bars_4h, "4h", 4 * H, symbol)
    bars_1d = [(100.0, 105.0, 99.0, 103.0)] * max(1, n // 24)
    s_1d = _series(bars_1d, "1d", 24 * H, symbol)
    return MTFData(symbol=symbol, primary_tf="1h",
                   series={"1h": s_1h, "4h": s_4h, "1d": s_1d})


def test_build_returns_dataset_with_features_and_labels(tmp_path):
    pattern = EngulfingMTF()
    label_cfg = LabelConfig(tp_pct=0.05, sl_pct=0.05, max_holding_bars=10)
    builder = EventDatasetBuilder(
        pattern=pattern, label_config=label_cfg, cache_dir=tmp_path,
    )
    mtf_per_symbol = {
        "BTCUSDT": _make_mtf("BTCUSDT"),
        "ETHUSDT": _make_mtf("ETHUSDT"),
    }
    dataset = builder.build(mtf_per_symbol)
    assert isinstance(dataset, EventDataset)
    df = dataset.to_dataframe()
    assert "label" in df.columns
    assert "symbol_id" in df.columns
    assert "sample_weight" in df.columns
    assert "timestamp_ms" in df.columns
    assert len(df) >= 4  # 2 symbols × 2 events each


def test_pooled_concat_and_symbol_one_hot(tmp_path):
    pattern = EngulfingMTF()
    label_cfg = LabelConfig(tp_pct=0.05, sl_pct=0.05, max_holding_bars=10)
    builder = EventDatasetBuilder(
        pattern=pattern, label_config=label_cfg, cache_dir=tmp_path,
    )
    mtf = {"BTCUSDT": _make_mtf("BTCUSDT"), "ETHUSDT": _make_mtf("ETHUSDT")}
    df = builder.build(mtf).to_dataframe()
    assert "symbol_id_BTCUSDT" in df.columns
    assert "symbol_id_ETHUSDT" in df.columns


def test_label_triple_barrier_is_binary(tmp_path):
    pattern = EngulfingMTF()
    label_cfg = LabelConfig(tp_pct=0.02, sl_pct=0.02, max_holding_bars=15)
    builder = EventDatasetBuilder(
        pattern=pattern, label_config=label_cfg, cache_dir=tmp_path,
    )
    df = builder.build({"BTCUSDT": _make_mtf("BTCUSDT")}).to_dataframe()
    assert set(df["label"].unique()).issubset({0, 1})


def test_cache_hit(tmp_path):
    pattern = EngulfingMTF()
    label_cfg = LabelConfig(tp_pct=0.02, sl_pct=0.02, max_holding_bars=15)
    builder = EventDatasetBuilder(
        pattern=pattern, label_config=label_cfg, cache_dir=tmp_path,
    )
    mtf = {"BTCUSDT": _make_mtf("BTCUSDT")}
    builder.build(mtf)
    cache_files = list(tmp_path.rglob("*.parquet"))
    assert len(cache_files) >= 1


def test_sample_weight_inverse_symbol_count(tmp_path):
    pattern = EngulfingMTF()
    label_cfg = LabelConfig(tp_pct=0.05, sl_pct=0.05, max_holding_bars=10)
    builder = EventDatasetBuilder(
        pattern=pattern, label_config=label_cfg, cache_dir=tmp_path,
    )
    mtf = {"BTCUSDT": _make_mtf("BTCUSDT"), "ETHUSDT": _make_mtf("ETHUSDT")}
    df = builder.build(mtf).to_dataframe()
    # Rescaled inverse-symbol-count weighting: mean weight == 1.0 and every
    # symbol's total contribution (count * per-event weight) is equal, so
    # the absolute scale works with XGBoost's default min_child_weight.
    assert abs(df["sample_weight"].mean() - 1.0) < 1e-9
    btc_count = (df["symbol_id"] == "BTCUSDT").sum()
    eth_count = (df["symbol_id"] == "ETHUSDT").sum()
    if btc_count > 0 and eth_count > 0:
        btc_total = df.loc[df["symbol_id"] == "BTCUSDT", "sample_weight"].sum()
        eth_total = df.loc[df["symbol_id"] == "ETHUSDT", "sample_weight"].sum()
        assert abs(btc_total - eth_total) < 1e-9


def test_label_mode_atr_produces_binary_labels(tmp_path):
    """ATR-mode barriers should still yield a binary label column."""
    pattern = EngulfingMTF()
    label_cfg = LabelConfig(
        tp_pct=0.02, sl_pct=0.02,             # ignored in atr mode but must be set
        max_holding_bars=15,
        label_mode="atr",
        tp_atr_mult=2.0,
        sl_atr_mult=1.0,
        atr_period=14,
    )
    builder = EventDatasetBuilder(
        pattern=pattern, label_config=label_cfg, cache_dir=tmp_path,
    )
    df = builder.build({"BTCUSDT": _make_mtf("BTCUSDT")}).to_dataframe()
    assert not df.empty
    assert set(df["label"].unique()).issubset({0, 1})


def test_label_mode_cache_key_differs(tmp_path):
    """pct-mode and atr-mode runs should produce different cache files."""
    pattern = EngulfingMTF()
    pct_cfg = LabelConfig(tp_pct=0.02, sl_pct=0.02, max_holding_bars=15)
    atr_cfg = LabelConfig(
        tp_pct=0.02, sl_pct=0.02, max_holding_bars=15,
        label_mode="atr", tp_atr_mult=2.0, sl_atr_mult=1.0,
    )
    EventDatasetBuilder(
        pattern=pattern, label_config=pct_cfg, cache_dir=tmp_path,
    ).build({"BTCUSDT": _make_mtf("BTCUSDT")})
    EventDatasetBuilder(
        pattern=pattern, label_config=atr_cfg, cache_dir=tmp_path,
    ).build({"BTCUSDT": _make_mtf("BTCUSDT")})
    cache_files = list(tmp_path.rglob("*.parquet"))
    assert len(cache_files) >= 2
