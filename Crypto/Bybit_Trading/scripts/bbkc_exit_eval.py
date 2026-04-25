"""BBKC Exit Round 2 evaluation runner.

Sweeps 12 exit cells × BIGTHREE × 9 walk-forward windows.
Reuses the existing HoldoutSpec/run_strategy_on_holdout pipeline; each
WF window is one HoldoutSpec invocation per (cell, symbol).

Output dir: logs/research/bbkc_squeeze/exit_round/
  - wf_results.jsonl   per-window per-(cell, symbol) metrics
  - auxiliary.json     per-(cell, symbol) auxiliary metrics (avg over windows)
  - summary.json       per-(cell, symbol) WF stability + verdict
  - report.md          human-readable report

Usage:
    python -m scripts.bbkc_exit_eval --smoke         # 1 cell × 1 symbol × 1 window
    python -m scripts.bbkc_exit_eval --full          # all 324 runs
    python -m scripts.bbkc_exit_eval --cell F0 --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import load_config
from src.data_manager.db import DBManager
from src.evaluation.holdout import HoldoutSpec, run_strategy_on_holdout
from src.strategies.bbkc_squeeze import BBKCSqueeze
from src.strategies.registry_builder import STRATEGY_CONFIGS


SYMBOLS = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
DATA_START = "2024-03-01"
DATA_END = "2026-04-30"
OUTPUT_DIR = PROJECT_ROOT / "logs" / "research" / "bbkc_squeeze" / "exit_round"

logger = logging.getLogger("bbkc_exit_eval")


@dataclass
class WindowResult:
    cell_id: str
    symbol: str
    window_idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    oos_pnl: float
    oos_trades: int
    oos_winrate: float
    oos_max_dd: float
    oos_r_per_trade: float


def make_strategy_factory(cell: Dict[str, Any]):
    """Return a zero-arg factory that builds BBKCSqueeze with cell params."""
    kwargs: Dict[str, Any] = dict(
        bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
        atr_period=14, rsi_period=14, rsi_filter=70.0,
        tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h",
        exit_mode=cell["exit_mode"],
        trail_be_r=1.0,
        trail_start_r=2.0,
        time_stop_bars=cell["time_stop_bars"],
    )
    if cell["trail_distance_r"] is not None:
        kwargs["trail_distance_r"] = cell["trail_distance_r"]
    return lambda: BBKCSqueeze(**kwargs)


def _add_months(dt: datetime, months: int) -> datetime:
    """Approximate month addition (30 days/month) — fine for window definitions."""
    return dt + timedelta(days=months * 30)


def build_wf_windows(
    data_start: str, data_end: str,
    is_months: int = 6, oos_months: int = 2, step_months: int = 2,
    n_windows: int = 9,
) -> List[Tuple[datetime, datetime, datetime, datetime]]:
    """Return list of (is_start, is_end, oos_start, oos_end) datetimes.

    First IS window starts at data_start. Each subsequent window steps forward
    by step_months. OOS immediately follows IS.
    """
    fmt = "%Y-%m-%d"
    start = datetime.strptime(data_start, fmt)
    end = datetime.strptime(data_end, fmt)
    out: List[Tuple[datetime, datetime, datetime, datetime]] = []
    for k in range(n_windows):
        is_s = _add_months(start, step_months * k)
        is_e = _add_months(is_s, is_months)
        oos_s = is_e
        oos_e = _add_months(oos_s, oos_months)
        if oos_e > end:
            logger.warning(
                "window %d oos_end %s exceeds data_end %s, clipping",
                k, oos_e.strftime(fmt), end.strftime(fmt),
            )
            oos_e = end
        out.append((is_s, is_e, oos_s, oos_e))
    return out


def run_one_window(
    cell: Dict[str, Any], symbol: str,
    oos_start: datetime, oos_end: datetime,
    db, warmup_days: int = 30,
) -> Tuple[List[Any], Dict[str, Any]]:
    """Run a single (cell, symbol, OOS-window) and return (trades, metrics).

    Uses run_strategy_on_holdout under the hood. The IS portion of the WF
    pair is implicit in the warmup region (HoldoutSpec starts feed
    warmup_days before holdout_start_dt and the strategy gets full history
    via prepare()).
    """
    spec = HoldoutSpec(
        symbols=[symbol], timeframe="1h",
        holdout_start_dt=oos_start,
        holdout_end_dt=oos_end,
        warmup_days=warmup_days,
    )
    factory = make_strategy_factory(cell)
    run = run_strategy_on_holdout(factory, spec, db)
    return run["trades"], run["per_symbol"][symbol]


def build_report(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
    auxiliary: Dict[str, Dict[str, Dict[str, Any]]],
    out_path: Path,
) -> None:
    """Generate human-readable Markdown report."""
    lines: List[str] = [
        "# BBKC Exit Round 2 — Sweep Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Per-Symbol Verdicts",
        "",
    ]

    # Sort cell IDs in our canonical grid order
    cell_order = [c["cell_id"] for c in STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]]
    seen_cells = set()
    for c in cell_order:
        if c in summary_judged:
            seen_cells.add(c)
    extra_cells = sorted(set(summary_judged.keys()) - seen_cells)
    final_cells = [c for c in cell_order if c in summary_judged] + extra_cells

    for sym in SYMBOLS:
        lines.append(f"### {sym}")
        lines.append("")
        lines.append("| Cell | WF OOS+/9 | R/trade | Max DD | Trades | Mean PnL | Verdict |")
        lines.append("|---|---|---|---|---|---|---|")
        for cell_id in final_cells:
            m = summary_judged.get(cell_id, {}).get(sym)
            if not m:
                continue
            verdict = m["verdict"]
            if m.get("warning"):
                verdict += " (WARN: low trade count)"
            lines.append(
                f"| {cell_id} | {m['wf_oos_positive']}/{m['wf_total']} | "
                f"{m['mean_r_per_trade']:+.3f} | {m['max_dd']*100:.2f}% | "
                f"{m['trade_count']} | {m['mean_oos_pnl']:+.2f} | {verdict} |"
            )
        lines.append("")

    lines.append("## Auxiliary Metrics (per cell × symbol, averaged across windows)")
    lines.append("")
    for cell_id in final_cells:
        if cell_id not in auxiliary:
            continue
        lines.append(f"### {cell_id}")
        lines.append("")
        for sym, aux in auxiliary[cell_id].items():
            lines.append(f"**{sym}**")
            lines.append("")
            er = aux.get("exit_reason_dist", {})
            er_str = ", ".join(f"{k}: {v*100:.1f}%" for k, v in sorted(er.items()))
            lines.append(f"- Exit reasons: {er_str or '(none)'}")
            lines.append(f"- Mean R/win: {aux['mean_r_win']:+.3f}, R/loss: {aux['mean_r_loss']:+.3f}")
            lines.append(f"- MFE retention: {aux['mfe_retention']:+.3f}")
            lines.append(f"- Mean holding bars: {aux['mean_holding_bars']:.1f}")
            lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_summary(jsonl_path: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Aggregate per-window WindowResult into per-(cell, symbol) summary."""
    rows: List[Dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        by_pair.setdefault((r["cell_id"], r["symbol"]), []).append(r)

    summary: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for (cell_id, sym), windows in by_pair.items():
        oos_pos = sum(1 for w in windows if w["oos_pnl"] > 0)
        mean_r = sum(w["oos_r_per_trade"] for w in windows) / len(windows)
        max_dd = max(w["oos_max_dd"] for w in windows)
        n_trades = sum(w["oos_trades"] for w in windows)
        mean_pnl = sum(w["oos_pnl"] for w in windows) / len(windows)
        summary.setdefault(cell_id, {})[sym] = {
            "wf_oos_positive": oos_pos,
            "wf_total": len(windows),
            "mean_r_per_trade": mean_r,
            "max_dd": max_dd,
            "trade_count": n_trades,
            "mean_oos_pnl": mean_pnl,
        }
    return summary


def judge(summary: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Apply PROMOTE / STRONG_PROMOTE / KILL / WARNING per (cell, symbol).

    baseline = F0 of the same symbol. Cells without an F0 baseline (e.g. when
    --cell skips F0) get verdict='UNKNOWN'.
    """
    f0 = summary.get("F0", {})
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for cell_id, by_sym in summary.items():
        for sym, m in by_sym.items():
            base = f0.get(sym)
            verdict = "UNKNOWN"
            warning = False
            if base is not None:
                if cell_id == "F0":
                    verdict = "BASELINE"
                else:
                    if m["trade_count"] < base["trade_count"] * 0.5:
                        warning = True
                    if m["wf_oos_positive"] >= 7 and m["mean_r_per_trade"] >= base["mean_r_per_trade"]:
                        verdict = "PROMOTE"
                        if m["max_dd"] <= base["max_dd"]:
                            verdict = "STRONG_PROMOTE"
                    else:
                        verdict = "KILL"
            entry = dict(m)
            entry["verdict"] = verdict
            entry["warning"] = warning
            out.setdefault(cell_id, {})[sym] = entry
    return out


def compute_auxiliary(
    trades: List[Any], sl_pct: float = 0.07, leverage: int = 3,
) -> Dict[str, Any]:
    """Per-window auxiliary metrics. Used for interpretation, NOT for PROMOTE/KILL.

    Reads max_favorable from TradeRecord (added in Phase A).
    """
    if not trades:
        return {
            "exit_reason_dist": {},
            "mean_r_win": 0.0,
            "mean_r_loss": 0.0,
            "mfe_retention": 0.0,
            "mean_holding_bars": 0.0,
        }

    # Exit reason distribution
    counts: Dict[str, int] = {}
    for t in trades:
        counts[t.exit_reason] = counts.get(t.exit_reason, 0) + 1
    total = len(trades)
    dist = {k: v / total for k, v in counts.items()}

    win_rs: List[float] = []
    loss_rs: List[float] = []
    retentions: List[float] = []
    holdings: List[float] = []
    for t in trades:
        risk = t.entry_price * sl_pct / leverage * t.qty
        if risk <= 0:
            continue
        r = t.pnl / risk
        if t.pnl > 0:
            win_rs.append(r)
        else:
            loss_rs.append(r)
        # MFE retention: realized_R / max_favorable_R; max_favorable is in price terms
        max_fav_pnl = t.max_favorable * t.qty   # absolute distance × qty = max favorable PnL
        if max_fav_pnl > 0:
            retentions.append(t.pnl / max_fav_pnl)
        # Holding bars (1h timeframe)
        holdings.append((t.exit_time - t.entry_time) / (60 * 60 * 1000))

    return {
        "exit_reason_dist": dist,
        "mean_r_win": sum(win_rs) / len(win_rs) if win_rs else 0.0,
        "mean_r_loss": sum(loss_rs) / len(loss_rs) if loss_rs else 0.0,
        "mfe_retention": sum(retentions) / len(retentions) if retentions else 0.0,
        "mean_holding_bars": sum(holdings) / len(holdings) if holdings else 0.0,
    }


def _avg_dist(dists: List[Dict[str, float]]) -> Dict[str, float]:
    """Average distribution dicts (e.g. exit_reason_dist) across windows."""
    keys: set = set()
    for d in dists:
        keys.update(d.keys())
    if not dists:
        return {}
    return {k: sum(d.get(k, 0.0) for d in dists) / len(dists) for k in keys}


def compute_window_metrics(
    trades: List[Any], metrics_block: Dict[str, Any], cell: Dict[str, Any], symbol: str,
    w_idx: int, is_s: datetime, is_e: datetime, oos_s: datetime, oos_e: datetime,
    sl_pct: float = 0.07, leverage: int = 3,
) -> WindowResult:
    """Convert holdout-block metrics to a WindowResult. R/trade computed here."""
    fmt = "%Y-%m-%d"
    n = metrics_block.get("n_trades", 0)
    pnl = metrics_block.get("total_pnl", 0.0)
    wr = metrics_block.get("win_rate", 0.0)
    max_dd = metrics_block.get("max_drawdown", 0.0)

    # R/trade: pnl / (qty * entry × sl_pct/leverage). Average across trades.
    rs: List[float] = []
    for t in trades:
        risk = t.entry_price * sl_pct / leverage * t.qty
        if risk > 0:
            rs.append(t.pnl / risk)
    r_per_trade = sum(rs) / len(rs) if rs else 0.0

    return WindowResult(
        cell_id=cell["cell_id"], symbol=symbol, window_idx=w_idx,
        is_start=is_s.strftime(fmt), is_end=is_e.strftime(fmt),
        oos_start=oos_s.strftime(fmt), oos_end=oos_e.strftime(fmt),
        oos_pnl=pnl, oos_trades=n, oos_winrate=wr,
        oos_max_dd=max_dd, oos_r_per_trade=r_per_trade,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BBKC exit-mode WF sweep")
    p.add_argument("--smoke", action="store_true",
                   help="1 cell × 1 symbol × 1 window")
    p.add_argument("--full", action="store_true",
                   help="all 12 cells × 3 symbols × 9 windows = 324 runs")
    p.add_argument("--cell", default=None, help="run only this cell_id (e.g. F0)")
    p.add_argument("--symbol", default=None, help="run only this symbol")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cells = [c for c in grid if args.cell is None or c["cell_id"] == args.cell]
    symbols = SYMBOLS if args.symbol is None else [args.symbol]
    windows = build_wf_windows(DATA_START, DATA_END)
    if args.smoke:
        cells = cells[:1]
        symbols = symbols[:1]
        windows = windows[:1]

    logger.info("running %d cells × %d symbols × %d windows = %d runs",
                len(cells), len(symbols), len(windows),
                len(cells) * len(symbols) * len(windows))

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    out_jsonl = OUTPUT_DIR / "wf_results.jsonl"
    aux_buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    n_done = 0
    n_total = len(cells) * len(symbols) * len(windows)
    with out_jsonl.open("w", encoding="utf-8") as fout:
        for sym in symbols:
            for cell in cells:
                for w_idx, (is_s, is_e, oos_s, oos_e) in enumerate(windows):
                    n_done += 1
                    logger.info(
                        "[%d/%d] cell=%s sym=%s window=%d oos=%s..%s",
                        n_done, n_total, cell["cell_id"], sym, w_idx,
                        oos_s.strftime("%Y-%m-%d"), oos_e.strftime("%Y-%m-%d"),
                    )
                    trades, metrics_block = run_one_window(cell, sym, oos_s, oos_e, db)
                    result = compute_window_metrics(
                        trades, metrics_block, cell, sym, w_idx, is_s, is_e, oos_s, oos_e,
                    )
                    fout.write(json.dumps(asdict(result)) + "\n")
                    fout.flush()
                    aux = compute_auxiliary(trades)
                    aux_buckets.setdefault((cell["cell_id"], sym), []).append(aux)
    logger.info("wrote %s", out_jsonl)

    # Aggregate auxiliary across windows for each (cell, symbol)
    auxiliary: Dict[str, Dict[str, Any]] = {}
    for (cell_id, sym), lst in aux_buckets.items():
        avg = {
            "exit_reason_dist": _avg_dist([d["exit_reason_dist"] for d in lst]),
            "mean_r_win": sum(d["mean_r_win"] for d in lst) / len(lst),
            "mean_r_loss": sum(d["mean_r_loss"] for d in lst) / len(lst),
            "mfe_retention": sum(d["mfe_retention"] for d in lst) / len(lst),
            "mean_holding_bars": sum(d["mean_holding_bars"] for d in lst) / len(lst),
        }
        auxiliary.setdefault(cell_id, {})[sym] = avg
    aux_path = OUTPUT_DIR / "auxiliary.json"
    aux_path.write_text(json.dumps(auxiliary, indent=2), encoding="utf-8")
    logger.info("wrote %s", aux_path)

    # Summary + verdict
    summary_raw = build_summary(out_jsonl)
    summary_judged = judge(summary_raw)
    sum_path = OUTPUT_DIR / "summary.json"
    sum_path.write_text(json.dumps(summary_judged, indent=2), encoding="utf-8")
    logger.info("wrote %s", sum_path)

    report_path = OUTPUT_DIR / "report.md"
    build_report(summary_judged, auxiliary, report_path)
    logger.info("wrote %s", report_path)


if __name__ == "__main__":
    main()
