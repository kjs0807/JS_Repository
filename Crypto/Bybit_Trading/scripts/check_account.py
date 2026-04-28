"""Bybit demo account status query — equivalent of legacy `check.py`.

Prints:
- all open positions (symbol, side, qty, entry, mark, uPnL, TP/SL)
- wallet equity / available balance

Requires ``BYBIT_API_KEY`` and ``BYBIT_API_SECRET`` in environment.

Usage::

    python -m scripts.check_account
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.rest_client import BybitRestClient
from src.core.config import load_config


def main() -> int:
    cfg = load_config(str(PROJECT_ROOT / "config.yaml"))
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    if not api_key or not api_secret:
        print("ERROR: BYBIT_API_KEY / BYBIT_API_SECRET not set in environment.")
        return 1

    rest = BybitRestClient(api_key, api_secret, cfg.app.base_url)

    print(f"base_url: {cfg.app.base_url}")
    print()

    # Positions
    try:
        positions = rest.get_positions()
    except Exception as exc:
        print(f"get_positions failed: {exc}")
        positions = []

    if positions:
        print("=== Positions ===")
        for p in positions:
            size = float(p.get("size", 0) or 0)
            if size <= 0:
                continue
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
    else:
        print("=== Positions ===")
        print("  (none)")

    # Wallet
    print()
    try:
        bal = rest.get_wallet_balance()
    except Exception as exc:
        print(f"get_wallet_balance failed: {exc}")
        bal = {}
    if bal:
        print("=== Wallet ===")
        print(f"  equity     : {bal.get('equity', 0):.2f} USDT")
        print(f"  available  : {bal.get('available', 0):.2f} USDT")
    else:
        print("=== Wallet ===")
        print("  (no balance info returned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
