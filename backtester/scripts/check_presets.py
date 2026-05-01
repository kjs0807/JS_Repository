"""``_BYBIT_LINEAR_PERP_TABLE`` vs DB or Bybit live 차이 보고 (Phase 2.5 후속).

Usage:
    python scripts/check_presets.py --db Crypto/Bybit_Trading/db/bybit_data.db
    python scripts/check_presets.py --live

DB 모드: 로컬 ``products_master`` 테이블 (Bybit instruments-info 캐시) 와 비교.
Live 모드: Bybit REST 에서 즉시 fetch 후 비교.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from backtester.instruments.bybit_fetcher import (
    BybitInstrumentSpec,
    BybitInstrumentSpecFetcher,
    diff_against_preset,
)
from backtester.instruments.presets import _BYBIT_LINEAR_PERP_TABLE


def _from_db(db_path: Path) -> dict[str, BybitInstrumentSpec]:
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    out: dict[str, BybitInstrumentSpec] = {}
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        symbols = sorted(_BYBIT_LINEAR_PERP_TABLE.keys())
        placeholders = ",".join("?" * len(symbols))
        cur.execute(
            f"SELECT symbol, base_coin, quote_coin, min_qty, qty_step, tick_size, "
            f"min_notional, max_leverage FROM products_master WHERE symbol IN "
            f"({placeholders})",
            symbols,
        )
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for row in cur.fetchall():
            sym, base, quote, min_qty, qty_step, tick, min_n, max_lev = row
            out[sym] = BybitInstrumentSpec(
                symbol=sym,
                base_coin=base,
                quote_coin=quote or "USDT",
                price_tick=Decimal(str(tick)),
                qty_step=Decimal(str(qty_step)),
                min_qty=Decimal(str(min_qty)),
                min_notional=(
                    Decimal(str(min_n)) if min_n is not None else Decimal("0")
                ),
                max_leverage=(
                    Decimal(str(max_lev))
                    if max_lev is not None
                    else Decimal("0")
                ),
                fetched_at=now,
            )
    finally:
        con.close()
    return out


def _from_live() -> dict[str, BybitInstrumentSpec]:
    fetcher = BybitInstrumentSpecFetcher()
    return fetcher.fetch_linear_perp(
        symbols=sorted(_BYBIT_LINEAR_PERP_TABLE.keys())
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument(
        "--db", type=Path, help="local SQLite DB with products_master table"
    )
    grp.add_argument("--live", action="store_true", help="fetch live from Bybit REST")
    args = parser.parse_args(argv)

    fetched: dict[str, BybitInstrumentSpec]
    if args.db is not None:
        fetched = _from_db(args.db)
        source = f"DB ({args.db})"
    else:
        fetched = _from_live()
        source = "Bybit REST"

    diffs = diff_against_preset(fetched)
    print(f"=== preset diff vs {source} ===")
    if not diffs:
        print(
            "no diffs — preset table 일치. ({0} symbols checked)".format(
                len(_BYBIT_LINEAR_PERP_TABLE)
            )
        )
        return 0
    print(f"{len(diffs)} mismatches:")
    for d in diffs:
        print(f"  {d.symbol:<12s} {d.field_name:<14s} preset={d.preset_value:<12s} "
              f"fetched={d.fetched_value}")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] if len(sys.argv) > 1 else None))
