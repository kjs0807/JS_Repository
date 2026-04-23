"""End-to-end integration: EngulfingMTF pipeline on synthetic MTF data."""
import pandas as pd

from src.core.types import BarSeries
from src.ml.persistence import load_run
from src.ml.patterns.engulfing_mtf import EngulfingMTF
from src.ml.types import MTFData
from src.strategies.pattern_ml_filter import PatternMLFilterStrategy
import scripts.train_ml_pattern as cli


H = 3_600_000


def _series(bars_args, tf, step_ms, symbol):
    rows = []
    for i, (o, h, l, c) in enumerate(bars_args):
        rows.append({
            "timestamp": i * step_ms,
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": 1.0, "turnover": 1.0,
        })
    return BarSeries(symbol=symbol, timeframe=tf, bars=pd.DataFrame(rows))


def _make_mtf(symbol):
    n = 400
    # Default gentle uptrend
    bars_1h = [(100.0 + i * 0.05, 101.0 + i * 0.05,
                99.0 + i * 0.05, 100.5 + i * 0.05) for i in range(n)]
    # Inject a handful of bullish engulfings across the series
    for k in (50, 80, 130, 200, 260, 320, 360):
        bars_1h[k - 1] = (110.0, 111.0, 104.0, 105.0)
        bars_1h[k] = (104.0, 116.0, 103.0, 115.0)
    s_1h = _series(bars_1h, "1h", H, symbol)
    s_4h = _series(
        [(100.0, 102.0, 99.0, 101.0)] * (n // 4),
        "4h", 4 * H, symbol,
    )
    s_1d = _series(
        [(100.0, 105.0, 99.0, 103.0)] * max(1, n // 24),
        "1d", 24 * H, symbol,
    )
    return MTFData(
        symbol=symbol, primary_tf="1h",
        series={"1h": s_1h, "4h": s_4h, "1d": s_1d},
    )


def test_e2e_engulfing_mtf(tmp_path, monkeypatch):
    mtf_per_symbol = {
        "BTCUSDT": _make_mtf("BTCUSDT"),
        "ETHUSDT": _make_mtf("ETHUSDT"),
    }
    monkeypatch.setattr(
        cli, "load_mtf_data",
        lambda symbols, timeframes, start_ms, end_ms, primary_tf="1h": {
            s: mtf_per_symbol[s] for s in symbols
        },
    )

    primary_len = len(mtf_per_symbol["BTCUSDT"].get_primary())
    end_ms = primary_len * H

    artifact_dir = cli.run_pipeline(
        pattern_name="engulfing_mtf",
        symbols=["BTCUSDT", "ETHUSDT"],
        is_start_ms=0,
        is_end_ms=int(end_ms * 0.75),
        oos_start_ms=int(end_ms * 0.75),
        oos_end_ms=end_ms,
        tp_pct=0.05, sl_pct=0.05, max_holding_bars=10,
        n_trials=2, hpo_timeout=60,
        cache_dir=tmp_path / "cache",
        out_root=tmp_path / "logs" / "ml",
    )

    artifact = load_run(artifact_dir)
    assert artifact.meta["pattern_name"] == "engulfing_mtf"
    assert "verdict" in artifact.report
    assert "metrics" in artifact.report

    strat = PatternMLFilterStrategy.from_artifact(
        run_dir=artifact_dir,
        pattern_factory=lambda: EngulfingMTF(),
    )
    assert strat.pattern.name == "engulfing_mtf"

    # Holdout section must be present in the report so the top-level
    # verdict reflects true-holdout performance, not just IS walk-forward.
    holdout = artifact.report.get("metrics", {}).get("holdout")
    assert holdout is not None, "report.metrics.holdout should be emitted"
    assert holdout["verdict"] in (
        "HOLDOUT_PASS", "HOLDOUT_FAIL", "HOLDOUT_NO_TRADES"
    )
    assert holdout["oos_period_ms"][0] < holdout["oos_period_ms"][1]
