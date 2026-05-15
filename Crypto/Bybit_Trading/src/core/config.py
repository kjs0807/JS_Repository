"""Configuration loader. ``config.yaml`` + ``.env`` -> ``AppConfig``.

Sections:

  app           Runtime (mode, leverage, db_path, ...). ``app.mode`` is the
                single source of truth for the Bybit endpoint via
                :mod:`src.core.mode`.
  backtest      Fees / slippage / starting capital for the engine.
  risk          Per-trade and per-day guards used by the live broker.
  data          OHLCV ingestion knobs.
  alert         Telegram / on_error / on_daily_summary toggles.
  trading       Stage A-2: generic strategy runner inputs - strategy name,
                universe, timeframe, output dir.
  strategies    Stage A-2: per-strategy parameter bag, looked up by
                strategy ``name``. e.g. ``strategies.BBKCSqueeze.params``.
  bbkc_exit     Legacy BBKC exit cell. Kept for back-compat - the new
                generic path prefers ``strategies.BBKCSqueeze.params`` but
                falls back to this block when the new key is absent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

try:
    from dotenv import load_dotenv
    _DOTENV = True
except ImportError:
    _DOTENV = False


def _load_env() -> None:
    if not _DOTENV:
        return
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


# Load .env once at module import time so monkeypatch.delenv works correctly in tests.
_load_env()


@dataclass
class AppSettings:
    base_url: str = "https://api-demo.bybit.com"
    ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    db_path: str = "db/bybit_data.db"
    leverage: int = 3
    recv_window: int = 5000
    log_level: str = "INFO"
    mode: str = "demo"

    @property
    def api_key(self) -> str:
        return os.getenv("BYBIT_API_KEY", "")

    @property
    def api_secret(self) -> str:
        return os.getenv("BYBIT_API_SECRET", "")


@dataclass
class BacktestConfig:
    initial_capital: float = 50000.0
    taker_fee_pct: float = 0.00055
    maker_fee_pct: float = 0.0002
    slippage_pct: float = 0.0003


@dataclass
class RiskConfig:
    max_position_pct: float = 0.05
    max_concurrent: int = 10
    daily_loss_limit_pct: float = 0.05
    max_drawdown_pct: float = 0.15


@dataclass
class DataConfig:
    universe_size: int = 30
    meme_blacklist: List[str] = field(default_factory=lambda: [
        "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT",
        "BONKUSDT", "WIFUSDT", "MEMEUSDT",
    ])
    default_timeframes: List[str] = field(default_factory=lambda: ["15m", "1h", "4h"])
    history_days: int = 365


@dataclass
class AlertConfig:
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""
    alert_on_trade: bool = True
    alert_on_error: bool = True
    alert_on_daily_summary: bool = True


@dataclass
class TradingConfig:
    """Generic strategy-runner inputs (Stage A-2 + B-2).

    The runtime layer (mode, REST client, broker, monitoring) is decoupled
    from the strategy. ``trading`` says *which* strategy to run, on *what*
    universe, at *what* timeframe, and *where* to put the run directory.
    Per-strategy parameters live in :class:`AppConfig.strategies`.

    Stage B-2: ``weights`` overrides ``risk.max_position_pct`` per symbol.
    When empty (default), every symbol uses the uniform ``risk`` value.
    Example: ``{"ETHUSDT": 0.30, "BTCUSDT": 0.10}`` runs ETH at 30% and
    BTC at 10% of the account equity per trade.

    SCOPE: ``weights`` is consumed by
    :meth:`BbkcBroker.calc_legacy_notional_qty` only. Strategies that
    size positions via ``broker.calc_qty(risk_pct, stop_distance)`` (the
    risk-per-trade percent path used by e.g. some Donchian variants) do
    NOT see ``weights``. This is intentional for the current BBKC
    deployment; extending ``calc_qty`` to honour per-symbol weights is
    a separate design decision and is tracked for Stage B+.
    """
    strategy: str = "BBKCSqueeze"
    universe: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    timeframe: str = "1h"
    root_out_dir: str = "logs/live_demo"
    weights: Dict[str, float] = field(default_factory=dict)


@dataclass
class BBKCExitConfig:
    """Round 5 BBKC 청산 운영 정책. config.yaml의 ``bbkc_exit`` 섹션 + env var.

    env BBKC_EXIT_MODE 설정 시 yaml의 mode를 override (kill switch).
    Defaults: round 4 ROBUST_PROMOTE 1순위 후보 be25_st60_di30.
    """
    mode: str = "be_trail"
    trail_be_at_tp_frac: float = 0.25
    trail_start_at_tp_frac: float = 0.60
    trail_distance_tp_frac: float = 0.30
    drop_tp: bool = False
    time_stop_bars: int = 0


@dataclass
class AppConfig:
    app: AppSettings = field(default_factory=AppSettings)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data: DataConfig = field(default_factory=DataConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    # Per-strategy parameter bag. Looked up by strategy.name. Each entry
    # typically has a ``params`` dict that the registry-based instantiation
    # passes through ``strategy.set_params(...)``.
    strategies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    bbkc_exit: BBKCExitConfig = field(default_factory=BBKCExitConfig)


def _merge_dataclass(instance: object, overrides: dict) -> None:
    for key, value in overrides.items():
        if hasattr(instance, key):
            setattr(instance, key, value)


def load_config(config_path: str = "config.yaml") -> AppConfig:
    config = AppConfig()
    yaml_path = Path(config_path)
    if yaml_path.exists():
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        section_map = {
            "app": config.app, "backtest": config.backtest,
            "risk": config.risk, "data": config.data, "alert": config.alert,
            "trading": config.trading, "bbkc_exit": config.bbkc_exit,
        }
        for section_name, instance in section_map.items():
            if section_name in raw and isinstance(raw[section_name], dict):
                _merge_dataclass(instance, raw[section_name])
        # ``strategies`` is a free-form Dict[str, Dict[str, Any]] - we
        # store it as-is so the runner can look up params by strategy name.
        if "strategies" in raw and isinstance(raw["strategies"], dict):
            config.strategies = dict(raw["strategies"])
    env_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if env_token:
        config.alert.telegram_token = env_token
    env_chat = os.getenv("TELEGRAM_CHAT_ID")
    if env_chat:
        config.alert.telegram_chat_id = env_chat
    # Round 5 §7.1: BBKC_EXIT_MODE env override (kill switch path)
    env_bbkc_mode = os.getenv("BBKC_EXIT_MODE")
    if env_bbkc_mode:
        import logging
        logging.getLogger(__name__).warning(
            "BBKC_EXIT_MODE env override active: mode=%s "
            "(kill-switch path; check rollback procedure in runbook §7.2)",
            env_bbkc_mode,
        )
        config.bbkc_exit.mode = env_bbkc_mode

    # Stage A: app.mode is the single source of truth for base_url.
    # Any base_url set explicitly in yaml is overridden by the mode-derived
    # value with a WARN so operators notice the deprecation. Stage A-
    # hardening: invalid modes FAIL FAST (no silent demo fallback) so a
    # typo in the yaml cannot quietly route to the wrong environment.
    import logging as _logging
    from src.core.mode import base_url_for, VALID_MODES, MODE_DEMO, ModeError
    _log = _logging.getLogger(__name__)
    requested_mode = (config.app.mode or MODE_DEMO).lower().strip()
    if requested_mode not in VALID_MODES:
        raise ModeError(
            f"invalid app.mode={config.app.mode!r} in config; "
            f"must be one of {list(VALID_MODES)}"
        )
    config.app.mode = requested_mode
    derived_url = base_url_for(requested_mode)
    if config.app.base_url and config.app.base_url != derived_url:
        _log.warning(
            "config.app.base_url=%r is overridden by app.mode=%s -> %s. "
            "base_url is no longer user-configurable; remove it from yaml.",
            config.app.base_url, requested_mode, derived_url,
        )
    config.app.base_url = derived_url

    return config


__all__ = [
    "AppConfig", "AppSettings", "BacktestConfig", "RiskConfig",
    "DataConfig", "AlertConfig", "TradingConfig", "BBKCExitConfig",
    "load_config",
]
