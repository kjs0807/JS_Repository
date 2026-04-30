"""PR 4 BaseStrategy 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest

from backtester.core.clock import ClockHelper
from backtester.core.context import BarsView, StrategyContext
from backtester.core.orderbook import Order
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.indicators.stateless.bb import BollingerBands
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


def _build_ctx() -> StrategyContext:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(3)],
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [1.0, 1.0, 1.0],
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    timestamps = df["timestamp"].to_list()
    view = BarsView(
        bars={"BTCUSDT": {"1h": df}},
        timestamp_index={"BTCUSDT": {"1h": {ts: i for i, ts in enumerate(timestamps)}}},
        timestamps={"BTCUSDT": {"1h": timestamps}},
        clock_helper=ClockHelper(),
        now=base + timedelta(hours=3),
    )
    return StrategyContext(
        now=base + timedelta(hours=3),
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        bars=view,
    )


def _btc_perp() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


# ---------- 기본 동작 -------------------------------------------------------


def test_base_strategy_on_bar_raises_not_implemented() -> None:
    """spec §3.5: on_bar는 서브클래스가 반드시 override."""
    s = BaseStrategy()
    ctx = _build_ctx()
    with pytest.raises(NotImplementedError, match="on_bar"):
        s.on_bar(ctx)


def test_base_strategy_required_indicators_default_empty() -> None:
    s = BaseStrategy()
    assert s.required_indicators() == []


def test_base_strategy_on_pending_orders_default_empty() -> None:
    s = BaseStrategy()
    ctx = _build_ctx()
    assert s.on_pending_orders(ctx, pending=[]) == []


def test_base_strategy_on_data_gap_default_empty() -> None:
    s = BaseStrategy()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    assert s.on_data_gap("BTCUSDT", base, base + timedelta(hours=1)) == []


def test_base_strategy_on_init_default_noop() -> None:
    s = BaseStrategy()
    s.on_init([_btc_perp()])  # raise 없으면 OK


# ---------- 서브클래스 패턴 -------------------------------------------------


def test_subclass_overriding_on_bar_works() -> None:
    """on_bar만 override하면 동작."""

    class BuyOnceStrategy(BaseStrategy):
        def __init__(self) -> None:
            self.fired = False

        def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
            if self.fired:
                return []
            self.fired = True
            return [
                OrderIntent(
                    symbol=ctx.primary_symbol,
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="entry",
                )
            ]

    s = BuyOnceStrategy()
    ctx = _build_ctx()
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].symbol == "BTCUSDT"
    # 두 번째 호출은 빈 리스트
    assert s.on_bar(ctx) == []


def test_subclass_overriding_required_indicators() -> None:
    class WithBB(BaseStrategy):
        def required_indicators(self) -> list[BollingerBands]:  # type: ignore[override]
            return [BollingerBands(period=20)]

        def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
            return []

    s = WithBB()
    inds = s.required_indicators()
    assert len(inds) == 1
    assert inds[0].period == 20


def test_subclass_overriding_on_pending_orders_uses_orders() -> None:
    """on_pending_orders가 Order 리스트를 받아 처리할 수 있다."""
    received: list[Order] = []

    class Watcher(BaseStrategy):
        def on_pending_orders(
            self,
            ctx: StrategyContext,
            pending: list[Order],
        ) -> list:  # type: ignore[type-arg]
            received.extend(pending)
            return []

        def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
            return []

    s = Watcher()
    ctx = _build_ctx()
    s.on_pending_orders(ctx, pending=[])
    assert received == []
