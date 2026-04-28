"""Legacy TradingEngine in BBKCSqueeze[BIGTHREE]-only mode.

This is a thin wrapper over ``_legacy/main.py cmd_trade``:
- forces ``settings.symbols = [BTCUSDT, ETHUSDT, AVAXUSDT]``
- disables every strategy except ``BBKCSqueeze``
- points DB/env paths up to ``Trading/Bybit_Trading/`` so the existing
  ``db/bybit_data.db`` and ``.env`` are reused (legacy's original
  ``_legacy/db/`` was moved up during the refactor)
- otherwise runs the exact same TradingEngine + WebSocket loop as
  legacy main.py trade. Sizing (max_position_pct=5%, leverage 3x),
  risk manager, order placement, state persistence — all untouched.

Run:
    cd _legacy
    python run_bbkc_trade.py
    # (Ctrl+C to stop, like legacy)

Resume is automatic: TradingEngine.load_state() is called in
__init__, so if ``_legacy/logs/engine_state.json`` exists the engine
restarts from there. Combined with ``_reconcile_with_api`` this keeps
local state in sync with the real Bybit demo account.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

LEGACY_DIR = Path(__file__).resolve().parent
PARENT_DIR = LEGACY_DIR.parent  # Trading/Bybit_Trading/
# Both on sys.path:
# - LEGACY_DIR so `from config.settings import ...` resolves to _legacy/config/settings.py
# - PARENT_DIR so `from db.db_manager import ...` resolves to db/db_manager.py at
#   Trading/Bybit_Trading/db/ (legacy kept `db` outside `_legacy/` historically).
sys.path.insert(0, str(PARENT_DIR))
sys.path.insert(0, str(LEGACY_DIR))

# Patch config paths so legacy code finds the real DB / schema / .env.
# Must run before any legacy module imports config.* as those read
# DB_PATH / SCHEMA_FILE / ENV_FILE at module load time.
import config as _cfg_pkg  # noqa: E402

_cfg_pkg.DB_PATH = str(PARENT_DIR / "db" / "bybit_data.db")
_cfg_pkg.SCHEMA_FILE = str(PARENT_DIR / "db" / "schema.sql")
_cfg_pkg.ENV_FILE = str(PARENT_DIR / ".env")
# Also bubble to env var so AppSettings.__post_init__ picks it up.
os.environ.setdefault("BYBIT_DB_PATH", _cfg_pkg.DB_PATH)

# ---- logging ------------------------------------------------------
LOGS_DIR = PARENT_DIR / "logs" / "live_demo" / "bbkc_legacy"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bbkc_legacy")

# ---- legacy imports (order matters: config first) -----------------
import config.settings as _cfg_s  # noqa: E402
from config.settings import RiskParams, backtest_config  # noqa: E402
from config.products import fetch_products_from_api  # noqa: E402
from api.rest_client import BybitRestClient  # noqa: E402
from api.ws_client import BybitWebSocketClient  # noqa: E402
from db.db_manager import DBManager  # noqa: E402
from risk.risk_manager import RiskManager  # noqa: E402
from paper_engine.trading_engine import TradingEngine  # noqa: E402
from utils.data_gap import fill_data_gap  # noqa: E402


BIGTHREE = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
DISABLE_STRATEGIES = ("PairsTrading", "IchimokuCloud", "RSIMACDStrategy")


def main() -> None:
    # Force universe BEFORE anyone reads settings.symbols.
    _cfg_s.settings = _cfg_s.AppSettings()
    _cfg_s.settings.symbols = list(BIGTHREE)
    settings = _cfg_s.settings
    risk_params = RiskParams()

    logger.info(
        "base_url=%s leverage=%sx universe=%s",
        settings.base_url, settings.leverage, BIGTHREE,
    )
    logger.info(
        "risk: max_position_pct=%.0f%% → per-trade notional ≈ "
        "capital * %.0f%% * %dx = capital * %.1f%%",
        risk_params.max_position_pct * 100,
        risk_params.max_position_pct * 100,
        settings.leverage,
        risk_params.max_position_pct * settings.leverage * 100,
    )

    # Instrument specs for _round_qty
    try:
        fetch_products_from_api()
    except Exception as exc:
        logger.warning("fetch_products_from_api failed: %s", exc)

    rest_client = BybitRestClient(base_url=settings.base_url)
    db = DBManager(_cfg_pkg.DB_PATH)
    db.initialize()

    logger.info("gap fill for %s starting...", BIGTHREE)
    try:
        fill_data_gap(db, BIGTHREE)
    except Exception as exc:
        logger.warning("fill_data_gap raised: %s", exc)
    logger.info("gap fill done")

    risk_mgr = RiskManager(
        risk_params,
        initial_capital=backtest_config.initial_capital,
        leverage=settings.leverage,
    )

    engine = TradingEngine(
        db=db,
        rest_client=rest_client,
        risk_manager=risk_mgr,
        leverage=settings.leverage,
    )
    for name in DISABLE_STRATEGIES:
        engine.set_strategy_enabled(name, False)
    engine.set_strategy_enabled("BBKCSqueeze", True)
    logger.info("strategies enabled: %s", engine.get_strategy_enabled())

    ws = BybitWebSocketClient(ws_url=settings.ws_url)

    def on_kline_closed(symbol: str, interval: str, kline: dict) -> None:
        if interval != "15":
            return
        try:
            bar = {
                "open_time": int(kline.get("start", 0)),
                "open":  float(kline.get("open",  0)),
                "high":  float(kline.get("high",  0)),
                "low":   float(kline.get("low",   0)),
                "close": float(kline.get("close", 0)),
                "volume": float(kline.get("volume", 0)),
            }
        except (TypeError, ValueError) as exc:
            logger.warning("bar parse failed %s: %s", symbol, exc)
            return
        engine.on_new_bar_15m(symbol, bar)

    ws.on_kline_closed = on_kline_closed

    print("=== Legacy BBKCSqueeze[BIGTHREE] Live Demo ===")
    print(f"  universe : {BIGTHREE}")
    print(f"  base_url : {settings.base_url}")
    print(f"  leverage : {settings.leverage}x")
    print(f"  max_pos% : {risk_params.max_position_pct * 100:.0f}% per position")
    print(f"  notional : capital * {risk_params.max_position_pct * settings.leverage * 100:.1f}% per trade")
    print(f"  log file : {LOGS_DIR / 'run.log'}")

    ws.start(symbols=BIGTHREE, intervals=["15"])
    logger.info("ws subscribed; running until Ctrl+C")

    # Optional auto-stop for smoke tests. Env var preferred over argparse
    # because legacy main.py uses a positional subcommand parser that
    # we are bypassing.
    _smoke_env = os.getenv("BBKC_SMOKE_SECONDS", "").strip()
    smoke_seconds = int(_smoke_env) if _smoke_env.isdigit() else 0
    deadline = (time.time() + smoke_seconds) if smoke_seconds > 0 else None
    if deadline:
        logger.info("smoke mode: auto-stop after %d seconds", smoke_seconds)

    try:
        while True:
            if deadline and time.time() >= deadline:
                logger.info("smoke deadline reached, stopping")
                break
            time.sleep(60)
            try:
                status = engine.get_status()
                risk_st = status.get("risk_status", {})
                logger.info(
                    "엔진: equity=%.0f daily_pnl=%+.2f DD=%.2f%% "
                    "positions=%d bars=%d",
                    risk_st.get("equity", 0.0),
                    status.get("daily_pnl", 0.0),
                    risk_st.get("drawdown_pct", 0.0),
                    status.get("position_count", 0),
                    status.get("total_bars_processed", 0),
                )
            except Exception as exc:
                logger.error("status read failed: %s", exc)
    except KeyboardInterrupt:
        logger.info("모의거래 중단 (Ctrl+C)")
    finally:
        try:
            ws.stop()
        except Exception:
            pass
        try:
            final = engine.get_status()
            logger.info("final status: %s", final)
        except Exception as exc:
            logger.warning("final status read failed: %s", exc)


if __name__ == "__main__":
    main()
