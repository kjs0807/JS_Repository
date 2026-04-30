"""PR 11 viz/run_chart 테스트 (Phase 1.5, spec §10.4)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from backtester.viz.run_chart import (
    _load_run_config,
    build_run_chart,
    render_run_chart,
)

UTC = timezone.utc


def _make_btc_synthetic_parquet(target: Path, n_bars: int = 60) -> None:
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
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


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


def _run_engine(tmp_path: Path, run_id: str = "chart_smoke") -> Path:
    """Synthetic OHLCV + BBKC strategy 로 BacktestEngine 실행. ``run_dir`` 반환."""
    data_dir = tmp_path / "data"
    _make_btc_synthetic_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=60)
    cfg = BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 4, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    result = engine.run()
    return result.run_dir


# ---------- _load_run_config ------------------------------------------------


def test_load_run_config_prefers_yaml_over_json(tmp_path: Path) -> None:
    rd = tmp_path / "rd"
    rd.mkdir()
    (rd / "config.yaml").write_text("primary_symbol: BTCUSDT\n", encoding="utf-8")
    (rd / "config.json").write_text(
        json.dumps({"primary_symbol": "ETHUSDT"}), encoding="utf-8"
    )
    cfg = _load_run_config(rd)
    assert cfg["primary_symbol"] == "BTCUSDT"


def test_load_run_config_falls_back_to_json(tmp_path: Path) -> None:
    rd = tmp_path / "rd"
    rd.mkdir()
    (rd / "config.json").write_text(
        json.dumps({"primary_symbol": "ETHUSDT"}), encoding="utf-8"
    )
    cfg = _load_run_config(rd)
    assert cfg["primary_symbol"] == "ETHUSDT"


def test_load_run_config_missing_raises(tmp_path: Path) -> None:
    rd = tmp_path / "empty"
    rd.mkdir()
    with pytest.raises(FileNotFoundError, match="config"):
        _load_run_config(rd)


# ---------- build_run_chart -------------------------------------------------


def test_build_run_chart_returns_figure(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path)
    fig = build_run_chart(run_dir)
    assert isinstance(fig, go.Figure)
    # 4 row subplot 로 layout 안에 4 개의 yaxis 가 생긴다.
    yaxis_keys = [k for k in fig.layout if k.startswith("yaxis")]
    assert len(yaxis_keys) == 4


def test_build_run_chart_has_candle_and_equity_traces(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path)
    fig = build_run_chart(run_dir)
    types = {trace.type for trace in fig.data}
    # candlestick + scatter (indicators / equity / drawdown) 둘 다 있어야 한다.
    assert "candlestick" in types
    assert "scatter" in types


def test_build_run_chart_uses_resolved_run_id_in_title(tmp_path: Path) -> None:
    """auto_suffix 로 resolved_run_id 가 바뀌면 title 도 그것을 반영."""
    data_dir = tmp_path / "data"
    _make_btc_synthetic_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=60)
    output_dir = tmp_path / "runs"
    output_dir.mkdir()
    (output_dir / "auto_test").mkdir()  # 충돌 유발

    cfg = BacktestConfig(
        run_id="auto_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 4, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=output_dir,
        on_run_exists="auto_suffix",
    )
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    result = engine.run()
    fig = build_run_chart(result.run_dir)
    assert "auto_test_2" in fig.layout.title.text


# ---------- render_run_chart -----------------------------------------------


def test_render_run_chart_writes_html(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path)
    out = render_run_chart(run_dir)
    assert out == run_dir / "charts" / "run_chart.html"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # plotly HTML 의 핵심 신호
    assert "plotly" in text.lower()
    # CDN include 사용 (오프라인 임베딩 대신, spec §10.4)
    assert "cdn" in text.lower()


# ---------- self-contained (cache-clean) 회귀 ------------------------------


def test_run_chart_self_contained_after_external_cache_deleted(tmp_path: Path) -> None:
    """spec §10.1: 시각화 함수 입력은 ``run_dir`` 하나. 외부 cache (data_dir) 삭제해도
    동일 chart 가 만들어져야 한다."""
    run_dir = _run_engine(tmp_path)
    # 외부 데이터 cache 제거
    shutil.rmtree(tmp_path / "data")
    # 그래도 build_run_chart 는 run_dir/bars 만으로 동작해야 한다
    fig = build_run_chart(run_dir)
    assert isinstance(fig, go.Figure)
    out = render_run_chart(run_dir)
    assert out.exists()


# ---------- 엣지 케이스 ----------------------------------------------------


def test_build_run_chart_no_bars_dir_still_returns_figure(tmp_path: Path) -> None:
    """bars/ 디렉토리가 없거나 비어 있어도 (예: persist_run_data='none') Figure 반환.
    candlestick trace 없이 events/equity 만 그려진다."""
    run_dir = _run_engine(tmp_path)
    # bars/ 삭제 → 다른 영속물은 유지
    shutil.rmtree(run_dir / "bars")
    fig = build_run_chart(run_dir)
    types = {trace.type for trace in fig.data}
    assert "candlestick" not in types
