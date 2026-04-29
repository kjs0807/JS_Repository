"""RSI Divergence — visual-mark vs detector case study (Round 6 kickoff).

Background
----------
사용자가 ``docs/Screenshot/`` 에 1d 7건 + 4h 6건의 visual divergence
사례를 표시했다. 모두 BTCUSDT. 본 스크립트는 ``RSIDivergence`` detector를
실제 BarSeries에 돌려서 각 사례 시점에 detector가 이벤트를 발생시켰는지를
표로 정리한다.

ML 학습/라벨링/holding 평가는 다루지 않는다 — 오직 **detector의 시각
대응력** 만 측정하기 위해 ``detect_at(mtf, i)`` 를 직접 루프 호출한다.

Usage
-----

    python -m scripts.rsi_divergence_case_study \
        --symbols BTCUSDT \
        --report logs/research/rsi_divergence/case_study_round6_kickoff.md
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.types import BarSeries
from src.ml.types import MTFData, PatternEvent
from src.ml.patterns.rsi_divergence import RSIDivergence


# ---------------------------------------------------------------------------
# Visual-mark cases (BTCUSDT, taken from docs/Screenshot/)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    case_id: str
    timeframe: str  # "1d" or "4h"
    direction: str  # "bull" / "bear"
    anchor_start: str  # YYYY-MM-DD (1d range start, or 4h single date)
    anchor_end: Optional[str] = None  # YYYY-MM-DD (1d range end), None for 4h
    note: str = ""


DAILY_CASES: List[Case] = [
    Case("D1", "1d", "bear", "2021-01-06", "2021-04-13",
         "BTC ATH→ correction"),
    Case("D2", "1d", "bull", "2021-05-18", "2021-06-24", "May crash bottom"),
    Case("D3", "1d", "bear", "2021-10-19", "2021-11-08", "BTC peak"),
    Case("D4", "1d", "bull", "2022-06-18", "2022-11-21",
         "long base, FTX bottom"),
    Case("D5", "1d", "bull", "2023-08-17", "2023-09-11", "summer pullback"),
    Case("D6", "1d", "bull", "2025-02-25", "2025-04-06", "Q1 selloff bottom"),
    Case("D7", "1d", "bear", "2025-05-22", "2025-10-06",
         "Q2-Q3 distribution top"),
]

H4_CASES: List[Case] = [
    Case("H1", "4h", "bear", "2025-01-17", note="post-100K push"),
    Case("H2", "4h", "bull", "2025-02-25", note="V-shape bottom"),
    Case("H3", "4h", "bull", "2025-04-07", note="parallel-line bullish"),
    Case("H4", "4h", "bear", "2025-07-14", note="flat-top distribution"),
    Case("H5", "4h", "bear", "2025-10-02", note="rounding top"),
    Case("H6", "4h", "bear", "2026-01-05", note="early-2026 top"),
]


# Window (in primary-TF bars) around the anchor where we look for detector events.
# 1d: ±30 bars (~1 month) — visual mark line spans from first pivot to confirmation.
# 4h: ±20 bars (~3.3 days) — single-anchor mark from chart cursor.
WINDOW_DAILY_BARS = 30
WINDOW_H4_BARS = 20


# ---------------------------------------------------------------------------
# Loader (re-uses scripts.train_ml_pattern.load_mtf_data shape)
# ---------------------------------------------------------------------------

def _ms(dt_str: str) -> int:
    return int(datetime.strptime(dt_str, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def _ts_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _df_from_db(db_df: pd.DataFrame) -> pd.DataFrame:
    if db_df is None or db_df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low",
                                     "close", "volume", "turnover"])
    out = db_df.reset_index(drop=True).copy()
    out = out.rename(columns={"open_time": "timestamp"})
    keep = ["timestamp", "open", "high", "low", "close", "volume"]
    if "turnover" in out.columns:
        keep.append("turnover")
    return out[keep]


def load_mtf_for_case_study(
    symbols: List[str], primary_tf: str,
) -> Dict[str, MTFData]:
    """Load full available history for case study (no IS/OOS split needed)."""
    from src.core.config import load_config
    from src.data_manager.db import DBManager

    cfg = load_config()
    db = DBManager(str(PROJECT_ROOT / cfg.app.db_path))

    timeframes = list(RSIDivergence.timeframes)  # ["1h", "4h", "1d"]
    if primary_tf not in timeframes:
        raise ValueError(f"primary_tf={primary_tf!r} not in {timeframes}")

    out: Dict[str, MTFData] = {}
    for sym in symbols:
        series_map: Dict[str, BarSeries] = {}
        for tf in timeframes:
            raw = db.get_bars(symbol=sym, timeframe=tf)
            normalized = _df_from_db(raw)
            series_map[tf] = BarSeries(symbol=sym, timeframe=tf, bars=normalized)
        out[sym] = MTFData(symbol=sym, primary_tf=primary_tf, series=series_map)
    return out


# ---------------------------------------------------------------------------
# Detector loop
# ---------------------------------------------------------------------------

def _detect_all_events(pat: RSIDivergence, mtf: MTFData) -> List[PatternEvent]:
    primary = mtf.get_primary()
    n = len(primary.bars)
    events: List[PatternEvent] = []
    for i in range(pat.warmup_bars, n):
        ev = pat.detect_at(mtf, i)
        if ev is not None:
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Case matching
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case: Case
    symbol: str
    detector_match: str  # MATCH / WEAK / EARLY_LATE / MISMATCH / MISS
    event_count_in_window: int
    best_event_ts: Optional[str]
    best_event_direction: Optional[str]
    best_event_strength: Optional[float]
    best_event_div_type: Optional[str]
    delta_bars_from_anchor: Optional[int]
    pass_threshold_030: Optional[bool]
    pass_threshold_045: Optional[bool]


def _classify_case(
    case: Case, events: List[PatternEvent], primary: BarSeries,
) -> Tuple[str, Optional[PatternEvent], Optional[int]]:
    """Return (verdict, best_event, delta_bars).

    Verdict rules:
      MATCH        — event in anchor window, direction matches, strength ≥ 0.30
      WEAK         — same as MATCH but strength < 0.30
      EARLY_LATE   — event in extended window (±2x), direction matches, but
                     |delta_bars| > anchor window
      MISMATCH     — event in window but opposite direction
      MISS         — no event in extended window
    """
    primary_ts = primary.bars["timestamp"].to_numpy()
    n = len(primary_ts)

    # Anchor center: for 1d range we use the END date (= confirmation point of
    # divergence pattern in user's marking — the second pivot's confirm bar).
    # For 4h single-date anchor, we use the date itself.
    if case.timeframe == "1d":
        anchor_ms = _ms(case.anchor_end or case.anchor_start)
        window_bars = WINDOW_DAILY_BARS
    else:
        anchor_ms = _ms(case.anchor_start)
        # For 4h, need to align to the day's nearest 4h bar; close enough
        # since we use ±20 bars window.
        window_bars = WINDOW_H4_BARS

    # Find nearest primary bar to anchor
    anchor_idx_arr = (primary_ts - anchor_ms).__abs__().argsort()
    if len(anchor_idx_arr) == 0:
        return "MISS", None, None
    anchor_idx = int(anchor_idx_arr[0])

    expected_dir = "long" if case.direction == "bull" else "short"

    # Score each event
    candidates_in_window: List[Tuple[PatternEvent, int]] = []
    candidates_extended: List[Tuple[PatternEvent, int]] = []
    for ev in events:
        delta = ev.bar_index - anchor_idx
        if abs(delta) <= window_bars:
            candidates_in_window.append((ev, delta))
        elif abs(delta) <= window_bars * 2:
            candidates_extended.append((ev, delta))

    def _pick_best(cands: List[Tuple[PatternEvent, int]]) -> Optional[Tuple[PatternEvent, int]]:
        # Prefer matching direction first, then nearest delta, then highest strength.
        if not cands:
            return None
        matching = [c for c in cands if c[0].direction == expected_dir]
        pool = matching or cands
        pool.sort(key=lambda x: (
            abs(x[1]), -float(x[0].metadata.get("divergence_strength", 0.0))
        ))
        return pool[0]

    best_in_window = _pick_best(candidates_in_window)
    if best_in_window is not None:
        ev, delta = best_in_window
        if ev.direction != expected_dir:
            return "MISMATCH", ev, delta
        strength = float(ev.metadata.get("divergence_strength", 0.0))
        if strength >= 0.30:
            return "MATCH", ev, delta
        return "WEAK", ev, delta

    best_ext = _pick_best(candidates_extended)
    if best_ext is not None:
        ev, delta = best_ext
        if ev.direction == expected_dir:
            return "EARLY_LATE", ev, delta

    return "MISS", None, None


def run_case_study_for(symbol: str, primary_tf: str,
                       cases: List[Case]) -> List[CaseResult]:
    print(f"[case-study] loading MTF symbol={symbol} primary_tf={primary_tf} ...")
    mtfs = load_mtf_for_case_study([symbol], primary_tf)
    mtf = mtfs[symbol]
    primary = mtf.get_primary()
    n_bars = len(primary.bars)
    print(f"[case-study]   bars={n_bars}")

    pat = RSIDivergence()
    print(f"[case-study]   warmup_bars={pat.warmup_bars}")
    print(f"[case-study]   detecting events on {primary_tf} ...")
    events = _detect_all_events(pat, mtf)
    print(f"[case-study]   total events detected: {len(events)} "
          f"({sum(1 for e in events if e.direction == 'long')} long, "
          f"{sum(1 for e in events if e.direction == 'short')} short)")

    results: List[CaseResult] = []
    for case in cases:
        verdict, best, delta = _classify_case(case, events, primary)
        if best is None:
            results.append(CaseResult(
                case=case, symbol=symbol, detector_match=verdict,
                event_count_in_window=0,
                best_event_ts=None, best_event_direction=None,
                best_event_strength=None, best_event_div_type=None,
                delta_bars_from_anchor=None,
                pass_threshold_030=None, pass_threshold_045=None,
            ))
            continue

        # Count how many events fall in the strict window
        primary_ts = primary.bars["timestamp"].to_numpy()
        if case.timeframe == "1d":
            anchor_ms = _ms(case.anchor_end or case.anchor_start)
            wb = WINDOW_DAILY_BARS
        else:
            anchor_ms = _ms(case.anchor_start)
            wb = WINDOW_H4_BARS
        anchor_idx = int((primary_ts - anchor_ms).__abs__().argmin())
        n_in_window = sum(
            1 for ev in events if abs(ev.bar_index - anchor_idx) <= wb
        )

        strength = float(best.metadata.get("divergence_strength", 0.0))
        results.append(CaseResult(
            case=case, symbol=symbol, detector_match=verdict,
            event_count_in_window=n_in_window,
            best_event_ts=_ts_str(best.timestamp_ms),
            best_event_direction=best.direction,
            best_event_strength=strength,
            best_event_div_type=str(best.metadata.get("div_type", "?")),
            delta_bars_from_anchor=delta,
            pass_threshold_030=strength >= 0.30,
            pass_threshold_045=strength >= 0.45,
        ))
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _format_table(results: List[CaseResult]) -> str:
    header = (
        "| case | tf | dir | symbol | verdict | n_in_win | best_event_ts | "
        "best_dir | strength | div_type | Δbars | ≥0.30 | ≥0.45 | note |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    rows = [header, sep]
    for r in results:
        c = r.case
        rows.append(
            f"| {c.case_id} | {c.timeframe} | {c.direction} | {r.symbol} | "
            f"**{r.detector_match}** | {r.event_count_in_window} | "
            f"{r.best_event_ts or '-'} | {r.best_event_direction or '-'} | "
            f"{(f'{r.best_event_strength:.4f}' if r.best_event_strength is not None else '-')} | "
            f"{r.best_event_div_type or '-'} | "
            f"{r.delta_bars_from_anchor if r.delta_bars_from_anchor is not None else '-'} | "
            f"{('✓' if r.pass_threshold_030 else ('✗' if r.pass_threshold_030 is not None else '-'))} | "
            f"{('✓' if r.pass_threshold_045 else ('✗' if r.pass_threshold_045 is not None else '-'))} | "
            f"{c.note} |"
        )
    return "\n".join(rows)


def _summary(results: List[CaseResult]) -> str:
    n = len(results)
    by_verdict: Dict[str, int] = {}
    for r in results:
        by_verdict[r.detector_match] = by_verdict.get(r.detector_match, 0) + 1
    parts = [f"total={n}"]
    for k in ["MATCH", "WEAK", "EARLY_LATE", "MISMATCH", "MISS"]:
        parts.append(f"{k}={by_verdict.get(k, 0)}")
    return ", ".join(parts)


def _write_report(report_path: Path, all_results: List[CaseResult]) -> None:
    daily = [r for r in all_results if r.case.timeframe == "1d"]
    h4 = [r for r in all_results if r.case.timeframe == "4h"]
    body = []
    body.append(f"# RSI Divergence — Visual-mark vs Detector Case Study\n")
    body.append(f"**Date**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    body.append(f"**Pattern**: `RSIDivergence` (`src/ml/patterns/rsi_divergence.py`)\n")
    body.append("**Method**: `for i in range(warmup_bars, n): pat.detect_at(mtf, i)`. "
                "ML 학습/라벨/holding 평가는 별개. 본 스크립트는 시각 마킹 시점 "
                "에서 detector가 **이벤트를 냈는가 + 방향이 일치하는가 + "
                "strength threshold 통과** 만 본다.\n")
    body.append("**Window**: 1d ±30 bars (~1개월), 4h ±20 bars (~3.3일).\n")
    body.append("**Verdict**: MATCH = 윈도우 내 + 방향 일치 + strength ≥ 0.30 / "
                "WEAK = 방향 일치인데 strength < 0.30 / "
                "EARLY_LATE = 확장 윈도우(±2x) 안 일치 / "
                "MISMATCH = 방향 반대 / MISS = 이벤트 자체 없음\n")

    body.append("\n## 1d cases (7)\n")
    body.append(_format_table(daily))
    body.append(f"\n**Summary**: {_summary(daily)}\n")

    body.append("\n## 4h cases (6)\n")
    body.append(_format_table(h4))
    body.append(f"\n**Summary**: {_summary(h4)}\n")

    body.append("\n## Combined\n")
    body.append(f"**Summary all 13**: {_summary(all_results)}\n")

    body.append("\n## Next-step gate (per Round 6 plan)\n")
    n_match = sum(1 for r in all_results if r.detector_match == "MATCH")
    body.append(f"- MATCH count: **{n_match} / 13**\n")
    if n_match >= 10:
        body.append("- → **B 진행 가능** (1d ML 매트릭스). Detector 신뢰성 충분.\n")
    elif n_match >= 5:
        body.append("- → **B + C 병행 검토**. Detector 부분 신뢰성 — "
                    "ML 매트릭스 + 룰베이스 wrapper 비교가 의미 있음.\n")
    else:
        body.append("- → **C 우선** (룰베이스 fallback). Detector 정의가 "
                    "시각 마킹과 차이 큼.\n")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(body), encoding="utf-8")
    print(f"[case-study] report written to {report_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="RSI divergence visual-mark vs detector case study"
    )
    parser.add_argument("--symbols", default="BTCUSDT",
                        help="comma-separated; default BTCUSDT")
    parser.add_argument(
        "--report", default="logs/research/rsi_divergence/case_study_round6_kickoff.md"
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    all_results: List[CaseResult] = []

    for sym in symbols:
        # 1d daily cases
        daily_results = run_case_study_for(sym, "1d", DAILY_CASES)
        # 4h cases
        h4_results = run_case_study_for(sym, "4h", H4_CASES)
        all_results.extend(daily_results)
        all_results.extend(h4_results)

    report_path = PROJECT_ROOT / args.report
    _write_report(report_path, all_results)

    print()
    print("=== summary ===")
    print(f"all (n={len(all_results)}):", _summary(all_results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
