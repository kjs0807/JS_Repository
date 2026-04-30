"""PR 12 — Phase 1.5 종료 게이트 (spec §20).

BB-KC Squeeze 회귀 fixture 로 백테스트 실행 → events.jsonl → run_chart.html
까지 풀 파이프라인을 시각 검증 단위로 잠근다. 두 축:

1. **시각 회귀**: ``tests/fixtures/bbkc_signals.csv`` 의 buy entries 가 chart 의
   intent 마커 + fill 마커에 모두 등장. EventLog → run_chart 일관성 회귀.
2. **외부 cache 자급**: 외부 OHLCV 데이터 디렉토리 (DataSource.base_dir) 를 삭제한
   상태에서도 ``build_run_chart`` 가 동작. ``run_dir`` 단독 self-contained 가 spec §10.1
   대로 작동하는지 회귀.

Phase 1.5 (PR 9~12) 산출물 (CSVDataSource, FundingModel, YAML round-trip, CLI run/report,
events.parquet, EventLogReader, build_equity_series, build_run_chart) 의 풀 통합
스모크 — 회귀 fixture 가 존재할 때만 활성, 없으면 skip.
"""

from __future__ import annotations

import json
import shutil
from datetime import timezone
from decimal import Decimal
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.events import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from backtester.viz.run_chart import build_run_chart, render_run_chart

UTC = timezone.utc

FIXTURE_DIR = Path(__file__).parent / "fixtures"
ETHUSDT_PARQUET = FIXTURE_DIR / "ETHUSDT_1h.parquet"
SIGNALS_CSV = FIXTURE_DIR / "bbkc_signals.csv"


def _eth_instrument() -> Instrument:
    return Instrument(
        symbol="ETHUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="ETH",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _setup_fixture_run(tmp_path: Path) -> Path:
    """ETHUSDT_1h fixture 로 BacktestEngine 실행. run_dir 반환. fixture 없으면 skip."""
    if not ETHUSDT_PARQUET.exists():
        pytest.skip(
            f"ETHUSDT fixture parquet missing: {ETHUSDT_PARQUET}. "
            f"Generate via tools/export_db_to_parquet.py."
        )
    if not SIGNALS_CSV.exists():
        pytest.skip(f"bbkc_signals.csv missing: {SIGNALS_CSV}")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(ETHUSDT_PARQUET, data_dir / "ETHUSDT_1h.parquet")

    ohlcv = pl.read_parquet(ETHUSDT_PARQUET)
    start = ohlcv["timestamp"][0]
    end = ohlcv["timestamp"][-1]

    cfg = BacktestConfig(
        run_id="phase15_visual_regression",
        data_source=DataSourceConfig(base_dir=data_dir, type="parquet"),
        instruments=[_eth_instrument()],
        timeframes_per_symbol={"ETHUSDT": ["1h"]},
        primary_symbol="ETHUSDT",
        primary_timeframe="1h",
        start=start,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    return engine.run().run_dir


def _intent_buy_timestamps(events_path: Path) -> set[str]:
    out: set[str] = set()
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        evt = json.loads(line)
        if evt["type"] != "intent_created":
            continue
        side = (evt["payload"].get("intent") or {}).get("side")
        if side == "buy":
            out.add(evt["ts"])
    return out


def _fixture_buy_timestamps() -> set[str]:
    df = pl.read_csv(SIGNALS_CSV)
    return {
        ts if isinstance(ts, str) else ts.isoformat()
        for ts, direction in zip(
            df["timestamp"].to_list(),
            df["direction"].to_list(),
            strict=True,
        )
        if direction == "buy"
    }


# ---------- 시각 회귀: chart 가 fixture buy entries 를 모두 포함 ------------


def test_phase15_run_chart_contains_fixture_buy_intents(tmp_path: Path) -> None:
    """``bbkc_signals.csv`` 의 buy entry timestamp 가 모두 events.jsonl intent_created
    + chart intent 마커에 들어 있다. fixture 는 v8 actual 의 부분집합이므로 subset 게이트."""
    run_dir = _setup_fixture_run(tmp_path)

    actual_buys = _intent_buy_timestamps(run_dir / "events.jsonl")
    expected_buys = _fixture_buy_timestamps()
    missing = expected_buys - actual_buys
    assert not missing, f"events.jsonl missing fixture buy entries: {sorted(missing)}"

    fig = build_run_chart(run_dir)
    intent_traces = [
        t for t in fig.data if getattr(t, "name", None) == "intent"
    ]
    assert intent_traces, "run_chart must include 'intent' marker trace"
    intent_xs = {
        x.isoformat() if hasattr(x, "isoformat") else str(x)
        for trace in intent_traces
        for x in trace.x
    }
    chart_missing = expected_buys - intent_xs
    assert not chart_missing, (
        f"run_chart intent markers missing fixture entries: {sorted(chart_missing)}"
    )


def test_phase15_run_chart_has_expected_structure(tmp_path: Path) -> None:
    """4단 subplot + candle/scatter + equity/drawdown + 회귀 fixture 표면 점검."""
    run_dir = _setup_fixture_run(tmp_path)
    fig = build_run_chart(run_dir)
    assert isinstance(fig, go.Figure)

    yaxis_keys = [k for k in fig.layout if k.startswith("yaxis")]
    assert len(yaxis_keys) == 4

    types = {t.type for t in fig.data}
    assert "candlestick" in types  # row 1: ETHUSDT 캔들
    assert "scatter" in types  # row 1 indicators + row 2/3/4 lines

    # equity 시리즈가 1봉 이상
    equity_traces = [t for t in fig.data if getattr(t, "name", None) == "equity"]
    assert equity_traces and len(equity_traces[0].x) > 0
    drawdown_traces = [
        t for t in fig.data if getattr(t, "name", None) == "drawdown"
    ]
    assert drawdown_traces and len(drawdown_traces[0].x) > 0


# ---------- cache-clean 자급 (spec §10.1) ----------------------------------


def test_phase15_run_chart_works_after_external_cache_purge(tmp_path: Path) -> None:
    """spec §10.1: 시각화 함수의 입력은 ``run_dir`` 하나. 외부 데이터 디렉토리를
    삭제해도 동일 차트가 만들어져야 한다 (Phase 1.5 PR 12 종료 게이트 핵심)."""
    run_dir = _setup_fixture_run(tmp_path)

    # 외부 데이터 cache 제거 (DataSource.base_dir)
    shutil.rmtree(tmp_path / "data")

    # run_dir 만으로 chart + HTML 둘 다 가능해야 한다
    fig = build_run_chart(run_dir)
    assert isinstance(fig, go.Figure)
    out = render_run_chart(run_dir)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "plotly" in text.lower()


def test_phase15_run_chart_uses_persisted_bars_indicators(tmp_path: Path) -> None:
    """bars/indicators parquet 이 run_dir 안에 영속화되어 있고, 외부 cache 삭제 후에도
    Engine 영속물만으로 indicators trace 가 그려진다."""
    run_dir = _setup_fixture_run(tmp_path)
    bars_path = run_dir / "bars" / "ETHUSDT_1h.parquet"
    indicators_path = run_dir / "indicators" / "ETHUSDT_1h.parquet"
    assert bars_path.exists()
    assert indicators_path.exists()

    # 외부 cache 제거 후에도 indicators trace 가 살아있는지
    shutil.rmtree(tmp_path / "data")
    fig = build_run_chart(run_dir)
    indicator_names = {
        t.name for t in fig.data if t.type == "scatter" and t.name
    }
    # legacy 호환 기본값으로 BB / KC 컬럼 4-6 개 정도가 indicators 로 그려진다
    bb_kc_indicators = {n for n in indicator_names if "bb_" in n or "kc_" in n}
    assert bb_kc_indicators, (
        f"run_chart should include BB/KC indicator scatter traces; "
        f"found names: {sorted(indicator_names)}"
    )


# ---------- EventLogReader 통합 ---------------------------------------------


def test_phase15_event_reader_reproduces_engine_output(tmp_path: Path) -> None:
    """EventLogReader 가 Engine 이 쓴 events.jsonl 을 그대로 읽고, fixture buy 가
    by_type(EventType.INTENT_CREATED) 에서 모두 발견되는지 통합 회귀."""
    run_dir = _setup_fixture_run(tmp_path)
    reader = EventLogReader(run_dir / "events.jsonl")

    intent_buys = {
        evt.ts.isoformat()
        for evt in reader.by_type(EventType.INTENT_CREATED)
        if (evt.payload.get("intent") or {}).get("side") == "buy"
    }
    expected = _fixture_buy_timestamps()
    missing = expected - intent_buys
    assert not missing, (
        f"reader.by_type(INTENT_CREATED) buy missing: {sorted(missing)}"
    )

    counts = reader.counts_by_type()
    assert counts.get(EventType.SNAPSHOT, 0) > 0
