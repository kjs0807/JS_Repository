"""Generic Bybit strategy trade runner (Stage A-2).

Mode-agnostic and strategy-agnostic entry point. Replaces the BBKC-only
``run_bbkc_live_trade.py``, which is now a thin compatibility wrapper.

Usage
-----
    # Demo run, BBKCSqueeze on BTC+ETH (config-driven defaults)
    python -m scripts.run_strategy_trade --run-id today

    # Override strategy and universe from CLI
    python -m scripts.run_strategy_trade --run-id today \\
        --strategy BBKCSqueeze --universe BTCUSDT ETHUSDT

    # Switch to mainnet (requires explicit ack)
    python -m scripts.run_strategy_trade --run-id stage1 \\
        --mode live --i-understand-real-money

Design contract
---------------
* CLI / config: ``app.mode`` (demo|live) + optional ``--mode`` override.
  Endpoint is derived in :mod:`src.core.mode`; no other knob.
* Credentials: ``BYBIT_DEMO_API_KEY`` / ``BYBIT_LIVE_API_KEY`` in ``.env``.
  Live mode rejects the legacy ``BYBIT_API_KEY`` fallback.
* Strategy: looked up by ``trading.strategy`` (overridable by
  ``--strategy``) in :func:`src.strategies.registry_builder.build_strategy_registry`.
  Slug aliases (e.g. ``bbkc_squeeze``) are accepted - see
  :func:`resolve_strategy_name`.
* Parameters: ``strategies.<Name>.params`` from ``config.yaml`` are
  applied via ``strategy.set_params``. For BBKCSqueeze, the legacy
  ``bbkc_exit`` block is used as a fallback so existing runs are not
  disturbed.
* Risk: the broker is constructed with ``risk_config=cfg.risk`` (Stage A
  carried a bug where ``RiskConfig()`` defaults were used regardless).
* Output: ``trading.root_out_dir`` (overridable). Per-run dir is
  ``<root_out_dir>/<strategy_name>/<run_id>``.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.rest_client import BybitRestClient
from src.core.alert import AlertManager
from src.core.config import AppConfig, load_config
from src.core.mode import (
    LIVE_ACK_FLAG,
    MODE_DEMO,
    MODE_LIVE,
    VALID_MODES,
    ModeError,
    fingerprint,
    live_startup_banner,
    resolve_runtime,
    ws_url_for,
)
from src.data_manager.db import DBManager
from src.execution.bbkc_demo_broker import BbkcBroker
from src.runtime.strategy_runner import StrategyTradeRunner
from src.strategies.registry import StrategyRegistry
from src.strategies.registry_builder import build_strategy_registry

logger = logging.getLogger(__name__)

LIVE_STARTUP_WAIT_SEC = 5


# ---------------------------------------------------------------------------
# Strategy name resolution (exact + slug alias)
# ---------------------------------------------------------------------------
def _normalise_strategy_token(s: str) -> str:
    """Lowercase + strip every non-alphanumeric for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve_strategy_name(
    name_or_slug: str, registry: StrategyRegistry,
) -> str:
    """Resolve a CLI strategy token to a registered strategy ``name``.

    Tries an exact match first (case-sensitive on registry key), then a
    normalised slug match (``bbkc_squeeze`` -> ``BBKCSqueeze``,
    ``donchian_fixed_rr`` -> ``Donchian_FixedRR``). Raises ``KeyError``
    with the list of registered names on failure.
    """
    registered = [entry["name"] for entry in registry.list_all()]
    if name_or_slug in registered:
        return name_or_slug
    target = _normalise_strategy_token(name_or_slug)
    for name in registered:
        if _normalise_strategy_token(name) == target:
            return name
    raise KeyError(
        f"strategy {name_or_slug!r} is not registered. "
        f"Available: {sorted(registered)}"
    )


# ---------------------------------------------------------------------------
# Strategy parameter assembly (Stage A-2 generic + BBKC legacy adapter)
# ---------------------------------------------------------------------------
def _strategy_params_from_config(
    strategy_name: str, cfg: AppConfig,
) -> Dict[str, Any]:
    """Return the params dict to pass to ``StrategyRegistry.get(...)``.

    Lookup order:

    1. ``cfg.strategies[<strategy_name>]["params"]`` if present.
    2. For BBKCSqueeze only, fall back to the legacy ``cfg.bbkc_exit``
       block so existing runs continue to work without a yaml rewrite.
    3. Empty dict (registry will use the strategy's own __init__ defaults).
    """
    block = cfg.strategies.get(strategy_name) if cfg.strategies else None
    if isinstance(block, dict):
        new_params = block.get("params")
        if isinstance(new_params, dict) and new_params:
            return dict(new_params)
    if strategy_name == "BBKCSqueeze":
        legacy = cfg.bbkc_exit
        return {
            "exit_mode": legacy.mode,
            "trail_be_at_tp_frac": legacy.trail_be_at_tp_frac,
            "trail_start_at_tp_frac": legacy.trail_start_at_tp_frac,
            "trail_distance_tp_frac": legacy.trail_distance_tp_frac,
            "drop_tp": legacy.drop_tp,
            "time_stop_bars": legacy.time_stop_bars,
        }
    return {}


def build_strategy_factory(
    strategy_name: str, registry: StrategyRegistry,
    params: Optional[Dict[str, Any]] = None,
) -> Callable[[], Any]:
    """Return a zero-arg factory that creates a parameterised strategy.

    Each call to the factory returns a *new* instance so the runner can
    keep per-symbol state without cross-symbol contamination.
    """
    safe_params = dict(params) if params else None

    def _factory() -> Any:
        return registry.get(strategy_name, params=safe_params)
    return _factory


# ---------------------------------------------------------------------------
# CLI parser - shared by the wrapper too
# ---------------------------------------------------------------------------
def build_parser(prog: Optional[str] = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Generic Bybit demo/live strategy trade runner. Mode-agnostic; "
            "live runs require --mode live + --i-understand-real-money."
        ),
    )
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument(
        "--strategy", type=str, default=None,
        help="Strategy name or slug (e.g. BBKCSqueeze, bbkc_squeeze, "
             "donchian_fixed_rr). Default = trading.strategy in config.",
    )
    parser.add_argument(
        "--universe", nargs="+", default=None,
        help="Symbols to trade. Default = trading.universe in config.",
    )
    parser.add_argument(
        "--timeframe", type=str, default=None,
        help="Strategy timeframe (1m,5m,15m,30m,1h,4h,1d,...). "
             "Default = trading.timeframe in config.",
    )
    parser.add_argument(
        "--mode", choices=list(VALID_MODES), default=None,
        help="Runtime mode (demo|live). Overrides app.mode in config.",
    )
    parser.add_argument(
        "--i-understand-real-money", action="store_true",
        help="Required when mode resolves to 'live'. Without it the "
             "script refuses to start. No effect in demo.",
    )
    parser.add_argument(
        "--force-live", action="store_true",
        help=argparse.SUPPRESS,   # deprecated; see resolve_runtime
    )
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
        "--root-out-dir", type=Path, default=None,
        help="Root directory for run logs. Default = trading.root_out_dir.",
    )
    return parser


def _parse_stop(args: argparse.Namespace) -> tuple:
    now_dt = datetime.now(timezone.utc)
    if args.stop_in_minutes is not None:
        stop_dt = now_dt + timedelta(minutes=args.stop_in_minutes)
        return stop_dt, int(stop_dt.timestamp() * 1000)
    if args.stop_at:
        stop_dt = datetime.strptime(args.stop_at, "%Y-%m-%d").replace(
            tzinfo=timezone.utc,
        )
        return stop_dt, int(stop_dt.timestamp() * 1000)
    return None, 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(str(PROJECT_ROOT / "config.yaml"))

    # ------------------------------------------------------------------
    # Mode + safety gate (real-money side cannot be reached by accident).
    # ------------------------------------------------------------------
    try:
        mode, base_url, api_key, api_secret = resolve_runtime(
            config_mode=cfg.app.mode,
            cli_mode=args.mode,
            ack=args.i_understand_real_money,
            force_live_deprecated=args.force_live,
        )
    except ModeError as exc:
        print(f"ERROR: {exc}")
        return 1
    cfg.app.mode = mode
    cfg.app.base_url = base_url

    # ------------------------------------------------------------------
    # Strategy resolution (config + CLI override + slug alias).
    # ------------------------------------------------------------------
    requested_strategy = args.strategy or cfg.trading.strategy
    registry = build_strategy_registry()
    try:
        strategy_name = resolve_strategy_name(requested_strategy, registry)
    except KeyError as exc:
        print(f"ERROR: {exc}")
        return 1
    params = _strategy_params_from_config(strategy_name, cfg)
    strategy_factory = build_strategy_factory(strategy_name, registry, params)

    universe = args.universe or list(cfg.trading.universe)
    if not universe:
        print("ERROR: empty universe (set trading.universe or pass --universe).")
        return 1
    timeframe = args.timeframe or cfg.trading.timeframe
    root_out_dir = args.root_out_dir or Path(
        PROJECT_ROOT / cfg.trading.root_out_dir
    )

    stop_dt, stop_ms = _parse_stop(args)

    run_dir = Path(root_out_dir) / strategy_name / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Demo banner (no real-money risk - short and stdout-flushed).
    # ------------------------------------------------------------------
    if mode == MODE_DEMO:
        demo_lines = [
            "=== STRATEGY TRADE - DEMO ===",
            f"  mode      : {mode}",
            f"  strategy  : {strategy_name}",
            f"  universe  : {universe}",
            f"  timeframe : {timeframe}",
            f"  run_id    : {args.run_id}",
            f"  run_dir   : {run_dir}",
            f"  base_url  : {base_url}",
            f"  api key   : {fingerprint(api_key)}",
            f"  warmup    : {args.warmup_days} days",
            f"  leverage  : {cfg.app.leverage}",
            f"  stop-at   : "
            f"{stop_dt.isoformat() if stop_dt else '(indefinite, Ctrl+C to stop)'}",
        ]
        print("\n".join(demo_lines), flush=True)

    # ------------------------------------------------------------------
    # Clients (mode-derived endpoints; broker uses cfg.risk - Stage A-2
    # bug fix vs Stage A which silently used RiskConfig() defaults).
    # ------------------------------------------------------------------
    rest = BybitRestClient(api_key, api_secret, base_url)
    alert = AlertManager(cfg.alert) if hasattr(cfg, "alert") else None
    db = DBManager(
        str(PROJECT_ROOT / cfg.app.db_path),
        str(PROJECT_ROOT / "db" / "schema.sql"),
    )
    db.initialize()

    broker = BbkcBroker(
        rest_client=rest,
        run_dir=run_dir,
        symbols_allowed=universe,
        alert_manager=alert,
        risk_config=cfg.risk,             # FIX: was RiskConfig() default
        leverage=cfg.app.leverage,
    )

    # ------------------------------------------------------------------
    # Live banner + 5s pre-flight wait (real-money safety).
    # ------------------------------------------------------------------
    if mode == MODE_LIVE:
        try:
            broker.sync()
            portfolio = broker.live_portfolio()
            live_equity = float(portfolio.equity)
            open_positions = len(portfolio.positions)
        except Exception as exc:
            print(f"ERROR: pre-flight broker.sync() failed: {exc}")
            return 1
        est_max_notional = (
            live_equity
            * cfg.risk.max_position_pct
            * cfg.app.leverage
            * len(universe)
        )
        banner = live_startup_banner(
            mode=mode,
            base_url=base_url,
            universe=universe,
            leverage=cfg.app.leverage,
            equity=live_equity,
            api_key_fingerprint=fingerprint(api_key),
            estimated_max_notional=est_max_notional,
            extras={
                "strategy": strategy_name,
                "timeframe": timeframe,
                "run_id": args.run_id,
                "run_dir": str(run_dir),
                "open positions": open_positions,
                "ws_url": ws_url_for(mode),
                "stop-at": stop_dt.isoformat() if stop_dt else "(indefinite, Ctrl+C to stop)",
                "warmup": f"{args.warmup_days} days",
            },
        )
        print(banner, flush=True)
        print(
            f"\nStarting in {LIVE_STARTUP_WAIT_SEC} seconds - "
            "press Ctrl+C now to abort.",
            flush=True,
        )
        try:
            time.sleep(LIVE_STARTUP_WAIT_SEC)
        except KeyboardInterrupt:
            print("Aborted by operator before live start.")
            return 0

    # ------------------------------------------------------------------
    # Runner (strategy-agnostic).
    # ------------------------------------------------------------------
    runner = StrategyTradeRunner(
        broker=broker,
        db=db,
        universe=universe,
        timeframe=timeframe,
        warmup_days=args.warmup_days,
        stop_at_ms=stop_ms,
        strategy_factory=strategy_factory,
        ws_url=ws_url_for(mode),
    )
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
