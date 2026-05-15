"""BBKC universe experiment - Bybit Demo minimum-valid-qty order feasibility test.

For each candidate symbol:
  1. Fetch instrument specs (qty_step, min_qty, min_notional, tick_size).
  2. Fetch current price.
  3. Compute the minimum *valid* order qty:
       - default: min_qty
       - if min_qty * price < min_notional:
             min_valid_qty = ceil((min_notional / price) / qty_step) * qty_step
       - formatted to qty_step's decimal places (qty_step == 1.0 -> integer).
  4. Place a Market BUY for min_valid_qty (hedge-mode positionIdx auto-detected).
  5. Place a reduceOnly Market SELL for the same qty.
  6. Confirm no position is left open.

Hard safety rails:
  - Resolves credentials via :mod:`src.core.mode` so this script can
    NEVER accidentally hit mainnet - the demo endpoint is hard-coded
    and the resolver is forced into demo mode. Legacy
    ``BYBIT_BASE_URL`` / ``BYBIT_API_KEY`` env vars are not honoured.
  - After all symbols, re-checks every position; if anything is open it
    prints a RED ALERT and exits non-zero (caller must stop the experiment).

Usage:
    python -m scripts.bbkc_universe_order_test
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.mode import (
    BASE_URL_BY_MODE, MODE_DEMO, ModeError,
    fingerprint, resolve_runtime,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "AVAXUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT"]
DEMO_HOST = "api-demo.bybit.com"


def _qty_decimals(qty_step: float) -> int:
    s = ("%.10f" % qty_step).rstrip("0")
    if "." not in s:
        return 0
    return len(s.split(".", 1)[1])


def _fmt_qty(qty: float, qty_step: float) -> str:
    dec = _qty_decimals(qty_step)
    if dec == 0:
        return str(int(round(qty)))
    return f"{qty:.{dec}f}"


def _classify_failure(msg: str) -> str:
    m = (msg or "").lower()
    if "qtystep" in m or "qty step" in m or "multiple" in m or "decimal" in m:
        return "qty_step"
    if "notional" in m or "min order value" in m or "too small" in m:
        return "min_notional"
    if "minorderqty" in m or "qty too small" in m or "min qty" in m or "lower than" in m:
        return "min_qty"
    if "position idx" in m or "position mode" in m:
        return "position_mode"
    return "other"


def main() -> int:
    # C-2c: resolve mode through src.core.mode. We FORCE demo here
    # because this script intentionally only runs against the Bybit
    # demo endpoint - no CLI override, no env-var override, no
    # i-understand-real-money escape hatch is exposed. Anyone wanting
    # to live-test should use scripts.run_strategy_trade, not this.
    try:
        mode, base_url, api_key, api_secret = resolve_runtime(
            config_mode=MODE_DEMO,
            cli_mode=MODE_DEMO,
            ack=False,
        )
    except ModeError as exc:
        print(f"ABORT: {exc}")
        return 2

    print("=" * 70)
    print("BBKC universe - Bybit Demo order feasibility test")
    print(f"  mode      = {mode}")
    print(f"  base_url  = {base_url}")
    print(f"  api key   = {fingerprint(api_key)}")
    if mode != MODE_DEMO or DEMO_HOST not in base_url:
        # Belt-and-braces guard: even if resolve_runtime returned demo
        # somehow without the demo host, refuse to send any order.
        print(
            f"  ABORT: resolver did not produce the demo endpoint "
            f"({DEMO_HOST}). Refusing to place any order.",
        )
        return 2
    print("  demo endpoint confirmed -> proceeding.")
    print("=" * 70)

    from pybit.unified_trading import HTTP
    s = HTTP(api_key=api_key, api_secret=api_secret, demo=True)

    # --- detect position mode (hedge vs one-way) from an existing symbol
    pos_idx_long = 1  # hedge-mode default; codebase assumes hedge
    try:
        r = s.get_positions(category="linear", symbol="BTCUSDT")
        lst = r["result"]["list"]
        idxs = sorted({int(p.get("positionIdx", 0)) for p in lst})
        if idxs == [0]:
            pos_idx_long = 0
            print("  position mode: ONE-WAY (positionIdx=0)")
        else:
            pos_idx_long = 1
            print(f"  position mode: HEDGE (positionIdx in {idxs}) -> using 1 for LONG")
    except Exception as exc:
        print(f"  (could not detect position mode: {exc}; assuming HEDGE, idx=1)")

    # --- pull instrument spec per symbol (linear has >500 instruments;
    #     the unfiltered list is paginated, so query each symbol directly)
    instr_map: Dict[str, Dict[str, Any]] = {}
    for sym in SYMBOLS:
        try:
            raw = s.get_instruments_info(category="linear", symbol=sym)
            lst = raw.get("result", {}).get("list", [])
            if lst:
                instr_map[sym] = lst[0]
        except Exception as exc:
            print(f"  instruments_info({sym}) failed: {exc}")

    rows: List[Dict[str, Any]] = []
    for sym in SYMBOLS:
        row: Dict[str, Any] = {"symbol": sym}
        it = instr_map.get(sym)
        if not it:
            row.update({"error": "not in instruments_info"})
            rows.append(row)
            continue
        lot = it.get("lotSizeFilter", {})
        pricef = it.get("priceFilter", {})
        qty_step = float(lot.get("qtyStep", "0"))
        min_qty = float(lot.get("minOrderQty", "0"))
        min_notional = float(lot.get("minNotionalValue", "0") or 0)
        tick_size = float(pricef.get("tickSize", "0"))
        # current price
        tk = s.get_tickers(category="linear", symbol=sym)
        price = float(tk["result"]["list"][0]["lastPrice"])

        min_qty_notional = min_qty * price
        if min_notional > 0 and min_qty_notional < min_notional:
            raw_q = (min_notional / price) / qty_step
            min_valid_qty = math.ceil(raw_q) * qty_step
        else:
            min_valid_qty = min_qty
        qty_str = _fmt_qty(min_valid_qty, qty_step)
        order_notional = float(qty_str) * price

        row.update({
            "qty_step": qty_step, "min_qty": min_qty, "min_notional": min_notional,
            "tick_size": tick_size, "price": price,
            "min_qty_notional": round(min_qty_notional, 4),
            "min_valid_qty": qty_str, "min_valid_qty_notional": round(order_notional, 4),
        })

        # --- BUY (market)
        buy_ok = False
        buy_err = ""
        try:
            resp = s.place_order(category="linear", symbol=sym, side="Buy",
                                 orderType="Market", qty=qty_str,
                                 positionIdx=pos_idx_long)
            if resp.get("retCode") == 0:
                buy_ok = True
            else:
                buy_err = resp.get("retMsg", "?")
        except Exception as exc:
            buy_err = str(exc)
        row["buy_ok"] = buy_ok
        row["buy_err"] = buy_err
        row["buy_fail_class"] = "" if buy_ok else _classify_failure(buy_err)

        time.sleep(1.5)
        # position after buy
        pos_after_buy = 0.0
        try:
            r = s.get_positions(category="linear", symbol=sym)
            for p in r["result"]["list"]:
                if int(p.get("positionIdx", 0)) == pos_idx_long:
                    pos_after_buy = float(p.get("size", 0) or 0)
        except Exception as exc:
            row["pos_after_buy_err"] = str(exc)
        row["pos_after_buy"] = pos_after_buy

        # --- SELL reduceOnly (market) - only if we actually have a position
        sell_ok = False
        sell_err = ""
        if pos_after_buy > 0:
            sell_qty = _fmt_qty(pos_after_buy, qty_step)
            try:
                resp = s.place_order(category="linear", symbol=sym, side="Sell",
                                     orderType="Market", qty=sell_qty,
                                     positionIdx=pos_idx_long, reduceOnly=True)
                if resp.get("retCode") == 0:
                    sell_ok = True
                else:
                    sell_err = resp.get("retMsg", "?")
            except Exception as exc:
                sell_err = str(exc)
        else:
            sell_err = "skipped (no position after buy)"
        row["sell_ok"] = sell_ok
        row["sell_err"] = sell_err
        row["sell_fail_class"] = "" if sell_ok else _classify_failure(sell_err)

        time.sleep(1.5)
        # final position
        pos_final = 0.0
        try:
            r = s.get_positions(category="linear", symbol=sym)
            for p in r["result"]["list"]:
                pos_final += float(p.get("size", 0) or 0)
        except Exception as exc:
            row["pos_final_err"] = str(exc)
        row["pos_final_open"] = pos_final
        row["candidate_ok"] = bool(buy_ok and sell_ok and pos_final == 0.0)
        rows.append(row)

        print(f"\n  {sym}: price={price} qty_step={qty_step} min_qty={min_qty} "
              f"min_notional={min_notional} -> min_valid_qty={qty_str} "
              f"(notional~={order_notional:.2f})")
        print(f"    BUY ok={buy_ok} err={buy_err!r} | pos_after_buy={pos_after_buy} | "
              f"SELL ok={sell_ok} err={sell_err!r} | pos_final={pos_final}")

    # --- final safety sweep across the whole account
    print("\n" + "=" * 70)
    print("FINAL POSITION SWEEP (settleCoin=USDT):")
    leftover = []
    try:
        r = s.get_positions(category="linear", settleCoin="USDT")
        for p in r["result"]["list"]:
            sz = float(p.get("size", 0) or 0)
            if sz > 0:
                leftover.append({"symbol": p["symbol"], "side": p.get("side"),
                                 "size": sz, "positionIdx": p.get("positionIdx"),
                                 "avgPrice": p.get("avgPrice")})
    except Exception as exc:
        print(f"  sweep failed: {exc}")
        leftover = [{"sweep_error": str(exc)}]
    if leftover:
        print("  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("  !!! OPEN POSITION(S) REMAIN - STOP THE EXPERIMENT, INVESTIGATE")
        for x in leftover:
            print(f"  !!! {x}")
        print("  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    else:
        print("  clean - no open positions.")

    out_dir = PROJECT_ROOT / "logs" / "research" / "bbkc_squeeze" / "universe_exp"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "order_feasibility.json"
    out_path.write_text(json.dumps({"rows": rows, "leftover": leftover}, indent=2,
                                   default=str), encoding="utf-8")
    print(f"\nSaved {out_path}")

    print("\n=== SUMMARY ===")
    hdr = ("symbol", "price", "qty_step", "min_qty", "min_notional", "tick_size",
           "min_valid_qty", "qty_notional", "buy_ok", "sell_ok", "pos_final",
           "candidate_ok", "fail_class")
    print("  " + " | ".join(f"{h}" for h in hdr))
    for r in rows:
        if "error" in r and "qty_step" not in r:
            print(f"  {r['symbol']} | ERROR: {r['error']}")
            continue
        fc = r.get("buy_fail_class") or r.get("sell_fail_class") or ""
        print("  " + " | ".join(str(x) for x in (
            r["symbol"], r["price"], r["qty_step"], r["min_qty"], r["min_notional"],
            r["tick_size"], r["min_valid_qty"], r["min_valid_qty_notional"],
            r["buy_ok"], r["sell_ok"], r["pos_final_open"], r["candidate_ok"], fc)))

    return 1 if leftover else 0


if __name__ == "__main__":
    raise SystemExit(main())
