"""PR 15a AtrSlippageExecution 테스트 (Phase 2 minimum interface)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import pytest

from backtester.core.orderbook import Order, OrderBook
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.snapshot import MarketSnapshot
from backtester.execution.slippage_atr import AtrSlippageExecution
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


def _const_atr(value: Decimal) -> Callable[[datetime], Decimal]:
    def _provider(ts: datetime) -> Decimal:
        del ts
        return value

    return _provider


# ---------- 기본 동작 -------------------------------------------------------


def test_atr_slippage_buy_pays_open_plus_k_atr() -> None:
    em = AtrSlippageExecution(
        atr_multiplier=Decimal("0.5"),
        atr_provider=_const_atr(Decimal("2")),
    )
    fill = em.try_fill(_market_order("buy"), _snap(Decimal("100")), _btc())
    assert fill is not None
    # open 100 + 0.5 * 2 = 101
    assert fill.price == Decimal("101")


def test_atr_slippage_sell_receives_open_minus_k_atr() -> None:
    em = AtrSlippageExecution(
        atr_multiplier=Decimal("0.5"),
        atr_provider=_const_atr(Decimal("2")),
    )
    fill = em.try_fill(_market_order("sell"), _snap(Decimal("100")), _btc())
    assert fill is not None
    assert fill.price == Decimal("99")


def test_atr_slippage_zero_multiplier_no_change() -> None:
    em = AtrSlippageExecution(
        atr_multiplier=Decimal("0"),
        atr_provider=_const_atr(Decimal("2")),
    )
    fill = em.try_fill(_market_order("buy"), _snap(Decimal("100")), _btc())
    assert fill is not None
    assert fill.price == Decimal("100")


def test_atr_slippage_zero_atr_no_change() -> None:
    em = AtrSlippageExecution(
        atr_multiplier=Decimal("1"),
        atr_provider=_const_atr(Decimal("0")),
    )
    fill = em.try_fill(_market_order("buy"), _snap(Decimal("100")), _btc())
    assert fill is not None
    assert fill.price == Decimal("100")


def test_atr_slippage_taker_fee_on_slipped_notional() -> None:
    em = AtrSlippageExecution(
        atr_multiplier=Decimal("0.5"),
        atr_provider=_const_atr(Decimal("2")),
    )
    fill = em.try_fill(_market_order("buy"), _snap(Decimal("100")), _btc())
    assert fill is not None
    # notional = 1 * 101 = 101, taker=0.001 → fee = 0.101
    assert fill.fee == Decimal("0.101")


def test_atr_slippage_uses_provider_with_snapshot_timestamp() -> None:
    """atr_provider 가 snapshot.timestamp 로 호출되는지 검증."""
    seen_ts: list[datetime] = []

    def _record(ts: datetime) -> Decimal:
        seen_ts.append(ts)
        return Decimal("1")

    em = AtrSlippageExecution(atr_multiplier=Decimal("1"), atr_provider=_record)
    em.try_fill(_market_order("buy"), _snap(), _btc())
    assert seen_ts == [TS]


# ---------- 검증 ------------------------------------------------------------


def test_atr_slippage_negative_multiplier_rejected() -> None:
    with pytest.raises(ValueError, match="atr_multiplier"):
        AtrSlippageExecution(
            atr_multiplier=Decimal("-1"),
            atr_provider=_const_atr(Decimal("2")),
        )


def test_atr_slippage_negative_atr_value_rejected() -> None:
    em = AtrSlippageExecution(
        atr_multiplier=Decimal("0.5"),
        atr_provider=_const_atr(Decimal("-1")),
    )
    with pytest.raises(ValueError, match="negative atr_value"):
        em.try_fill(_market_order("buy"), _snap(), _btc())


def test_atr_slippage_rejects_non_market_order() -> None:
    """OrderBook 은 PR 15b 에서 limit 입수를 활성화하므로 본 테스트는 Order 를 수동
    구성해 ExecutionModel 의 type 가드만 검증한다 (PR 15b 도입 후에도 유효한 회귀)."""
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("99"),
    )
    order = Order(
        id="ord_test",
        intent=intent,
        state="pending",
        submitted_at=TS,
        sized_quantity=Decimal("1"),
        remaining=Decimal("1"),
    )
    em = AtrSlippageExecution(
        atr_multiplier=Decimal("0.5"),
        atr_provider=_const_atr(Decimal("2")),
    )
    with pytest.raises(NotImplementedError, match="PR 15b"):
        em.try_fill(order, _snap(), _btc())
