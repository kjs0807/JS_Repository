"""Replay a trained ML artifact through the main BacktestEngine.

Purpose: end-to-end parity check between the training/walk-forward path
and the production wrapper path. Loads an ML artifact via
PatternMLFilterStrategy.from_artifact, builds the MTFData that the
pattern needs from the DB, runs one BacktestEngine per symbol over a
requested period, and prints per-symbol trade counts, P&L, and
derived metrics.

Usage:
    # Deployment simulation (wrapper replay on whatever --start/--end range):
    python -m scripts.backtest_ml_artifact deployment \\
        --run-dir logs/ml/rsi_divergence/2026-04-14_010319 \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,LINKUSDT,AVAXUSDT \\
        --start 2024-04-01 --end 2026-04-10

    # Match the artifact's OOS period exactly (from meta.data.oos_period_ms):
    python -m scripts.backtest_ml_artifact wf-oos-only \\
        --run-dir logs/ml/rsi_divergence/2026-04-14_010319 \\
        --symbols artifact

Measurement notes (read before comparing to the walk-forward report):

1) The walk-forward validator in src/ml/validator.py does NOT simulate
   bar-level execution. It iterates the cached event dataset, retrains
   one XGBoost per fold, and counts threshold-passing events as
   "trades" using labels as oracle truth. Walk-forward "trades" is an
   event-level classification metric, not a trade-execution metric.

2) BacktestEngine + PatternMLFilterStrategy is bar-level. It applies
   a holding lock, intra-bar TP/SL detection, and broker position
   sizing (calc_qty with risk_pct * current_equity, stop_distance from
   the ATR at entry). Different object -> different number of "trades".

3) approx_R_per_trade printed below is a rough reference -- it divides
   the avg trade P&L by (initial_capital * 0.02). The BacktestBroker
   actually sizes on *current* equity, so as equity compounds the risk
   budget per trade drifts. Treat approx_R as directional, not exact,
   unless you patch TradeRecord to carry the realized stop_distance.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.backtester.engine import BacktestEngine
from src.core.config import BacktestConfig, RiskConfig, load_config
from src.core.types import BarSeries
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.ml.patterns.bbkc_filter import BBKCFilterPattern
from src.ml.patterns.rsi_divergence import RSIDivergence
from src.ml.patterns.engulfing_mtf import EngulfingMTF
from src.ml.persistence import load_run
from src.ml.types import MTFData
from src.strategies.pattern_ml_filter import PatternMLFilterStrategy


PATTERN_REGISTRY = {
    "rsi_divergence": RSIDivergence,
    "engulfing_mtf": EngulfingMTF,
    "bbkc_filter": BBKCFilterPattern,
}


def _df_from_db(db_df: pd.DataFrame) -> pd.DataFrame:
    if db_df is None or db_df.empty:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"]
        )
    out = db_df.reset_index(drop=True).copy()
    out = out.rename(columns={"open_time": "timestamp"})
    keep = ["timestamp", "open", "high", "low", "close", "volume"]
    if "turnover" in out.columns:
        keep.append("turnover")
    return out[keep]


def _build_mtf(
    db: DBManager,
    symbol: str,
    timeframes: List[str],
    primary_tf: str,
    start_ms: int,
    end_ms: int,
) -> MTFData:
    series_map: Dict[str, BarSeries] = {}
    for tf in timeframes:
        raw = db.get_bars(symbol=symbol, timeframe=tf,
                          start_time=start_ms, end_time=end_ms)
        normalized = _df_from_db(raw)
        series_map[tf] = BarSeries(symbol=symbol, timeframe=tf, bars=normalized)
    return MTFData(symbol=symbol, primary_tf=primary_tf, series=series_map)


def _date_to_ms(s: str) -> int:
    return int(dt.datetime.fromisoformat(s).timestamp() * 1000)


def _ms_to_date(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def _resolve_period(
    meta: dict,
    mode: str,
    start_arg: Optional[str],
    end_arg: Optional[str],
) -> Tuple[int, int, str]:
    """Resolve (start_ms, end_ms, label) based on compare-mode.

    - ``deployment``: use user-provided --start / --end; matches nothing
      specific in the artifact, represents a "run this model on any
      window you want" view.
    - ``wf-oos-only``: use artifact.meta.data.oos_period_ms; this is the
      closest thing to the window the walk-forward validator's OOS
      slices conceptually cover. Even so, see the module docstring --
      WF trades are event-level, BT trades are execution-level, and the
      two are still not 1:1 numerically.
    """
    if mode == "wf-oos-only":
        oos = meta.get("data", {}).get("oos_period_ms")
        if not oos or len(oos) != 2:
            raise ValueError(
                "wf-oos-only mode requires meta.data.oos_period_ms in the artifact."
            )
        start_ms, end_ms = int(oos[0]), int(oos[1])
        label = (
            f"OOS ({_ms_to_date(start_ms)} -> {_ms_to_date(end_ms)})"
        )
        return start_ms, end_ms, label
    # deployment mode
    if start_arg is None or end_arg is None:
        raise ValueError("deployment mode requires --start and --end")
    start_ms, end_ms = _date_to_ms(start_arg), _date_to_ms(end_arg)
    label = f"DEPLOY ({start_arg} -> {end_arg})"
    return start_ms, end_ms, label


def run_backtest(
    run_dir: Path,
    symbols: List[str],
    start_ms: int,
    end_ms: int,
    initial_capital: float = 10_000.0,
) -> Dict[str, dict]:
    artifact = load_run(run_dir)
    meta = artifact.meta
    pattern_name = meta["pattern_name"]
    primary_tf = meta["data"]["primary_tf"]
    timeframes = meta["data"]["timeframes"]
    label_mode = meta["policy"]["label"]["mode"]
    print(
        f"[backtest] run_dir={run_dir}\n"
        f"[backtest] pattern={pattern_name} primary_tf={primary_tf} "
        f"label_mode={label_mode}"
    )

    if pattern_name not in PATTERN_REGISTRY:
        raise KeyError(f"Unknown pattern in artifact: {pattern_name}")
    pattern_cls = PATTERN_REGISTRY[pattern_name]

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    results: Dict[str, dict] = {}
    engine = BacktestEngine()
    bt_config = BacktestConfig(initial_capital=initial_capital)
    risk_config = RiskConfig()

    for sym in symbols:
        mtf = _build_mtf(
            db, symbol=sym, timeframes=timeframes,
            primary_tf=primary_tf, start_ms=start_ms, end_ms=end_ms,
        )
        primary_series = mtf.get_primary()
        n_bars = len(primary_series)
        if n_bars == 0:
            print(f"[backtest] {sym}: no bars in range, skipping")
            continue

        # Fresh wrapper per symbol (state is held in self._open / self._mtf
        # and we want no cross-symbol leakage).
        strat = PatternMLFilterStrategy.from_artifact(
            run_dir=run_dir,
            pattern_factory=pattern_cls,
            mtf_data=mtf,
        )

        feed = HistoricalDataFeed(
            db=db, symbols=[sym], timeframe=primary_tf,
            start_time=start_ms, end_time=end_ms,
        )

        result = engine.run(
            strategy=strat,
            data_feed=feed,
            config=bt_config,
            symbol=sym,
            risk_config=risk_config,
        )
        n_trades = result.total_trades
        pnl = result.total_pnl
        win_rate = result.win_rate
        avg = result.avg_trade_pnl
        approx_r_denom = initial_capital * 0.02
        approx_r_per_trade = (
            avg / approx_r_denom if approx_r_denom > 0 else 0.0
        )
        results[sym] = {
            "n_bars_primary": n_bars,
            "trades": n_trades,
            "total_pnl": pnl,
            "avg_pnl": avg,
            "win_rate": win_rate,
            "sharpe": result.sharpe_ratio,
            "max_dd": result.max_drawdown,
            # NOTE: approximation, not realized R. See module docstring.
            "approx_R_per_trade": approx_r_per_trade,
        }
        print(
            f"[backtest] {sym}: bars={n_bars} trades={n_trades} "
            f"pnl={pnl:+.2f} win_rate={win_rate:.1%} "
            f"avg={avg:+.3f} approx_R/tr={approx_r_per_trade:+.3f}"
        )

    return results


def event_level_probe(
    run_dir: Path,
    symbols: List[str],
    start_ms: int,
    end_ms: int,
) -> Dict[str, dict]:
    """Count raw pattern events and threshold-passing events per symbol,
    bypassing BacktestEngine entirely. This is the wrapper-parity signal:
    bar-level BT trades should equal the 'passed' count (holding lock
    blocks 0 when events are sparse)."""
    import numpy as np

    artifact = load_run(run_dir)
    meta = artifact.meta
    model = artifact.model
    threshold = float(meta["policy"]["threshold"])
    feature_columns = meta["feature_columns"]
    primary_tf = meta["data"]["primary_tf"]
    timeframes = meta["data"]["timeframes"]
    pattern_name = meta["pattern_name"]
    if pattern_name not in PATTERN_REGISTRY:
        raise KeyError(f"Unknown pattern: {pattern_name}")
    pattern = PATTERN_REGISTRY[pattern_name]()

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    out: Dict[str, dict] = {}
    for sym in symbols:
        mtf = _build_mtf(
            db, symbol=sym, timeframes=timeframes,
            primary_tf=primary_tf, start_ms=start_ms, end_ms=end_ms,
        )
        primary = mtf.get_primary()
        n = len(primary)
        if n == 0:
            out[sym] = {"bars": 0, "events": 0, "passed": 0}
            continue
        events = 0
        passed = 0
        for i in range(pattern.warmup_bars, n):
            ev = pattern.detect_at(mtf, i)
            if ev is None:
                continue
            events += 1
            feats = pattern.extract_features(ev, mtf)
            vec = np.array(
                [[feats.get(c, 0.0) for c in feature_columns]], dtype=float
            )
            proba = float(model.predict_proba(vec)[0, 1])
            if proba >= threshold:
                passed += 1
        out[sym] = {"bars": n, "events": events, "passed": passed}
    return out


def _print_header_warning(mode: str) -> None:
    rule = "-" * 90
    warnings = [
        rule,
        "WARNING: Walk-forward vs BacktestEngine 'trades' are NOT the same thing.",
        "  WF 'trades'  = events passing proba >= threshold under per-fold retrained",
        "                 models, no bar-level simulation (see src/ml/validator.py)",
        "  BT 'trades'  = positions actually opened by BacktestEngine under holding",
        "                 lock + intra-bar TP/SL + broker.calc_qty sizing",
        "  approx_R/tr  = avg_pnl / (initial_capital * 0.02), NOT realized R.",
        "                 Broker uses current equity for sizing; treat as directional.",
        rule,
    ]
    print()
    for w in warnings:
        print(w)
    if mode == "deployment":
        print(
            "Mode: DEPLOYMENT -- BT period is user-provided. Side-by-side with WF\n"
            "  OOS-only metrics is misleading; use 'wf-oos-only' mode for the\n"
            "  closest (still non-exact) comparison window."
        )
    elif mode == "wf-oos-only":
        print(
            "Mode: WF-OOS-ONLY -- BT period matches artifact.meta.data.oos_period_ms.\n"
            "  Compares single-final-model BT against WF's per-fold ensemble counts."
        )
    print(rule)
    print()


def compare_to_walk_forward(
    run_dir: Path, bt_results: Dict[str, dict], probe: Optional[Dict[str, dict]]
) -> None:
    """Side-by-side print of the wrapper-replay vs the walk-forward report."""
    report_path = Path(run_dir) / "report.json"
    if not report_path.exists():
        print("[compare] no report.json in run_dir, skipping WF comparison")
        return
    report = json.loads(report_path.read_text())
    per_symbol_wf = report.get("metrics", {}).get("per_symbol_oos", {})
    wf = report.get("metrics", {}).get("walk_forward", {})

    print()
    header = (
        f"{'symbol':10s} | {'WF n':>5s} {'WF pnl(R)':>10s} | "
        f"{'BT n':>5s} {'BT pnl($)':>11s} {'approxR/tr':>11s}"
    )
    if probe is not None:
        header += f" | {'probe ev':>9s} {'probe pass':>11s}"
    print(header)
    print("-" * len(header))
    all_syms = sorted(
        set(list(per_symbol_wf.keys()) + list(bt_results.keys()))
    )
    for sym in all_syms:
        wf_cell = per_symbol_wf.get(sym, {})
        bt_cell = bt_results.get(sym, {})
        line = (
            f"{sym:10s} | "
            f"{wf_cell.get('trades', 0):>5d} "
            f"{wf_cell.get('pnl', 0.0):+10.1f} | "
            f"{bt_cell.get('trades', 0):>5d} "
            f"{bt_cell.get('total_pnl', 0.0):+11.2f} "
            f"{bt_cell.get('approx_R_per_trade', 0.0):+11.3f}"
        )
        if probe is not None:
            pb = probe.get(sym, {})
            line += f" | {pb.get('events', 0):>9d} {pb.get('passed', 0):>11d}"
        print(line)
    print()
    print(
        f"WF aggregate: n={wf.get('oos_total_trades', 0)} "
        f"pnl={wf.get('oos_total_pnl', 0.0):+.1f}R "
        f"sharpe_mean={wf.get('oos_sharpe_mean', 0.0):+.3f} "
        f"pos_folds={wf.get('oos_pos_pct', 0.0):.1%}"
    )
    bt_total_trades = sum(r["trades"] for r in bt_results.values())
    bt_total_pnl = sum(r["total_pnl"] for r in bt_results.values())
    print(f"BT aggregate: n={bt_total_trades} pnl={bt_total_pnl:+.2f}")
    if probe is not None:
        ev_total = sum(p["events"] for p in probe.values())
        pass_total = sum(p["passed"] for p in probe.values())
        print(
            f"Probe aggregate: events={ev_total} passed={pass_total} "
            f"(wrapper BT 'n' must equal 'passed' when holding lock is inactive)"
        )


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "mode",
        choices=("deployment", "wf-oos-only"),
        help="deployment: replay over --start/--end. "
             "wf-oos-only: replay over artifact.meta.data.oos_period_ms.",
    )
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument(
        "--symbols", required=True,
        help="Comma-separated. Use 'artifact' to read from meta.",
    )
    p.add_argument("--start", help="ISO date. Required in deployment mode.")
    p.add_argument("--end", help="ISO date. Required in deployment mode.")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument(
        "--probe", action="store_true",
        help="Also run the event-level probe (no BacktestEngine) and print "
             "per-symbol events + threshold-passed counts next to the BT table. "
             "When holding lock is inactive, BT trades should equal probe passed.",
    )
    p.add_argument("--no-compare", action="store_true")
    args = p.parse_args(argv)

    if args.symbols == "artifact":
        meta = json.loads((args.run_dir / "meta.json").read_text())
        symbols = meta["data"]["symbols"]
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    artifact_meta = json.loads((args.run_dir / "meta.json").read_text())
    start_ms, end_ms, label = _resolve_period(
        artifact_meta, args.mode, args.start, args.end,
    )
    print(f"[backtest] period={label}")

    _print_header_warning(args.mode)

    bt_results = run_backtest(
        run_dir=args.run_dir, symbols=symbols,
        start_ms=start_ms, end_ms=end_ms,
        initial_capital=args.capital,
    )
    probe = None
    if args.probe:
        probe = event_level_probe(
            run_dir=args.run_dir, symbols=symbols,
            start_ms=start_ms, end_ms=end_ms,
        )
    if not args.no_compare:
        compare_to_walk_forward(args.run_dir, bt_results, probe)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
