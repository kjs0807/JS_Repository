"""BBKC Exit Round 3 evaluation runner.

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
OUTPUT_BASE = PROJECT_ROOT / "logs" / "research" / "bbkc_squeeze" / "exit_round"
# Per-run output dir is OUTPUT_BASE/<timestamp>/. After success, files are
# also copied to OUTPUT_BASE/latest/ for convenience.
OUTPUT_DIR = OUTPUT_BASE  # placeholder; overwritten in main()

# ── Round 4 §7: reproducibility sanity ─────────────────────────────────────
# `be30_st60_di30 × ETHUSDT` re-runs round 3's TF_early × ETH (same params,
# same code path). Expected exact floats from
# logs/research/bbkc_squeeze/exit_round/2026-04-28_T2104/summary.json.
REPRODUCE_CELL_ID = "be30_st60_di30"
REPRODUCE_SYMBOL = "ETHUSDT"
EXPECTED_REPRODUCE = {
    "wf_oos_positive": 6,
    "mean_r_per_trade": 0.0635821965450038,
    "trade_count": 154,
    "max_dd": 0.11123736375303807,
    "mean_oos_pnl": 325.6180389395652,
}
REPRODUCE_TOLERANCE = {
    "wf_oos_positive_exact": 6,
    "mean_r_per_trade_abs": 0.005,
    "trade_count_abs": 2,
}

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
    """Return a zero-arg factory that builds BBKCSqueeze with cell params (round 3 schema).

    fixed cells leave trail/be defaults untouched (no be_trail invariant check).
    be_trail cells supply the three TP-fraction params explicitly.
    """
    kwargs: Dict[str, Any] = dict(
        bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
        atr_period=14, rsi_period=14, rsi_filter=70.0,
        tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h",
        exit_mode=cell["exit_mode"],
        drop_tp=cell.get("drop_tp", False),
        time_stop_bars=cell["time_stop_bars"],
    )
    if cell["exit_mode"] == "be_trail":
        kwargs["trail_be_at_tp_frac"] = cell["trail_be_at_tp_frac"]
        kwargs["trail_start_at_tp_frac"] = cell["trail_start_at_tp_frac"]
        kwargs["trail_distance_tp_frac"] = cell["trail_distance_tp_frac"]
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


def format_reproducibility_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[str]:
    """Round 4 §7: Reproducibility sanity vs round 3 TF_early × ETH.

    Compares summary_judged[REPRODUCE_CELL_ID][REPRODUCE_SYMBOL] against
    EXPECTED_REPRODUCE within REPRODUCE_TOLERANCE. Always emits a block
    (even if cell missing — uses fallback message).
    """
    lines: List[str] = [
        f"## Reproducibility Sanity ({REPRODUCE_CELL_ID} × {REPRODUCE_SYMBOL} vs Round 3 TF_early)",
        "",
    ]
    cell = summary_judged.get(REPRODUCE_CELL_ID, {})
    actual = cell.get(REPRODUCE_SYMBOL)
    if not actual:
        lines.append(
            f"❗ MATCH SKIPPED — `{REPRODUCE_CELL_ID} × {REPRODUCE_SYMBOL}` "
            f"not in summary (partial run?)."
        )
        lines.append("")
        return lines

    diffs: List[str] = []
    if actual["wf_oos_positive"] != REPRODUCE_TOLERANCE["wf_oos_positive_exact"]:
        diffs.append(
            f"wf {actual['wf_oos_positive']} != {REPRODUCE_TOLERANCE['wf_oos_positive_exact']}"
        )
    if abs(actual["mean_r_per_trade"] - EXPECTED_REPRODUCE["mean_r_per_trade"]) > REPRODUCE_TOLERANCE["mean_r_per_trade_abs"]:
        diffs.append(
            f"R {actual['mean_r_per_trade']:+.6f} vs "
            f"{EXPECTED_REPRODUCE['mean_r_per_trade']:+.6f} "
            f"(tol ±{REPRODUCE_TOLERANCE['mean_r_per_trade_abs']})"
        )
    if abs(actual["trade_count"] - EXPECTED_REPRODUCE["trade_count"]) > REPRODUCE_TOLERANCE["trade_count_abs"]:
        diffs.append(
            f"n {actual['trade_count']} vs {EXPECTED_REPRODUCE['trade_count']} "
            f"(tol ±{REPRODUCE_TOLERANCE['trade_count_abs']})"
        )

    expected_str = (
        f"  Round 3 TF_early ETH: wf {EXPECTED_REPRODUCE['wf_oos_positive']}/9, "
        f"R/trade {EXPECTED_REPRODUCE['mean_r_per_trade']:+.4f}, "
        f"n={EXPECTED_REPRODUCE['trade_count']}"
    )
    actual_str = (
        f"  Round 4 reproduce:    wf {actual['wf_oos_positive']}/9, "
        f"R/trade {actual['mean_r_per_trade']:+.4f}, "
        f"n={actual['trade_count']}"
    )
    lines.append(expected_str)
    lines.append(actual_str)
    if not diffs:
        lines.append("  Match: ✓")
    else:
        lines.append("  Match: ✗")
        lines.append("  Diffs: " + "; ".join(diffs))
        lines.append("  ⚠️  REPRODUCIBILITY MISMATCH — investigate before trusting other cells.")
    lines.append("")
    return lines


def format_integrated_labels_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
    grid: List[Dict[str, Any]],
) -> List[str]:
    """Round 4 §8.2: Markdown table of (cell, label, ETH/BTC/AVAX verdict)."""
    lines: List[str] = [
        "## Per-Cell Integrated Labels",
        "",
        "| cell | label | ETH | BTC | AVAX |",
        "|---|---|---|---|---|",
    ]
    for c in grid:
        cell_id = c["cell_id"]
        cell_entry = summary_judged.get(cell_id, {})
        label = cell_entry.get("_cell", {}).get("integrated_label", "?")
        eth_v = cell_entry.get("ETHUSDT", {}).get("verdict", "-")
        btc_v = cell_entry.get("BTCUSDT", {}).get("verdict", "-")
        avx_v = cell_entry.get("AVAXUSDT", {}).get("verdict", "-")
        eth_w = " *" if cell_entry.get("ETHUSDT", {}).get("warning") else ""
        btc_w = " *" if cell_entry.get("BTCUSDT", {}).get("warning") else ""
        avx_w = " *" if cell_entry.get("AVAXUSDT", {}).get("warning") else ""
        lines.append(
            f"| {cell_id} | {label} | {eth_v}{eth_w} | {btc_v}{btc_w} | {avx_v}{avx_w} |"
        )
    lines.append("")
    return lines


def format_label_distribution_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[str]:
    """Round 4 §8.2: integrated label counter."""
    counts: Dict[str, int] = {}
    for cell_id, cell_entry in summary_judged.items():
        label = cell_entry.get("_cell", {}).get("integrated_label", "?")
        counts[label] = counts.get(label, 0) + 1
    label_order = [
        "ROBUST_PROMOTE", "ETH_ONLY_PROMOTE", "ETH_PROMOTE_MIXED",
        "DAMAGING", "NO_SIGNAL", "BASELINE",
    ]
    lines: List[str] = ["## Label Distribution", ""]
    for label in label_order:
        n = counts.get(label, 0)
        lines.append(f"- {label}: {n}")
    for label, n in counts.items():
        if label not in label_order:
            lines.append(f"- {label}: {n}  (unexpected)")
    lines.append("")
    return lines


_VERDICT_ABBREV = {
    "STRONG_PROMOTE": "SP",
    "PROMOTE": "P",
    "NEUTRAL": "N",
    "KILL": "K",
    "BASELINE": "B",
    "UNKNOWN": "U",
}


def _abbrev(per_sym_entry: Dict[str, Any]) -> str:
    """Abbreviate per-symbol verdict; suffix '*' if warning=True."""
    v = per_sym_entry.get("verdict", "?")
    abbrev = _VERDICT_ABBREV.get(v, v[:2] if v else "?")
    if per_sym_entry.get("warning"):
        abbrev += "*"
    return abbrev


def format_heatmaps_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[str]:
    """Round 4 §8.2: 9 heatmaps (3 symbols × 3 dist), each 3×3 (be × start)."""
    lines: List[str] = [
        "## Per-Symbol × Distance Heatmaps (3×3 grid of be × start, per-symbol verdict per cell)",
        "",
    ]
    be_values = (0.25, 0.30, 0.35)
    st_values = (0.50, 0.60, 0.70)
    di_values = (0.20, 0.30, 0.40)
    symbols = ["ETHUSDT", "BTCUSDT", "AVAXUSDT"]   # ETH first (primary)

    for sym in symbols:
        for di in di_values:
            lines.append(f"### {sym}, dist={di:.2f}")
            lines.append("")
            header = "|        | " + " | ".join(f"st={st:.2f}" for st in st_values) + " |"
            sep = "|" + "|".join(["--------"] * (len(st_values) + 1)) + "|"
            lines.append(header)
            lines.append(sep)
            for be in be_values:
                row_cells = []
                for st in st_values:
                    cell_id = (
                        f"be{int(round(be * 100)):02d}"
                        f"_st{int(round(st * 100)):02d}"
                        f"_di{int(round(di * 100)):02d}"
                    )
                    entry = summary_judged.get(cell_id, {}).get(sym, {})
                    row_cells.append(_abbrev(entry) if entry else "-")
                lines.append(f"| be={be:.2f} | " + " | ".join(row_cells) + " |")
            lines.append("")
    return lines


def build_report(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
    auxiliary: Dict[str, Dict[str, Dict[str, Any]]],
    out_path: Path,
) -> None:
    """Generate human-readable Markdown report."""
    lines: List[str] = [
        "# BBKC Exit Round 3 — Sweep Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    lines.extend(format_reproducibility_block(summary_judged))
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    lines.extend(format_integrated_labels_block(summary_judged, grid))
    lines.extend(format_label_distribution_block(summary_judged))
    lines.extend(format_heatmaps_block(summary_judged))
    lines.extend([
        "## Per-Symbol Verdicts",
        "",
    ])

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


def integrate_label(cell_id: str, by_sym: Dict[str, Dict[str, Any]]) -> str:
    """Per-cell integrated label from per-symbol verdicts (round 4 §6.2).

    Priority order (first match wins):
      1. F0                                                → BASELINE
      2. ETH promote AND ETH warning=True                  → ETH_PROMOTE_MIXED
      3. ETH promote AND any (BTC/AVAX) KILL               → ETH_ONLY_PROMOTE
      4. ETH promote AND any (BTC/AVAX) UNKNOWN/warning    → ETH_PROMOTE_MIXED
      5. ETH promote AND no KILL/UNKNOWN/warning anywhere  → ROBUST_PROMOTE
      6. ETH not promote AND any (BTC/AVAX) KILL           → DAMAGING
      7. otherwise                                          → NO_SIGNAL

    "promote" means verdict in {"STRONG_PROMOTE", "PROMOTE"}.
    """
    if cell_id == "F0":
        return "BASELINE"

    eth = by_sym.get("ETHUSDT", {})
    others = [by_sym.get("BTCUSDT", {}), by_sym.get("AVAXUSDT", {})]

    eth_promote = eth.get("verdict") in ("STRONG_PROMOTE", "PROMOTE")
    eth_warning = eth.get("warning") is True
    has_kill = any(o.get("verdict") == "KILL" for o in others)
    has_unknown_or_warning = any(
        o.get("verdict") == "UNKNOWN" or o.get("warning") is True
        for o in others
    )

    if eth_promote:
        if eth_warning:
            return "ETH_PROMOTE_MIXED"
        if has_kill:
            return "ETH_ONLY_PROMOTE"
        if has_unknown_or_warning:
            return "ETH_PROMOTE_MIXED"
        return "ROBUST_PROMOTE"
    else:
        if has_kill:
            return "DAMAGING"
        return "NO_SIGNAL"


def judge(summary: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Apply baseline-relative delta rules per (cell, symbol). Round 3 §9.

    F0 is BASELINE per symbol. Cells without an F0 baseline (e.g. when --cell
    skipped F0) get verdict='UNKNOWN'.

    Verdict tiers (baseline = F0 same symbol):
      STRONG_PROMOTE — Δwf_oos+ ≥ 2  AND  Δr ≥ 0  AND  DD ≤ baseline
      PROMOTE        — Δwf_oos+ ≥ 1  AND  Δr ≥ 0
      NEUTRAL        — |Δwf_oos+| ≤ 1  AND  |Δr| ≤ 0.05
      KILL           — Δwf_oos+ < -1  OR  Δr < -0.05
    WARNING (덧붙음) — trade_count < baseline × 0.5 (verdict와 별도 플래그)
    """
    f0 = summary.get("F0", {})
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for cell_id, by_sym in summary.items():
        for sym, m in by_sym.items():
            entry = dict(m)
            if cell_id == "F0":
                entry["verdict"] = "BASELINE"
                entry["warning"] = False
                out.setdefault(cell_id, {})[sym] = entry
                continue
            base = f0.get(sym)
            if base is None:
                # F0 없이 부분 실행된 경우. baseline 비교 불가.
                entry["verdict"] = "UNKNOWN"
                entry["warning"] = False
                out.setdefault(cell_id, {})[sym] = entry
                continue

            warning = m["trade_count"] < base["trade_count"] * 0.5
            pos_delta = m["wf_oos_positive"] - base["wf_oos_positive"]
            r_delta = m["mean_r_per_trade"] - base["mean_r_per_trade"]

            if pos_delta >= 2 and r_delta >= 0 and m["max_dd"] <= base["max_dd"]:
                verdict = "STRONG_PROMOTE"
            elif pos_delta >= 1 and r_delta >= 0:
                verdict = "PROMOTE"
            elif abs(pos_delta) <= 1 and abs(r_delta) <= 0.05:
                verdict = "NEUTRAL"
            elif pos_delta < -1 or r_delta < -0.05:
                verdict = "KILL"
            else:
                verdict = "NEUTRAL"   # safety fallback

            entry["verdict"] = verdict
            entry["warning"] = warning
            out.setdefault(cell_id, {})[sym] = entry

    # Round 4 §6.2: attach per-cell integrated label.
    # Stored under synthetic "_cell" key inside each cell's dict so existing
    # per-symbol entries (BTCUSDT/ETHUSDT/AVAXUSDT) remain untouched.
    for cell_id, by_sym in out.items():
        cell_label = integrate_label(cell_id, by_sym)
        by_sym["_cell"] = {"integrated_label": cell_label}
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


def _publish_to_latest(run_dir: Path, base: Path) -> None:
    """Copy run_dir/* into base/latest/ for the canonical 'most recent' view."""
    import shutil
    latest = base / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    latest.mkdir(parents=True)
    for f in run_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, latest / f.name)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    args = parse_args()

    # Per-run timestamped output dir + latest/ pointer.
    timestamp = datetime.now().strftime("%Y-%m-%d_T%H%M")
    if args.smoke:
        timestamp = f"{timestamp}_smoke"
    global OUTPUT_DIR
    OUTPUT_DIR = OUTPUT_BASE / timestamp
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("output dir: %s", OUTPUT_DIR)

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

    # Publish to latest/ for easy access (full overwrite each run)
    _publish_to_latest(OUTPUT_DIR, OUTPUT_BASE)
    logger.info("published to %s", OUTPUT_BASE / "latest")


if __name__ == "__main__":
    main()
