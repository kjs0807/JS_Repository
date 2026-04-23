"""설정 모듈. config.yaml + .env → AppConfig 통합 로드."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

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
class AppConfig:
    app: AppSettings = field(default_factory=AppSettings)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data: DataConfig = field(default_factory=DataConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)


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
        }
        for section_name, instance in section_map.items():
            if section_name in raw and isinstance(raw[section_name], dict):
                _merge_dataclass(instance, raw[section_name])
    env_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if env_token:
        config.alert.telegram_token = env_token
    env_chat = os.getenv("TELEGRAM_CHAT_ID")
    if env_chat:
        config.alert.telegram_chat_id = env_chat
    return config


__all__ = [
    "AppConfig", "AppSettings", "BacktestConfig", "RiskConfig",
    "DataConfig", "AlertConfig", "load_config",
]
