"""Bybit demo/live account status query.

Prints:

  * all open positions (symbol, side, qty, entry, mark, uPnL, TP/SL)
  * wallet equity / available balance
  * the API-key fingerprint (never the full secret)

Stage C-2c rewrite: uses :mod:`src.core.mode` so demo vs live is
resolved the same way :mod:`scripts.run_strategy_trade` does. Legacy
``BYBIT_API_KEY`` / ``BYBIT_API_SECRET`` env vars are no longer
honoured anywhere on this path; set per-mode credentials in ``.env``
(see ``.env.example``).

Usage::

    # Demo (config app.mode = "demo")
    python -m scripts.check_account

    # Mainnet - requires explicit acknowledgement
    python -m scripts.check_account --mode live --i-understand-real-money
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.rest_client import BybitRestClient
from src.core.config import load_config
from src.core.mode import (
    MODE_LIVE,
    VALID_MODES,
    ModeError,
    fingerprint,
    resolve_runtime,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_account",
        description=(
            "Read-only Bybit account status. Demo by default; live "
            "requires --mode live + --i-understand-real-money."
        ),
    )
    parser.add_argument(
        "--mode", choices=list(VALID_MODES), default=None,
        help="Override app.mode in config.",
    )
    parser.add_argument(
        "--i-understand-real-money", action="store_true",
        help="Required when --mode resolves to 'live'.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = load_config(str(PROJECT_ROOT / "config.yaml"))

    try:
        mode, base_url, api_key, api_secret = resolve_runtime(
            config_mode=cfg.app.mode,
            cli_mode=args.mode,
            ack=args.i_understand_real_money,
        )
    except ModeError as exc:
        print(f"ERROR: {exc}")
        return 1

    rest = BybitRestClient(api_key, api_secret, base_url)

    print(f"mode      : {mode}")
    print(f"base_url  : {base_url}")
    print(f"api key   : {fingerprint(api_key)}")
    if mode == MODE_LIVE:
        print("*** LIVE / MAINNET - read-only, but you are looking at real money ***")
    print()

    # Positions
    try:
        positions = rest.get_positions()
    except Exception as exc:
        print(f"get_positions failed: {exc}")
        positions = []

    print("=== Positions ===")
    open_count = 0
    if positions:
        for p in positions:
            size = float(p.get("size", 0) or 0)
            if size <= 0:
                continue
            open_count += 1
            side = p.get("side", "?")
            symbol = p.get("symbol", "?")
            entry = p.get("avgPrice", "?")
            mark = p.get("markPrice", "?")
            upnl = p.get("unrealisedPnl", "?")
            tp = p.get("takeProfit", "-") or "-"
            sl = p.get("stopLoss", "-") or "-"
            print(
                f"  {symbol:10s} {side:4s} qty={size} entry={entry} "
                f"mark={mark} uPnL={upnl} TP={tp} SL={sl}"
            )
    if open_count == 0:
        print("  (none)")

    # Wallet
    print()
    try:
        bal = rest.get_wallet_balance()
    except Exception as exc:
        print(f"get_wallet_balance failed: {exc}")
        bal = {}
    print("=== Wallet ===")
    if bal:
        print(f"  equity     : {bal.get('equity', 0):.2f} USDT")
        print(f"  available  : {bal.get('available', 0):.2f} USDT")
    else:
        print("  (no balance info returned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
