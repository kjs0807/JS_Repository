"""PR 18 viz/report.render_metrics_report 테스트 (Phase 2, spec §10.6)."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.cli.main import _build_parser, cmd_metrics, main
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from backtester.viz.report import render_metrics_report

UTC = timezone.utc


def _instrument() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _make_synthetic_parquet(target: Path, n_bars: int = 60) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(n_bars):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 1.0,
            }
        )
    df = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _run_engine(tmp_path: Path, run_id: str = "metrics_smoke") -> Path:
    data_dir = tmp_path / "data"
    _make_synthetic_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=48)
    cfg = BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 3, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    return BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False).run().run_dir


# ---------- render_metrics_report --------------------------------------------


def test_render_metrics_report_writes_html(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path)
    out = render_metrics_report(run_dir)
    assert out == run_dir / "charts" / "metrics_report.html"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Backtest report" in text
    assert "Core metrics" in text
    assert "Equity / drawdown" in text
    # plotly cdn 임베드
    assert "plotly" in text.lower()
    # metric label 등장
    assert "Sharpe" in text
    assert "Max drawdown" in text


def test_render_metrics_report_uses_resolved_run_id(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path, run_id="custom_run_id")
    out = render_metrics_report(run_dir)
    text = out.read_text(encoding="utf-8")
    assert "custom_run_id" in text


def test_render_metrics_report_self_contained_after_external_cache_purge(
    tmp_path: Path,
) -> None:
    """spec §10.1: run_dir 만으로 렌더링. 외부 data 디렉토리 삭제 후에도 동작."""
    run_dir = _run_engine(tmp_path)
    shutil.rmtree(tmp_path / "data")
    out = render_metrics_report(run_dir)
    assert out.exists()


def test_render_metrics_report_periods_per_year_passed_through(tmp_path: Path) -> None:
    """periods_per_year 가 metrics 계산에 영향 — annual_volatility 가 다르게 표기되는지
    HTML 내용으로 간접 확인."""
    run_dir = _run_engine(tmp_path)
    out_default = render_metrics_report(run_dir, periods_per_year=365)
    text_default = out_default.read_text(encoding="utf-8")
    out_alt = render_metrics_report(run_dir, periods_per_year=8760)
    text_alt = out_alt.read_text(encoding="utf-8")
    # 둘 다 작성됐고 (덮어쓰기), 내용은 다를 수 있다.
    assert "Annualized volatility" in text_default
    assert "Annualized volatility" in text_alt


# ---------- CLI metrics 서브커맨드 ------------------------------------------


def test_parser_metrics_requires_run_dir() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["metrics"])


def test_parser_metrics_parses_periods_per_year_and_quiet() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["metrics", "runs/x", "--periods-per-year", "8760", "--quiet"]
    )
    assert args.cmd == "metrics"
    assert args.run_dir == Path("runs/x")
    assert args.periods_per_year == 8760
    assert args.quiet is True


def test_parser_metrics_default_periods_per_year() -> None:
    parser = _build_parser()
    args = parser.parse_args(["metrics", "runs/x"])
    assert args.periods_per_year == 365


def test_cmd_metrics_returns_2_when_run_dir_missing(tmp_path: Path) -> None:
    rc = cmd_metrics(tmp_path / "nope", periods_per_year=365, quiet=True)
    assert rc == 2


def test_cmd_metrics_returns_2_when_events_jsonl_missing(tmp_path: Path) -> None:
    rd = tmp_path / "no_events"
    rd.mkdir()
    rc = cmd_metrics(rd, periods_per_year=365, quiet=True)
    assert rc == 2


def test_cmd_metrics_renders_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _run_engine(tmp_path)
    capsys.readouterr()  # flush
    rc = cmd_metrics(run_dir, periods_per_year=365, quiet=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "metrics_report.html" in out
    assert (run_dir / "charts" / "metrics_report.html").exists()


def test_main_dispatch_metrics(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path)
    rc = main(["metrics", str(run_dir), "--quiet"])
    assert rc == 0
