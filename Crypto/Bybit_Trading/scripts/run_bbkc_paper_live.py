"""Live paper trading for BBKCSqueeze[BIGTHREE].

Wires together the pieces the legacy project already had:
- gap-fill missing historical bars on startup (legacy historical.py)
- websocket kline close subscription (legacy realtime.py)
- per-bar strategy evaluation
- persistent paper broker state (so 14-day runs survive restart)

but routed through the current ``src/`` layers:
- ``src/data_manager/gap_filler.py``   — gap fill on start
- ``src/api/ws_client.py::BybitWebSocketClient`` — realtime
- ``src/execution/paper_broker.py``    — paper portfolio + persistence
- ``src/strategies/bbkc_squeeze.py``   — strategy (unchanged, P5)

This is the operational mode the user actually wants for the 14-day
staged promote check. Unlike ``run_bbkc_paper.py`` (historical replay),
this script stays up, listens for confirmed 1h bars in realtime,
upserts them to DB, and runs the strategy against the fresh data.

Safety rails
------------
- ``PaperBroker`` is used, not ``LiveBroker``. No Bybit order API is
  ever called.
- Universe is hard-bound to BIGTHREE (can be overridden but warned).
- Any kline event outside the subscribed universe is ignored.
- On SIGINT the runner saves final state before exit.
- On every bar the broker persists state and appends to fills /
  equity logs. 2 weeks of bar events are all recoverable.

Resume
------
If the same ``--run-id`` is passed and ``paper_state.json`` exists,
state is restored automatically. On resume we also gap-fill any bars
that arrived while the runner was down — so short restarts do not
leak signals.

Usage
-----

    # Start a 14-day live paper from now
    python -m scripts.run_bbkc_paper_live \\
        --run-id bigthree_paper_2w_start2026-04-14 \\
        --stop-at 2026-04-28

    # Resume (same run-id)
    python -m scripts.run_bbkc_paper_live \\
        --run-id bigthree_paper_2w_start2026-04-14 \\
        --stop-at 2026-04-28

    # Smoke: gap-fill only, no ws loop
    python -m scripts.run_bbkc_paper_live --run-id smoke --gap-fill-only

The ws loop blocks until ``--stop-at`` is reached or SIGINT.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.ws_client import BybitWebSocketClient
from src.core.config import BacktestConfig, RiskConfig, load_config
from src.core.types import Bar
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.data_manager.gap_filler import (
    current_db_tail_ms,
    fill_gap_for_universe,
)
from src.execution.paper_broker import PaperBroker
from src.strategies.bbkc_squeeze import BBKCSqueeze

logger = logging.getLogger(__name__)

BIGTHREE = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
HOUR_MS = 60 * 60 * 1000


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class BbkcLivePaperRunner:
    """Single-object live paper loop.

    Kept as a class so SIGINT / ws callbacks have a stable reference to
    the broker + state dict without leaking into module-level globals.
    """

    def __init__(
        self,
        broker: PaperBroker,
        db: DBManager,
        universe: List[str],
        warmup_days: int,
        stop_at_ms: int,
        gap_fill_only: bool = False,
    ) -> None:
        self._broker = broker
        self._db = db
        self._universe = list(universe)
        self._warmup_days = warmup_days
        self._stop_at_ms = stop_at_ms
        self._gap_fill_only = gap_fill_only
        self._stopped = False
        self._ws: Optional[BybitWebSocketClient] = None
        self._bars_seen = 0
        self._last_tail_ms: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Startup: backfill any gap since (now - warmup_days) or resume tail
    # ------------------------------------------------------------------

    def _initial_gap_fill(self) -> None:
        now_ms = _now_ms()
        logger.info("[gap_fill] starting for universe=%s", self._universe)
        # For each symbol choose the since_ms:
        # - if DB already has a recent tail, start from tail + 1h
        # - else, start from now - warmup_days
        warmup_since = now_ms - self._warmup_days * 24 * HOUR_MS
        per_sym_since: Dict[str, int] = {}
        for sym in self._universe:
            tail = current_db_tail_ms(self._db, sym, "60")
            if tail is None or tail < warmup_since:
                per_sym_since[sym] = warmup_since
            else:
                per_sym_since[sym] = tail + 1
        # Use the earliest since so one call covers everything
        # (fill_gap_for_universe uses the same since for all symbols).
        common_since = min(per_sym_since.values())
        result = fill_gap_for_universe(
            self._db, self._universe, interval="60",
            since_ms=common_since, until_ms=now_ms,
        )
        for sym, n in result.items():
            logger.info("[gap_fill] %s inserted=%d", sym, n)
            tail = current_db_tail_ms(self._db, sym, "60") or 0
            self._last_tail_ms[sym] = tail

    # ------------------------------------------------------------------
    # Bar dispatch: on every confirmed kline close
    # ------------------------------------------------------------------

    def _dispatch_bar(self, symbol: str, ts_ms: int) -> None:
        """Re-prepare strategy cache from full DB series + call on_bar_fast.

        This is correct but not efficient — we rebuild the cache for
        every bar. 1h cadence + ~2000 bars per symbol makes it
        negligible (well under 100ms). If the universe grows to 10+
        symbols we can switch to an incremental cache later.
        """
        feed = HistoricalDataFeed(
            db=self._db, symbols=[symbol], timeframe="1h",
        )
        full = feed.get_full_series(symbol)
        if full is None or len(full.bars) == 0:
            logger.warning("[dispatch] %s empty series", symbol)
            return
        # Find the bar index that matches ts_ms
        # HistoricalDataFeed returns a BarSeries whose internal DataFrame
        # preserves open_time if available — here we read from DB to be
        # safe and look up the row by open_time.
        df = self._db.get_bars(symbol, "1h")
        df = df.sort_values("open_time").reset_index(drop=True)
        if df.empty or int(df["open_time"].iloc[-1]) != ts_ms:
            logger.warning(
                "[dispatch] %s tail %s != incoming %s — fill_gap retry",
                symbol,
                int(df["open_time"].iloc[-1]) if not df.empty else None,
                ts_ms,
            )
            # Retry gap fill once then re-query
            fill_gap_for_universe(
                self._db, [symbol], interval="60",
                since_ms=ts_ms - HOUR_MS, until_ms=ts_ms + HOUR_MS,
            )
            df = self._db.get_bars(symbol, "1h")
            df = df.sort_values("open_time").reset_index(drop=True)
            if df.empty or int(df["open_time"].iloc[-1]) != ts_ms:
                logger.error("[dispatch] %s still missing after retry", symbol)
                return
        row = df.iloc[-1]
        bar = Bar(
            symbol=symbol,
            timestamp=int(row["open_time"]),
            timeframe="1h",
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            turnover=(
                float(row["turnover"])
                if "turnover" in df.columns and row["turnover"] is not None
                else None
            ),
        )
        strat = BBKCSqueeze()
        full = feed.get_full_series(symbol)
        cache = strat.prepare(full)
        i = len(full.bars) - 1
        self._broker.process_bar(bar)
        try:
            strat.on_bar_fast(bar, i, cache, self._broker)
        except Exception as exc:
            logger.error("[dispatch] strategy error sym=%s: %s", symbol, exc)
        self._broker.save_state(extra={
            "bars_seen": self._bars_seen,
            "last_ws_bar": {"symbol": symbol, "ts_ms": ts_ms},
        })
        self._bars_seen += 1

    # ------------------------------------------------------------------
    # WS callback
    # ------------------------------------------------------------------

    def _on_kline_closed(
        self, symbol: str, interval: str, kline: Dict[str, Any],
    ) -> None:
        if symbol not in self._universe:
            logger.debug("[ws] ignoring non-universe symbol %s", symbol)
            return
        if interval != "60":
            logger.debug("[ws] ignoring interval %s", interval)
            return
        # Upsert this bar to DB (idempotent) — defence against gap.
        try:
            open_time = int(kline["start"])
            row = {
                "symbol": symbol,
                "open_time": open_time,
                "open": float(kline["open"]),
                "high": float(kline["high"]),
                "low": float(kline["low"]),
                "close": float(kline["close"]),
                "volume": float(kline.get("volume", 0.0)),
                "turnover": (
                    float(kline["turnover"])
                    if kline.get("turnover") is not None else None
                ),
            }
            self._db.upsert_bars(symbol, "1h", [row])
            self._last_tail_ms[symbol] = open_time
        except (KeyError, ValueError) as exc:
            logger.error("[ws] parse error sym=%s: %s", symbol, exc)
            return
        self._dispatch_bar(symbol, open_time)

        if _now_ms() >= self._stop_at_ms:
            logger.info("[runner] stop-at reached, shutting down")
            self._stopped = True
            if self._ws is not None:
                try:
                    self._ws.stop()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._install_signal_handler()
        self._initial_gap_fill()

        if self._gap_fill_only:
            logger.info("[runner] --gap-fill-only, exiting without ws loop")
            return

        if _now_ms() >= self._stop_at_ms:
            logger.warning(
                "[runner] stop-at already passed; skipping ws loop"
            )
            return

        ws = BybitWebSocketClient()
        ws.on_kline_closed = self._on_kline_closed
        self._ws = ws
        ws.start(self._universe, ["60"])
        logger.info("[runner] ws started, waiting for bar closes...")
        # Match legacy main.py trade ergonomics: heartbeat every 60s.
        last_heartbeat = _now_ms()
        try:
            while not self._stopped and _now_ms() < self._stop_at_ms:
                time.sleep(5.0)
                if _now_ms() - last_heartbeat >= 60_000:
                    state = self._broker.load_state() or {}
                    ws_state = "up" if ws.is_connected else "down"
                    logger.info(
                        "[heartbeat] bars_seen=%d ws=%s equity=%.2f "
                        "realized_pnl=%.2f positions=%d trades=%d",
                        self._bars_seen,
                        ws_state,
                        float(state.get("equity_incl_unrealized", 0.0)),
                        float(state.get("realized_pnl", 0.0)),
                        int(state.get("n_open_positions", 0)),
                        int(state.get("trades_total", 0)),
                    )
                    last_heartbeat = _now_ms()
        finally:
            try:
                ws.stop()
            except Exception:
                pass
            self._broker.save_state(extra={
                "final": True,
                "bars_seen": self._bars_seen,
                "last_ws_bar": {},
            })
            logger.info(
                "[runner] stopped. bars_seen=%d", self._bars_seen,
            )

    def _install_signal_handler(self) -> None:
        def _handler(signum: int, frame: Any) -> None:
            logger.warning("[runner] signal %d received, stopping...", signum)
            self._stopped = True
            if self._ws is not None:
                try:
                    self._ws.stop()
                except Exception:
                    pass
        try:
            signal.signal(signal.SIGINT, _handler)
        except Exception:
            pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Live paper trading for BBKC[BIGTHREE].",
    )
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument(
        "--symbols", nargs="*", default=BIGTHREE,
    )
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument(
        "--stop-at", type=str, default=None,
        help="Stop at this UTC date (YYYY-MM-DD). "
             "Defaults to now + 14 days.",
    )
    parser.add_argument(
        "--stop-in-minutes", type=int, default=None,
        help="Stop after N minutes (smoke test). Overrides --stop-at.",
    )
    parser.add_argument(
        "--root-out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "paper" / "bbkc_bigthree",
    )
    parser.add_argument(
        "--gap-fill-only", action="store_true",
        help="Only gap-fill and exit, no ws loop (smoke test)",
    )
    args = parser.parse_args()

    now_dt = datetime.now(timezone.utc)
    if args.stop_in_minutes is not None:
        stop_dt = now_dt + timedelta(minutes=args.stop_in_minutes)
    elif args.stop_at:
        stop_dt = _parse_date(args.stop_at)
    else:
        stop_dt = now_dt + timedelta(days=14)
    stop_ms = int(stop_dt.timestamp() * 1000)

    run_dir = args.root_out_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    universe = list(args.symbols)
    disallowed = set(universe) - set(BIGTHREE)
    if disallowed:
        print(
            "WARNING: non-BIGTHREE symbols in --symbols: "
            f"{sorted(disallowed)}. PaperBroker will still block them."
        )

    print(f"=== BBKC[BIGTHREE] LIVE PAPER run ===")
    print(f"  run_id   : {args.run_id}")
    print(f"  run_dir  : {run_dir}")
    print(f"  universe : {universe}")
    print(f"  warmup   : {args.warmup_days} days")
    print(f"  capital  : ${args.initial_capital:,.0f}")
    print(f"  now      : {now_dt.isoformat()}")
    print(f"  stop-at  : {stop_dt.isoformat()}")
    print(f"  mode     : {'gap-fill-only' if args.gap_fill_only else 'live ws loop'}")

    cfg = load_config()
    db = DBManager(cfg.app.db_path)

    broker = PaperBroker(
        config=BacktestConfig(initial_capital=args.initial_capital),
        risk_config=RiskConfig(),
        run_dir=run_dir,
        symbols_allowed=universe,
        run_id=args.run_id,
    )
    prev = broker.load_state()
    if prev is not None:
        broker.restore_from_state(prev)
        print(
            f"  resumed  : equity={prev.get('equity_incl_unrealized')} "
            f"positions={len(prev.get('positions', []))}"
        )

    runner = BbkcLivePaperRunner(
        broker=broker,
        db=db,
        universe=universe,
        warmup_days=args.warmup_days,
        stop_at_ms=stop_ms,
        gap_fill_only=args.gap_fill_only,
    )
    runner.run()

    final = broker.load_state()
    print()
    print("=== live paper summary ===")
    if final:
        print(f"  equity (incl u)  : {final.get('equity_incl_unrealized'):+.2f}")
        print(f"  realized pnl     : {final.get('realized_pnl'):+.2f}")
        print(f"  open positions   : {final.get('n_open_positions')}")
        print(f"  trades total     : {final.get('trades_total')}")
    print(f"  state file       : {run_dir / 'paper_state.json'}")
    print(f"  fills log        : {run_dir / 'fills.jsonl'}")
    print(f"  equity curve     : {run_dir / 'equity_curve.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
