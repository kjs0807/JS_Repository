"""PR 16 — FRAMA outputs end-to-end.

Validates that a FRAMA multi-symbol run produces the same family of
human-inspectable artifacts as BBKC: ``exports/*.csv``, ``exports/summary.json``,
``charts/trades/index.html`` (PR X), and ``charts/index.html`` (PR Y).

Without these, a FRAMA backtest leaves only ``parquet`` + ``events.jsonl``
behind, which forces the user to spin up Polars/pandas just to see what
happened — exactly the gap PR Z / PR X / PR Y closed for BBKC.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.analysis.export import export_run_data
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.frama_multi_channel import FRAMAMultiChannelStrategy
from backtester.viz.result_browser import render_result_browser
from backtester.viz.trade_review import render_trade_review

UTC = timezone.utc


def _make_flat_then_breakout(target: Path, *, base_price: float = 100.0) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(250):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": base_price,
                "high": base_price + 0.05,
                "low": base_price - 0.05,
                "close": base_price + (0.01 if i % 2 else -0.01),
                "volume": 1.0,
            }
        )
    for i in range(80):
        p = base_price + (i + 1) * 1.0
        rows.append(
            {
                "timestamp": base + timedelta(hours=250 + i),
                "open": p - 0.5,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0,
            }
        )
    peak = base_price + 80.0
    for i in range(80):
        p = peak - (i + 1) * 1.0
        rows.append(
            {
                "timestamp": base + timedelta(hours=330 + i),
                "open": p + 0.5,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0,
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _instrument(symbol: str, base: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _run_frama_multi(tmp_path: Path) -> Path:
    syms = [
        ("BTCUSDT", "BTC", 100.0),
        ("ETHUSDT", "ETH", 50.0),
        ("AVAXUSDT", "AVAX", 25.0),
    ]
    data_dir = tmp_path / "data"
    for s, _b, p in syms:
        _make_flat_then_breakout(data_dir / f"{s}_1h.parquet", base_price=p)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=410 + 1)
    cfg = BacktestConfig(
        run_id="frama_outputs_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(s, b) for s, b, _ in syms],
        timeframes_per_symbol={s: ["1h"] for s, _, _ in syms},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = FRAMAMultiChannelStrategy(
        symbols=[s for s, _, _ in syms],
        timeframe="1h",
        child_params={
            "length": 26,
            "distance": "1.5",
            "volatility_window": 200,
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "tp_pct": Decimal("0.06"),
            "sl_pct": Decimal("0.07"),
        },
    )
    return BacktestEngine(cfg, strategy, verbose=False).run().run_dir


# ---------- 1. exports ------------------------------------------------------


def test_export_creates_all_five_artifacts_for_frama(tmp_path: Path) -> None:
    run_dir = _run_frama_multi(tmp_path)
    outputs = export_run_data(run_dir)
    assert set(outputs.keys()) == {
        "fills",
        "intents",
        "orders",
        "equity_curve",
        "summary",
    }
    for path in outputs.values():
        assert path.exists(), f"missing artifact: {path}"
        assert path.stat().st_size > 0, f"empty artifact: {path}"


def test_summary_json_records_per_symbol_activity(tmp_path: Path) -> None:
    run_dir = _run_frama_multi(tmp_path)
    outputs = export_run_data(run_dir)
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    assert summary["primary_symbol"] == "BTCUSDT"
    assert summary["primary_timeframe"] == "1h"
    assert summary["initial_equity"] == 100000.0
    assert summary["n_fills"] >= 3, summary
    fills_by = summary["fills_by_symbol"]
    for sym in ("BTCUSDT", "ETHUSDT", "AVAXUSDT"):
        assert fills_by.get(sym, 0) >= 1, fills_by


def test_intents_csv_carries_frama_reason(tmp_path: Path) -> None:
    """``reason`` should mark FRAMA entries — guards against the strategy
    forgetting to set ``OrderIntent.reason`` and silently producing blank
    intents in the export.
    """
    run_dir = _run_frama_multi(tmp_path)
    outputs = export_run_data(run_dir)
    with open(outputs["intents"], encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    reasons = {r["reason"] for r in rows if r.get("reason")}
    assert any("frama_channel_break" in r for r in reasons), reasons


# ---------- 2. trade-review chart -------------------------------------------


def test_trade_review_renders_index_and_per_trade_html(tmp_path: Path) -> None:
    run_dir = _run_frama_multi(tmp_path)
    index = render_trade_review(run_dir, pre_bars=72, post_bars=48)
    assert index.exists()
    assert index.name == "index.html"
    # At least one per-trade chart should exist alongside the index.
    siblings = [p for p in index.parent.iterdir() if p.suffix == ".html"]
    assert len(siblings) >= 2  # index + at least one trade chart


# ---------- 3. result browser landing page ----------------------------------


def test_browser_renders_charts_index(tmp_path: Path) -> None:
    run_dir = _run_frama_multi(tmp_path)
    # PR Y: must work even before exports/trade_review are generated.
    landing = render_result_browser(run_dir)
    assert landing.exists()
    assert landing.name == "index.html"
    assert landing.parent.name == "charts"
    body = landing.read_text(encoding="utf-8")
    # Mini equity figure embedded as plotly inline div.
    assert "plotly" in body.lower()
    # Summary section pulls run_id.
    assert "frama_outputs_test" in body


def test_browser_after_exports_shows_links(tmp_path: Path) -> None:
    run_dir = _run_frama_multi(tmp_path)
    export_run_data(run_dir)
    render_trade_review(run_dir)
    body = render_result_browser(run_dir).read_text(encoding="utf-8")
    # Once both export + trade-review have run the landing page should expose
    # working (non-(missing)) links to the key artifacts.
    for needle in ("fills.csv", "summary.json", "trades/index.html"):
        assert needle in body, needle
