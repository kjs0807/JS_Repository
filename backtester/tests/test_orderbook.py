"""PR 4 OrderBook 테스트 (spec §20 PR 4 acceptance)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtester.core.orderbook import Order, OrderBook
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.types import Fill

UTC = timezone.utc
TS = datetime(2026, 1, 1, 14, tzinfo=UTC)


def _market_buy_intent(symbol: str = "BTCUSDT", units: str = "1") -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal(units)),
    )


def _fill(order_id: str, size: str, ts: datetime = TS) -> Fill:
    return Fill(
        timestamp=ts,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        size=Decimal(size),
        side="buy",
        fee=Decimal("0.5"),
        fee_currency="USDT",
        order_id=order_id,
        intent_reason="entry",
    )


# ---------- add -------------------------------------------------------------


def test_orderbook_add_returns_pending_order() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), sized_quantity=Decimal("1"), ts=TS)
    assert order.state == "pending"
    assert order.sized_quantity == Decimal("1")
    assert order.remaining == Decimal("1")
    assert order.fills == []
    assert order.is_active
    assert not order.is_terminal


def test_orderbook_add_assigns_unique_ids() -> None:
    ob = OrderBook()
    o1 = ob.add(_market_buy_intent(), Decimal("1"), TS)
    o2 = ob.add(_market_buy_intent(), Decimal("2"), TS)
    assert o1.id != o2.id
    assert len(ob) == 2


# ---------- 주문 타입별 정상 진입 (PR 15b) ----------------------------------


@pytest.mark.parametrize(
    "order_type, limit_price, stop_price",
    [
        ("limit", Decimal("50000"), None),
        ("stop", None, Decimal("50000")),
        ("stop_limit", Decimal("50000"), Decimal("49500")),
    ],
)
def test_orderbook_add_accepts_limit_stop_variants(
    order_type: str,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
) -> None:
    """PR 15b: limit / stop / stop_limit 정상 진입 + price 필드 보존."""
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type=order_type,  # type: ignore[arg-type]
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=limit_price,
        stop_price=stop_price,
    )
    order = ob.add(intent, Decimal("1"), TS)
    assert order.state == "pending"
    assert order.intent.type == order_type
    assert order.intent.limit_price == limit_price
    assert order.intent.stop_price == stop_price


# ---------- TIF / expires_at / price 정합성 가드 ----------------------------


@pytest.mark.parametrize("tif", ["IOC", "FOK", "DAY"])
def test_orderbook_add_rejects_non_gtc_tif(tif: str) -> None:
    """PR 15b 까지는 tif='GTC'만 허용. IOC/FOK/DAY 는 후속 PR."""
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
        tif=tif,  # type: ignore[arg-type]
    )
    with pytest.raises(NotImplementedError, match="GTC"):
        ob.add(intent, Decimal("1"), TS)


def test_orderbook_add_accepts_expires_at_in_future() -> None:
    """PR D: ``expires_at`` 활성. ``ts < expires_at`` 이면 정상 add."""
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
        expires_at=TS + timedelta(days=1),
    )
    order = ob.add(intent, Decimal("1"), TS)
    assert order.is_active
    assert order.intent.expires_at == TS + timedelta(days=1)


def test_orderbook_add_rejects_expires_at_past() -> None:
    """PR D: ``expires_at <= submit ts`` 는 ``ValueError`` (이미 만료된 주문 거부)."""
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
        expires_at=TS - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="expires_at"):
        ob.add(intent, Decimal("1"), TS)


def test_orderbook_add_rejects_limit_price_on_market() -> None:
    """market 은 limit_price/stop_price 가 None 이어야 한다."""
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000"),
    )
    with pytest.raises(ValueError, match="market order must not have"):
        ob.add(intent, Decimal("1"), TS)


def test_orderbook_add_rejects_limit_without_limit_price() -> None:
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=None,
    )
    with pytest.raises(ValueError, match="limit order requires limit_price"):
        ob.add(intent, Decimal("1"), TS)


def test_orderbook_add_rejects_stop_without_stop_price() -> None:
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="stop",
        size_spec=TargetUnits(units=Decimal("1")),
        stop_price=None,
    )
    with pytest.raises(ValueError, match="stop order requires stop_price"):
        ob.add(intent, Decimal("1"), TS)


def test_orderbook_add_rejects_stop_limit_missing_either_price() -> None:
    ob = OrderBook()
    intent_only_limit = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="stop_limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000"),
        stop_price=None,
    )
    with pytest.raises(ValueError, match="stop_limit"):
        ob.add(intent_only_limit, Decimal("1"), TS)
    intent_only_stop = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="stop_limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=None,
        stop_price=Decimal("49500"),
    )
    with pytest.raises(ValueError, match="stop_limit"):
        ob.add(intent_only_stop, Decimal("1"), TS)


def test_orderbook_add_rejects_limit_with_stop_price_set() -> None:
    """limit 은 stop_price 가 None 이어야 한다 (stop_limit 와 구분)."""
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000"),
        stop_price=Decimal("49500"),
    )
    with pytest.raises(ValueError, match="limit order must not have stop_price"):
        ob.add(intent, Decimal("1"), TS)


@pytest.mark.parametrize("bad_size", ["0", "-1", "-0.5"])
def test_orderbook_add_rejects_non_positive_sized_quantity(bad_size: str) -> None:
    """sized_quantity는 절대값(Sizer 출력) — 0이나 음수는 ValueError."""
    ob = OrderBook()
    with pytest.raises(ValueError, match="must be > 0"):
        ob.add(_market_buy_intent(), Decimal(bad_size), TS)


# ---------- Fill ↔ Order 정합성 ---------------------------------------------


def test_orderbook_fill_rejects_wrong_order_id_in_fill() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    bad_fill = _fill("ord_999", "1")  # 다른 order_id
    with pytest.raises(ValueError, match="Fill.order_id mismatch"):
        ob.fill(order.id, bad_fill)


def test_orderbook_fill_rejects_wrong_symbol() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent("BTCUSDT"), Decimal("1"), TS)
    bad_fill = Fill(
        timestamp=TS,
        symbol="ETHUSDT",  # mismatch
        price=Decimal("3000"),
        size=Decimal("1"),
        side="buy",
        fee=Decimal("0.5"),
        fee_currency="USDT",
        order_id=order.id,
        intent_reason="entry",
    )
    with pytest.raises(ValueError, match="Fill.symbol mismatch"):
        ob.fill(order.id, bad_fill)


def test_orderbook_fill_rejects_wrong_side() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    bad_fill = Fill(
        timestamp=TS,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        size=Decimal("1"),
        side="sell",  # mismatch (order is buy)
        fee=Decimal("0.5"),
        fee_currency="USDT",
        order_id=order.id,
        intent_reason="entry",
    )
    with pytest.raises(ValueError, match="Fill.side mismatch"):
        ob.fill(order.id, bad_fill)


# ---------- cancel ----------------------------------------------------------


def test_orderbook_cancel_pending_returns_true() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    assert ob.cancel(order.id, TS) is True
    assert order.state == "cancelled"
    assert not order.is_active


def test_orderbook_cancel_unknown_returns_false() -> None:
    ob = OrderBook()
    assert ob.cancel("ord_999", TS) is False


def test_orderbook_cancel_already_filled_returns_false() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    ob.fill(order.id, _fill(order.id, "1"))
    assert order.state == "filled"
    assert ob.cancel(order.id, TS) is False
    assert order.state == "filled"  # unchanged


def test_orderbook_cancel_already_cancelled_returns_false() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    ob.cancel(order.id, TS)
    assert ob.cancel(order.id, TS) is False


# ---------- get_active ------------------------------------------------------


def test_orderbook_get_active_includes_pending_and_partial() -> None:
    ob = OrderBook()
    pending = ob.add(_market_buy_intent(), Decimal("1"), TS)
    partial = ob.add(_market_buy_intent(units="2"), Decimal("2"), TS)
    ob.fill(partial.id, _fill(partial.id, "1"))  # 절반 체결
    assert partial.state == "partially_filled"

    actives = ob.get_active()
    assert {o.id for o in actives} == {pending.id, partial.id}


def test_orderbook_get_active_excludes_terminal() -> None:
    ob = OrderBook()
    cancelled = ob.add(_market_buy_intent(), Decimal("1"), TS)
    filled = ob.add(_market_buy_intent(), Decimal("1"), TS)
    ob.cancel(cancelled.id, TS)
    ob.fill(filled.id, _fill(filled.id, "1"))

    assert ob.get_active() == []


def test_orderbook_get_active_filter_by_symbol() -> None:
    ob = OrderBook()
    btc = ob.add(_market_buy_intent("BTCUSDT"), Decimal("1"), TS)
    eth = ob.add(_market_buy_intent("ETHUSDT"), Decimal("1"), TS)

    btc_only = ob.get_active(symbol="BTCUSDT")
    assert [o.id for o in btc_only] == [btc.id]
    eth_only = ob.get_active(symbol="ETHUSDT")
    assert [o.id for o in eth_only] == [eth.id]


# ---------- fill ------------------------------------------------------------


def test_orderbook_fill_full_transitions_to_filled() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    ob.fill(order.id, _fill(order.id, "1"))
    assert order.state == "filled"
    assert order.remaining == Decimal("0")
    assert len(order.fills) == 1


def test_orderbook_fill_partial_then_full() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(units="2"), Decimal("2"), TS)

    # 부분 체결 후 상태/잔량 캡처 (assert 전에 변수 추출 — mypy literal narrowing 회피)
    ob.fill(order.id, _fill(order.id, "0.5"))
    state_after_partial = order.state
    remaining_after_partial = order.remaining

    ob.fill(order.id, _fill(order.id, "1.5"))
    state_after_full = order.state
    remaining_after_full = order.remaining

    assert state_after_partial == "partially_filled"
    assert remaining_after_partial == Decimal("1.5")
    assert state_after_full == "filled"
    assert remaining_after_full == Decimal("0")
    assert len(order.fills) == 2


def test_orderbook_fill_unknown_raises() -> None:
    ob = OrderBook()
    with pytest.raises(KeyError, match="not found"):
        ob.fill("ord_999", _fill("ord_999", "1"))


def test_orderbook_fill_terminal_raises() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    ob.cancel(order.id, TS)
    with pytest.raises(RuntimeError, match="terminal state"):
        ob.fill(order.id, _fill(order.id, "1"))


def test_orderbook_fill_overfill_raises() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    with pytest.raises(ValueError, match="exceeds remaining"):
        ob.fill(order.id, _fill(order.id, "2"))


def test_orderbook_fill_zero_size_raises() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    with pytest.raises(ValueError, match="must be positive"):
        ob.fill(order.id, _fill(order.id, "0"))


# ---------- modify (PR D) ---------------------------------------------------


def _limit_buy_intent() -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000"),
    )


def _stop_buy_intent() -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="stop",
        size_spec=TargetUnits(units=Decimal("1")),
        stop_price=Decimal("60000"),
    )


def _stop_limit_buy_intent() -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="stop_limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000"),
        stop_price=Decimal("60000"),
    )


def test_orderbook_modify_limit_price() -> None:
    ob = OrderBook()
    order = ob.add(_limit_buy_intent(), Decimal("1"), TS)
    assert ob.modify(order.id, limit_price=Decimal("48000")) is True
    assert order.intent.limit_price == Decimal("48000")


def test_orderbook_modify_stop_price() -> None:
    ob = OrderBook()
    order = ob.add(_stop_buy_intent(), Decimal("1"), TS)
    assert ob.modify(order.id, stop_price=Decimal("65000")) is True
    assert order.intent.stop_price == Decimal("65000")


def test_orderbook_modify_stop_limit_both_prices() -> None:
    ob = OrderBook()
    order = ob.add(_stop_limit_buy_intent(), Decimal("1"), TS)
    assert ob.modify(
        order.id,
        limit_price=Decimal("48000"),
        stop_price=Decimal("65000"),
    ) is True
    assert order.intent.limit_price == Decimal("48000")
    assert order.intent.stop_price == Decimal("65000")


def test_orderbook_modify_market_raises() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    with pytest.raises(ValueError, match="market"):
        ob.modify(order.id, limit_price=Decimal("50000"))


def test_orderbook_modify_limit_with_stop_price_raises() -> None:
    """limit 주문에 stop_price modify 시도 → ValueError."""
    ob = OrderBook()
    order = ob.add(_limit_buy_intent(), Decimal("1"), TS)
    with pytest.raises(ValueError, match="stop_price"):
        ob.modify(order.id, stop_price=Decimal("60000"))


def test_orderbook_modify_no_changes_returns_false() -> None:
    ob = OrderBook()
    order = ob.add(_limit_buy_intent(), Decimal("1"), TS)
    assert ob.modify(order.id) is False


def test_orderbook_modify_unknown_id_returns_false() -> None:
    ob = OrderBook()
    assert ob.modify("ord_99", limit_price=Decimal("100")) is False


def test_orderbook_modify_terminal_returns_false() -> None:
    ob = OrderBook()
    order = ob.add(_limit_buy_intent(), Decimal("1"), TS)
    ob.cancel(order.id, TS)
    assert ob.modify(order.id, limit_price=Decimal("48000")) is False


# ---------- expire_pending (Phase 1.5+) -------------------------------------


def test_orderbook_expire_pending_no_expires_at_returns_empty() -> None:
    """``expires_at=None`` (GTC) 주문은 영원히 active — expire_pending 영향 없음."""
    ob = OrderBook()
    ob.add(_market_buy_intent(), Decimal("1"), TS)
    ob.add(_market_buy_intent(), Decimal("1"), TS)

    result = ob.expire_pending(TS + timedelta(days=365))
    assert result == []
    # 그 어떤 주문도 expired로 전이되지 않았다
    for order in ob.get_active():
        assert order.state == "pending"


def test_orderbook_expire_pending_with_expires_at() -> None:
    """PR D: ``expires_at <= ts`` 인 active 주문은 expired 로 전이."""
    ob = OrderBook()
    expires_intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000"),
        expires_at=TS + timedelta(hours=1),
    )
    gtc_intent = _limit_buy_intent()  # expires_at=None
    expiring = ob.add(expires_intent, Decimal("1"), TS)
    keeping = ob.add(gtc_intent, Decimal("1"), TS)

    # ts 가 expires_at 미만 → 만료 없음
    assert ob.expire_pending(TS) == []
    assert expiring.is_active

    # ts >= expires_at → 만료
    expired = ob.expire_pending(TS + timedelta(hours=2))
    assert len(expired) == 1
    assert expired[0].id == expiring.id
    assert expiring.state == "expired"
    assert keeping.state == "pending"


def test_orderbook_expire_pending_idempotent() -> None:
    """이미 expired 된 주문을 두 번째 호출 시 빈 리스트."""
    ob = OrderBook()
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="limit",
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=Decimal("50000"),
        expires_at=TS + timedelta(hours=1),
    )
    ob.add(intent, Decimal("1"), TS)
    first = ob.expire_pending(TS + timedelta(hours=2))
    assert len(first) == 1
    second = ob.expire_pending(TS + timedelta(hours=3))
    assert second == []  # 이미 expired


# ---------- get -------------------------------------------------------------


def test_orderbook_get_returns_order() -> None:
    ob = OrderBook()
    order = ob.add(_market_buy_intent(), Decimal("1"), TS)
    assert ob.get(order.id) is order


def test_orderbook_get_unknown_raises() -> None:
    ob = OrderBook()
    with pytest.raises(KeyError):
        ob.get("ord_999")


# ---------- Order properties ------------------------------------------------


def test_order_is_active_property() -> None:
    o = Order(
        id="ord_0",
        intent=_market_buy_intent(),
        state="pending",
        submitted_at=TS,
        sized_quantity=Decimal("1"),
        remaining=Decimal("1"),
    )
    assert o.is_active
    assert not o.is_terminal
    o.state = "filled"
    assert not o.is_active
    assert o.is_terminal
