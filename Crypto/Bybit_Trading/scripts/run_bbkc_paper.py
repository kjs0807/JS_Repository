"""Paper trading entrypoint for BBKCSqueeze[BIGTHREE].

This is the first paper-trading runnable in the project. It is
intentionally **replay-based**: we feed historical 1h bars from the
local DB into a ``PaperBroker`` + ``PaperRunner`` + ``BBKCSqueeze``
pipeline and persist the resulting portfolio state so the exact run
can be audited or resumed.

Why replay-only for now
-----------------------
A live ws loop needs:
- bar-close detection on a websocket stream
- kline resample consistency across 3 symbols
- retry / reconnect / heartbeat / alerting
That is a separate follow-up. The current script delivers a
deterministic, reproducible paper session that exercises the
PaperBroker code path end-to-end. Once this is stable we can add a
``LivePaperRunner`` subclass without touching the broker.

Safety rails
------------
- Universe is fixed to ``BIGTHREE = [BTCUSDT, ETHUSDT, AVAXUSDT]`` at
  the broker level. Any strategy code call on another symbol is
  blocked and logged.
- No Bybit REST client is instantiated. This script imports
  ``PaperBroker``, not ``LiveBroker``, so a network misconfig cannot
  accidentally submit real orders.
- The BBKCSqueeze instance uses its default parameters (P5: entry
  logic unchanged).
- Run artifacts land in ``logs/paper/bbkc_bigthree/<run_id>/``:
    - ``paper_state.json``
    - ``signals.jsonl``
    - ``fills.jsonl``
    - ``equity_curve.csv``

Usage
-----

    # default: last 14 days holdout + 14d warmup, BIGTHREE
    python -m scripts.run_bbkc_paper

    # explicit window + run id
    python -m scripts.run_bbkc_paper \\
        --start 2026-03-25 --end 2026-04-10 \\
        --run-id 2026-03-25_2026-04-10

    # smoke (only 3 days)
    python -m scripts.run_bbkc_paper --start 2026-04-07 --end 2026-04-10 \\
        --run-id smoke
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import BacktestConfig, RiskConfig, load_config
from src.data_manager.db import DBManager
from src.evaluation.holdout import HoldoutSpec
from src.execution.paper_broker import PaperBroker
from src.execution.paper_runner import PaperRunner
from src.strategies.bbkc_squeeze import BBKCSqueeze


BIGTHREE = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Paper trading BBKCSqueeze on BIGTHREE universe.",
    )
    parser.add_argument(
        "--symbols", nargs="*", default=BIGTHREE,
        help="Universe (default = BIGTHREE)",
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="Holdout start YYYY-MM-DD (default: 14 days ago)",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="Holdout end YYYY-MM-DD (default: today)",
    )
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Override run directory name (default: timestamp)",
    )
    parser.add_argument(
        "--root-out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "paper" / "bbkc_bigthree",
    )
    parser.add_argument(
        "--checkpoint-every", type=int, default=200,
        help="Save state every N processed bars",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Prepare and exit without running the bar loop",
    )
    args = parser.parse_args()

    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    end_dt = _parse_date(args.end) if args.end else today
    start_dt = (
        _parse_date(args.start) if args.start else end_dt - timedelta(days=14)
    )
    if end_dt <= start_dt:
        print("ERROR: --end must be strictly after --start")
        return 1

    run_id = args.run_id or (
        f"{start_dt:%Y-%m-%d}_{end_dt:%Y-%m-%d}_"
        f"{datetime.now(timezone.utc):%H%M%S}"
    )
    run_dir = args.root_out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Guard: never allow symbols outside the default universe unless
    # the user explicitly passed a different --symbols list. Doing this
    # at CLI-time doubles up the universe guard inside PaperBroker.
    universe = list(args.symbols)
    disallowed = set(universe) - set(BIGTHREE)
    if disallowed:
        print(
            "WARNING: universe overrides the default BIGTHREE with "
            f"extra symbols: {sorted(disallowed)}. "
            "PaperBroker will still block anything not in this list."
        )

    spec = HoldoutSpec(
        symbols=universe,
        timeframe="1h",
        holdout_start_dt=start_dt,
        holdout_end_dt=end_dt,
        warmup_days=args.warmup_days,
        initial_capital=args.initial_capital,
    )

    print(f"=== BBKC[BIGTHREE] paper run ===")
    print(f"  run_id   : {run_id}")
    print(f"  run_dir  : {run_dir}")
    print(f"  universe : {universe}")
    print(f"  window   : {start_dt:%Y-%m-%d} -> {end_dt:%Y-%m-%d}")
    print(f"  warmup   : {args.warmup_days} days")
    print(f"  capital  : ${args.initial_capital:,.0f}")

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    broker = PaperBroker(
        config=BacktestConfig(initial_capital=args.initial_capital),
        risk_config=RiskConfig(),
        run_dir=run_dir,
        symbols_allowed=universe,
        run_id=run_id,
    )

    # If a previous state file exists in the same run_dir, load it and
    # restore positions. The user can either resume or start a fresh
    # run by choosing a new --run-id.
    prev = broker.load_state()
    if prev is not None:
        broker.restore_from_state(prev)
        print(
            f"  resumed from prior state: "
            f"equity={prev.get('equity_incl_unrealized')} "
            f"positions={len(prev.get('positions', []))}"
        )

    runner = PaperRunner(
        strategy_factory=lambda: BBKCSqueeze(),
        broker=broker,
        spec=spec,
        db=db,
        checkpoint_every_bars=args.checkpoint_every,
    )

    if args.dry_run:
        print("dry-run: broker + runner constructed. Exiting.")
        broker.save_state(extra={"dry_run": True})
        return 0

    stats = runner.run()
    print()
    print("=== paper run summary ===")
    print(f"  bars processed   : {stats.bars_processed}")
    print(f"  per symbol bars  : {stats.per_symbol_bars}")
    print(f"  signals logged   : {stats.signals_logged}")
    final_state = broker.load_state()
    if final_state:
        print(
            f"  equity (incl u)  : "
            f"{final_state.get('equity_incl_unrealized'):+.2f}"
        )
        print(f"  realized pnl     : {final_state.get('realized_pnl'):+.2f}")
        print(
            f"  open positions   : "
            f"{final_state.get('n_open_positions')}"
        )
        print(f"  trades total     : {final_state.get('trades_total')}")
    print(f"  state file       : {run_dir / 'paper_state.json'}")
    print(f"  fills log        : {run_dir / 'fills.jsonl'}")
    print(f"  equity curve     : {run_dir / 'equity_curve.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
