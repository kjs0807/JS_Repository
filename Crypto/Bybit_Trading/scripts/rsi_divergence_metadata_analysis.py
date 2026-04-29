"""RSI Divergence — visual-mark vs all-detected metadata distribution analysis.

A2 (Round 6 kickoff). Uses the same `detect_at` loop as case study to collect
**every** detected event's metadata, then matches the 13 visual cases (BTCUSDT)
to their best-matching detector event, and finally compares per-metric
distributions to identify hard-filter candidates that retain visual cases
while filtering noise.

Output: logs/research/rsi_divergence/metadata_analysis_round6.md + .csv

Usage:
    python -m scripts.rsi_divergence_metadata_analysis
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ml.types import MTFData, PatternEvent
from src.ml.patterns.rsi_divergence import RSIDivergence
from scripts.rsi_divergence_case_study import (
    DAILY_CASES, H4_CASES, Case, CaseResult,
    load_mtf_for_case_study, _detect_all_events, _classify_case,
    WINDOW_DAILY_BARS, WINDOW_H4_BARS, _ms,
)


METRIC_COLS = [
    "divergence_strength",
    "slope_divergence_ratio",
    "price_diff_abs",
    "rsi_diff_abs",
    "pivot_distance_bars",
    "pivot_prominence",
    "intervening_retracement_ratio",
    "confirmation_lag",
]


def events_to_df(events: List[PatternEvent]) -> pd.DataFrame:
    rows = []
    for ev in events:
        m = ev.metadata
        row = {
            "timestamp_ms": ev.timestamp_ms,
            "bar_index": ev.bar_index,
            "direction": ev.direction,
            "div_type": str(m.get("div_type", "?")),
        }
        for c in METRIC_COLS:
            v = m.get(c)
            try:
                row[c] = float(v) if v is not None else np.nan
            except Exception:
                row[c] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def visual_case_events(symbol: str, primary_tf: str,
                       cases: List[Case]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (visual_df, all_events_df).

    visual_df has one row per case, with the best matched detector event's
    metadata. all_events_df has every detected event in the series.
    """
    print(f"[meta] loading {symbol} primary_tf={primary_tf}")
    mtfs = load_mtf_for_case_study([symbol], primary_tf)
    mtf = mtfs[symbol]
    primary = mtf.get_primary()

    pat = RSIDivergence()
    print(f"[meta]   warmup={pat.warmup_bars} bars={len(primary.bars)}")
    events = _detect_all_events(pat, mtf)
    print(f"[meta]   events={len(events)}")

    all_df = events_to_df(events)

    visual_rows = []
    for case in cases:
        verdict, best, delta = _classify_case(case, events, primary)
        if best is None:
            visual_rows.append({
                "case_id": case.case_id, "tf": case.timeframe,
                "dir": case.direction, "verdict": verdict,
                "delta_bars": None, "div_type": None, "direction_event": None,
                **{c: np.nan for c in METRIC_COLS},
            })
            continue
        m = best.metadata
        row = {
            "case_id": case.case_id, "tf": case.timeframe,
            "dir": case.direction, "verdict": verdict,
            "delta_bars": delta,
            "div_type": str(m.get("div_type", "?")),
            "direction_event": best.direction,
        }
        for c in METRIC_COLS:
            v = m.get(c)
            try:
                row[c] = float(v) if v is not None else np.nan
            except Exception:
                row[c] = np.nan
        visual_rows.append(row)
    return pd.DataFrame(visual_rows), all_df


def quantile_summary(df: pd.DataFrame, label: str) -> pd.DataFrame:
    out = []
    for c in METRIC_COLS:
        s = df[c].dropna()
        if s.empty:
            row = {"metric": c, "label": label, "n": 0}
            for q in ("min", "p25", "p50", "p75", "max", "mean"):
                row[q] = np.nan
            out.append(row)
            continue
        out.append({
            "metric": c, "label": label, "n": len(s),
            "min": float(s.min()),
            "p25": float(s.quantile(0.25)),
            "p50": float(s.quantile(0.50)),
            "p75": float(s.quantile(0.75)),
            "max": float(s.max()),
            "mean": float(s.mean()),
        })
    return pd.DataFrame(out)


def lift_table(visual: pd.DataFrame, all_events: pd.DataFrame) -> pd.DataFrame:
    """For each metric, compute how 'high' visual cases sit in the all-events
    distribution. Output:
      - visual_p25: 25th percentile of visual values
      - all_pct_at_visual_p25: what percentile of all-events that visual_p25
        corresponds to (higher = visual cases are in the upper tail)
      - filter_keep_visual: if we filter "all_events ≥ visual_p25", how many
        visual cases survive (out of n_visual_with_match)
      - filter_keep_all: how many of all_events survive
      - selectivity: filter_keep_all / total_all (lower = more selective)
    """
    rows = []
    for c in METRIC_COLS:
        v = visual[c].dropna()
        a = all_events[c].dropna()
        if v.empty or a.empty:
            rows.append({
                "metric": c,
                "n_visual": len(v), "n_all": len(a),
                "visual_p25": np.nan, "all_pct_at_visual_p25": np.nan,
                "filter_keep_visual": np.nan,
                "filter_keep_all": np.nan,
                "selectivity": np.nan,
            })
            continue
        vp25 = float(v.quantile(0.25))
        # what percentile in all-events does vp25 correspond to?
        pct = float((a < vp25).sum()) / len(a) * 100.0
        keep_visual = int((v >= vp25).sum())
        keep_all = int((a >= vp25).sum())
        rows.append({
            "metric": c, "n_visual": len(v), "n_all": len(a),
            "visual_p25": vp25,
            "all_pct_at_visual_p25": pct,
            "filter_keep_visual": keep_visual,
            "filter_keep_all": keep_all,
            "selectivity": keep_all / len(a),
        })
    return pd.DataFrame(rows)


def write_report(out_path: Path, sections: List[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n\n".join(sections), encoding="utf-8")


def fmt_df(df: pd.DataFrame, floatfmt: str = "{:.4f}") -> str:
    return df.to_markdown(index=False, floatfmt=lambda x: floatfmt.format(x)
                           if isinstance(x, (float, int)) and not isinstance(x, bool)
                           else str(x))


def fmt_quantile(df: pd.DataFrame) -> str:
    out = ["| metric | label | n | min | p25 | p50 | p75 | max | mean |",
           "|---|---|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        out.append(
            f"| {r['metric']} | {r['label']} | {int(r['n'])} | "
            f"{r['min']:.4f} | {r['p25']:.4f} | {r['p50']:.4f} | "
            f"{r['p75']:.4f} | {r['max']:.4f} | {r['mean']:.4f} |"
        )
    return "\n".join(out)


def fmt_lift(df: pd.DataFrame) -> str:
    out = ["| metric | n_visual | n_all | visual_p25 | all_pct_at_visual_p25 | "
           "keep_visual | keep_all | selectivity (lower=better) |",
           "|---|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        out.append(
            f"| {r['metric']} | {int(r['n_visual'])} | {int(r['n_all'])} | "
            f"{r['visual_p25']:.4f} | {r['all_pct_at_visual_p25']:.1f}% | "
            f"{int(r['filter_keep_visual'])}/{int(r['n_visual'])} | "
            f"{int(r['filter_keep_all'])}/{int(r['n_all'])} | "
            f"{r['selectivity']*100:.1f}% |"
        )
    return "\n".join(out)


def main() -> int:
    sym = "BTCUSDT"

    print("=== 1d ===")
    visual_1d, all_1d = visual_case_events(sym, "1d", DAILY_CASES)
    print("=== 4h ===")
    visual_4h, all_4h = visual_case_events(sym, "4h", H4_CASES)

    visual_combined = pd.concat([visual_1d, visual_4h], ignore_index=True)

    # Per-TF quantiles
    q_1d_visual = quantile_summary(visual_1d.dropna(subset=METRIC_COLS),
                                   "visual_1d")
    q_1d_all = quantile_summary(all_1d, "all_1d")
    q_4h_visual = quantile_summary(visual_4h.dropna(subset=METRIC_COLS),
                                   "visual_4h")
    q_4h_all = quantile_summary(all_4h, "all_4h")
    q_combined_visual = quantile_summary(visual_combined.dropna(subset=METRIC_COLS),
                                         "visual_combined")

    lift_1d = lift_table(visual_1d.dropna(subset=METRIC_COLS), all_1d)
    lift_4h = lift_table(visual_4h.dropna(subset=METRIC_COLS), all_4h)

    # Visual events table
    visual_table_md = visual_combined.to_markdown(index=False, floatfmt=".4f")

    # Save raw CSV too
    out_dir = PROJECT_ROOT / "logs" / "research" / "rsi_divergence"
    out_dir.mkdir(parents=True, exist_ok=True)
    visual_combined.to_csv(out_dir / "metadata_analysis_visual_events.csv",
                           index=False)
    all_1d.to_csv(out_dir / "metadata_analysis_all_events_1d.csv", index=False)
    all_4h.to_csv(out_dir / "metadata_analysis_all_events_4h.csv", index=False)

    # Sections
    sections = []
    sections.append(
        "# RSI Divergence Metadata Analysis — Round 6 A2\n\n"
        "**Goal**: Identify hard-filter metric thresholds that retain the 13 "
        "visual-mark cases while reducing noise from the bulk of detected events. "
        "If clean separation exists, this becomes the rule-based wrapper "
        "candidate.\n\n"
        "**Method**: For each TF (1d, 4h on BTCUSDT), run `detect_at` over the "
        "full series and collect every event's metadata. Match the 13 visual "
        "cases to their best-fit event (same as case study) and compare metric "
        "distributions: visual vs all.\n\n"
        f"**Visual cases**: 1d={len(DAILY_CASES)} + 4h={len(H4_CASES)} = "
        f"{len(DAILY_CASES) + len(H4_CASES)} total.\n"
        f"**Detected events**: 1d={len(all_1d)}, 4h={len(all_4h)}."
    )

    sections.append(
        "## 1) Visual case events (best match per case)\n\n"
        f"{visual_table_md}"
    )

    sections.append(
        "## 2) Quantile summary (1d) — visual vs all\n\n"
        f"{fmt_quantile(pd.concat([q_1d_visual, q_1d_all], ignore_index=True))}"
    )

    sections.append(
        "## 3) Quantile summary (4h) — visual vs all\n\n"
        f"{fmt_quantile(pd.concat([q_4h_visual, q_4h_all], ignore_index=True))}"
    )

    sections.append(
        "## 4) Quantile summary (visual combined 1d + 4h)\n\n"
        f"{fmt_quantile(q_combined_visual)}"
    )

    sections.append(
        "## 5) Lift table — 1d (filter `metric >= visual_p25`)\n\n"
        "Higher `all_pct_at_visual_p25` = visual cases are in the upper tail of "
        "all-events distribution = stronger separation candidate.\n\n"
        f"{fmt_lift(lift_1d)}"
    )

    sections.append(
        "## 6) Lift table — 4h (filter `metric >= visual_p25`)\n\n"
        f"{fmt_lift(lift_4h)}"
    )

    # Auto-recommendation
    rec = []
    rec.append("## 7) Auto recommendation — top filter candidates\n")
    rec.append(
        "Criteria: (a) visual cases sit in upper 70% of all-events for that "
        "metric (`all_pct_at_visual_p25 >= 70%`), and (b) selectivity ≤ 30% "
        "(filter keeps ≤ 30% of all events).\n"
    )
    rec.append("**1d candidates:**")
    cands_1d = lift_1d[
        (lift_1d["all_pct_at_visual_p25"] >= 70.0)
        & (lift_1d["selectivity"] <= 0.30)
    ].sort_values("all_pct_at_visual_p25", ascending=False)
    if cands_1d.empty:
        rec.append("- (none meet both criteria — try relaxed threshold or combination)")
    else:
        for _, r in cands_1d.iterrows():
            rec.append(
                f"- `{r['metric']} >= {r['visual_p25']:.4f}` → "
                f"keeps {int(r['filter_keep_visual'])}/{int(r['n_visual'])} visual, "
                f"{int(r['filter_keep_all'])}/{int(r['n_all'])} all "
                f"(selectivity {r['selectivity']*100:.1f}%, "
                f"visual at {r['all_pct_at_visual_p25']:.1f}-th pct)"
            )
    rec.append("")
    rec.append("**4h candidates:**")
    cands_4h = lift_4h[
        (lift_4h["all_pct_at_visual_p25"] >= 70.0)
        & (lift_4h["selectivity"] <= 0.30)
    ].sort_values("all_pct_at_visual_p25", ascending=False)
    if cands_4h.empty:
        rec.append("- (none meet both criteria — try relaxed threshold or combination)")
    else:
        for _, r in cands_4h.iterrows():
            rec.append(
                f"- `{r['metric']} >= {r['visual_p25']:.4f}` → "
                f"keeps {int(r['filter_keep_visual'])}/{int(r['n_visual'])} visual, "
                f"{int(r['filter_keep_all'])}/{int(r['n_all'])} all "
                f"(selectivity {r['selectivity']*100:.1f}%, "
                f"visual at {r['all_pct_at_visual_p25']:.1f}-th pct)"
            )
    rec.append("")
    rec.append(
        "## 8) Next step\n"
        "If at least one metric in 1d shows ≥80% lift with ≤20% selectivity, "
        "draft a rule-based wrapper spec: `event must satisfy filter_X AND "
        "filter_Y AND div_type ∈ {regular_bull, regular_bear}` (regular only, "
        "drop hidden until separately validated). Then backtest the rule on "
        "1d full history (2021-2026) using simple ATR triple-barrier exits."
    )

    sections.append("\n\n".join(rec))

    out_path = out_dir / "metadata_analysis_round6.md"
    write_report(out_path, sections)
    print(f"[meta] report: {out_path}")
    print(f"[meta] CSVs: visual_events.csv, all_events_1d.csv, all_events_4h.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
