"""Tests for PatternMLFilterStrategy."""
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from src.core.types import BarSeries
from src.ml.persistence import save_run
from src.ml.patterns.engulfing_mtf import EngulfingMTF
from src.ml.types import MTFData
from src.strategies.pattern_ml_filter import PatternMLFilterStrategy


H = 3_600_000


def _series(bars_args, tf, step_ms, symbol="BTCUSDT"):
    rows = []
    for i, (o, h, l, c) in enumerate(bars_args):
        rows.append({
            "timestamp": i * step_ms,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": 1.0, "turnover": 1.0,
        })
    return BarSeries(symbol=symbol, timeframe=tf, bars=pd.DataFrame(rows))


def _make_mtf_with_engulfing_at_100():
    n = 120
    bars_1h = [(100.0, 101.0, 99.0, 100.5)] * n
    bars_1h[99] = (110.0, 111.0, 104.0, 105.0)   # red
    bars_1h[100] = (104.0, 116.0, 103.0, 115.0)  # green engulfing
    s_1h = _series(bars_1h, "1h", H)
    s_4h = _series(
        [(100.0 + i * 0.5, 102.0 + i * 0.5, 99.5 + i * 0.5, 101.0 + i * 0.5)
         for i in range(n // 4)],
        "4h", 4 * H,
    )
    s_1d = _series(
        [(100.0 + i, 105.0 + i, 99.0 + i, 103.0 + i) for i in range(max(1, n // 24))],
        "1d", 24 * H,
    )
    return MTFData(symbol="BTCUSDT", primary_tf="1h",
                   series={"1h": s_1h, "4h": s_4h, "1d": s_1d})


def test_from_artifact_loads(tmp_path):
    pattern = EngulfingMTF()
    rng = np.random.default_rng(0)
    X = rng.normal(size=(50, 4))
    y = (X[:, 0] > 0).astype(int)
    model = XGBClassifier(n_estimators=5, max_depth=3, verbosity=0,
                          use_label_encoder=False)
    model.fit(X, y)
    save_run(
        run_dir=tmp_path / "r1",
        model=model,
        meta={
            "pattern_name": "engulfing_mtf",
            "pattern_version": "1.0.0",
            "policy": {
                "threshold": 0.55,
                "tp_pct": 0.04,
                "sl_pct": 0.02,
                "max_holding_bars": 24,
            },
            "feature_columns": ["engulf_size_ratio", "h4_trend_up",
                                "d1_trend_up", "is_long"],
        },
        report={},
    )
    strat = PatternMLFilterStrategy.from_artifact(
        run_dir=tmp_path / "r1",
        pattern_factory=lambda: EngulfingMTF(),
    )
    assert strat.threshold == 0.55
    assert strat.tp_pct == 0.04
    assert strat.sl_pct == 0.02
    assert strat.max_holding_bars == 24
    assert strat.pattern.name == "engulfing_mtf"


def test_on_bar_fast_calls_broker_when_pattern_and_score_pass():
    pattern = EngulfingMTF()
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.1, 0.9]])
    mtf = _make_mtf_with_engulfing_at_100()
    strat = PatternMLFilterStrategy(
        pattern=pattern, model=model,
        feature_columns=["engulf_size_ratio", "h4_trend_up", "d1_trend_up", "is_long"],
        threshold=0.5, tp_pct=0.04, sl_pct=0.02, max_holding_bars=24,
        mtf_data=mtf,
    )
    cache = strat.prepare(mtf.get_primary())
    primary = mtf.get_primary()
    bar_row = primary.bars.iloc[100]
    bar = MagicMock()
    bar.symbol = "BTCUSDT"
    bar.close = float(bar_row["close"])
    bar.high = float(bar_row["high"])
    bar.low = float(bar_row["low"])
    broker = MagicMock()
    broker.get_position.return_value = None
    broker.calc_qty.return_value = 1.0

    strat.on_bar_fast(bar=bar, i=100, cache=cache, broker=broker)
    assert broker.buy.called or broker.sell.called


def test_on_bar_fast_skips_when_score_below_threshold():
    pattern = EngulfingMTF()
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.7, 0.3]])
    mtf = _make_mtf_with_engulfing_at_100()
    strat = PatternMLFilterStrategy(
        pattern=pattern, model=model,
        feature_columns=["engulf_size_ratio", "h4_trend_up", "d1_trend_up", "is_long"],
        threshold=0.5, tp_pct=0.04, sl_pct=0.02, max_holding_bars=24,
        mtf_data=mtf,
    )
    cache = strat.prepare(mtf.get_primary())
    primary = mtf.get_primary()
    bar_row = primary.bars.iloc[100]
    bar = MagicMock()
    bar.symbol = "BTCUSDT"
    bar.close = float(bar_row["close"])
    bar.high = float(bar_row["high"])
    bar.low = float(bar_row["low"])
    broker = MagicMock()
    broker.get_position.return_value = None
    broker.calc_qty.return_value = 1.0

    strat.on_bar_fast(bar=bar, i=100, cache=cache, broker=broker)
    assert not broker.buy.called
    assert not broker.sell.called


def test_on_bar_fast_skips_when_no_pattern():
    pattern = EngulfingMTF()
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.1, 0.9]])
    mtf = _make_mtf_with_engulfing_at_100()
    strat = PatternMLFilterStrategy(
        pattern=pattern, model=model,
        feature_columns=["engulf_size_ratio", "h4_trend_up", "d1_trend_up", "is_long"],
        threshold=0.5, tp_pct=0.04, sl_pct=0.02, max_holding_bars=24,
        mtf_data=mtf,
    )
    cache = strat.prepare(mtf.get_primary())
    primary = mtf.get_primary()
    bar_row = primary.bars.iloc[50]  # no engulfing here
    bar = MagicMock()
    bar.symbol = "BTCUSDT"
    bar.close = float(bar_row["close"])
    bar.high = float(bar_row["high"])
    bar.low = float(bar_row["low"])
    broker = MagicMock()
    broker.get_position.return_value = None
    broker.calc_qty.return_value = 1.0

    strat.on_bar_fast(bar=bar, i=50, cache=cache, broker=broker)
    assert not broker.buy.called
    assert not broker.sell.called
