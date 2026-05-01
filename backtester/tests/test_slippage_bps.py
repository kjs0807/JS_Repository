"""PR 15a apply_bps_slippage + NextBarOpenExecution(slippage_bps) 테스트 (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.orderbook import Order, OrderBook
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill
from backtester.execution.next_bar import NextBarOpenExecution
from backtester.execution.slippage_bps import apply_bps_slippage
from backtester.instruments.base import FeeModel, Instrument

UTC = timezone.utc
TS = datetime(2026, 4, 1, tzinfo=UTC)


def _btc() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.001"), maker=Decimal("0.0005")),
    )


def _market_order(side: Literal["buy", "sell"] = "buy") -> Order:
    intent = OrderIntent(
        symbol="BTCUSDT",
        side=side,
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
    )
    book = OrderBook()
    return book.add(intent, sized_quantity=Decimal("1"), ts=TS)


def _snap(open_price: Decimal = Decimal("100")) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=TS,
        open=open_price,
        high=open_price + Decimal("1"),
        low=open_price - Decimal("1"),
        close=open_price,
        volume=Decimal("0"),
    )


# ---------- apply_bps_slippage 단위 -----------------------------------------


def test_apply_bps_slippage_buy_increases_price() -> None:
    out = apply_bps_slippage(Decimal("100"), "buy", Decimal("10"))
    # 10 bps = 0.1%
    assert out == Decimal("100.1000")


def test_apply_bps_slippage_sell_decreases_price() -> None:
    out = apply_bps_slippage(Decimal("100"), "sell", Decimal("10"))
    assert out == Decimal("99.9000")


def test_apply_bps_slippage_zero_returns_input() -> None:
    p = Decimal("123.456")
    assert apply_bps_slippage(p, "buy", Decimal("0")) == p
    assert apply_bps_slippage(p, "sell", Decimal("0")) == p


def test_apply_bps_slippage_negative_bps_rejected() -> None:
    with pytest.raises(ValueError, match="bps must be >= 0"):
        apply_bps_slippage(Decimal("100"), "buy", Decimal("-1"))


def test_apply_bps_slippage_unknown_side_rejected() -> None:
    with pytest.raises(ValueError, match="side must be"):
        apply_bps_slippage(Decimal("100"), "long", Decimal("10"))


# ---------- NextBarOpenExecution slippage 통합 ------------------------------


def test_next_bar_open_zero_slippage_matches_phase1_behavior() -> None:
    """slippage_bps=0 (default) → Phase 1 회귀 그대로."""
    em = NextBarOpenExecution()
    fill = em.try_fill(_market_order("buy"), _snap(Decimal("100")), _btc())
    assert isinstance(fill, Fill)
    assert fill.price == Decimal("100")


def test_next_bar_open_buy_with_slippage_bumps_price_up() -> None:
    em = NextBarOpenExecution(slippage_bps=Decimal("25"))
    fill = em.try_fill(_market_order("buy"), _snap(Decimal("100")), _btc())
    assert fill is not None
    # 25 bps = 0.25% → 100 * 1.0025 = 100.25
    assert fill.price == Decimal("100.2500")


def test_next_bar_open_sell_with_slippage_bumps_price_down() -> None:
    em = NextBarOpenExecution(slippage_bps=Decimal("25"))
    fill = em.try_fill(_market_order("sell"), _snap(Decimal("100")), _btc())
    assert fill is not None
    assert fill.price == Decimal("99.7500")


def test_next_bar_open_fee_uses_slipped_notional_and_taker_rate() -> None:
    """slippage 후 가격 기준으로 notional 계산 + taker rate 적용."""
    em = NextBarOpenExecution(slippage_bps=Decimal("100"))  # 1% slip
    fill = em.try_fill(_market_order("buy"), _snap(Decimal("100")), _btc())
    assert fill is not None
    assert fill.price == Decimal("101.0000")
    # taker=0.001, notional = 1 * 101 = 101 → fee = 0.101
    assert fill.fee == Decimal("0.1010000")


def test_next_bar_open_negative_slippage_rejected() -> None:
    with pytest.raises(ValueError, match="slippage_bps"):
        NextBarOpenExecution(slippage_bps=Decimal("-1"))


def test_next_bar_open_accepts_int_or_float_slippage() -> None:
    em_int = NextBarOpenExecution(slippage_bps=10)
    em_float = NextBarOpenExecution(slippage_bps=10.0)
    assert em_int.slippage_bps == em_float.slippage_bps == Decimal("10")


# ---------- Engine wiring ---------------------------------------------------


def _engine_with_execution_model(
    tmp_path: Path,
    *,
    execution_model: str,
    slippage_bps: float = 0.0,
) -> BacktestConfig:
    import polars as pl

    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": base.replace(hour=h),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1.0,
        }
        for h in range(24)
    ]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(data_dir / "BTCUSDT_1h.parquet")

    return BacktestConfig(
        run_id=f"slippage_smoke_{execution_model}",
        data_source=DataSourceConfig(base_dir=data_dir, type="parquet"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base.replace(hour=23),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        execution_model=execution_model,  # type: ignore[arg-type]
        slippage_bps=slippage_bps,
    )


def test_engine_builds_next_bar_open_with_zero_slippage_for_default(tmp_path: Path) -> None:
    cfg = _engine_with_execution_model(tmp_path, execution_model="next_bar_open")
    from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    em = engine.execution
    assert isinstance(em, NextBarOpenExecution)
    assert em.slippage_bps == Decimal("0")


def test_engine_builds_slippage_bps_with_config_value(tmp_path: Path) -> None:
    cfg = _engine_with_execution_model(
        tmp_path, execution_model="slippage_bps", slippage_bps=15.0
    )
    from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    em = engine.execution
    assert isinstance(em, NextBarOpenExecution)
    assert em.slippage_bps == Decimal("15.0")


def test_atr_slippage_rejected_at_config_level(tmp_path: Path) -> None:
    """PR 16 prep 2차: atr_slippage 는 config 레벨 fail-fast (ConfigError).

    이전엔 BacktestConfig 가 통과시키고 BacktestEngine.__init__ 에서 NotImplementedError.
    이제는 ``BacktestConfig.__post_init__`` 가 즉시 차단 — 사용자에게 더 빠른 피드백.
    AtrSlippageExecution 단위 클래스는 ``execution/slippage_atr.py`` 에서 직접 사용 가능.
    """
    from backtester.core.errors import ConfigError

    with pytest.raises(ConfigError, match="execution_model"):
        _engine_with_execution_model(tmp_path, execution_model="atr_slippage")
