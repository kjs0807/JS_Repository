"""Human-readable exports — events.jsonl → CSV / JSON (Phase 2.5 PR Z).

기존 ``run_dir/`` 산출물은:
- ``events.jsonl`` (1차 원본, JSONL — 분석자 친화 X)
- ``results/equity_curve.parquet`` 등 parquet — pandas / polars 없으면 열기 어려움.

PR Z 는 사람이 Excel / Numbers / Notepad 로 열 수 있는 ``exports/*.csv`` 와
``exports/summary.json`` 을 추가한다.

산출:
- ``exports/fills.csv`` — FILL 이벤트
- ``exports/intents.csv`` — INTENT_CREATED 이벤트
- ``exports/orders.csv`` — ORDER_ADDED / CANCELLED / MODIFIED / EXPIRED / REJECTED
- ``exports/equity_curve.csv`` — SNAPSHOT 으로부터 build_equity_series + drawdown
- ``exports/summary.json`` — run_id / 기간 / metric / 심볼별 fill/intent count

CLI: ``backtester export runs/{run_id}/`` (cli/main.py 에 등록).

events.jsonl 만으로 동작 — config 가 없어도 summary 의 일부 필드만 None.
"""

from __future__ import annotations

import csv
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.viz.equity import build_equity_series
from backtester.viz.metrics import compute_core_metrics
from backtester.viz.run_chart import _load_run_config


def _to_str(value: Any) -> str:
    """Decimal/None 안전한 csv 셀 변환. None -> "" / 그 외 str()."""
    if value is None:
        return ""
    return str(value)


def _write_csv(
    path: Path,
    headers: list[str],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" — Windows 에서 csv 가 \r\n\r\n 이중을 만드는 것을 방지.
    with open(path, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _to_str(row.get(k)) for k in headers})


# ---------- per-event extractors --------------------------------------------


def _extract_fills(reader: EventLogReader) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for evt in reader.by_type(EventType.FILL):
        p = evt.payload
        rows.append(
            {
                "timestamp": evt.ts.isoformat(),
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "price": p.get("price"),
                "size": p.get("size"),
                "fee": p.get("fee"),
                "fee_currency": p.get("fee_currency"),
                "order_id": p.get("order_id"),
                "intent_reason": p.get("intent_reason"),
            }
        )
    return rows


def _classify_bracket(bracket: Any) -> tuple[str, Any, int | None, str]:
    """Return ``(kind, tp_price, tp_legs_n, tp_legs_prices)`` from a serialized
    bracket payload.

    - ``kind``: ``""`` if no bracket, ``"single"`` for ``BracketSpec`` (one TP /
      one SL), ``"multi"`` for ``MultiBracketSpec``.
    - ``tp_price``: most-useful single price — single bracket's
      ``take_profit_price`` or multi bracket's *closest* leg price (first leg
      in the spec tuple). ``None`` when the bracket has no TP.
    - ``tp_legs_n``: number of TP legs for multi (``None`` for single / no
      bracket).
    - ``tp_legs_prices``: ``";"``-joined leg prices for multi (``""`` for
      single / no bracket).
    """
    if bracket is None:
        return ("", None, None, "")
    legs = bracket.get("take_profits")
    if isinstance(legs, list):  # MultiBracketSpec
        prices = [leg.get("price") for leg in legs]
        first = prices[0] if prices else None
        joined = ";".join(_to_str(p) for p in prices)
        return ("multi", first, len(prices), joined)
    return ("single", bracket.get("take_profit_price"), None, "")


def _extract_intents(reader: EventLogReader) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for evt in reader.by_type(EventType.INTENT_CREATED):
        p = evt.payload
        intent = p.get("intent") or {}
        bracket = intent.get("bracket")
        kind, tp_price, tp_legs_n, tp_legs_prices = _classify_bracket(bracket)
        rows.append(
            {
                "decision_ts": evt.ts.isoformat(),
                "bar_timestamp": p.get("bar_timestamp"),
                "bar_close_price": p.get("bar_close_price"),
                "symbol": intent.get("symbol"),
                "side": intent.get("side"),
                "type": intent.get("type"),
                "reason": intent.get("reason"),
                "reduce_only": intent.get("reduce_only"),
                "limit_price": intent.get("limit_price"),
                "stop_price": intent.get("stop_price"),
                "has_bracket": bracket is not None,
                "bracket_kind": kind,
                "tp_price": tp_price,
                "sl_price": (bracket or {}).get("stop_loss_price"),
                "tp_legs_n": tp_legs_n,
                "tp_legs_prices": tp_legs_prices,
            }
        )
    return rows


_ORDER_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.ORDER_ADDED,
    EventType.ORDER_CANCELLED,
    EventType.ORDER_MODIFIED,
    EventType.ORDER_EXPIRED,
    EventType.ORDER_REJECTED,
    EventType.ORDER_RESIZED,  # Phase 3.5 — multi-leg SL auto-shrink
)


def _extract_orders(reader: EventLogReader) -> list[dict[str, Any]]:
    """Flatten order-lifecycle events into a single CSV-friendly stream.

    ``ORDER_RESIZED`` payloads have a different shape (no nested ``intent``,
    no ``parent_order_id`` at the top level — instead ``bracket_group_id`` +
    ``trigger_order_id`` + ``old/new_sized_quantity``). We map them onto the
    common columns so the timeline reads chronologically:

    - ``order_id`` ← payload's ``order_id`` (the resized SL).
    - ``sized_quantity`` ← ``new_sized_quantity`` (post-resize value).
    - ``parent_order_id`` ← payload's ``trigger_order_id`` (the TP that
      caused the resize) — repurposed so the relationship survives in CSV.
    - ``reason`` ← combined "tp_leg_filled: <old>->{new}" so analysts can see
      both old and new in one cell.
    """
    rows: list[dict[str, Any]] = []
    for et in _ORDER_EVENT_TYPES:
        for evt in reader.by_type(et):
            p = evt.payload
            if et == EventType.ORDER_RESIZED:
                old = p.get("old_sized_quantity")
                new = p.get("new_sized_quantity")
                rows.append(
                    {
                        "timestamp": evt.ts.isoformat(),
                        "event_type": et.value,
                        "order_id": p.get("order_id"),
                        "symbol": None,
                        "side": None,
                        "type": None,
                        "parent_order_id": p.get("trigger_order_id"),
                        "oco_group_id": None,
                        "bracket_group_id": p.get("bracket_group_id"),
                        "bracket_role": "protector_sl",
                        "tp_leg_index": None,
                        "sized_quantity": new,
                        "limit_price": None,
                        "stop_price": None,
                        "reason": (
                            f"{p.get('reason') or 'resize'}: {_to_str(old)}->{_to_str(new)}"
                        ),
                    }
                )
                continue
            intent = p.get("intent") or {}
            rows.append(
                {
                    "timestamp": evt.ts.isoformat(),
                    "event_type": et.value,
                    "order_id": p.get("order_id"),
                    "symbol": p.get("symbol") or intent.get("symbol"),
                    "side": intent.get("side"),
                    "type": intent.get("type"),
                    "parent_order_id": p.get("parent_order_id"),
                    "oco_group_id": p.get("oco_group_id"),
                    "bracket_group_id": p.get("bracket_group_id"),
                    "bracket_role": p.get("bracket_role"),
                    "tp_leg_index": p.get("tp_leg_index"),
                    "sized_quantity": p.get("sized_quantity"),
                    "limit_price": p.get("limit_price") or intent.get("limit_price"),
                    "stop_price": p.get("stop_price") or intent.get("stop_price"),
                    "reason": p.get("reason"),
                }
            )
    rows.sort(key=lambda r: (r["timestamp"], r.get("order_id") or ""))
    return rows


# ---------- summary ----------------------------------------------------------


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def _build_summary(
    run_dir: Path,
    reader: EventLogReader,
    *,
    fills: list[dict[str, Any]],
    intents: list[dict[str, Any]],
) -> dict[str, Any]:
    """summary.json 페이로드. config 가 없으면 일부 필드 None."""
    try:
        config = _load_run_config(run_dir)
    except FileNotFoundError:
        config = {}

    initial_equity_raw = config.get("initial_equity")
    initial_equity = (
        Decimal(str(initial_equity_raw)) if initial_equity_raw is not None else None
    )

    equity = build_equity_series(reader, initial_equity or Decimal("0"))
    metrics = compute_core_metrics(equity, periods_per_year=365)

    fills_by_symbol: dict[str, int] = {}
    for f in fills:
        s = f.get("symbol")
        if s:
            fills_by_symbol[s] = fills_by_symbol.get(s, 0) + 1
    intents_by_symbol: dict[str, int] = {}
    for it in intents:
        s = it.get("symbol")
        if s:
            intents_by_symbol[s] = intents_by_symbol.get(s, 0) + 1

    final_equity = (
        float(equity["equity"][-1]) if equity.height > 0 else None
    )

    return {
        "run_id": str(
            config.get("resolved_run_id")
            or config.get("run_id")
            or run_dir.name
        ),
        "start": str(config.get("start") or "") or None,
        "end": str(config.get("end") or "") or None,
        "primary_symbol": config.get("primary_symbol"),
        "primary_timeframe": config.get("primary_timeframe"),
        "initial_equity": float(initial_equity) if initial_equity is not None else None,
        "final_equity": final_equity,
        "total_return": _safe_float(metrics.get("total_return")),
        "max_drawdown_pct": _safe_float(metrics.get("max_drawdown_pct")),
        "max_drawdown_duration_bars": metrics.get("max_drawdown_duration_bars"),
        "sharpe_ratio": _safe_float(metrics.get("sharpe_ratio")),
        "sortino_ratio": _safe_float(metrics.get("sortino_ratio")),
        "calmar_ratio": _safe_float(metrics.get("calmar_ratio")),
        "annual_volatility": _safe_float(metrics.get("annual_volatility")),
        "n_periods": metrics.get("n_periods"),
        "n_fills": len(fills),
        "n_intents": len(intents),
        "fills_by_symbol": fills_by_symbol,
        "intents_by_symbol": intents_by_symbol,
    }


# ---------- public API -------------------------------------------------------


_FILL_HEADERS = [
    "timestamp",
    "symbol",
    "side",
    "price",
    "size",
    "fee",
    "fee_currency",
    "order_id",
    "intent_reason",
]
_INTENT_HEADERS = [
    "decision_ts",
    "bar_timestamp",
    "bar_close_price",
    "symbol",
    "side",
    "type",
    "reason",
    "reduce_only",
    "limit_price",
    "stop_price",
    "has_bracket",
    "bracket_kind",
    "tp_price",
    "sl_price",
    "tp_legs_n",
    "tp_legs_prices",
]
_ORDER_HEADERS = [
    "timestamp",
    "event_type",
    "order_id",
    "symbol",
    "side",
    "type",
    "parent_order_id",
    "oco_group_id",
    "bracket_group_id",
    "bracket_role",
    "tp_leg_index",
    "sized_quantity",
    "limit_price",
    "stop_price",
    "reason",
]
_EQUITY_HEADERS = [
    "timestamp",
    "equity",
    "cash",
    "realized_pnl",
    "unrealized_pnl",
    "drawdown",
    "drawdown_pct",
]


def export_run_data(run_dir: Path) -> dict[str, Path]:
    """``run_dir`` 의 events.jsonl + config 를 사람이 읽기 쉬운 CSV / JSON 으로 내보낸다.

    출력 디렉토리: ``run_dir/exports/``. 반환은 ``{name: path}``.

    빈 events 도 빈 CSV (header 만) 가 작성된다 — 후속 도구가 파일 존재만으로 분기 가능.
    """
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"run dir not found: {run_dir}")
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        raise FileNotFoundError(f"events.jsonl missing: {events_path}")

    reader = EventLogReader(events_path)
    fills = _extract_fills(reader)
    intents = _extract_intents(reader)
    orders = _extract_orders(reader)

    out: dict[str, Path] = {}
    fills_path = run_dir / "exports" / "fills.csv"
    intents_path = run_dir / "exports" / "intents.csv"
    orders_path = run_dir / "exports" / "orders.csv"
    equity_path = run_dir / "exports" / "equity_curve.csv"
    summary_path = run_dir / "exports" / "summary.json"

    _write_csv(fills_path, _FILL_HEADERS, fills)
    _write_csv(intents_path, _INTENT_HEADERS, intents)
    _write_csv(orders_path, _ORDER_HEADERS, orders)

    # equity_curve.csv — build_equity_series 사용. 빈 시리즈여도 header 만 작성.
    try:
        config = _load_run_config(run_dir)
        initial_equity = Decimal(str(config.get("initial_equity") or "0"))
    except FileNotFoundError:
        initial_equity = Decimal("0")
    equity = build_equity_series(reader, initial_equity)
    equity_rows: list[dict[str, Any]] = []
    if equity.height > 0:
        for r in equity.to_dicts():
            equity_rows.append(
                {
                    "timestamp": r["timestamp"].isoformat() if r.get("timestamp") else "",
                    "equity": r.get("equity"),
                    "cash": r.get("cash"),
                    "realized_pnl": r.get("realized_pnl"),
                    "unrealized_pnl": r.get("unrealized_pnl"),
                    "drawdown": r.get("drawdown"),
                    "drawdown_pct": r.get("drawdown_pct"),
                }
            )
    _write_csv(equity_path, _EQUITY_HEADERS, equity_rows)

    summary = _build_summary(run_dir, reader, fills=fills, intents=intents)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    out["fills"] = fills_path
    out["intents"] = intents_path
    out["orders"] = orders_path
    out["equity_curve"] = equity_path
    out["summary"] = summary_path
    return out
