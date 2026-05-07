"""Trade Review Chart (Phase 2.5 PR X).

기존 ``run_chart.html`` 은 전체 백테스트 기간을 한 화면에 보여주므로 진입/청산 마커가
가격축 위에서 분간이 어렵다. PR X 는 trade 단위로 zoom 한 chart 를 따로 발행해 사람이
실제로 검토할 수 있게 한다.

산출 (run_dir/charts/trades/):
- ``index.html`` — symbol 별 trade 테이블 + 각 chart 링크
- ``{symbol}_trade_{n}.html`` — trade 주변 (pre_bars + 보유 + post_bars) 봉만 그린 candlestick

각 chart 구성:
- candlestick (해당 윈도우의 bars)
- entry / exit 마커 (가격 = fill price)
- 진입봉 mid-trade 의 추가 fill 마커 (scale-in/out 등)
- ORDER_MODIFIED 마커 (trailing stop / BE 이동 추적)
- y-축은 윈도우 내 high/low + 작은 padding 으로 자동 zoom

Trade 식별:
- FILL 이벤트를 시간순으로 stream 처리하며 per-symbol running position size 를 추적.
- flat → non-flat = entry. non-flat → flat = exit. flip 은 close + open.
- 미청산 trade (run 종료 시점) 도 ``open=True`` 로 별도 표시.

CLI: ``backtester trade-review runs/{run_id}/ [--pre-bars N] [--post-bars N]``.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import plotly.graph_objects as go
import polars as pl

from backtester.data.base import parse_timeframe, sanitize_symbol
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.viz.run_chart import _load_run_config


@dataclass
class FillRecord:
    ts: datetime
    price: Decimal
    side: str
    size: Decimal
    intent_reason: str = ""


@dataclass
class ModifyRecord:
    ts: datetime
    stop_price: Decimal | None
    limit_price: Decimal | None


@dataclass
class TradeRecord:
    """식별된 trade 한 건. fills/modifies 로 마커 그릴 정보 모두 보존.

    Phase 3.5 — multi-leg awareness:

    - ``bracket_kind`` is inferred from the exit fills' ``intent_reason``
      strings: ``"single"`` when only one ``bracket_tp:*`` (or single SL)
      label appears, ``"multi"`` when multiple ``bracket_tp:*:tpN`` labels
      appear within the same trade, ``None`` when no bracket-emitted
      reasons are seen (e.g. manual close, time-stop ClosePosition).
    - ``exit_legs`` is the ordered list of exit-leg labels parsed from the
      reasons (``"tp1"``, ``"tp2"``, ``"tp3"``, ``"sl"``, ``"timeout"``,
      etc.) — preserves *which* TP fired in *what* order.
    - ``weighted_exit_price`` is the volume-weighted average of all
      reduce-only / exit fills, so realized PnL% reflects partial closes
      correctly. ``exit_price`` keeps the legacy meaning (last fill's
      price) for backwards compatibility with existing chart code.
    """

    symbol: str
    direction: str  # "long" | "short"
    entry_ts: datetime
    entry_price: Decimal
    entry_size: Decimal
    exit_ts: datetime | None = None
    exit_price: Decimal | None = None
    fills: list[FillRecord] = field(default_factory=list)
    modifies: list[ModifyRecord] = field(default_factory=list)
    open: bool = False  # run 종료 시점에 미청산이면 True
    # Phase 3.5 multi-leg metadata.
    bracket_kind: str | None = None  # "single" | "multi" | None
    exit_legs: list[str] = field(default_factory=list)
    weighted_exit_price: Decimal | None = None

    @property
    def realized_pnl_pct(self) -> Decimal | None:
        """Volume-weighted realized PnL% across all exit fills.

        Falls back to the legacy ``exit_price`` (last fill) when
        ``weighted_exit_price`` is unset (open trades, or paths that
        bypass ``identify_trades`` post-processing).
        """
        ref_exit = self.weighted_exit_price or self.exit_price
        if ref_exit is None or self.entry_price <= 0:
            return None
        if self.direction == "long":
            return (ref_exit - self.entry_price) / self.entry_price
        return (self.entry_price - ref_exit) / self.entry_price


# ---------- trade identification ---------------------------------------------


def identify_trades(reader: EventLogReader) -> list[TradeRecord]:
    """FILL 이벤트 시퀀스를 per-symbol position 트래킹으로 trade 단위 묶기.

    파셜 fill / scale-in 등 mid-trade fill 도 trade.fills 에 보존되어 chart 마커로 표시.
    flip 은 한 trade 종료 + 새 trade 시작.
    """
    trades: list[TradeRecord] = []
    pos: dict[str, Decimal] = {}
    open_trade: dict[str, TradeRecord] = {}

    fills_sorted = sorted(reader.by_type(EventType.FILL), key=lambda e: e.ts)

    for evt in fills_sorted:
        p = evt.payload
        sym = p["symbol"]
        side = p["side"]
        size = Decimal(str(p["size"]))
        price = Decimal(str(p["price"]))
        signed = size if side == "buy" else -size
        prev = pos.get(sym, Decimal("0"))
        new = prev + signed
        pos[sym] = new

        was_flat = prev == 0
        is_flat = new == 0
        flipped = (prev > 0 and new < 0) or (prev < 0 and new > 0)
        record = FillRecord(
            ts=evt.ts,
            price=price,
            side=side,
            size=size,
            intent_reason=str(p.get("intent_reason") or ""),
        )

        if was_flat and not is_flat:
            t = TradeRecord(
                symbol=sym,
                direction="long" if new > 0 else "short",
                entry_ts=evt.ts,
                entry_price=price,
                entry_size=abs(new),
            )
            t.fills.append(record)
            open_trade[sym] = t
        elif not was_flat and is_flat:
            if sym in open_trade:
                closed = open_trade.pop(sym)
                closed.exit_ts = evt.ts
                closed.exit_price = price
                closed.fills.append(record)
                trades.append(closed)
        elif flipped:
            if sym in open_trade:
                closed = open_trade.pop(sym)
                closed.exit_ts = evt.ts
                closed.exit_price = price
                closed.fills.append(record)
                trades.append(closed)
            new_t = TradeRecord(
                symbol=sym,
                direction="long" if new > 0 else "short",
                entry_ts=evt.ts,
                entry_price=price,
                entry_size=abs(new),
            )
            new_t.fills.append(record)
            open_trade[sym] = new_t
        else:  # mid-trade fill (scale-in/out)
            if sym in open_trade:
                open_trade[sym].fills.append(record)

    # ORDER_MODIFIED payload 에는 symbol 이 없으므로 trade record 에 직접 attach 하지 않고
    # render_trade_review 단계의 시간-윈도우 매칭으로 marker 만 그린다 — multi-symbol 에서
    # 다른 심볼의 modify 가 잘못 잡히지 않도록 window 가 trade.entry_ts ~ exit_ts 안에 든
    # event 만 사용한다.

    # 미청산 trade — open 상태로 보존 (chart 는 entry 만 표시)
    for t in open_trade.values():
        t.open = True
        trades.append(t)

    # Phase 3.5 — multi-leg post-processing: parse exit-leg labels and
    # weighted exit price from each trade's fills.
    for t in trades:
        _annotate_multi_leg(t)

    return trades


# ---------- Phase 3.5 multi-leg post-processing -----------------------------


def _parse_exit_label(intent_reason: str) -> str | None:
    """Map a fill's ``intent_reason`` to a short exit-leg label.

    - ``bracket_tp:<parent>:tp1`` → ``"tp1"`` (multi-leg label after second colon)
    - ``bracket_tp:<parent>``     → ``"tp"``  (single-bracket TP)
    - ``bracket_sl:<parent>``     → ``"sl"``
    - ``*time_stop*``             → ``"timeout"``
    - anything else with ``"close"`` in it → ``"close"``

    Returns ``None`` for non-exit fills (entry / scale-in reasons we don't
    classify as legs).
    """
    if not intent_reason:
        return None
    if intent_reason.startswith("bracket_tp:"):
        parts = intent_reason.split(":")
        # bracket_tp:<parent>:<label> → label is parts[2]
        if len(parts) >= 3 and parts[2]:
            return parts[2]
        return "tp"
    if intent_reason.startswith("bracket_sl:"):
        return "sl"
    if "time_stop" in intent_reason or "timeout" in intent_reason:
        return "timeout"
    if "close" in intent_reason:
        return "close"
    return None


def _annotate_multi_leg(trade: TradeRecord) -> None:
    """Populate ``bracket_kind`` / ``exit_legs`` / ``weighted_exit_price`` on
    a finalized ``TradeRecord`` from its fills.

    Closing fills are detected by side relative to ``trade.direction``:

    - long trade → ``side == "sell"`` fills reduce / close.
    - short trade → ``side == "buy"`` fills reduce / close.

    The weighted price ignores the entry fill (first one in the same
    direction as the trade) and aggregates only the reduce-side fills, so
    it works the same for partial closes as for a single full close.
    """
    if trade.entry_size <= 0:
        return
    closing_side = "sell" if trade.direction == "long" else "buy"
    legs: list[str] = []
    weighted_num = Decimal("0")
    weighted_den = Decimal("0")
    multi_tp_labels: set[str] = set()
    saw_single_tp = False
    for fill in trade.fills:
        if fill.side != closing_side:
            continue
        weighted_num += fill.price * fill.size
        weighted_den += fill.size
        label = _parse_exit_label(fill.intent_reason)
        if label is not None:
            legs.append(label)
            if label.startswith("tp") and label != "tp":
                multi_tp_labels.add(label)
            elif label == "tp":
                saw_single_tp = True
    if weighted_den > 0:
        trade.weighted_exit_price = weighted_num / weighted_den
    trade.exit_legs = legs
    if multi_tp_labels:
        trade.bracket_kind = "multi"
    elif saw_single_tp or "sl" in legs:
        trade.bracket_kind = "single"
    else:
        trade.bracket_kind = None


# ---------- bar window slicing ----------------------------------------------


def _window_bars(
    bars: pl.DataFrame,
    *,
    start_ts: datetime,
    end_ts: datetime,
) -> pl.DataFrame:
    if bars.height == 0:
        return bars
    return bars.filter(
        (pl.col("timestamp") >= start_ts) & (pl.col("timestamp") <= end_ts)
    )


def _bars_for_symbol(
    run_dir: Path,
    symbol: str,
    timeframe: str,
) -> pl.DataFrame | None:
    path = run_dir / "bars" / f"{sanitize_symbol(symbol)}_{timeframe}.parquet"
    if not path.exists():
        return None
    df = pl.read_parquet(path)
    return df if df.height > 0 else None


def _modifies_in_window(
    reader: EventLogReader,
    *,
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[datetime, Decimal | None]]:
    """ORDER_MODIFIED 의 stop_price 이동을 윈도우 내에서 시간순 반환."""
    rows: list[tuple[datetime, Decimal | None]] = []
    for evt in reader.by_type(EventType.ORDER_MODIFIED):
        if evt.ts < start_ts or evt.ts > end_ts:
            continue
        sp = evt.payload.get("stop_price")
        rows.append(
            (evt.ts, Decimal(str(sp)) if sp is not None else None)
        )
    rows.sort(key=lambda r: r[0])
    return rows


# ---------- per-trade chart -------------------------------------------------


def _build_trade_figure(
    trade: TradeRecord,
    bars_window: pl.DataFrame,
    modifies: list[tuple[datetime, Decimal | None]],
    *,
    title: str,
) -> go.Figure:
    fig = go.Figure()
    if bars_window.height > 0:
        fig.add_trace(
            go.Candlestick(
                x=bars_window["timestamp"].to_list(),
                open=bars_window["open"].to_list(),
                high=bars_window["high"].to_list(),
                low=bars_window["low"].to_list(),
                close=bars_window["close"].to_list(),
                name=trade.symbol,
                showlegend=False,
            )
        )
    # entry marker
    entry_color = "green" if trade.direction == "long" else "red"
    entry_symbol = "triangle-up" if trade.direction == "long" else "triangle-down"
    fig.add_trace(
        go.Scatter(
            x=[trade.entry_ts],
            y=[float(trade.entry_price)],
            mode="markers+text",
            marker={
                "symbol": entry_symbol,
                "size": 16,
                "color": entry_color,
                "line": {"width": 2, "color": "black"},
            },
            text=[f"entry {trade.direction}"],
            textposition="top center",
            name="entry",
        )
    )
    # exit marker
    if trade.exit_ts is not None and trade.exit_price is not None:
        pnl = trade.realized_pnl_pct
        exit_text = f"exit ({float(pnl) * 100:+.2f}%)" if pnl is not None else "exit"
        fig.add_trace(
            go.Scatter(
                x=[trade.exit_ts],
                y=[float(trade.exit_price)],
                mode="markers+text",
                marker={
                    "symbol": "x",
                    "size": 14,
                    "color": "black",
                    "line": {"width": 2},
                },
                text=[exit_text],
                textposition="bottom center",
                name="exit",
            )
        )
    # mid-trade fills (scale-in/out) — entry / exit 외 fill 표시
    boundary_ts = {trade.entry_ts}
    if trade.exit_ts is not None:
        boundary_ts.add(trade.exit_ts)
    mid_x: list[Any] = []
    mid_y: list[float] = []
    mid_text: list[str] = []
    for f in trade.fills:
        if f.ts in boundary_ts:
            continue
        mid_x.append(f.ts)
        mid_y.append(float(f.price))
        mid_text.append(f"{f.side} {f.size}")
    if mid_x:
        fig.add_trace(
            go.Scatter(
                x=mid_x,
                y=mid_y,
                mode="markers",
                marker={"symbol": "circle-open", "size": 10, "color": "purple"},
                text=mid_text,
                name="mid-trade fill",
            )
        )
    # SL/BE/trail modify markers
    if modifies:
        mx: list[Any] = []
        my: list[float] = []
        mt: list[str] = []
        for ts, sp in modifies:
            if sp is None:
                continue
            mx.append(ts)
            my.append(float(sp))
            mt.append(f"SL→{sp}")
        if mx:
            fig.add_trace(
                go.Scatter(
                    x=mx,
                    y=my,
                    mode="markers",
                    marker={
                        "symbol": "diamond",
                        "size": 8,
                        "color": "orange",
                    },
                    text=mt,
                    name="stop modified",
                )
            )

    # y-축 자동 zoom — 윈도우 high/low + 1% padding.
    if bars_window.height > 0:
        lo_val = cast(float | None, bars_window["low"].min())
        hi_val = cast(float | None, bars_window["high"].max())
        if lo_val is not None and hi_val is not None:
            lo = float(lo_val)
            hi = float(hi_val)
            span = hi - lo if hi > lo else max(abs(hi), 1.0) * 0.01
            pad = span * 0.05
            fig.update_yaxes(range=[lo - pad, hi + pad])

    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        height=520,
        showlegend=True,
        hovermode="x unified",
    )
    return fig


# ---------- public API -------------------------------------------------------


def _summarize(t: TradeRecord) -> dict[str, Any]:
    pnl = t.realized_pnl_pct
    return {
        "symbol": t.symbol,
        "direction": t.direction,
        "entry_ts": t.entry_ts.isoformat(),
        "exit_ts": t.exit_ts.isoformat() if t.exit_ts else "(open)",
        "entry_price": str(t.entry_price),
        "exit_price": str(t.exit_price) if t.exit_price else "",
        "pnl_pct": (
            f"{float(pnl) * 100:+.2f}%" if pnl is not None else ""
        ),
        "open": t.open,
    }


def render_trade_review(
    run_dir: Path,
    *,
    pre_bars: int = 72,
    post_bars: int = 48,
) -> Path:
    """Trade Review chart 트리 생성.

    반환: ``run_dir/charts/trades/index.html`` 의 절대 경로.
    """
    if not (run_dir / "events.jsonl").exists():
        raise FileNotFoundError(f"events.jsonl missing in {run_dir}")
    config = _load_run_config(run_dir)
    primary_tf = str(config.get("primary_timeframe") or "1h")
    interval = parse_timeframe(primary_tf)
    timeframes_per_symbol_raw = config.get("timeframes_per_symbol") or {}
    # config.yaml 에서는 dict[str, list[str]]. 값이 없으면 primary_tf 로 fallback.
    timeframes_per_symbol: dict[str, str] = {}
    if isinstance(timeframes_per_symbol_raw, dict):
        for sym, tfs in timeframes_per_symbol_raw.items():
            if isinstance(tfs, list) and tfs:
                timeframes_per_symbol[sym] = tfs[0]

    out_dir = run_dir / "charts" / "trades"
    out_dir.mkdir(parents=True, exist_ok=True)

    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)

    # symbol 별 인덱싱
    by_symbol: dict[str, list[TradeRecord]] = {}
    for t in trades:
        by_symbol.setdefault(t.symbol, []).append(t)

    chart_links: dict[str, list[tuple[TradeRecord, Path]]] = {}
    for sym, sym_trades in by_symbol.items():
        tf = timeframes_per_symbol.get(sym, primary_tf)
        bars = _bars_for_symbol(run_dir, sym, tf)
        chart_links[sym] = []
        for n, t in enumerate(sym_trades, start=1):
            window_end = t.exit_ts if t.exit_ts is not None else t.entry_ts + interval
            start_ts = t.entry_ts - interval * pre_bars
            end_ts = window_end + interval * post_bars
            bars_win = (
                _window_bars(bars, start_ts=start_ts, end_ts=end_ts)
                if bars is not None
                else pl.DataFrame()
            )
            modifies = _modifies_in_window(
                reader, start_ts=t.entry_ts, end_ts=window_end
            )
            pnl = t.realized_pnl_pct
            pnl_str = (
                f"{float(pnl) * 100:+.2f}%" if pnl is not None else "(open)"
            )
            title = (
                f"{sym} trade #{n} — {t.direction} — "
                f"{t.entry_ts.isoformat()} → "
                f"{t.exit_ts.isoformat() if t.exit_ts else 'open'} ({pnl_str})"
            )
            fig = _build_trade_figure(
                t, bars_win, modifies, title=title
            )
            out_path = out_dir / f"{sanitize_symbol(sym)}_trade_{n}.html"
            fig.write_html(str(out_path), include_plotlyjs="cdn")
            chart_links[sym].append((t, out_path))

    index_path = out_dir / "index.html"
    index_path.write_text(
        _render_index_html(run_dir, chart_links, pre_bars=pre_bars, post_bars=post_bars),
        encoding="utf-8",
    )
    return index_path


# ---------- index.html -------------------------------------------------------


def _render_index_html(
    run_dir: Path,
    chart_links: dict[str, list[tuple[TradeRecord, Path]]],
    *,
    pre_bars: int,
    post_bars: int,
) -> str:
    sections = []
    for sym, items in chart_links.items():
        rows = []
        for n, (trade, path) in enumerate(items, start=1):
            summary = _summarize(trade)
            href = path.name  # 같은 디렉토리 상대 링크.
            pnl_class = ""
            if summary["pnl_pct"].startswith("+"):
                pnl_class = "pnl-pos"
            elif summary["pnl_pct"].startswith("-"):
                pnl_class = "pnl-neg"
            rows.append(
                "<tr>"
                f"<td>{n}</td>"
                f"<td>{_html.escape(summary['direction'])}</td>"
                f"<td>{_html.escape(summary['entry_ts'])}</td>"
                f"<td>{_html.escape(summary['exit_ts'])}</td>"
                f"<td>{_html.escape(summary['entry_price'])}</td>"
                f"<td>{_html.escape(summary['exit_price'])}</td>"
                f"<td class=\"{pnl_class}\">{_html.escape(summary['pnl_pct'])}</td>"
                f"<td><a href=\"{_html.escape(href)}\">chart</a></td>"
                "</tr>"
            )
        section = (
            f"<h2>{_html.escape(sym)} ({len(items)} trades)</h2>"
            "<table class=\"trades\">"
            "<thead><tr>"
            "<th>#</th><th>side</th><th>entry</th><th>exit</th>"
            "<th>entry px</th><th>exit px</th><th>pnl</th><th></th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
        sections.append(section)

    body = "\n".join(sections) if sections else "<p>No trades found.</p>"
    safe_run = _html.escape(run_dir.name)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Trade Review — {safe_run}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1100px;
         margin: 2em auto; color: #222; padding: 0 1em; }}
  h1 {{ font-size: 1.4em; }}
  h2 {{ font-size: 1.1em; margin-top: 1.5em; border-bottom: 1px solid #ddd; }}
  table.trades {{ border-collapse: collapse; width: 100%; margin: 0.5em 0 1.5em; }}
  table.trades th, table.trades td {{ padding: 0.4em 0.7em; border: 1px solid #ddd;
                                       font-size: 0.9em; text-align: left; }}
  table.trades tr:nth-child(odd) td {{ background: #fafafa; }}
  .pnl-pos {{ color: #1a7f37; }}
  .pnl-neg {{ color: #b1361e; }}
  .meta {{ color: #666; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>Trade review — {safe_run}</h1>
<p class="meta">window: pre={pre_bars} bars, post={post_bars} bars (after exit).
Generated by backtester.viz.trade_review.</p>
{body}
</body>
</html>
"""


__all__ = [
    "FillRecord",
    "ModifyRecord",
    "TradeRecord",
    "identify_trades",
    "render_trade_review",
]
