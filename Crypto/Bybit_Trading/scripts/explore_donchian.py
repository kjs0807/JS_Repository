"""Donchian Breakout 전략 탐색 스크립트 (강건 버전).

특징:
- Incremental JSONL 저장 (프로세스 중단 시 재개 가능)
- Heartbeat + PID 파일 (상태 모니터링)
- Signal handler (Ctrl+C 시 안전 종료)
- 구조화 로그 파일
- 에러 복구 (개별 백테스트 실패 시 건너뛰고 계속)

Usage:
    python scripts/explore_donchian.py coarse          # 이어서 진행 또는 새로 시작
    python scripts/explore_donchian.py coarse --reset   # 이전 결과 지우고 새로 시작
    python scripts/explore_donchian.py status           # 진행 상황 확인
    python scripts/explore_donchian.py fine
    python scripts/explore_donchian.py walkforward
    python scripts/explore_donchian.py overfit
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Suppress risk manager warning spam
logging.getLogger("src.execution.risk").setLevel(logging.ERROR)
logging.getLogger("src").setLevel(logging.ERROR)

from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.donchian_trend_filter import DonchianTrendFilter
from src.strategies.donchian_fixed_rr import DonchianFixedRR
from src.backtester.engine import BacktestEngine


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]
TIMEFRAMES = ["1h", "4h"]

COARSE_GRID_B = {
    "entry_period": [10, 30, 55],
    "exit_period": [5, 10, 20],
    "ema_filter": [100, 200, 300],
    "stop_atr": [1.5, 2.5],
}

COARSE_GRID_C = {
    "entry_period": [10, 30, 55],
    "stop_atr": [2.0, 3.0],
    "tp_r_ratio": [1.5, 2.5, 4.0],
}

# Global stop flag for signal handler
_stop_requested = False


# ==================== Infrastructure ====================

def setup_logger(log_file: Path) -> logging.Logger:
    """Setup logger with file + stderr output."""
    logger = logging.getLogger("donchian_explore")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


def write_pid_file(pid_file: Path) -> None:
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def remove_pid_file(pid_file: Path) -> None:
    if pid_file.exists():
        try:
            pid_file.unlink()
        except OSError:
            pass


def start_heartbeat_thread(heartbeat_file: Path, interval: float = 10.0) -> threading.Thread:
    """Background thread that updates heartbeat file."""
    def _loop():
        while not _stop_requested:
            try:
                heartbeat_file.write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


def install_signal_handler(logger: logging.Logger) -> None:
    def _handler(signum, frame):
        global _stop_requested
        logger.info(f"Signal {signum} received, requesting stop...")
        _stop_requested = True

    signal.signal(signal.SIGINT, _handler)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (AttributeError, ValueError):
        pass  # SIGTERM not available on Windows


def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is alive."""
    if pid <= 0:
        return False
    try:
        # psutil is optional; fallback to OS-specific
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    # Fallback: try os.kill(pid, 0) — works on Unix, Windows raises OSError
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ==================== Result storage ====================

def params_key(params: Dict[str, Any]) -> str:
    """Canonical string representation of params dict for dedup."""
    return json.dumps(params, sort_keys=True)


def combo_key(variant: str, symbol: str, tf: str, params: Dict[str, Any]) -> str:
    return f"{variant}|{symbol}|{tf}|{params_key(params)}"


def load_existing_jsonl(file: Path) -> Tuple[List[Dict], Set[str]]:
    """Load existing JSONL file and return (results, done_keys)."""
    if not file.exists():
        return [], set()

    results = []
    done_keys = set()
    with open(file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            results.append(r)
            done_keys.add(combo_key(r["variant"], r["symbol"], r["tf"], r["params"]))
    return results, done_keys


def append_jsonl(file: Path, record: Dict) -> None:
    """Append a single JSON record to file."""
    with open(file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
        f.flush()


# ==================== Core helpers ====================

def expand_grid(grid: Dict[str, List]) -> List[Dict[str, Any]]:
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*values)]


def run_backtest(strategy, db, symbol, tf, config, risk_config):
    feed = HistoricalDataFeed(db=db, symbols=[symbol], timeframe=tf)
    return BacktestEngine().run(strategy, feed, config, symbol=symbol, risk_config=risk_config)


def result_to_dict(variant, symbol, tf, params, result) -> Dict:
    return {
        "variant": variant,
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


def error_record(variant, symbol, tf, params, err_msg) -> Dict:
    return {
        "variant": variant,
        "symbol": symbol,
        "tf": tf,
        "params": params,
        "error": err_msg,
        "trades": 0, "pnl": 0.0, "sharpe": 0.0, "max_dd": 0.0,
        "profit_factor": 0.0, "win_rate": 0.0,
    }


# ==================== Stage 1: Coarse ====================

def build_all_coarse_combos() -> List[Tuple[str, str, str, Dict]]:
    """Generate all (variant, symbol, tf, params) tuples for coarse stage."""
    combos = []
    grid_b = expand_grid(COARSE_GRID_B)
    grid_c = expand_grid(COARSE_GRID_C)
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            for params in grid_b:
                combos.append(("B", symbol, tf, params))
            for params in grid_c:
                combos.append(("C", symbol, tf, params))
    return combos


def coarse_search(db, output_dir: Path, logger: logging.Logger, reset: bool):
    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    risk_config = RiskConfig(max_drawdown_pct=0.50, daily_loss_limit_pct=0.50, max_concurrent=10)

    jsonl_file = output_dir / "coarse_results.jsonl"
    if reset and jsonl_file.exists():
        jsonl_file.unlink()
        logger.info("Reset: deleted existing coarse_results.jsonl")

    existing, done_keys = load_existing_jsonl(jsonl_file)
    all_combos = build_all_coarse_combos()
    remaining = [c for c in all_combos if combo_key(*c) not in done_keys]

    logger.info(f"Coarse: total={len(all_combos)}, done={len(existing)}, remaining={len(remaining)}")

    t_start = time.time()
    processed = 0

    for variant, symbol, tf, params in remaining:
        if _stop_requested:
            logger.info("Stop requested, saving state and exiting")
            break

        try:
            cls = DonchianTrendFilter if variant == "B" else DonchianFixedRR
            strategy = cls(timeframe=tf, **params)
            result = run_backtest(strategy, db, symbol, tf, config, risk_config)
            record = result_to_dict(variant, symbol, tf, params, result)
        except Exception as exc:
            logger.error(f"Backtest failed: {variant} {symbol} {tf} {params}: {exc}")
            record = error_record(variant, symbol, tf, params, str(exc))

        append_jsonl(jsonl_file, record)
        processed += 1

        elapsed = time.time() - t_start
        rate = processed / elapsed if elapsed > 0 else 0
        eta_sec = (len(remaining) - processed) / rate if rate > 0 else 0
        eta_min = eta_sec / 60

        if processed % 5 == 0 or processed == len(remaining):
            logger.info(
                f"Progress: {processed + len(existing)}/{len(all_combos)} "
                f"({processed}/{len(remaining)} new) | "
                f"{rate*60:.1f}/min | ETA {eta_min:.0f}min"
            )

    # Build summary files from JSONL
    all_results, _ = load_existing_jsonl(jsonl_file)
    logger.info(f"Coarse stage ended. Total records: {len(all_results)}")

    # coarse_all.json (full list)
    with open(output_dir / "coarse_all.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    # coarse_top3.json (Top 3 per variant/symbol/tf by sharpe)
    tops = {}
    for r in all_results:
        if "error" in r:
            continue
        key = (r["variant"], r["symbol"], r["tf"])
        tops.setdefault(key, []).append(r)

    top3 = []
    for key, results in tops.items():
        top3.extend(sorted(results, key=lambda x: x["sharpe"], reverse=True)[:3])

    with open(output_dir / "coarse_top3.json", "w", encoding="utf-8") as f:
        json.dump(top3, f, indent=2)

    logger.info(f"Coarse summary files written. Top3 count: {len(top3)}")


# ==================== Stage 2: Fine ====================

def build_fine_grid_b(top_params: Dict) -> List[Dict]:
    ep = top_params["entry_period"]
    xp = top_params["exit_period"]
    ema = top_params["ema_filter"]
    stop = top_params["stop_atr"]
    return expand_grid({
        "entry_period": sorted(set([max(5, ep - 5), ep, ep + 5, ep + 10])),
        "exit_period": sorted(set([max(3, xp - 2), xp, xp + 2, xp + 5])),
        "ema_filter": sorted(set([max(50, ema - 50), ema, ema + 50])),
        "stop_atr": sorted(set([max(1.0, stop - 0.5), stop, stop + 0.5])),
    })


def build_fine_grid_c(top_params: Dict) -> List[Dict]:
    ep = top_params["entry_period"]
    stop = top_params["stop_atr"]
    tp = top_params["tp_r_ratio"]
    return expand_grid({
        "entry_period": sorted(set([max(5, ep - 5), ep, ep + 5, ep + 10])),
        "stop_atr": sorted(set([max(1.5, stop - 0.5), stop, stop + 0.5])),
        "tp_r_ratio": sorted(set([max(1.0, tp - 0.5), tp, tp + 0.5, tp + 1.0])),
        "trail_activate_atr": [1.0, 1.5, 2.0],
        "trail_distance_atr": [0.75, 1.0, 1.5],
    })


def build_all_fine_combos(coarse_top3: List[Dict]) -> List[Tuple[str, str, str, Dict]]:
    top1_map = {}
    for r in coarse_top3:
        key = (r["variant"], r["symbol"], r["tf"])
        if key not in top1_map:
            top1_map[key] = r

    combos = []
    for key, top1 in top1_map.items():
        variant, symbol, tf = key
        grid = build_fine_grid_b(top1["params"]) if variant == "B" else build_fine_grid_c(top1["params"])
        for params in grid:
            combos.append((variant, symbol, tf, params))
    return combos


def fine_search(db, output_dir: Path, logger: logging.Logger, reset: bool):
    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    risk_config = RiskConfig(max_drawdown_pct=0.50, daily_loss_limit_pct=0.50, max_concurrent=10)

    coarse_top3_file = output_dir / "coarse_top3.json"
    if not coarse_top3_file.exists():
        logger.error("coarse_top3.json not found. Run coarse stage first.")
        return

    with open(coarse_top3_file, "r", encoding="utf-8") as f:
        coarse_top3 = json.load(f)

    jsonl_file = output_dir / "fine_results.jsonl"
    if reset and jsonl_file.exists():
        jsonl_file.unlink()
        logger.info("Reset: deleted existing fine_results.jsonl")

    existing, done_keys = load_existing_jsonl(jsonl_file)
    all_combos = build_all_fine_combos(coarse_top3)
    remaining = [c for c in all_combos if combo_key(*c) not in done_keys]

    logger.info(f"Fine: total={len(all_combos)}, done={len(existing)}, remaining={len(remaining)}")

    t_start = time.time()
    processed = 0

    for variant, symbol, tf, params in remaining:
        if _stop_requested:
            logger.info("Stop requested")
            break
        try:
            cls = DonchianTrendFilter if variant == "B" else DonchianFixedRR
            strategy = cls(timeframe=tf, **params)
            result = run_backtest(strategy, db, symbol, tf, config, risk_config)
            record = result_to_dict(variant, symbol, tf, params, result)
        except Exception as exc:
            logger.error(f"Fine backtest failed: {variant} {symbol} {tf}: {exc}")
            record = error_record(variant, symbol, tf, params, str(exc))

        append_jsonl(jsonl_file, record)
        processed += 1

        elapsed = time.time() - t_start
        rate = processed / elapsed if elapsed > 0 else 0
        eta_min = (len(remaining) - processed) / rate / 60 if rate > 0 else 0

        if processed % 10 == 0 or processed == len(remaining):
            logger.info(
                f"Fine progress: {processed}/{len(remaining)} new | "
                f"{rate*60:.1f}/min | ETA {eta_min:.0f}min"
            )

    # Summary files
    all_results, _ = load_existing_jsonl(jsonl_file)

    with open(output_dir / "fine_all.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    # fine_best: Top 1 per variant/symbol/tf by sharpe
    best_map: Dict[Tuple, Dict] = {}
    for r in all_results:
        if "error" in r:
            continue
        key = (r["variant"], r["symbol"], r["tf"])
        if key not in best_map or r["sharpe"] > best_map[key]["sharpe"]:
            best_map[key] = r

    with open(output_dir / "fine_best.json", "w", encoding="utf-8") as f:
        json.dump(list(best_map.values()), f, indent=2)

    logger.info(f"Fine summary files written. Best count: {len(best_map)}")


# ==================== Stage 3: Walk-Forward ====================

def walkforward_stage(db, output_dir: Path, logger: logging.Logger, reset: bool):
    from src.backtester.walk_forward import WalkForwardAnalyzer
    from src.backtester.config import WalkForwardConfig

    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)

    fine_best_file = output_dir / "fine_best.json"
    if not fine_best_file.exists():
        logger.error("fine_best.json not found. Run fine stage first.")
        return

    with open(fine_best_file, "r", encoding="utf-8") as f:
        best_list = json.load(f)

    jsonl_file = output_dir / "walkforward_results.jsonl"
    if reset and jsonl_file.exists():
        jsonl_file.unlink()

    existing, done_keys = load_existing_jsonl(jsonl_file)
    remaining = [
        (e["variant"], e["symbol"], e["tf"], e["params"])
        for e in best_list
        if combo_key(e["variant"], e["symbol"], e["tf"], e["params"]) not in done_keys
    ]

    logger.info(f"Walk-Forward: total={len(best_list)}, done={len(existing)}, remaining={len(remaining)}")

    wf_config = WalkForwardConfig(is_months=6, oos_months=2, min_windows=3)

    for variant, symbol, tf, params in remaining:
        if _stop_requested:
            break
        try:
            cls = DonchianTrendFilter if variant == "B" else DonchianFixedRR
            mini_space = {k: [v] for k, v in params.items()}
            wf = WalkForwardAnalyzer(wf_config)
            feed = HistoricalDataFeed(db=db, symbols=[symbol], timeframe=tf)
            wf_result = wf.run(cls, mini_space, feed, config, symbol=symbol)

            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "windows": len(wf_result.windows),
                "avg_oos_retention": wf_result.avg_oos_retention,
                "avg_oos_sharpe": wf_result.avg_oos_sharpe,
                "oos_positive_pct": wf_result.oos_positive_pct,
            }
            logger.info(f"  WF {variant} {symbol} {tf}: "
                       f"ret={wf_result.avg_oos_retention:.1%} oos+={wf_result.oos_positive_pct:.1%}")
        except Exception as exc:
            logger.error(f"WF failed: {variant} {symbol} {tf}: {exc}")
            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "error": str(exc),
                "windows": 0, "avg_oos_retention": 0.0,
                "avg_oos_sharpe": 0.0, "oos_positive_pct": 0.0,
            }

        append_jsonl(jsonl_file, record)

    # Summary
    all_results, _ = load_existing_jsonl(jsonl_file)
    with open(output_dir / "walkforward.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    logger.info(f"Walk-Forward stage done. Records: {len(all_results)}")


# ==================== Stage 4: Overfit ====================

def overfit_stage(db, output_dir: Path, logger: logging.Logger, reset: bool):
    from src.backtester.overfit import OverfitDetector

    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    risk_config = RiskConfig(max_drawdown_pct=0.50, daily_loss_limit_pct=0.50, max_concurrent=10)

    fine_best_file = output_dir / "fine_best.json"
    fine_all_file = output_dir / "fine_all.json"
    if not (fine_best_file.exists() and fine_all_file.exists()):
        logger.error("Fine files not found. Run fine stage first.")
        return

    with open(fine_best_file, "r", encoding="utf-8") as f:
        fine_best = json.load(f)
    with open(fine_all_file, "r", encoding="utf-8") as f:
        fine_all = json.load(f)

    jsonl_file = output_dir / "overfit_results.jsonl"
    if reset and jsonl_file.exists():
        jsonl_file.unlink()

    existing, done_keys = load_existing_jsonl(jsonl_file)
    remaining = [
        (e["variant"], e["symbol"], e["tf"], e["params"])
        for e in fine_best
        if combo_key(e["variant"], e["symbol"], e["tf"], e["params"]) not in done_keys
    ]

    logger.info(f"Overfit: total={len(fine_best)}, done={len(existing)}, remaining={len(remaining)}")

    detector = OverfitDetector()

    for variant, symbol, tf, params in remaining:
        if _stop_requested:
            break
        try:
            cls = DonchianTrendFilter if variant == "B" else DonchianFixedRR
            s = cls(timeframe=tf, **params)
            result = run_backtest(s, db, symbol, tf, config, risk_config)
            pnl_list = [t.pnl for t in result.trades]

            scores = {}
            for fr in fine_all:
                if "error" in fr:
                    continue
                if fr["variant"] == variant and fr["symbol"] == symbol and fr["tf"] == tf:
                    scores[str(fr["params"])] = fr["sharpe"]

            verdict = detector.detect(pnl_list, scores, n_shuffles=500)
            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "verdict": verdict.verdict,
                "p_value": verdict.p_value,
                "sensitivity": verdict.sensitivity,
                "reason": verdict.reason,
            }
            logger.info(f"  Overfit {variant} {symbol} {tf}: {verdict.verdict} "
                       f"p={verdict.p_value:.3f} sens={verdict.sensitivity:.2f}")
        except Exception as exc:
            logger.error(f"Overfit failed: {variant} {symbol} {tf}: {exc}")
            record = {
                "variant": variant, "symbol": symbol, "tf": tf, "params": params,
                "error": str(exc), "verdict": "ERROR",
                "p_value": 1.0, "sensitivity": 1.0, "reason": str(exc),
            }

        append_jsonl(jsonl_file, record)

    all_results, _ = load_existing_jsonl(jsonl_file)
    with open(output_dir / "overfit.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    logger.info(f"Overfit stage done. Records: {len(all_results)}")


# ==================== Status ====================

def cmd_status(output_dir: Path):
    """Print current exploration status."""
    pid_file = output_dir / "explore.pid"
    heartbeat_file = output_dir / "heartbeat.txt"

    print(f"=== Donchian Exploration Status ===")
    print(f"Output dir: {output_dir}")

    if pid_file.exists():
        pid = int(pid_file.read_text().strip())
        alive = is_process_alive(pid)
        print(f"PID: {pid} ({'ALIVE' if alive else 'DEAD'})")
    else:
        print("PID file: not found (no active run)")

    if heartbeat_file.exists():
        hb_ts = float(heartbeat_file.read_text().strip())
        age = time.time() - hb_ts
        print(f"Heartbeat: {age:.0f}s ago")
    else:
        print("Heartbeat: not found")

    print()
    for stage in ["coarse", "fine", "walkforward", "overfit"]:
        jsonl = output_dir / f"{stage}_results.jsonl"
        if jsonl.exists():
            count = sum(1 for _ in open(jsonl, "r", encoding="utf-8"))
            print(f"  {stage}: {count} records")
        else:
            print(f"  {stage}: 0 records")


# ==================== Main ====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["coarse", "fine", "walkforward", "overfit", "all", "status"])
    parser.add_argument("--reset", action="store_true", help="Delete existing results and start fresh")
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "logs" / "research" / "donchian"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.stage == "status":
        cmd_status(output_dir)
        return

    log_file = output_dir / "explore.log"
    pid_file = output_dir / "explore.pid"
    heartbeat_file = output_dir / "heartbeat.txt"

    logger = setup_logger(log_file)
    logger.info(f"=== Starting stage: {args.stage} (reset={args.reset}) ===")
    logger.info(f"PID: {os.getpid()}")

    write_pid_file(pid_file)
    install_signal_handler(logger)
    start_heartbeat_thread(heartbeat_file)

    try:
        db = DBManager(db_path=str(PROJECT_ROOT / "db" / "bybit_data.db"))

        if args.stage in ("coarse", "all"):
            coarse_search(db, output_dir, logger, args.reset)
        if args.stage in ("fine", "all") and not _stop_requested:
            fine_search(db, output_dir, logger, args.reset)
        if args.stage in ("walkforward", "all") and not _stop_requested:
            walkforward_stage(db, output_dir, logger, args.reset)
        if args.stage in ("overfit", "all") and not _stop_requested:
            overfit_stage(db, output_dir, logger, args.reset)

        logger.info("=== All requested stages completed ===")
    except Exception as exc:
        logger.exception(f"Unhandled error: {exc}")
        raise
    finally:
        remove_pid_file(pid_file)
        logger.info("Exit cleanup done")


if __name__ == "__main__":
    main()
