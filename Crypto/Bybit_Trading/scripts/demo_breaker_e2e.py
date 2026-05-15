"""Operator-facing pre-live smoke: breaker → kill switch → block flow.

Runs the same failure-classification → circuit-breaker → kill-switch →
order-block chain that ``test_breaker_kill_switch_e2e.py`` exercises,
but with verbose stdout output and against a fresh tmp directory so an
operator can visually confirm the safety stack BEFORE flipping to live.

This script:
  * Touches NO real Bybit endpoint.
  * Creates a throw-away run directory under ``<root_out_dir>/_e2e_demo/<ts>``.
  * Prints a step-by-step transcript of every state change.
  * Returns a non-zero exit code if any expected invariant fails.

Run::

    python -m scripts.demo_breaker_e2e

Optional flags::

    --root-out-dir PATH    Where to put the throw-away run directory.
    --keep-run-dir         Leave the run directory on disk for inspection
                           (default: remove on success).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import RiskConfig
from src.execution.bbkc_demo_broker import BbkcBroker
from src.execution.broker import Position
from src.runtime.circuit_breaker import CircuitBreaker
from src.runtime.kill_switch import FLAG_FILENAME, KillSwitch
from src.runtime.order_failure import ALL_CATEGORIES, OrderFailureCategory
from src.runtime.order_logger import (
    OrderLogger,
    RESULT_EXCHANGE_REJECT,
    RESULT_KILL_SWITCH_BLOCK,
    RESULT_SUCCESS,
)


def _step(msg: str) -> None:
    print(f"[STEP] {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"[ OK ] {msg}", flush=True)


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", flush=True)


def _ensure(cond: bool, msg: str) -> None:
    if cond:
        _ok(msg)
    else:
        _fail(msg)
        raise SystemExit(2)


def _read_rows(path: Path):
    return [
        json.loads(l)
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


def _build_demo_broker(run_dir: Path):
    """Same wiring shape as ``scripts/run_strategy_trade.py``, just
    with a MagicMock REST client. Real CircuitBreaker, real KillSwitch,
    real OrderLogger so audit artefacts hit disk."""
    run_dir.mkdir(parents=True, exist_ok=True)

    kill_switch = KillSwitch(run_dir=run_dir)
    alert = MagicMock()
    circuit_breaker = CircuitBreaker(
        kill_switch=kill_switch,
        alert_manager=alert,
        window_seconds=3600.0,
        failure_rate_threshold=0.10,
        min_sample=5,
        min_failures=2,
    )
    order_logger = OrderLogger(run_dir / "orders.jsonl")

    broker = BbkcBroker.__new__(BbkcBroker)
    broker._rest = MagicMock()
    broker._alert = alert
    broker._risk = MagicMock()
    broker._risk.config = RiskConfig()
    broker._risk.daily_pnl = 0.0
    broker._risk.check_order = MagicMock(
        return_value=MagicMock(action="ALLOW", reason=""),
    )
    broker._leverage = 3
    broker._initial_capital = 50_000.0
    broker._positions = {}
    broker._equity = 50_000.0
    broker._run_dir = run_dir
    broker._orders_path = run_dir / "orders.jsonl"
    broker._symbols_allowed = {"BTCUSDT", "ETHUSDT"}
    broker._qty_step = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._min_qty = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
    broker._per_symbol_max_pos_pct = {}
    broker._kill_switch = kill_switch
    broker._failure_counters = {c: 0 for c in ALL_CATEGORIES}
    broker._success_count = 0
    broker._circuit_breaker = circuit_breaker
    broker._order_logger = order_logger
    broker._kill_switch_ref = kill_switch
    broker._last_bar_close = {}
    broker._fill_tracker = None
    broker._fill_logger = None
    return broker, circuit_breaker, kill_switch, alert


def _run_scenario(run_dir: Path) -> None:
    print("=" * 66)
    print("Bybit safety stack pre-live smoke")
    print(f"  run_dir   : {run_dir}")
    print(f"  threshold : 10% failure rate")
    print(f"  min_sample: 5")
    print(f"  min_fail  : 2")
    print("=" * 66)

    broker, cb, ks, alert = _build_demo_broker(run_dir)

    # ------------------------------------------------------------------
    # Phase 1: drive 5 exchange failures into the breaker.
    # ------------------------------------------------------------------
    _step("Phase 1: 5 simulated retCode=110012 (MIN_QTY) failures...")
    broker._rest.place_order.side_effect = RuntimeError(
        "ErrCode: 110012, ErrMsg: Order qty lower than the minimum order qty"
    )
    for i in range(5):
        oid = broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0)
        _ensure(oid == "", f"  buy #{i + 1} returned '' (rejected by exchange)")

    _ensure(cb.tripped, "CircuitBreaker.tripped == True after 5 failures")
    _ensure(
        (run_dir / FLAG_FILENAME).exists(),
        f"kill switch flag file written: {(run_dir / FLAG_FILENAME).name}",
    )
    _ensure(
        ks.is_new_entry_disabled(),
        "KillSwitch.is_new_entry_disabled() == True",
    )
    stats = cb.stats()
    _ensure(
        stats["top_category"] == OrderFailureCategory.MIN_QTY,
        f"breaker top_category = {stats['top_category']}",
    )
    _ensure(
        alert.on_breaker_tripped.call_count == 1,
        f"on_breaker_tripped alert fired exactly once "
        f"(actual: {alert.on_breaker_tripped.call_count})",
    )

    # ------------------------------------------------------------------
    # Phase 2: next buy is blocked BEFORE reaching the REST layer.
    # ------------------------------------------------------------------
    _step("Phase 2: next buy() must be blocked by kill switch...")
    prev_rest_calls = broker._rest.place_order.call_count
    oid = broker.buy("BTCUSDT", 0.01, stop_loss=70_000.0)
    _ensure(oid == "", "  blocked buy returns ''")
    _ensure(
        broker._rest.place_order.call_count == prev_rest_calls,
        "  REST place_order NOT called for the blocked buy",
    )

    # ------------------------------------------------------------------
    # Phase 3: existing position management must still pass through.
    # ------------------------------------------------------------------
    _step("Phase 3: close() / update_stop / update_tp survive kill switch...")
    # Seed a position to close.
    broker._positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="LONG", qty=0.01,
        entry_price=70_000.0, entry_time=0,
        stop_loss=68_000.0, take_profit=75_000.0,
        unrealized_pnl=0.0, strategy_name="STRATEGY",
    )
    broker._rest.place_order.side_effect = None
    broker._rest.place_order.return_value = {"orderId": "CLOSE-1"}
    close_oid = broker.close("BTCUSDT", reason="trail")
    _ensure(close_oid == "CLOSE-1", "  close() returned orderId despite kill switch")
    _ensure(
        "BTCUSDT" not in broker._positions,
        "  position cleared locally on successful close",
    )

    # Re-seed for SL/TP updates.
    broker._positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="LONG", qty=0.01,
        entry_price=70_000.0, entry_time=0,
        stop_loss=68_000.0, take_profit=75_000.0,
        unrealized_pnl=0.0, strategy_name="STRATEGY",
    )
    broker.update_stop("BTCUSDT", new_stop=69_000.0)
    broker.update_tp("BTCUSDT", new_tp=76_000.0)
    _ensure(
        broker._positions["BTCUSDT"].stop_loss == 69_000.0,
        "  update_stop() applied (kill switch did not block)",
    )
    _ensure(
        broker._positions["BTCUSDT"].take_profit == 76_000.0,
        "  update_tp() applied (kill switch did not block)",
    )

    # ------------------------------------------------------------------
    # Phase 4: orders.jsonl audit content.
    # ------------------------------------------------------------------
    _step("Phase 4: orders.jsonl audit content...")
    rows = _read_rows(run_dir / "orders.jsonl")
    rejects = [r for r in rows if r["result"] == RESULT_EXCHANGE_REJECT]
    blocks = [r for r in rows if r["result"] == RESULT_KILL_SWITCH_BLOCK]
    successes = [r for r in rows if r["result"] == RESULT_SUCCESS]
    _ensure(len(rejects) == 5, f"  5 exchange_reject rows (got {len(rejects)})")
    _ensure(len(blocks) == 1, f"  1 kill_switch_block row (got {len(blocks)})")
    _ensure(
        len(successes) >= 1,
        f"  >=1 success row (close orderId logged; got {len(successes)})",
    )
    for r in rejects:
        _ensure(
            r["failure_category"] == OrderFailureCategory.MIN_QTY,
            "  reject row classified MIN_QTY",
        )
        _ensure(
            r["breaker_eligible"] is True,
            "  reject row breaker_eligible == True",
        )
    _ensure(
        blocks[0]["breaker_eligible"] is False,
        "  block row breaker_eligible == False",
    )
    _ensure(
        blocks[0]["kill_switch_engaged"] is True,
        "  block row kill_switch_engaged == True",
    )

    print()
    print("=" * 66)
    print("ALL INVARIANTS PASSED.")
    print("Inspect artefacts:")
    print(f"  orders.jsonl : {run_dir / 'orders.jsonl'}")
    print(f"  kill flag    : {run_dir / FLAG_FILENAME}")
    print("=" * 66)


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pre-live safety-stack smoke. Drives the breaker → "
            "kill switch → block flow without touching a real exchange."
        ),
    )
    parser.add_argument(
        "--root-out-dir", type=Path, default=PROJECT_ROOT / "logs" / "_e2e_demo",
        help="Parent directory for the throw-away run dir.",
    )
    parser.add_argument(
        "--keep-run-dir", action="store_true",
        help="Leave the run dir on disk for manual inspection.",
    )
    args = parser.parse_args(argv)

    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    run_dir = Path(args.root_out_dir) / ts
    try:
        _run_scenario(run_dir)
    except SystemExit:
        raise
    except Exception as exc:
        _fail(f"unexpected exception: {exc}")
        return 2

    if not args.keep_run_dir:
        import shutil
        try:
            shutil.rmtree(run_dir)
            print(f"[CLEAN] removed throw-away run dir {run_dir}")
        except Exception as exc:
            print(f"[CLEAN] failed to remove {run_dir}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
