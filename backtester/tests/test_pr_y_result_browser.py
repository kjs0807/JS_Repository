"""PR Y — Result Browser 회귀.

검증:
1. render_result_browser 가 run_dir/charts/index.html 생성.
2. summary metric (final_equity / total_return / max_drawdown_pct) 포함.
3. 심볼별 activity 테이블 (intents/fills count) 포함.
4. 다른 산출물 링크 — 존재하면 정상 a href, 없으면 (missing).
5. CLI ``backtester browser``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.analysis.export import export_run_data
from backtester.cli.main import main as cli_main
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.bbkc_multi_legacy_compat import (
    BBKCMultiLegacyCompatStrategy,
)
from backtester.viz.result_browser import render_result_browser
from backtester.viz.trade_review import render_trade_review

UTC = timezone.utc


def _make_squeeze_breakout(
    target: Path, *, base_price: float = 100.0
) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(25):
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
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=25 + i),
                "open": base_price + i * 0.5,
                "high": base_price + 0.5 + i * 0.5,
                "low": base_price - 0.5 + i * 0.5,
                "close": base_price + 0.5 + i * 0.5,
                "volume": 1.0,
            }
        )
    peak = base_price + 0.5 + 24 * 0.5
    for i in range(30):
        rows.append(
            {
                "timestamp": base + timedelta(hours=50 + i),
                "open": peak - i * 0.4,
                "high": peak + 0.5 - i * 0.4,
                "low": peak - 0.5 - i * 0.4,
                "close": peak - i * 0.4,
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


def _run_single(tmp_path: Path) -> Path:
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    _make_squeeze_breakout(data_dir / f"{sym}_1h.parquet")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="browser_single",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(sym, "BTC")],
        timeframes_per_symbol={sym: ["1h"]},
        primary_symbol=sym,
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=81),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = BBKCLegacyCompatStrategy(
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        rsi_filter=100.0,
        exit_mode="fixed",
    )
    res = BacktestEngine(cfg, strategy, verbose=False).run()
    return res.run_dir


def _run_multi(tmp_path: Path) -> Path:
    syms = [
        ("BTCUSDT", "BTC", 100.0),
        ("ETHUSDT", "ETH", 50.0),
        ("AVAXUSDT", "AVAX", 25.0),
    ]
    data_dir = tmp_path / "data"
    for s, _, p in syms:
        _make_squeeze_breakout(data_dir / f"{s}_1h.parquet", base_price=p)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="browser_multi",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(s, b) for s, b, _ in syms],
        timeframes_per_symbol={s: ["1h"] for s, _, _ in syms},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=81),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = BBKCMultiLegacyCompatStrategy(
        symbols=[s for s, _, _ in syms],
        timeframe="1h",
        child_params={
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "rsi_filter": 100.0,
            "exit_mode": "fixed",
        },
    )
    res = BacktestEngine(cfg, strategy, verbose=False).run()
    return res.run_dir


# ---------- 1. file generation ----------------------------------------------


def test_render_result_browser_creates_index(tmp_path: Path) -> None:
    run_dir = _run_single(tmp_path)
    out = render_result_browser(run_dir)
    assert out == run_dir / "charts" / "index.html"
    assert out.exists()
    assert out.stat().st_size > 0


# ---------- 2. summary content ----------------------------------------------


def test_browser_index_includes_summary_metrics(tmp_path: Path) -> None:
    run_dir = _run_single(tmp_path)
    out = render_result_browser(run_dir)
    txt = out.read_text(encoding="utf-8")
    # 라벨 (사람이 읽는 텍스트) 이 모두 들어가야 함.
    for label in (
        "Run ID",
        "Initial equity",
        "Final equity",
        "Total return",
        "Max drawdown",
        "Sharpe",
    ):
        assert label in txt, f"missing label in browser index: {label!r}"


def test_browser_index_includes_per_symbol_table(tmp_path: Path) -> None:
    run_dir = _run_multi(tmp_path)
    out = render_result_browser(run_dir)
    txt = out.read_text(encoding="utf-8")
    # 3 심볼 모두 표 안에.
    for sym in ("BTCUSDT", "ETHUSDT", "AVAXUSDT"):
        assert sym in txt
    assert "intents" in txt
    assert "fills" in txt


# ---------- 3. artifact links -----------------------------------------------


def test_browser_marks_missing_artifacts(tmp_path: Path) -> None:
    """run_chart / metrics_report / exports 가 없는 상태에서 (missing) 표기."""
    run_dir = _run_single(tmp_path)
    out = render_result_browser(run_dir)
    txt = out.read_text(encoding="utf-8")
    # 이 시점엔 charts/run_chart.html / charts/metrics_report.html /
    # charts/trades/index.html / exports/* 모두 미존재.
    assert "(missing)" in txt
    # config.yaml 은 Engine 이 자동 작성하므로 정상 링크.
    assert "config.yaml" in txt


def test_browser_links_to_existing_exports(tmp_path: Path) -> None:
    run_dir = _run_single(tmp_path)
    # exports + trade-review 먼저 생성 → browser 가 정상 링크 표시.
    export_run_data(run_dir)
    render_trade_review(run_dir, pre_bars=20, post_bars=10)
    out = render_result_browser(run_dir)
    txt = out.read_text(encoding="utf-8")
    # exports/fills.csv 링크 — relative path 사용.
    assert "../exports/fills.csv" in txt
    assert "../charts/trades/index.html" in txt
    # missing 항목이 줄어 (run_chart 는 여전히 missing 일 수도 — 둘 다 OK).


# ---------- 4. CLI ----------------------------------------------------------


def test_cli_browser_smoke(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    run_dir = _run_single(tmp_path)
    rc = cli_main(["browser", str(run_dir)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "index.html" in captured.out
    assert (run_dir / "charts" / "index.html").exists()


def test_cli_browser_quiet(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    run_dir = _run_single(tmp_path)
    rc = cli_main(["browser", str(run_dir), "--quiet"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cli_browser_missing_dir(tmp_path: Path) -> None:
    rc = cli_main(["browser", str(tmp_path / "nonexistent")])
    assert rc == 2
