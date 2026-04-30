"""PR 6 NextBarOpenExecution 테스트."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backtester.core.orderbook import Order
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill
from backtester.execution.next_bar import NextBarOpenExecution
from backtester.instruments.base import FeeModel, Instrument

UTC = timezone.utc
TS = datetime(2026, 1, 1, 14, tzinfo=UTC)


def _btc(taker: str = "0.0006") -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal(taker)),
    )


def _market_order(
    side: str = "buy",
    size: str = "1",
    order_id: str = "ord_0",
    reason: str = "entry",
) -> Order:
    intent = OrderIntent(
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
        type="market",
        size_spec=TargetUnits(units=Decimal(size)),
        reason=reason,
    )
    return Order(
        id=order_id,
        intent=intent,
        state="pending",
        submitted_at=TS,
        sized_quantity=Decimal(size),
        remaining=Decimal(size),
    )


def _snap(
    open_price: str = "50000",
    ts: datetime = TS,
    symbol: str = "BTCUSDT",
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        timestamp=ts,
        open=Decimal(open_price),
        high=Decimal(open_price) + Decimal("100"),
        low=Decimal(open_price) - Decimal("100"),
        close=Decimal(open_price) + Decimal("50"),
        volume=Decimal("10"),
    )


# ---------- market BUY ------------------------------------------------------


def test_next_bar_open_market_buy_fills_at_snapshot_open() -> None:
    exec_model = NextBarOpenExecution()
    order = _market_order(side="buy", size="1.5")
    fill = exec_model.try_fill(order, _snap("50000"), _btc())

    assert fill is not None
    assert fill.symbol == "BTCUSDT"
    assert fill.side == "buy"
    assert fill.price == Decimal("50000")  # snapshot.open
    assert fill.size == Decimal("1.5")  # 전량
    # fee = notional * taker = 50000 * 1.5 * 0.0006 = 45
    assert fill.fee == Decimal("45.0000")
    assert fill.fee_currency == "USDT"
    assert fill.order_id == "ord_0"
    assert fill.timestamp == TS  # snapshot.timestamp


# ---------- market SELL -----------------------------------------------------


def test_next_bar_open_market_sell_fills_at_snapshot_open() -> None:
    exec_model = NextBarOpenExecution()
    order = _market_order(side="sell", size="0.5", order_id="ord_1", reason="exit")
    fill = exec_model.try_fill(order, _snap("51000"), _btc())

    assert fill is not None
    assert fill.side == "sell"
    assert fill.price == Decimal("51000")
    assert fill.size == Decimal("0.5")
    # fee = 51000 * 0.5 * 0.0006 = 15.3
    assert fill.fee == Decimal("15.3000")
    assert fill.intent_reason == "exit"


def test_next_bar_open_uses_remaining_not_full_size() -> None:
    """이미 부분 체결된 주문이라면 remaining만큼만 체결한다."""
    exec_model = NextBarOpenExecution()
    order = _market_order(size="2", order_id="ord_2")
    order.remaining = Decimal("0.5")  # 가상의 부분 체결 후 잔량

    fill = exec_model.try_fill(order, _snap("50000"), _btc())
    assert fill is not None
    assert fill.size == Decimal("0.5")


# ---------- timestamp / 정합성 ----------------------------------------------


def test_next_bar_open_fill_timestamp_matches_snapshot() -> None:
    """fill.timestamp = snapshot.timestamp (= 봉 시작 시각 = open 시각)."""
    exec_model = NextBarOpenExecution()
    order = _market_order()
    snap_ts = datetime(2026, 6, 15, 8, tzinfo=UTC)
    snap = _snap("60000", ts=snap_ts)
    fill = exec_model.try_fill(order, snap, _btc())

    assert fill is not None
    assert fill.timestamp == snap_ts


def test_next_bar_open_fee_zero_when_taker_zero() -> None:
    exec_model = NextBarOpenExecution()
    order = _market_order(size="1")
    fill = exec_model.try_fill(order, _snap("50000"), _btc(taker="0"))
    assert fill is not None
    assert fill.fee == Decimal("0")


# ---------- limit/stop/stop_limit → NotImplementedError ---------------------


@pytest.mark.parametrize("order_type", ["limit", "stop", "stop_limit"])
def test_next_bar_open_rejects_non_market_order(order_type: str) -> None:
    """spec §20 PR 6: limit/stop/stop_limit은 Phase 2."""
    exec_model = NextBarOpenExecution()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type=order_type,  # type: ignore[arg-type]
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000") if "limit" in order_type else None,
        stop_price=Decimal("50000") if "stop" in order_type else None,
    )
    order = Order(
        id="ord_0",
        intent=intent,
        state="pending",
        submitted_at=TS,
        sized_quantity=Decimal("1"),
        remaining=Decimal("1"),
    )
    with pytest.raises(NotImplementedError, match="Phase 2"):
        exec_model.try_fill(order, _snap(), _btc())


# ---------- Fill 타입 검증 --------------------------------------------------


def test_next_bar_open_returns_fill_instance() -> None:
    exec_model = NextBarOpenExecution()
    fill = exec_model.try_fill(_market_order(), _snap(), _btc())
    assert isinstance(fill, Fill)
