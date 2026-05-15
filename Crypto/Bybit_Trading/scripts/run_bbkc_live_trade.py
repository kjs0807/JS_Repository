"""BBKC trade-runner wrapper (Stage A-2 compatibility layer).

Historically this was the BBKC-only live demo runner. Stage A-2 split the
strategy-agnostic runtime out into :mod:`scripts.run_strategy_trade` and
:mod:`src.runtime.strategy_runner`. This script is preserved as a thin
wrapper so older runbooks, cron jobs and operator muscle-memory keep
working:

    python -m scripts.run_bbkc_live_trade --run-id <id>

is equivalent to::

    python -m scripts.run_strategy_trade --run-id <id> \\
        --strategy BBKCSqueeze \\
        --universe BTCUSDT ETHUSDT AVAXUSDT \\
        --timeframe 1h \\
        --root-out-dir logs/live_demo/bbkc_bigthree

with the same demo/live safety gates. Anything the operator passes on
the CLI (``--mode``, ``--i-understand-real-money``, ``--warmup-days``,
``--stop-at``, ``--stop-in-minutes``, ``--root-out-dir``, the deprecated
``--force-live``) is forwarded verbatim to the generic runner.

The BBKC strategy parameters come from ``strategies.BBKCSqueeze.params``
in ``config.yaml``; absent that block, the legacy ``bbkc_exit`` section
is used as a fallback. See :func:`scripts.run_strategy_trade._strategy_params_from_config`.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.mode import VALID_MODES   # noqa: E402

logger = logging.getLogger(__name__)

# Legacy default universe; the generic runner reads from
# ``trading.universe`` when no --universe is given. We pass it explicitly
# from the wrapper so the BBKC entry-point keeps its historical behaviour
# regardless of what ``trading.universe`` has been changed to.
BIGTHREE = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
DEFAULT_ROOT_OUT_DIR = "logs/live_demo/bbkc_bigthree"
DEFAULT_TIMEFRAME = "1h"
STRATEGY = "BBKCSqueeze"


def _build_wrapper_parser() -> argparse.ArgumentParser:
    """Legacy BBKC CLI surface. New ``--strategy``/``--universe``/
    ``--timeframe`` are intentionally NOT exposed here - this wrapper
    locks them to the BBKC defaults. Use ``run_strategy_trade.py``
    directly if you need to vary them."""
    parser = argparse.ArgumentParser(
        description=(
            "BBKC trade-runner wrapper. Forwards to scripts.run_strategy_trade "
            "with --strategy BBKCSqueeze and the legacy BIGTHREE universe."
        ),
    )
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--stop-at", type=str, default=None)
    parser.add_argument("--stop-in-minutes", type=int, default=None)
    parser.add_argument(
        "--root-out-dir", type=Path,
        default=Path(PROJECT_ROOT / DEFAULT_ROOT_OUT_DIR),
    )
    parser.add_argument(
        "--mode", choices=list(VALID_MODES), default=None,
    )
    parser.add_argument(
        "--i-understand-real-money", action="store_true",
    )
    parser.add_argument(
        "--force-live", action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _to_generic_argv(args: argparse.Namespace) -> List[str]:
    """Translate parsed wrapper args into the generic CLI argv list."""
    argv: List[str] = ["--run-id", args.run_id]
    argv += ["--strategy", STRATEGY]
    argv += ["--universe", *BIGTHREE]
    argv += ["--timeframe", DEFAULT_TIMEFRAME]
    argv += ["--warmup-days", str(args.warmup_days)]
    argv += ["--root-out-dir", str(args.root_out_dir)]
    if args.stop_at:
        argv += ["--stop-at", args.stop_at]
    if args.stop_in_minutes is not None:
        argv += ["--stop-in-minutes", str(args.stop_in_minutes)]
    if args.mode:
        argv += ["--mode", args.mode]
    if args.i_understand_real_money:
        argv += ["--i-understand-real-money"]
    if args.force_live:
        argv += ["--force-live"]
    return argv


def _enforce_round5_guard(args: argparse.Namespace) -> Optional[int]:
    """Round 5 forward operations forbid --stop-at / --stop-in-minutes
    when ``BBKC_ROUND5_MODE=true`` is set. Returns a non-zero exit code
    when the guard is violated, ``None`` otherwise."""
    if os.getenv("BBKC_ROUND5_MODE", "").lower() != "true":
        return None
    if args.stop_at or args.stop_in_minutes is not None:
        print(
            "ERROR: BBKC_ROUND5_MODE=true forbids --stop-at and "
            "--stop-in-minutes (per round 5 design 2.3). "
            "Unset BBKC_ROUND5_MODE for smoke tests."
        )
        return 2
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_wrapper_parser()
    args = parser.parse_args(argv)
    rc = _enforce_round5_guard(args)
    if rc is not None:
        return rc
    # Forward to the generic runner.
    from scripts.run_strategy_trade import main as generic_main
    return generic_main(_to_generic_argv(args))


if __name__ == "__main__":
    raise SystemExit(main())
