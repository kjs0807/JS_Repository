"""Run chart (Phase 1.5 PR 11, spec §10.4).

``build_run_chart(run_dir)`` 가 ``run_dir`` **하나만** 입력으로 4단 subplot Plotly Figure
를 만든다. 외부 cache (DataSource 의 base_dir 등) 의존 없음 — bars/indicators 는 Engine
이 ``run_dir/bars/`` / ``run_dir/indicators/`` 에 영속화한 parquet 을 사용.

4단 구성 (spec §10.4):
1. 캔들 + 지표 (BB / KC 등 indicators 컬럼) + intent/fill 마커
2. 포지션 size (step chart)
3. equity
4. drawdown (fill-to-zero)

config 로드:
- ``run_dir/config.yaml`` (Phase 1.5+) 우선, 없으면 ``run_dir/config.json`` (Phase 1).
- ``primary_symbol`` / ``primary_timeframe`` / ``initial_equity`` / ``resolved_run_id`` 추출.

빈 events / 빈 bars 등 엣지 케이스에서도 Figure 객체 자체는 반환 (각 row 의 trace 가
비어 있을 뿐).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import polars as pl
import yaml
from plotly.subplots import make_subplots

from backtester.data.base import sanitize_symbol
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.viz.equity import build_equity_series


def _load_run_config(run_dir: Path) -> dict[str, Any]:
    """``run_dir`` 의 영속화된 config 를 읽는다.

    yaml (Phase 1.5+) 우선, json (Phase 1) fallback. 둘 다 없으면 ``FileNotFoundError``.
    """
    yaml_path = run_dir / "config.yaml"
    json_path = run_dir / "config.json"
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        if not isinstance(data, dict):
            raise ValueError(f"{yaml_path} root is not a mapping")
        return data
    if json_path.exists():
        with open(json_path, encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise ValueError(f"{json_path} root is not a mapping")
        return data
    raise FileNotFoundError(
        f"No config.yaml or config.json found in {run_dir}"
    )


def _read_optional_parquet(path: Path) -> pl.DataFrame | None:
    if not path.exists():
        return None
    df = pl.read_parquet(path)
    return df if df.height > 0 else None


def _add_price_indicators(
    fig: go.Figure,
    bars: pl.DataFrame | None,
    indicators: pl.DataFrame | None,
    primary_sym: str,
    *,
    row: int,
) -> None:
    if bars is not None:
        fig.add_trace(
            go.Candlestick(
                x=bars["timestamp"].to_list(),
                open=bars["open"].to_list(),
                high=bars["high"].to_list(),
                low=bars["low"].to_list(),
                close=bars["close"].to_list(),
                name=primary_sym,
                showlegend=False,
            ),
            row=row,
            col=1,
        )
    if indicators is not None:
        for col in indicators.columns:
            if col == "timestamp":
                continue
            fig.add_trace(
                go.Scatter(
                    x=indicators["timestamp"].to_list(),
                    y=indicators[col].to_list(),
                    mode="lines",
                    name=col,
                    line={"width": 1},
                ),
                row=row,
                col=1,
            )


def _add_event_markers(
    fig: go.Figure, reader: EventLogReader, *, row: int
) -> None:
    intent_x: list[Any] = []
    intent_y: list[float] = []
    intent_text: list[str] = []
    for evt in reader.by_type(EventType.INTENT_CREATED):
        side = (evt.payload.get("intent") or {}).get("side", "?")
        try:
            price = float(evt.payload.get("bar_close_price", 0))
        except (TypeError, ValueError):
            price = 0.0
        intent_x.append(evt.ts)
        intent_y.append(price)
        intent_text.append(str(side))
    if intent_x:
        fig.add_trace(
            go.Scatter(
                x=intent_x,
                y=intent_y,
                mode="markers",
                marker={"symbol": "circle-open", "size": 8, "color": "purple"},
                name="intent",
                text=intent_text,
                hovertemplate="%{x}<br>side=%{text}<extra>intent</extra>",
            ),
            row=row,
            col=1,
        )

    fill_buy_x: list[Any] = []
    fill_buy_y: list[float] = []
    fill_sell_x: list[Any] = []
    fill_sell_y: list[float] = []
    for evt in reader.by_type(EventType.FILL):
        side = evt.payload.get("side", "?")
        try:
            price = float(evt.payload.get("price", 0))
        except (TypeError, ValueError):
            price = 0.0
        if side == "buy":
            fill_buy_x.append(evt.ts)
            fill_buy_y.append(price)
        elif side == "sell":
            fill_sell_x.append(evt.ts)
            fill_sell_y.append(price)
    if fill_buy_x:
        fig.add_trace(
            go.Scatter(
                x=fill_buy_x,
                y=fill_buy_y,
                mode="markers",
                marker={"symbol": "triangle-up", "size": 10, "color": "green"},
                name="fill buy",
            ),
            row=row,
            col=1,
        )
    if fill_sell_x:
        fig.add_trace(
            go.Scatter(
                x=fill_sell_x,
                y=fill_sell_y,
                mode="markers",
                marker={"symbol": "triangle-down", "size": 10, "color": "red"},
                name="fill sell",
            ),
            row=row,
            col=1,
        )


def _add_position_step(
    fig: go.Figure,
    equity_series: pl.DataFrame,
    primary_sym: str,
    *,
    row: int,
) -> None:
    if equity_series.height == 0:
        return
    pos_col = f"position_size_{primary_sym}"
    if pos_col not in equity_series.columns:
        return
    fig.add_trace(
        go.Scatter(
            x=equity_series["timestamp"].to_list(),
            y=equity_series[pos_col].fill_null(0).to_list(),
            mode="lines",
            line_shape="hv",
            name=f"pos {primary_sym}",
            showlegend=False,
        ),
        row=row,
        col=1,
    )


def _add_equity(fig: go.Figure, equity_series: pl.DataFrame, *, row: int) -> None:
    if equity_series.height == 0:
        return
    fig.add_trace(
        go.Scatter(
            x=equity_series["timestamp"].to_list(),
            y=equity_series["equity"].to_list(),
            mode="lines",
            name="equity",
            line={"color": "steelblue"},
            showlegend=False,
        ),
        row=row,
        col=1,
    )


def _add_drawdown(fig: go.Figure, equity_series: pl.DataFrame, *, row: int) -> None:
    if equity_series.height == 0:
        return
    fig.add_trace(
        go.Scatter(
            x=equity_series["timestamp"].to_list(),
            y=equity_series["drawdown"].to_list(),
            mode="lines",
            fill="tozeroy",
            line={"color": "indianred"},
            name="drawdown",
            showlegend=False,
        ),
        row=row,
        col=1,
    )


def build_run_chart(run_dir: Path) -> go.Figure:
    """``run_dir`` 만으로 완전 재현되는 4단 Plotly Figure 반환 (spec §10.4)."""
    config = _load_run_config(run_dir)
    primary_sym = str(config["primary_symbol"])
    primary_tf = str(config["primary_timeframe"])
    initial_equity = Decimal(str(config["initial_equity"]))
    title_run = str(
        config.get("resolved_run_id") or config.get("run_id") or run_dir.name
    )

    sanitized = sanitize_symbol(primary_sym)
    bars = _read_optional_parquet(
        run_dir / "bars" / f"{sanitized}_{primary_tf}.parquet"
    )
    indicators = _read_optional_parquet(
        run_dir / "indicators" / f"{sanitized}_{primary_tf}.parquet"
    )

    reader = EventLogReader(run_dir / "events.jsonl")
    equity_series = build_equity_series(reader, initial_equity)

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.5, 0.15, 0.2, 0.15],
        vertical_spacing=0.04,
        subplot_titles=(
            f"{primary_sym} {primary_tf} + indicators",
            f"position {primary_sym}",
            "equity",
            "drawdown",
        ),
    )

    _add_price_indicators(fig, bars, indicators, primary_sym, row=1)
    _add_event_markers(fig, reader, row=1)
    _add_position_step(fig, equity_series, primary_sym, row=2)
    _add_equity(fig, equity_series, row=3)
    _add_drawdown(fig, equity_series, row=4)

    fig.update_layout(
        title=f"Backtest run: {title_run}",
        xaxis_rangeslider_visible=False,  # 4단 subplot 과 충돌 방지
        height=900,
        showlegend=True,
        hovermode="x unified",
    )
    return fig


def render_run_chart(run_dir: Path) -> Path:
    """``build_run_chart`` 결과를 ``run_dir/charts/run_chart.html`` 로 저장."""
    fig = build_run_chart(run_dir)
    charts_dir = run_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    output = charts_dir / "run_chart.html"
    fig.write_html(str(output), include_plotlyjs="cdn")
    return output
