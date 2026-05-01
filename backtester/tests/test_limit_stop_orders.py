"""PR 15b limit/stop/stop_limit 체결 테스트 (Phase 2, spec §3.10).

PESSIMISTIC bar path 모델 (PR 15c 에서 다른 모델 추가):
- limit BUY: open<=L → open(taker), low<=L → L(maker), 그 외 no fill.
- limit SELL: open>=L → open(taker), high>=L → L(maker), 그 외 no fill.
- stop BUY: open>=S → open(taker), high>=S → S(taker), 그 외 no fill.
- stop SELL: open<=S → open(taker), low<=S → S(taker), 그 외 no fill.
- stop_limit: stop trigger + 같은 봉 limit 체결 가능 시 fill (PR 15b 한계 — trigger
  state 미보존, 후속 PR 에서 ``Order.triggered`` 도입 예정).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from backtester.core.orderbook import Order, OrderBook
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.snapshot import MarketSnapshot
from backtester.execution.next_bar import NextBarOpenExecution
from backtester.instruments.base import FeeModel, Instrument

UTC = timezone.utc
TS = datetime(2026, 4, 1, tzinfo=UTC)


def _btc(taker: str = "0.001", maker: str = "0.0005") -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal(taker), maker=Decimal(maker)),
    )


def _snap(o: str, h: str, low_: str, c: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=TS,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low_),
        close=Decimal(c),
        volume=Decimal("1"),
    )


def _order(
    type_: Literal["limit", "stop", "stop_limit"],
    side: Literal["buy", "sell"],
    *,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
) -> Order:
    intent = OrderIntent(
        symbol="BTCUSDT",
        side=side,
        type=type_,
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=limit_price,
        stop_price=stop_price,
    )
    return OrderBook().add(intent, sized_quantity=Decimal("1"), ts=TS)


# ---------- limit BUY -------------------------------------------------------


def test_limit_buy_open_at_or_below_fills_at_open_taker() -> None:
    em = NextBarOpenExecution()
    order = _order("limit", "buy", limit_price=Decimal("100"))
    # open=99 (<= 100) → fill at 99, taker
    fill = em.try_fill(order, _snap("99", "101", "98", "100"), _btc())
    assert fill is not None
    assert fill.price == Decimal("99")
    # taker fee: 1 * 99 * 0.001 = 0.099
    assert fill.fee == Decimal("0.099")


def test_limit_buy_low_touches_fills_at_limit_maker() -> None:
    em = NextBarOpenExecution()
    order = _order("limit", "buy", limit_price=Decimal("100"))
    # open=101, low=99 → fill at 100 (limit), maker
    fill = em.try_fill(order, _snap("101", "102", "99", "101"), _btc())
    assert fill is not None
    assert fill.price == Decimal("100")
    # maker fee: 1 * 100 * 0.0005 = 0.05
    assert fill.fee == Decimal("0.0500")


def test_limit_buy_bar_above_limit_no_fill() -> None:
    em = NextBarOpenExecution()
    order = _order("limit", "buy", limit_price=Decimal("100"))
    # 봉 전체가 100 초과 → no fill
    assert em.try_fill(order, _snap("105", "110", "101", "108"), _btc()) is None


# ---------- limit SELL ------------------------------------------------------


def test_limit_sell_open_at_or_above_fills_at_open_taker() -> None:
    em = NextBarOpenExecution()
    order = _order("limit", "sell", limit_price=Decimal("100"))
    fill = em.try_fill(order, _snap("101", "102", "99", "101"), _btc())
    assert fill is not None
    assert fill.price == Decimal("101")


def test_limit_sell_high_touches_fills_at_limit_maker() -> None:
    em = NextBarOpenExecution()
    order = _order("limit", "sell", limit_price=Decimal("100"))
    fill = em.try_fill(order, _snap("99", "101", "98", "99"), _btc())
    assert fill is not None
    assert fill.price == Decimal("100")
    # maker fee
    assert fill.fee == Decimal("0.0500")


def test_limit_sell_bar_below_limit_no_fill() -> None:
    em = NextBarOpenExecution()
    order = _order("limit", "sell", limit_price=Decimal("100"))
    assert em.try_fill(order, _snap("90", "95", "85", "92"), _btc()) is None


# ---------- stop BUY --------------------------------------------------------


def test_stop_buy_open_above_stop_fills_at_open() -> None:
    em = NextBarOpenExecution()
    order = _order("stop", "buy", stop_price=Decimal("100"))
    # 갭업: open=102 (>= 100) → fill at 102, taker
    fill = em.try_fill(order, _snap("102", "103", "100", "102.5"), _btc())
    assert fill is not None
    assert fill.price == Decimal("102")


def test_stop_buy_high_touches_fills_at_stop() -> None:
    em = NextBarOpenExecution()
    order = _order("stop", "buy", stop_price=Decimal("100"))
    fill = em.try_fill(order, _snap("99", "101", "98", "100.5"), _btc())
    assert fill is not None
    assert fill.price == Decimal("100")
    # taker fee (stop trigger market)
    assert fill.fee == Decimal("0.100")


def test_stop_buy_bar_below_stop_no_fill() -> None:
    em = NextBarOpenExecution()
    order = _order("stop", "buy", stop_price=Decimal("100"))
    assert em.try_fill(order, _snap("95", "99", "94", "98"), _btc()) is None


# ---------- stop SELL -------------------------------------------------------


def test_stop_sell_open_below_stop_fills_at_open() -> None:
    em = NextBarOpenExecution()
    order = _order("stop", "sell", stop_price=Decimal("100"))
    fill = em.try_fill(order, _snap("98", "99", "95", "97"), _btc())
    assert fill is not None
    assert fill.price == Decimal("98")


def test_stop_sell_low_touches_fills_at_stop() -> None:
    em = NextBarOpenExecution()
    order = _order("stop", "sell", stop_price=Decimal("100"))
    fill = em.try_fill(order, _snap("101", "102", "99", "101"), _btc())
    assert fill is not None
    assert fill.price == Decimal("100")


def test_stop_sell_bar_above_stop_no_fill() -> None:
    em = NextBarOpenExecution()
    order = _order("stop", "sell", stop_price=Decimal("100"))
    assert em.try_fill(order, _snap("105", "110", "101", "108"), _btc()) is None


# ---------- stop_limit BUY (S=trigger 100, L=limit 102) ---------------------


def test_stop_limit_buy_no_trigger_no_fill() -> None:
    em = NextBarOpenExecution()
    order = _order("stop_limit", "buy", limit_price=Decimal("102"), stop_price=Decimal("100"))
    # 봉 high=99 < S=100 → 미발동
    assert em.try_fill(order, _snap("95", "99", "94", "97"), _btc()) is None


def test_stop_limit_buy_trigger_and_open_below_limit_fills_at_open() -> None:
    em = NextBarOpenExecution()
    order = _order("stop_limit", "buy", limit_price=Decimal("102"), stop_price=Decimal("100"))
    # open=101 (>= S=100 → trigger), open=101 <= L=102 → fill at 101 taker
    fill = em.try_fill(order, _snap("101", "103", "100", "102"), _btc())
    assert fill is not None
    assert fill.price == Decimal("101")


def test_stop_limit_buy_trigger_and_low_touches_limit_fills_at_limit_maker() -> None:
    em = NextBarOpenExecution()
    order = _order("stop_limit", "buy", limit_price=Decimal("102"), stop_price=Decimal("100"))
    # open=104 (>= S → trigger), open > L. low=101 <= L=102 → fill at 102 maker
    fill = em.try_fill(order, _snap("104", "105", "101", "103"), _btc())
    assert fill is not None
    assert fill.price == Decimal("102")
    assert fill.fee == Decimal("0.0510")  # 1 * 102 * 0.0005


def test_stop_limit_buy_trigger_but_limit_unreached_no_fill() -> None:
    em = NextBarOpenExecution()
    order = _order("stop_limit", "buy", limit_price=Decimal("102"), stop_price=Decimal("100"))
    # trigger O (high=110 >= S=100) 인데 봉 전체가 limit=102 초과 → no fill
    # PR 15b 한계: 다음 봉에서 stop 재평가됨 (trigger state 미보존)
    fill = em.try_fill(order, _snap("105", "110", "103", "108"), _btc())
    assert fill is None


# ---------- stop_limit SELL (S=trigger 100, L=limit 98) ---------------------


def test_stop_limit_sell_trigger_and_open_above_limit_fills_at_open() -> None:
    em = NextBarOpenExecution()
    order = _order("stop_limit", "sell", limit_price=Decimal("98"), stop_price=Decimal("100"))
    # open=99 (<= S=100 → trigger), open=99 >= L=98 → fill at 99 taker
    fill = em.try_fill(order, _snap("99", "100", "97", "98"), _btc())
    assert fill is not None
    assert fill.price == Decimal("99")


def test_stop_limit_sell_trigger_and_high_touches_limit_fills_at_limit_maker() -> None:
    em = NextBarOpenExecution()
    order = _order("stop_limit", "sell", limit_price=Decimal("98"), stop_price=Decimal("100"))
    # open=96 (<= S=100 → trigger), open < L=98. high=99 >= L=98 → fill at 98 maker
    fill = em.try_fill(order, _snap("96", "99", "95", "97"), _btc())
    assert fill is not None
    assert fill.price == Decimal("98")


# ---------- 다중 봉 limit 지속성 (Engine wiring) ----------------------------


def test_limit_order_persists_across_bars_until_filled() -> None:
    """OrderBook 에 등록된 limit 주문은 fill 될 때까지 active 유지. ExecutionModel 가
    매 봉 try_fill 호출 → 첫 도달 봉에서 fill."""
    em = NextBarOpenExecution()
    book = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("100"),
    )
    order = book.add(intent, sized_quantity=Decimal("1"), ts=TS)

    # 봉 1: 가격 105 ~ 102 (limit 미도달) → no fill, order active
    bar1 = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=TS,
        open=Decimal("105"),
        high=Decimal("106"),
        low=Decimal("102"),
        close=Decimal("104"),
        volume=Decimal("1"),
    )
    assert em.try_fill(order, bar1, _btc()) is None
    assert order.is_active

    # 봉 2: 가격 102 ~ 99 (low <= 100) → fill at 100 maker
    bar2 = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=TS + timedelta(hours=1),
        open=Decimal("102"),
        high=Decimal("103"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    fill = em.try_fill(order, bar2, _btc())
    assert fill is not None
    assert fill.price == Decimal("100")
    book.fill(order.id, fill)
    assert order.state == "filled"


# ---------- slippage 는 limit/stop 에 적용되지 않음 -------------------------


def test_limit_fill_ignores_slippage_bps() -> None:
    """slippage_bps 는 market 에만 — limit 체결 가격은 OHLC 기반 정확값."""
    em = NextBarOpenExecution(slippage_bps=Decimal("100"))  # 1% slip
    order = _order("limit", "buy", limit_price=Decimal("100"))
    fill = em.try_fill(order, _snap("99", "101", "98", "100"), _btc())
    assert fill is not None
    # slippage 적용 안 됨 → open=99 그대로
    assert fill.price == Decimal("99")


def test_stop_fill_ignores_slippage_bps() -> None:
    em = NextBarOpenExecution(slippage_bps=Decimal("100"))
    order = _order("stop", "buy", stop_price=Decimal("100"))
    fill = em.try_fill(order, _snap("99", "101", "98", "100.5"), _btc())
    assert fill is not None
    # stop trigger 봉 안에서 → fill at S=100, slippage 적용 안 됨
    assert fill.price == Decimal("100")
