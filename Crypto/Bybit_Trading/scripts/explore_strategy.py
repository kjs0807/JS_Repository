"""Generic strategy exploration script.

Usage:
    python scripts/explore_strategy.py BBKCSqueeze coarse
    python scripts/explore_strategy.py BBKCSqueeze fine
    python scripts/explore_strategy.py BBKCSqueeze walkforward
    python scripts/explore_strategy.py BBKCSqueeze overfit
    python scripts/explore_strategy.py BBKCSqueeze status
    python scripts/explore_strategy.py BBKCSqueeze coarse --reset

전략 이름은 src/strategies/registry_builder.py의 STRATEGY_CONFIGS 키와 동일.
각 전략별로 logs/research/<strategy_snake_case>/ 디렉토리에 상태 저장.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.getLogger("src.execution.risk").setLevel(logging.ERROR)
logging.getLogger("src").setLevel(logging.ERROR)

from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.backtester.engine import BacktestEngine
from src.backtester.robust_explorer import (
    RobustExplorer,
    combo_key,
    setup_logger,
    is_process_alive,
)
from src.strategies.registry_builder import get_strategy_config


def snake_case(name: str) -> str:
    """CamelCase → snake_case."""
    import re
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def expand_grid(grid: Dict[str, List]) -> List[Dict[str, Any]]:
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*values)]


def build_all_coarse_combos(cfg: Dict[str, Any], strategy_name: str) -> List[Tuple]:
    """Generate all (variant, symbol, tf, params) tuples."""
    combos = []
    grid = expand_grid(cfg["coarse_grid"])
    for symbol in cfg["symbols"]:
        for tf in cfg["timeframes"]:
            for params in grid:
                combos.append((strategy_name, symbol, tf, params))
    return combos


def build_fine_grid(top_params: Dict[str, Any], coarse_grid: Dict[str, List]) -> List[Dict]:
    """±1 step fine grid around Coarse Top-1."""
    fine_space = {}
    for k, v in top_params.items():
        if k not in coarse_grid:
            fine_space[k] = [v]
            continue
        coarse_values = sorted(coarse_grid[k])
        # Numeric refinement
        if isinstance(v, (int, float)) and len(coarse_values) >= 2:
            idx = coarse_values.index(v) if v in coarse_values else 0
            # Take current and neighbors
            nearby = set()
            for offset in (-1, 0, 1):
                ni = idx + offset
                if 0 <= ni < len(coarse_values):
                    nearby.add(coarse_values[ni])
            # Add midpoints
            if isinstance(v, float):
                for nv in list(nearby):
                    if nv != v:
                        midpoint = (v + nv) / 2
                        nearby.add(round(midpoint, 4))
            fine_space[k] = sorted(nearby)
        else:
            fine_space[k] = [v]
    return expand_grid(fine_space)


def run_backtest(strategy, db, symbol, tf, config, risk_config, reference_symbols=None):
    symbols_needed = [symbol]
    if reference_symbols:
        symbols_needed = list(set([symbol] + reference_symbols))
    feed = HistoricalDataFeed(db=db, symbols=symbols_needed, timeframe=tf)
    return BacktestEngine().run(
        strategy, feed, config, symbol=symbol,
        risk_config=risk_config, reference_symbols=reference_symbols,
    )


def result_to_record(strategy_name, symbol, tf, params, result):
    return {
        "variant": strategy_name,
        "symbol": symbol,
        "tf": tf,
        "params": params,
        "trades": result.total_trades,
        "pnl": result.total_pnl,
        "sharpe": result.sharpe_ratio,
        "max_dd": result.max_drawdown,
        "profit_factor": result.profit_factor,
        "win_rate": result.win_rate,
    }


def error_record(strategy_name, symbol, tf, params, err_msg):
    return {
        "variant": strategy_name, "symbol": symbol, "tf": tf, "params": params,
        "error": err_msg,
        "trades": 0, "pnl": 0.0, "sharpe": 0.0, "max_dd": 0.0,
        "profit_factor": 0.0, "win_rate": 0.0,
    }


def coarse_stage(strategy_name: str, explorer: RobustExplorer, logger, reset: bool):
    cfg = get_strategy_config(strategy_name)
    cls = cfg["cls"]
    reference_symbols = cfg.get("reference_symbols", [])

    jsonl = explorer.jsonl_path("coarse")
    if reset and jsonl.exists():
        jsonl.unlink()
        logger.info("Reset: deleted existing coarse_results.jsonl")

    existing, done_keys = explorer.load_all_results("coarse"), explorer.load_done_keys("coarse")
    all_combos = build_all_coarse_combos(cfg, strategy_name)
    remaining = [c for c in all_combos if combo_key(*c) not in done_keys]
    logger.info(f"Coarse: total={len(all_combos)}, done={len(existing)}, remaining={len(remaining)}")

    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    risk_config = RiskConfig(max_drawdown_pct=0.50, daily_loss_limit_pct=0.50, max_concurrent=10)

    db = DBManager(db_path=str(PROJECT_ROOT / "db" / "bybit_data.db"))

    t_start = time.time()
    processed = 0

    for variant, symbol, tf, params in remaining:
        if explorer.stop_requested:
            logger.info("Stop requested, exiting")
            break
        try:
            strategy = cls(**params) if not reference_symbols else cls(**params)
            result = run_backtest(strategy, db, symbol, tf, config, risk_config,
                                   reference_symbols=reference_symbols or None)
            record = result_to_record(variant, symbol, tf, params, result)
        except Exception as exc:
            logger.error(f"Backtest failed: {symbol} {tf} {params}: {exc}")
            record = error_record(variant, symbol, tf, params, str(exc))
        explorer.append_result("coarse", record)
        processed += 1
        if processed % 10 == 0 or processed == len(remaining):
            elapsed = time.time() - t_start
            rate = processed / elapsed * 60 if elapsed > 0 else 0
            eta_min = (len(remaining) - processed) / (rate / 60) / 60 if rate > 0 else 0
            logger.info(f"Progress: {processed}/{len(remaining)} | {rate:.1f}/min | ETA {eta_min:.0f}min")

    # Summary files
    all_results = explorer.load_all_results("coarse")
    with open(explorer.output_dir / "coarse_all.json", "w") as f:
        json.dump(all_results, f, indent=2)
    tops = {}
    for r in all_results:
        if "error" in r: continue
        key = (r["variant"], r["symbol"], r["tf"])
        tops.setdefault(key, []).append(r)
    top3 = []
    for key, results in tops.items():
        top3.extend(sorted(results, key=lambda x: x["sharpe"], reverse=True)[:3])
    with open(explorer.output_dir / "coarse_top3.json", "w") as f:
        json.dump(top3, f, indent=2)
    logger.info(f"Coarse summary written. Top3 count: {len(top3)}")


def fine_stage(strategy_name: str, explorer: RobustExplorer, logger, reset: bool):
    cfg = get_strategy_config(strategy_name)
    cls = cfg["cls"]
    reference_symbols = cfg.get("reference_symbols", [])
    coarse_grid = cfg["coarse_grid"]

    top3_file = explorer.output_dir / "coarse_top3.json"
    if not top3_file.exists():
        logger.error("coarse_top3.json missing. Run coarse first.")
        return
    coarse_top3 = json.load(open(top3_file))

    jsonl = explorer.jsonl_path("fine")
    if reset and jsonl.exists():
        jsonl.unlink()

    # Top-1 per (variant, symbol, tf)
    top1_map = {}
    for r in coarse_top3:
        key = (r["variant"], r["symbol"], r["tf"])
        if key not in top1_map:
            top1_map[key] = r

    combos = []
    for key, top1 in top1_map.items():
        variant, symbol, tf = key
        fine_params_list = build_fine_grid(top1["params"], coarse_grid)
        for p in fine_params_list:
            combos.append((variant, symbol, tf, p))

    done_keys = explorer.load_done_keys("fine")
    remaining = [c for c in combos if combo_key(*c) not in done_keys]
    logger.info(f"Fine: total={len(combos)}, remaining={len(remaining)}")

    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    risk_config = RiskConfig(max_drawdown_pct=0.50, daily_loss_limit_pct=0.50, max_concurrent=10)
    db = DBManager(db_path=str(PROJECT_ROOT / "db" / "bybit_data.db"))

    t_start = time.time()
    processed = 0
    for variant, symbol, tf, params in remaining:
        if explorer.stop_requested: break
        try:
            strategy = cls(**params)
            result = run_backtest(strategy, db, symbol, tf, config, risk_config,
                                   reference_symbols=reference_symbols or None)
            record = result_to_record(variant, symbol, tf, params, result)
        except Exception as exc:
            logger.error(f"Fine failed: {exc}")
            record = error_record(variant, symbol, tf, params, str(exc))
        explorer.append_result("fine", record)
        processed += 1
        if processed % 10 == 0 or processed == len(remaining):
            elapsed = time.time() - t_start
            logger.info(f"Fine: {processed}/{len(remaining)} ({elapsed:.0f}s)")

    all_results = explorer.load_all_results("fine")
    with open(explorer.output_dir / "fine_all.json", "w") as f:
        json.dump(all_results, f, indent=2)
    best_map = {}
    for r in all_results:
        if "error" in r: continue
        key = (r["variant"], r["symbol"], r["tf"])
        if key not in best_map or r["sharpe"] > best_map[key]["sharpe"]:
            best_map[key] = r
    with open(explorer.output_dir / "fine_best.json", "w") as f:
        json.dump(list(best_map.values()), f, indent=2)
    logger.info(f"Fine summary written. Best count: {len(best_map)}")


def walkforward_stage(strategy_name: str, explorer: RobustExplorer, logger, reset: bool):
    from src.backtester.walk_forward import WalkForwardAnalyzer
    from src.backtester.config import WalkForwardConfig

    cfg = get_strategy_config(strategy_name)
    cls = cfg["cls"]
    reference_symbols = cfg.get("reference_symbols", [])

    fine_best_file = explorer.output_dir / "fine_best.json"
    if not fine_best_file.exists():
        logger.error("fine_best.json missing")
        return
    best_list = json.load(open(fine_best_file))

    jsonl = explorer.jsonl_path("walkforward")
    if reset and jsonl.exists():
        jsonl.unlink()

    done_keys = explorer.load_done_keys("walkforward")
    remaining = [
        (e["variant"], e["symbol"], e["tf"], e["params"])
        for e in best_list
        if combo_key(e["variant"], e["symbol"], e["tf"], e["params"]) not in done_keys
    ]
    logger.info(f"WF: {len(best_list)} total, {len(remaining)} remaining")

    wf_config = WalkForwardConfig(is_months=6, oos_months=2, min_windows=3)
    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    db = DBManager(db_path=str(PROJECT_ROOT / "db" / "bybit_data.db"))

    for variant, symbol, tf, params in remaining:
        if explorer.stop_requested: break
        try:
            mini_space = {k: [v] for k, v in params.items()}
            wf = WalkForwardAnalyzer(wf_config)
            symbols_needed = list(set([symbol] + (reference_symbols or [])))
            feed = HistoricalDataFeed(db=db, symbols=symbols_needed, timeframe=tf)
            wf_result = wf.run(cls, mini_space, feed, config, symbol=symbol)
            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "windows": len(wf_result.windows),
                "avg_oos_retention": wf_result.avg_oos_retention,
                "avg_oos_sharpe": wf_result.avg_oos_sharpe,
                "oos_positive_pct": wf_result.oos_positive_pct,
            }
        except Exception as exc:
            logger.error(f"WF failed: {exc}")
            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "error": str(exc), "windows": 0, "avg_oos_retention": 0.0,
                "avg_oos_sharpe": 0.0, "oos_positive_pct": 0.0,
            }
        explorer.append_result("walkforward", record)
        logger.info(f"  WF done: {variant} {symbol} {tf}")

    all_wf = explorer.load_all_results("walkforward")
    with open(explorer.output_dir / "walkforward.json", "w") as f:
        json.dump(all_wf, f, indent=2)
    logger.info(f"Walk-Forward done. Records: {len(all_wf)}")


def overfit_stage(strategy_name: str, explorer: RobustExplorer, logger, reset: bool):
    from src.backtester.overfit import OverfitDetector

    cfg = get_strategy_config(strategy_name)
    cls = cfg["cls"]
    reference_symbols = cfg.get("reference_symbols", [])

    fine_best_file = explorer.output_dir / "fine_best.json"
    fine_all_file = explorer.output_dir / "fine_all.json"
    if not (fine_best_file.exists() and fine_all_file.exists()):
        logger.error("Fine files missing")
        return
    fine_best = json.load(open(fine_best_file))
    fine_all = json.load(open(fine_all_file))

    jsonl = explorer.jsonl_path("overfit")
    if reset and jsonl.exists():
        jsonl.unlink()

    done_keys = explorer.load_done_keys("overfit")
    remaining = [
        (e["variant"], e["symbol"], e["tf"], e["params"])
        for e in fine_best
        if combo_key(e["variant"], e["symbol"], e["tf"], e["params"]) not in done_keys
    ]

    detector = OverfitDetector()
    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    risk_config = RiskConfig(max_drawdown_pct=0.50, daily_loss_limit_pct=0.50, max_concurrent=10)
    db = DBManager(db_path=str(PROJECT_ROOT / "db" / "bybit_data.db"))

    for variant, symbol, tf, params in remaining:
        if explorer.stop_requested: break
        try:
            strategy = cls(**params)
            result = run_backtest(strategy, db, symbol, tf, config, risk_config,
                                   reference_symbols=reference_symbols or None)
            pnl_list = [t.pnl for t in result.trades]
            scores = {str(fr["params"]): fr["sharpe"]
                      for fr in fine_all
                      if "error" not in fr and fr["variant"] == variant
                      and fr["symbol"] == symbol and fr["tf"] == tf}
            verdict = detector.detect(pnl_list, scores, n_shuffles=500)
            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "verdict": verdict.verdict, "p_value": verdict.p_value,
                "sensitivity": verdict.sensitivity, "reason": verdict.reason,
            }
        except Exception as exc:
            logger.error(f"Overfit failed: {exc}")
            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "error": str(exc), "verdict": "ERROR",
                "p_value": 1.0, "sensitivity": 1.0, "reason": str(exc),
            }
        explorer.append_result("overfit", record)
        logger.info(f"  Overfit: {variant} {symbol} {tf}: {record.get('verdict','ERROR')}")

    all_ov = explorer.load_all_results("overfit")
    with open(explorer.output_dir / "overfit.json", "w") as f:
        json.dump(all_ov, f, indent=2)


def cmd_status(output_dir: Path):
    print(f"=== Exploration Status: {output_dir.name} ===")
    pid_file = output_dir / "explore.pid"
    hb_file = output_dir / "heartbeat.txt"
    if pid_file.exists():
        pid = int(pid_file.read_text().strip())
        alive = is_process_alive(pid)
        print(f"PID: {pid} ({'ALIVE' if alive else 'DEAD'})")
    else:
        print("No active run")
    if hb_file.exists():
        age = time.time() - float(hb_file.read_text().strip())
        print(f"Heartbeat: {age:.0f}s ago")
    for stage in ["coarse", "fine", "walkforward", "overfit"]:
        jsonl = output_dir / f"{stage}_results.jsonl"
        count = sum(1 for _ in open(jsonl, encoding="utf-8")) if jsonl.exists() else 0
        print(f"  {stage}: {count} records")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy", help="Strategy name (e.g., BBKCSqueeze)")
    parser.add_argument("stage", choices=["coarse", "fine", "walkforward", "overfit", "status"])
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "logs" / "research" / snake_case(args.strategy)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.stage == "status":
        cmd_status(output_dir)
        return

    logger = setup_logger(output_dir / "explore.log", name=f"explore_{args.strategy}")
    logger.info(f"=== Starting {args.strategy} stage={args.stage} reset={args.reset} ===")
    logger.info(f"PID: {os.getpid()}")

    explorer = RobustExplorer(name=snake_case(args.strategy), output_dir=output_dir)
    explorer.write_pid()
    explorer.install_signal_handler()
    explorer.start_heartbeat()

    try:
        if args.stage == "coarse":
            coarse_stage(args.strategy, explorer, logger, args.reset)
        elif args.stage == "fine":
            fine_stage(args.strategy, explorer, logger, args.reset)
        elif args.stage == "walkforward":
            walkforward_stage(args.strategy, explorer, logger, args.reset)
        elif args.stage == "overfit":
            overfit_stage(args.strategy, explorer, logger, args.reset)
        logger.info("=== Stage completed ===")
    except Exception as exc:
        logger.exception(f"Unhandled: {exc}")
        raise
    finally:
        explorer.remove_pid()


if __name__ == "__main__":
    main()
