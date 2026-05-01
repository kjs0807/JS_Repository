"""Short YAML preset loader (Phase 2.5 후속).

사용자가 매번 Python 코드 (``crypto_perp_backtest_config(...)``) 를 짜지 않고 짧은
YAML 만으로 ``BacktestConfig`` 를 생성하도록 한다.

지원 preset:
- ``preset: crypto_perp`` — ``crypto_perp_backtest_config()`` 호출.
- ``preset`` 키 없으면 ``BacktestConfig.from_dict()`` (기존 full schema) 으로 fallback.

YAML 예시:

```yaml
preset: crypto_perp
run_id: bbkc_btcusdt_1h
symbol: BTCUSDT
timeframe: 1h
data_dir: data/bybit
output_dir: runs
start: "2026-01-01T00:00:00+00:00"
end: "2026-05-01T00:00:00+00:00"
strategy_name: bbkc_legacy_compat
strategy_params:
  leverage: "3"
  margin_pct: "0.05"
  tp_pct: "0.06"
  sl_pct: "0.07"
  rsi_filter: 70
  exit_mode: fixed
# 옵션:
# initial_equity: "50000"
# allow_short: true
# slippage_bps: 2.0
# extra_timeframes: [4h]
# funding:
#   interval_hours: 8
#   rate_source: constant
#   constant_rate: "0.0001"
```
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from backtester.core.config import BacktestConfig
from backtester.core.config_factory import crypto_perp_backtest_config
from backtester.core.errors import ConfigError
from backtester.core.types import BarPathModel
from backtester.execution.funding import FundingModel


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ConfigError(
            f"datetime must be tz-aware (UTC), got naive: {value!r}"
        )
    return dt


def _funding_from_dict(data: dict[str, Any]) -> FundingModel:
    rate = data.get("constant_rate")
    return FundingModel(
        interval_hours=int(data["interval_hours"]),
        rate_source=data.get("rate_source", "constant"),
        constant_rate=Decimal(rate) if rate is not None else None,
    )


def _crypto_perp_from_dict(data: dict[str, Any]) -> BacktestConfig:
    """``preset: crypto_perp`` short YAML → BacktestConfig."""
    required = {"run_id", "symbol", "timeframe", "data_dir", "output_dir", "start", "end"}
    missing = required - set(data.keys())
    if missing:
        raise ConfigError(
            f"crypto_perp preset YAML missing required keys: {sorted(missing)}"
        )
    funding_dict = data.get("funding")
    funding_model = _funding_from_dict(funding_dict) if funding_dict else None
    extra_tfs = data.get("extra_timeframes")
    initial_equity = data.get("initial_equity")
    allow_short = data.get("allow_short", True)
    slippage_bps = float(data.get("slippage_bps", 2.0))
    bar_path = data.get("bar_path_model", "pessimistic")
    on_run_exists = data.get("on_run_exists", "auto_suffix")
    snapshot_every_bars = int(data.get("snapshot_every_bars", 1))
    funding_source_dir = data.get("funding_source_dir")

    return crypto_perp_backtest_config(
        run_id=data["run_id"],
        symbol=data["symbol"],
        timeframe=data["timeframe"],
        data_dir=Path(data["data_dir"]),
        output_dir=Path(data["output_dir"]),
        start=_parse_iso(data["start"]),
        end=_parse_iso(data["end"]),
        initial_equity=(
            Decimal(initial_equity) if initial_equity is not None else Decimal("50000")
        ),
        strategy_name=data.get("strategy_name", ""),
        strategy_params=data.get("strategy_params", {}),
        funding_model=funding_model,
        funding_source_dir=Path(funding_source_dir) if funding_source_dir else None,
        extra_timeframes=list(extra_tfs) if extra_tfs else None,
        allow_short=bool(allow_short),
        slippage_bps=slippage_bps,
        bar_path_model=BarPathModel(bar_path),
        snapshot_every_bars=snapshot_every_bars,
        on_run_exists=on_run_exists,
    )


def load_preset_yaml(path: Path) -> BacktestConfig:
    """Short preset YAML 또는 full schema YAML 을 읽어 ``BacktestConfig`` 반환.

    ``preset: crypto_perp`` 키가 있으면 ``crypto_perp_backtest_config`` 경로,
    없으면 ``BacktestConfig.from_yaml`` 와 동일.
    """
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ConfigError(
            f"YAML root must be a mapping, got {type(data).__name__}: {path}"
        )
    preset = data.pop("preset", None)
    if preset == "crypto_perp":
        return _crypto_perp_from_dict(data)
    if preset is None:
        # full schema fallback
        return BacktestConfig.from_dict(data)
    raise ConfigError(
        f"unknown preset: {preset!r}. supported: 'crypto_perp' or omit for full schema."
    )
