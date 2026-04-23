"""Smoke test for train_ml_pattern.py CLI (function-level, not subprocess)."""
import pandas as pd

from src.core.types import BarSeries
from src.ml.types import MTFData
import scripts.train_ml_pattern as cli


H = 3_600_000


def _series(args, tf, step_ms, symbol):
    rows = []
    for i, (o, h, l, c) in enumerate(args):
        rows.append({
            "timestamp": i * step_ms,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": 1.0, "turnover": 1.0,
        })
    return BarSeries(symbol=symbol, timeframe=tf, bars=pd.DataFrame(rows))


def _mtf(symbol):
    n = 200
    bars_1h = [(100.0 + i * 0.05, 101.0 + i * 0.05,
                99.0 + i * 0.05, 100.5 + i * 0.05) for i in range(n)]
    for k in (50, 100, 150):
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
    return MTFData(symbol=symbol, primary_tf="1h",
                   series={"1h": s_1h, "4h": s_4h, "1d": s_1d})


def test_run_pipeline_e2e(tmp_path, monkeypatch):
    mtf_per_symbol = {
        "BTCUSDT": _mtf("BTCUSDT"),
        "ETHUSDT": _mtf("ETHUSDT"),
    }

    def fake_loader(symbols, timeframes, start_ms, end_ms, primary_tf="1h"):
        return {s: mtf_per_symbol[s] for s in symbols}

    monkeypatch.setattr(cli, "load_mtf_data", fake_loader)

    artifact_dir = cli.run_pipeline(
        pattern_name="engulfing_mtf",
        symbols=["BTCUSDT", "ETHUSDT"],
        is_start_ms=0, is_end_ms=200 * H,
        oos_start_ms=200 * H, oos_end_ms=400 * H,
        tp_pct=0.05, sl_pct=0.05, max_holding_bars=10,
        n_trials=2, hpo_timeout=60,
        cache_dir=tmp_path / "cache",
        out_root=tmp_path / "logs" / "ml",
    )

    assert (artifact_dir / "model.joblib").exists()
    assert (artifact_dir / "meta.json").exists()
    assert (artifact_dir / "report.json").exists()
