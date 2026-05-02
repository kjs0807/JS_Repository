"""crypto_perp_backtest_config 회귀.

검증:
1. 기본값으로 BacktestConfig 빌드 (자본금 50,000 USDT, allow_short, etc).
2. preset Instrument 자동 주입.
3. YAML round-trip 후에도 동일.
4. funding_model 주입 시 funding_models 채워짐.
5. risk_limits override 동작.
6. 작은 fixture 백테스트 smoke — Engine.run() 정상 종료.
7. instrument override 가능 (커스텀 fee tier).
8. unknown symbol + instrument 미주입 → ValueError.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core import (
    BacktestConfig,
    crypto_perp_backtest_config,
)
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.execution.funding import FundingModel
from backtester.instruments import (
    ExchangeRule,
    FeeModel,
    Instrument,
    MarginModel,
    bybit_ethusdt_perp,
)
from backtester.portfolio.risk import RiskLimits
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc
TS = datetime(2026, 1, 1, tzinfo=UTC)


def _make_parquet(target: Path, n_bars: int = 12) -> None:
    df = pl.DataFrame(
        {
            "timestamp": [TS + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0] * n_bars,
            "high": [101.0] * n_bars,
            "low": [99.0] * n_bars,
            "close": [100.0] * n_bars,
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


# ---------- 1. defaults ----------------------------------------------------


def test_factory_default_initial_equity_50k(tmp_path: Path) -> None:
    cfg = crypto_perp_backtest_config(
        run_id="smoke",
        symbol="BTCUSDT",
        timeframe="1h",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        start=TS,
        end=TS + timedelta(hours=12),
    )
    assert cfg.initial_equity == Decimal("50000")
    assert cfg.allow_short is True
    assert cfg.on_run_exists == "auto_suffix"
    assert cfg.persist_run_data == "copy"
    assert cfg.bar_path_model.value == "pessimistic"
    assert cfg.snapshot_every_bars == 1
    assert cfg.slippage_bps == 3.0
    # default risk limits — max_leverage=10, max_orders_per_symbol=10
    assert cfg.risk_limits.max_leverage == Decimal("10")
    assert cfg.risk_limits.max_orders_per_symbol == 10


# ---------- 2. preset Instrument 자동 주입 ---------------------------------


def test_factory_uses_preset_instrument(tmp_path: Path) -> None:
    cfg = crypto_perp_backtest_config(
        run_id="preset",
        symbol="ETHUSDT",
        timeframe="1h",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        start=TS,
        end=TS + timedelta(hours=12),
    )
    inst = cfg.instruments[0]
    assert inst.symbol == "ETHUSDT"
    assert inst.fee_model.taker == Decimal("0.00055")
    assert inst.exchange_rule is not None
    assert inst.exchange_rule.price_tick == Decimal("0.01")
    assert inst.margin_model is not None


# ---------- 3. YAML round-trip ---------------------------------------------


def test_factory_yaml_round_trip(tmp_path: Path) -> None:
    cfg = crypto_perp_backtest_config(
        run_id="yaml_rt",
        symbol="BTCUSDT",
        timeframe="1h",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        start=TS,
        end=TS + timedelta(hours=12),
        strategy_name="bbkc_legacy_compat",
        strategy_params={
            "leverage": "3",
            "tp_pct": "0.06",
            "rsi_filter": 70,
        },
    )
    yaml_path = tmp_path / "cfg.yaml"
    cfg.to_yaml(yaml_path)
    text = yaml_path.read_text(encoding="utf-8")
    assert "BTCUSDT" in text
    assert "exchange_rule" in text
    # PR margin-yaml: liquidation 재현성 — margin_model 도 영속화 + restore.
    assert "margin_model" in text
    assert "maintenance_margin_rate" in text
    restored = BacktestConfig.from_yaml(yaml_path)
    assert restored.initial_equity == Decimal("50000")
    assert restored.allow_short is True
    assert restored.strategy_name == "bbkc_legacy_compat"
    assert restored.strategy_params["leverage"] == "3"
    # margin_model 정확히 round-trip
    inst = restored.instruments[0]
    assert inst.margin_model is not None
    assert inst.margin_model.maintenance_margin_rate == Decimal("0.005")
    assert inst.margin_model.liquidation_fee_rate == Decimal("0.0006")


# ---------- 4. funding_model 주입 ------------------------------------------


def test_factory_funding_model_attached(tmp_path: Path) -> None:
    fm = FundingModel(
        interval_hours=8,
        rate_source="constant",
        constant_rate=Decimal("0.0001"),
    )
    cfg = crypto_perp_backtest_config(
        run_id="fund",
        symbol="BTCUSDT",
        timeframe="1h",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        start=TS,
        end=TS + timedelta(hours=12),
        funding_model=fm,
    )
    assert "BTCUSDT" in cfg.funding_models
    assert cfg.funding_models["BTCUSDT"].interval_hours == 8


# ---------- 5. risk_limits override ----------------------------------------


def test_factory_risk_limits_override(tmp_path: Path) -> None:
    custom = RiskLimits(
        max_orders_per_symbol=3,
        max_leverage=Decimal("5"),
    )
    cfg = crypto_perp_backtest_config(
        run_id="risk",
        symbol="BTCUSDT",
        timeframe="1h",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        start=TS,
        end=TS + timedelta(hours=12),
        risk_limits=custom,
    )
    assert cfg.risk_limits.max_leverage == Decimal("5")
    assert cfg.risk_limits.max_orders_per_symbol == 3


# ---------- 6. Engine smoke -------------------------------------------------


class _BuyOnceStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="market",
                size_spec=TargetUnits(units=Decimal("0.01")),
                reason="entry",
            )
        ]


def test_factory_drives_engine_smoke(tmp_path: Path) -> None:
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet")
    cfg = crypto_perp_backtest_config(
        run_id="engine_smoke",
        symbol="BTCUSDT",
        timeframe="1h",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        start=TS,
        end=TS + timedelta(hours=12),
        slippage_bps=0.0,
    )
    result = BacktestEngine(cfg, _BuyOnceStrategy(), verbose=False).run()
    assert (result.run_dir / "events.jsonl").exists()
    assert (result.run_dir / "config.yaml").exists()


# ---------- 7. instrument override -----------------------------------------


def test_factory_accepts_custom_instrument(tmp_path: Path) -> None:
    """preset 대신 커스텀 instrument (예: 다른 fee tier) 주입."""
    custom = Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.5"),
        tick_value=Decimal("0.5"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0001")),  # VIP tier
        exchange_rule=ExchangeRule(
            symbol="BTCUSDT",
            price_tick=Decimal("0.5"),
            qty_step=Decimal("0.01"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("10"),
        ),
        margin_model=MarginModel(maintenance_margin_rate=Decimal("0.003")),
    )
    cfg = crypto_perp_backtest_config(
        run_id="custom",
        symbol="BTCUSDT",
        timeframe="1h",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        start=TS,
        end=TS + timedelta(hours=12),
        instrument=custom,
    )
    assert cfg.instruments[0].fee_model.taker == Decimal("0.0001")
    assert cfg.instruments[0].exchange_rule is not None
    assert cfg.instruments[0].exchange_rule.price_tick == Decimal("0.5")


# ---------- 8. unknown symbol --------------------------------------------


def test_factory_unknown_symbol_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown Bybit"):
        crypto_perp_backtest_config(
            run_id="bad",
            symbol="MYSTERYUSDT",
            timeframe="1h",
            data_dir=tmp_path / "data",
            output_dir=tmp_path / "runs",
            start=TS,
            end=TS + timedelta(hours=12),
        )


# ---------- 9. instrument symbol mismatch --------------------------------


def test_factory_instrument_symbol_mismatch_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mismatches"):
        crypto_perp_backtest_config(
            run_id="mismatch",
            symbol="BTCUSDT",
            timeframe="1h",
            data_dir=tmp_path / "data",
            output_dir=tmp_path / "runs",
            start=TS,
            end=TS + timedelta(hours=12),
            instrument=bybit_ethusdt_perp(),  # ETH, not BTC
        )
