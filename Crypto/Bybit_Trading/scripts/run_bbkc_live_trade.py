"""Real Bybit Demo API live trade loop for BBKCSqueeze[BIGTHREE].

This is the direct analogue of legacy ``python main.py trade``:
- Uses the **real Bybit Demo REST API** for orders, positions, wallet.
- Uses the **real Bybit WebSocket** for 1h kline confirmations.
- Pulls live equity / positions on every heartbeat.
- Blocks until SIGINT or optional ``--stop-at`` date.

Scope reduction vs legacy
-------------------------
- **One strategy** (``BBKCSqueeze``) instead of 4
- **One universe** (``BIGTHREE = BTC+ETH+AVAX``) instead of 30+
- **1h bars** instead of 15m (BBKCSqueeze is a 1h strategy)
- No automatic symbol rotation / universe change restart loop
  (``engine._restart_requested`` in legacy)

Keep-the-same
-------------
- Gap-fill on startup (``src.data_manager.gap_filler``)
- WebSocket kline subscription + confirm event dispatch
- Heartbeat every 60s printing live equity / positions / daily pnl
- SIGINT → WS stop + final status log
- DB as the local OHLCV mirror (strategy still reads bars from DB)

The difference vs ``scripts/run_bbkc_paper_live.py``:
- That script uses ``PaperBroker`` (offline sim, state file)
- This script uses ``BbkcDemoBroker`` → ``LiveBroker`` → real Bybit
  REST calls. No state file — the real exchange IS the state.

Safety
------
- Requires ``BYBIT_API_KEY`` and ``BYBIT_API_SECRET`` env vars.
- Refuses to start if config.app.mode is not ``demo`` unless the user
  explicitly passes ``--force-live``. This is the last line of defence
  between a demo run and a real-money run.
- Universe is BIGTHREE — hardcoded at the broker level.

Usage
-----

    # Start an indefinite demo live run (stops on Ctrl+C)
    python -m scripts.run_bbkc_live_trade --run-id bigthree_demo_start2026-04-14

    # Start with an explicit stop date (UTC midnight)
    python -m scripts.run_bbkc_live_trade --run-id bigthree_demo_2w --stop-at 2026-04-28

    # Short smoke (2 minutes) to confirm ws + heartbeat wiring
    python -m scripts.run_bbkc_live_trade --run-id smoke --stop-in-minutes 2

Check account state at any time:
    python -m scripts.check_account
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.rest_client import BybitRestClient
from src.api.ws_client import BybitWebSocketClient
from src.core.alert import AlertManager
from src.core.config import BBKCExitConfig, RiskConfig, load_config
from src.core.types import Bar
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.data_manager.gap_filler import (
    current_db_tail_ms,
    fill_gap_for_universe,
)
from src.execution.bbkc_demo_broker import BbkcDemoBroker
from src.strategies.bbkc_squeeze import BBKCSqueeze

logger = logging.getLogger(__name__)

BIGTHREE = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
HOUR_MS = 60 * 60 * 1000
DEMO_BASE_URL = "https://api-demo.bybit.com"
MAINNET_BASE_URL = "https://api.bybit.com"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


class BbkcLiveTradeRunner:
    def __init__(
        self,
        broker: BbkcDemoBroker,
        db: DBManager,
        universe: List[str],
        warmup_days: int,
        stop_at_ms: int,
        exit_cfg: Optional[BBKCExitConfig] = None,
    ) -> None:
        self._broker = broker
        self._db = db
        self._universe = list(universe)
        self._warmup_days = warmup_days
        self._stop_at_ms = stop_at_ms
        self._stopped = False
        self._ws: Optional[BybitWebSocketClient] = None
        self._bars_seen = 0
        # Round 5 §7.1: config-derived BBKC exit profile (be_trail by default)
        self._exit_cfg = exit_cfg or BBKCExitConfig()
        logger.info(
            "[runner] BBKC exit profile: mode=%s be=%.2f start=%.2f dist=%.2f "
            "drop_tp=%s time_stop_bars=%d",
            self._exit_cfg.mode,
            self._exit_cfg.trail_be_at_tp_frac,
            self._exit_cfg.trail_start_at_tp_frac,
            self._exit_cfg.trail_distance_tp_frac,
            self._exit_cfg.drop_tp,
            self._exit_cfg.time_stop_bars,
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

    # ------------------------------------------------------------------
    # Startup sync: pull real account state from Bybit demo API
    # ------------------------------------------------------------------

    def _initial_sync(self) -> None:
        logger.info("[startup] pulling real account state from Bybit API...")
        try:
            self._broker.sync()
        except Exception as exc:
            logger.error("[startup] account sync failed: %s", exc)
            raise
        portfolio = self._broker.live_portfolio()
        logger.info(
            "[startup] equity=%.2f positions=%d",
            portfolio.equity, len(portfolio.positions),
        )
        for p in portfolio.positions:
            logger.info(
                "[startup] position: %s %s qty=%s entry=%.4f uPnL=%.2f",
                p.symbol, p.side, p.qty, p.entry_price, p.unrealized_pnl,
            )

    def _initial_gap_fill(self) -> None:
        now_ms = _now_ms()
        warmup_since = now_ms - self._warmup_days * 24 * HOUR_MS
        per_sym_since: Dict[str, int] = {}
        for sym in self._universe:
            tail = current_db_tail_ms(self._db, sym, "60")
            if tail is None or tail < warmup_since:
                per_sym_since[sym] = warmup_since
            else:
                per_sym_since[sym] = tail + 1
        common_since = min(per_sym_since.values())
        logger.info(
            "[gap_fill] since=%s until=%s universe=%s",
            datetime.fromtimestamp(common_since / 1000, timezone.utc),
            datetime.fromtimestamp(now_ms / 1000, timezone.utc),
            self._universe,
        )
        result = fill_gap_for_universe(
            self._db, self._universe, interval="60",
            since_ms=common_since, until_ms=now_ms,
        )
        for sym, n in result.items():
            logger.info("[gap_fill] %s inserted=%d", sym, n)

    # ------------------------------------------------------------------
    # Bar dispatch — same pattern as paper_live but broker is live demo
    # ------------------------------------------------------------------

    def _dispatch_bar(self, symbol: str, ts_ms: int) -> None:
        df = self._db.get_bars(symbol, "1h")
        df = df.sort_values("open_time").reset_index(drop=True)
        if df.empty or int(df["open_time"].iloc[-1]) != ts_ms:
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
        feed = HistoricalDataFeed(
            db=self._db, symbols=[symbol], timeframe="1h",
        )
        full = feed.get_full_series(symbol)
        # Round 5 §7.1: config-derived exit profile injected per dispatch.
        # `_pos_meta` lazy init (Round 3) recovers from broker.get_position(),
        # so per-bar instantiation is fine — no state file needed.
        strat = BBKCSqueeze(
            exit_mode=self._exit_cfg.mode,
            trail_be_at_tp_frac=self._exit_cfg.trail_be_at_tp_frac,
            trail_start_at_tp_frac=self._exit_cfg.trail_start_at_tp_frac,
            trail_distance_tp_frac=self._exit_cfg.trail_distance_tp_frac,
            drop_tp=self._exit_cfg.drop_tp,
            time_stop_bars=self._exit_cfg.time_stop_bars,
        )
        cache = strat.prepare(full)
        i = len(full.bars) - 1
        try:
            strat.on_bar_fast(bar, i, cache, self._broker)
        except Exception as exc:
            logger.error("[dispatch] strategy error sym=%s: %s", symbol, exc)
        self._bars_seen += 1
        logger.info(
            "[bar] sym=%s ts=%s close=%.2f → strategy dispatched",
            symbol,
            datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat(),
            bar.close,
        )

    def _on_kline_closed(
        self, symbol: str, interval: str, kline: Dict[str, Any],
    ) -> None:
        if symbol not in self._universe:
            return
        if interval != "60":
            return
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
        except (KeyError, ValueError) as exc:
            logger.error("[ws] parse error sym=%s: %s", symbol, exc)
            return
        self._dispatch_bar(symbol, open_time)

        if self._stop_at_ms > 0 and _now_ms() >= self._stop_at_ms:
            logger.info("[runner] stop-at reached, shutting down")
            self._stopped = True
            if self._ws is not None:
                try:
                    self._ws.stop()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._install_signal_handler()
        self._initial_sync()
        self._initial_gap_fill()

        ws = BybitWebSocketClient()
        ws.on_kline_closed = self._on_kline_closed
        self._ws = ws
        ws.start(self._universe, ["60"])
        logger.info(
            "[runner] ws started, bar-close subscription on %s",
            self._universe,
        )
        last_heartbeat = _now_ms()
        try:
            while not self._stopped:
                if self._stop_at_ms > 0 and _now_ms() >= self._stop_at_ms:
                    break
                time.sleep(5.0)
                if _now_ms() - last_heartbeat >= 60_000:
                    try:
                        self._broker.sync()
                        portfolio = self._broker.live_portfolio()
                        pos_str = ", ".join(
                            f"{p.symbol} {p.side} qty={p.qty} "
                            f"entry={p.entry_price:.2f} "
                            f"uPnL={p.unrealized_pnl:+.2f}"
                            for p in portfolio.positions
                        ) or "(none)"
                        logger.info(
                            "[heartbeat] bars_seen=%d equity=%.2f "
                            "daily_pnl=%+.2f positions=%d [%s]",
                            self._bars_seen,
                            portfolio.equity,
                            portfolio.daily_pnl,
                            len(portfolio.positions),
                            pos_str,
                        )
                    except Exception as exc:
                        logger.error("[heartbeat] sync failed: %s", exc)
                    last_heartbeat = _now_ms()
        finally:
            try:
                ws.stop()
            except Exception:
                pass
            try:
                self._broker.sync()
                portfolio = self._broker.live_portfolio()
                logger.info(
                    "[final] equity=%.2f daily_pnl=%+.2f positions=%d bars_seen=%d",
                    portfolio.equity,
                    portfolio.daily_pnl,
                    len(portfolio.positions),
                    self._bars_seen,
                )
            except Exception as exc:
                logger.error("[final] sync failed: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="BBKC[BIGTHREE] real Bybit Demo API live trade loop.",
    )
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument(
        "--stop-at", type=str, default=None,
        help="Stop at UTC date YYYY-MM-DD. None = run until SIGINT.",
    )
    parser.add_argument(
        "--stop-in-minutes", type=int, default=None,
        help="Stop after N minutes (smoke). Overrides --stop-at.",
    )
    parser.add_argument(
        "--root-out-dir", type=Path,
        default=PROJECT_ROOT / "logs" / "live_demo" / "bbkc_bigthree",
    )
    parser.add_argument(
        "--force-live", action="store_true",
        help="Allow running against mainnet base_url (DANGEROUS).",
    )
    args = parser.parse_args()

    # Round 5 §2.3: 자동 종료 금지 강제 가드 (BBKC_ROUND5_MODE=true).
    # forward 운영 시 운영자가 BBKC_ROUND5_MODE=true로 시작하면
    # --stop-at/--stop-in-minutes는 시작 거부됨. smoke 테스트는 가드 미설정으로.
    if os.getenv("BBKC_ROUND5_MODE", "").lower() == "true":
        if args.stop_at or args.stop_in_minutes is not None:
            parser.error(
                "BBKC_ROUND5_MODE=true: --stop-at/--stop-in-minutes are forbidden "
                "in Round 5 forward operations (per round 5 design §2.3). "
                "Unset BBKC_ROUND5_MODE for smoke tests."
            )

    cfg = load_config(str(PROJECT_ROOT / "config.yaml"))

    # --- safety gate: ensure we are on demo unless explicitly overridden
    base_url = cfg.app.base_url
    if not args.force_live:
        if base_url != DEMO_BASE_URL:
            print(
                f"ERROR: cfg.app.base_url={base_url} is not the demo "
                f"endpoint ({DEMO_BASE_URL}). Pass --force-live to override "
                f"(but you probably don't want to)."
            )
            return 1
    elif base_url == MAINNET_BASE_URL:
        print("WARNING: --force-live used with mainnet base_url — real money at risk.")

    # --- api keys
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    if not api_key or not api_secret:
        print(
            "ERROR: BYBIT_API_KEY / BYBIT_API_SECRET not set in environment. "
            "Demo API requires authenticated calls. Export your demo "
            "keys first:\n"
            "    $env:BYBIT_API_KEY=\"...\"\n"
            "    $env:BYBIT_API_SECRET=\"...\"\n"
        )
        return 1

    # --- resolve stop
    now_dt = datetime.now(timezone.utc)
    if args.stop_in_minutes is not None:
        stop_dt = now_dt + timedelta(minutes=args.stop_in_minutes)
        stop_ms = int(stop_dt.timestamp() * 1000)
    elif args.stop_at:
        stop_dt = _parse_date(args.stop_at)
        stop_ms = int(stop_dt.timestamp() * 1000)
    else:
        stop_dt = None
        stop_ms = 0  # 0 = run forever (until SIGINT)

    run_dir = args.root_out_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print("=== BBKC[BIGTHREE] LIVE DEMO TRADE ===")
    print(f"  run_id   : {args.run_id}")
    print(f"  run_dir  : {run_dir}")
    print(f"  base_url : {base_url}")
    print(f"  universe : {BIGTHREE}")
    print(f"  warmup   : {args.warmup_days} days (DB mirror)")
    print(f"  leverage : {cfg.app.leverage}")
    print(f"  now      : {now_dt.isoformat()}")
    print(f"  stop-at  : {stop_dt.isoformat() if stop_dt else '(indefinite, Ctrl+C to stop)'}")

    rest = BybitRestClient(api_key, api_secret, base_url)
    alert = AlertManager(cfg.alert) if hasattr(cfg, "alert") else None
    db = DBManager(
        str(PROJECT_ROOT / cfg.app.db_path),
        str(PROJECT_ROOT / "db" / "schema.sql"),
    )
    db.initialize()

    broker = BbkcDemoBroker(
        rest_client=rest,
        run_dir=run_dir,
        symbols_allowed=BIGTHREE,
        alert_manager=alert,
        risk_config=RiskConfig(),
        leverage=cfg.app.leverage,
    )

    runner = BbkcLiveTradeRunner(
        broker=broker,
        db=db,
        universe=BIGTHREE,
        warmup_days=args.warmup_days,
        stop_at_ms=stop_ms,
        exit_cfg=cfg.bbkc_exit,
    )
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
