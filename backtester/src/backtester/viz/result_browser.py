"""Result Browser (Phase 2.5 PR Y).

run_dir 의 모든 산출물 (run_chart / metrics_report / trade_review / exports) 을 한
페이지에서 탐색할 수 있는 ``charts/index.html`` 를 생성한다.

포함:
- 헤드라인 + summary metrics (export.summary 와 동일한 출처).
- 심볼별 fill / trade 카운트 테이블.
- equity + drawdown 인라인 mini chart.
- 다른 산출물 링크 — run_chart.html / metrics_report.html / trades/index.html /
  exports/*.csv / exports/summary.json. 누락된 파일은 회색 처리 (안 클릭됨) .

Engine 자동 호출은 하지 않는다 — 사용자가 ``backtester browser runs/{run_id}/`` 로
명시 호출 (또는 다른 viz 도구 실행 후 마지막 단계).
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import polars as pl

from backtester.analysis.export import _build_summary, _extract_fills, _extract_intents
from backtester.events.reader import EventLogReader
from backtester.viz.equity import build_equity_series
from backtester.viz.run_chart import _load_run_config


def _build_mini_equity_figure(equity: pl.DataFrame) -> go.Figure:
    fig = go.Figure()
    if equity.height > 0:
        fig.add_trace(
            go.Scatter(
                x=equity["timestamp"].to_list(),
                y=equity["equity"].to_list(),
                mode="lines",
                name="equity",
                line={"color": "steelblue"},
            )
        )
        if "drawdown" in equity.columns:
            fig.add_trace(
                go.Scatter(
                    x=equity["timestamp"].to_list(),
                    y=equity["drawdown"].to_list(),
                    mode="lines",
                    name="drawdown",
                    yaxis="y2",
                    line={"color": "indianred", "dash": "dot"},
                )
            )
    fig.update_layout(
        height=320,
        margin={"l": 40, "r": 20, "t": 20, "b": 40},
        legend={"orientation": "h", "y": -0.2},
        yaxis2={
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "zeroline": False,
        },
    )
    return fig


_PERCENT_KEYS: frozenset[str] = frozenset(
    {"total_return", "max_drawdown_pct", "annual_volatility"}
)


def _format_metric_value(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
    if isinstance(value, float):
        if key in _PERCENT_KEYS:
            return f"{value * 100:+.4f}%"
        if abs(value) >= 1000:
            return f"{value:,.2f}"
        if abs(value) >= 1:
            return f"{value:.4f}"
        return f"{value:.6f}"
    return str(value)


_SUMMARY_LABELS: dict[str, str] = {
    "run_id": "Run ID",
    "primary_symbol": "Primary symbol",
    "primary_timeframe": "Primary timeframe",
    "start": "Start",
    "end": "End",
    "initial_equity": "Initial equity",
    "final_equity": "Final equity",
    "total_return": "Total return",
    "max_drawdown_pct": "Max drawdown",
    "max_drawdown_duration_bars": "MDD duration (bars)",
    "sharpe_ratio": "Sharpe",
    "sortino_ratio": "Sortino",
    "calmar_ratio": "Calmar",
    "annual_volatility": "Annual vol",
    "n_periods": "n periods",
    "n_fills": "Fills",
    "n_intents": "Intents",
}


def _summary_rows_html(summary: dict[str, Any]) -> str:
    rows: list[str] = []
    for key, label in _SUMMARY_LABELS.items():
        if key not in summary:
            continue
        value = summary.get(key)
        rows.append(
            f"<tr><td>{_html.escape(label)}</td>"
            f"<td>{_html.escape(_format_metric_value(key, value))}</td></tr>"
        )
    return "".join(rows)


def _per_symbol_table_html(summary: dict[str, Any]) -> str:
    fills_by = summary.get("fills_by_symbol") or {}
    intents_by = summary.get("intents_by_symbol") or {}
    syms = sorted(set(fills_by.keys()) | set(intents_by.keys()))
    if not syms:
        return "<p class=\"meta\">No symbol activity recorded.</p>"
    rows = []
    for s in syms:
        rows.append(
            "<tr>"
            f"<td>{_html.escape(s)}</td>"
            f"<td>{intents_by.get(s, 0)}</td>"
            f"<td>{fills_by.get(s, 0)}</td>"
            "</tr>"
        )
    return (
        "<table class=\"sym\">"
        "<thead><tr><th>symbol</th><th>intents</th><th>fills</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _link(label: str, run_dir: Path, rel_path: str) -> str:
    full = run_dir / rel_path
    if not full.exists():
        return f"<span class=\"missing\">{_html.escape(label)} (missing)</span>"
    href = rel_path.replace("\\", "/")
    return f"<a href=\"../{_html.escape(href)}\">{_html.escape(label)}</a>"


def _artifact_links_html(run_dir: Path) -> str:
    sections: list[str] = []
    chart_links = [
        ("Run chart (full timeline)", "charts/run_chart.html"),
        ("Metrics report", "charts/metrics_report.html"),
        ("Trade review (per-trade zoom)", "charts/trades/index.html"),
    ]
    sections.append(
        "<h3>Charts</h3><ul class=\"links\">"
        + "".join(f"<li>{_link(label, run_dir, p)}</li>" for label, p in chart_links)
        + "</ul>"
    )
    export_links = [
        ("fills.csv", "exports/fills.csv"),
        ("intents.csv", "exports/intents.csv"),
        ("orders.csv", "exports/orders.csv"),
        ("equity_curve.csv", "exports/equity_curve.csv"),
        ("summary.json", "exports/summary.json"),
    ]
    sections.append(
        "<h3>Exports</h3><ul class=\"links\">"
        + "".join(f"<li>{_link(label, run_dir, p)}</li>" for label, p in export_links)
        + "</ul>"
    )
    raw_links = [
        ("config.yaml", "config.yaml"),
        ("events.jsonl", "events.jsonl"),
        ("instruments_snapshot.yaml", "instruments_snapshot.yaml"),
    ]
    sections.append(
        "<h3>Raw artifacts</h3><ul class=\"links\">"
        + "".join(f"<li>{_link(label, run_dir, p)}</li>" for label, p in raw_links)
        + "</ul>"
    )
    return "\n".join(sections)


def render_result_browser(run_dir: Path) -> Path:
    """``run_dir/charts/index.html`` 생성 + 경로 반환.

    ``events.jsonl`` 만 있으면 동작. 누락된 산출물 링크는 (missing) 으로 회색 처리.
    """
    if not (run_dir / "events.jsonl").exists():
        raise FileNotFoundError(f"events.jsonl missing in {run_dir}")

    try:
        config = _load_run_config(run_dir)
    except FileNotFoundError:
        config = {}

    reader = EventLogReader(run_dir / "events.jsonl")
    fills = _extract_fills(reader)
    intents = _extract_intents(reader)
    summary = _build_summary(run_dir, reader, fills=fills, intents=intents)

    from decimal import Decimal

    initial_equity_raw = config.get("initial_equity")
    initial_equity = (
        Decimal(str(initial_equity_raw)) if initial_equity_raw is not None
        else Decimal("0")
    )
    equity = build_equity_series(reader, initial_equity)
    fig = _build_mini_equity_figure(equity)
    fig_html = fig.to_html(include_plotlyjs="cdn", full_html=False)

    summary_rows = _summary_rows_html(summary)
    per_sym = _per_symbol_table_html(summary)
    artifacts = _artifact_links_html(run_dir)
    safe_run = _html.escape(summary.get("run_id") or run_dir.name)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Result Browser — {safe_run}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1100px;
         margin: 2em auto; color: #222; padding: 0 1em; }}
  h1 {{ font-size: 1.5em; }}
  h2 {{ font-size: 1.1em; margin-top: 1.5em; border-bottom: 1px solid #ddd; }}
  h3 {{ font-size: 0.95em; margin: 1em 0 0.4em; color: #444; }}
  table {{ border-collapse: collapse; }}
  table.summary, table.sym {{ width: 100%; margin: 0.5em 0; }}
  table.summary td, table.summary th, table.sym td, table.sym th {{
        padding: 0.4em 0.7em; border: 1px solid #ddd; font-size: 0.92em; }}
  table.summary tr:nth-child(odd) td {{ background: #fafafa; }}
  ul.links {{ list-style: none; padding-left: 0; }}
  ul.links li {{ margin: 0.2em 0; }}
  ul.links a {{ color: #0a66c2; text-decoration: none; }}
  ul.links a:hover {{ text-decoration: underline; }}
  .missing {{ color: #aaa; }}
  .meta {{ color: #666; font-size: 0.85em; }}
  .grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 2em; }}
</style>
</head>
<body>
<h1>Result browser — {safe_run}</h1>
<p class="meta">Generated by backtester.viz.result_browser. Use this page as the
starting point — links to charts, exports, and raw artifacts are below.</p>

<div class="grid">
<div>
<h2>Summary</h2>
<table class="summary">{summary_rows}</table>
</div>
<div>
<h2>Per-symbol activity</h2>
{per_sym}
</div>
</div>

<h2>Equity / drawdown</h2>
{fig_html}

<h2>Artifacts</h2>
{artifacts}
</body>
</html>
"""

    out_dir = run_dir / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "index.html"
    output.write_text(html_doc, encoding="utf-8")
    return output


__all__ = ["render_result_browser"]
