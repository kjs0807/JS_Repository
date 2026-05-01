"""PR 5 RiskManager 테스트."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backtester.core.orderbook import Order, OrderBook
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.ledger import Ledger
from backtester.portfolio.risk import RiskCheckResult, RiskLimits, RiskManager

UTC = timezone.utc
TS = datetime(2026, 1, 1, 14, tzinfo=UTC)


def _btc() -> Instrument:
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


def _intent(symbol: str = "BTCUSDT") -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
    )


def _add_pending_orders(ob: OrderBook, symbol: str, count: int) -> list[Order]:
    return [ob.add(_intent(symbol), Decimal("1"), TS) for _ in range(count)]


# ---------- RiskCheckResult --------------------------------------------------


def test_risk_check_result_accept_factory() -> None:
    r = RiskCheckResult.accept()
    assert r.accepted
    assert r.reason == ""


def test_risk_check_result_reject_factory() -> None:
    r = RiskCheckResult.reject("blacklisted")
    assert not r.accepted
    assert r.reason == "blacklisted"


# ---------- blacklist --------------------------------------------------------


def test_risk_blacklist_rejects() -> None:
    rm = RiskManager(RiskLimits(blacklist_symbols=frozenset({"BTCUSDT"})))
    result = rm.check(
        intent=_intent("BTCUSDT"),
        sized_quantity=Decimal("1"),
        instrument=_btc(),
        ledger=Ledger(initial_equity=10000),
        active_orders=[],
    )
    assert not result.accepted
    assert "blacklisted" in result.reason


def test_risk_non_blacklisted_accepts() -> None:
    rm = RiskManager(RiskLimits(blacklist_symbols=frozenset({"DOGEUSDT"})))
    result = rm.check(
        intent=_intent("BTCUSDT"),
        sized_quantity=Decimal("1"),
        instrument=_btc(),
        ledger=Ledger(initial_equity=10000),
        active_orders=[],
    )
    assert result.accepted


# ---------- max_orders_per_symbol -------------------------------------------


def test_risk_max_orders_under_limit_accepts() -> None:
    rm = RiskManager(RiskLimits(max_orders_per_symbol=3))
    ob = OrderBook()
    actives = _add_pending_orders(ob, "BTCUSDT", 2)
    result = rm.check(
        intent=_intent(),
        sized_quantity=Decimal("1"),
        instrument=_btc(),
        ledger=Ledger(initial_equity=10000),
        active_orders=actives,
    )
    assert result.accepted


def test_risk_max_orders_at_limit_rejects() -> None:
    """현재 active 수가 limit과 같으면 추가 발주 거부 (>= 비교)."""
    rm = RiskManager(RiskLimits(max_orders_per_symbol=3))
    ob = OrderBook()
    actives = _add_pending_orders(ob, "BTCUSDT", 3)
    result = rm.check(
        intent=_intent(),
        sized_quantity=Decimal("1"),
        instrument=_btc(),
        ledger=Ledger(initial_equity=10000),
        active_orders=actives,
    )
    assert not result.accepted
    assert "max_orders_per_symbol" in result.reason


def test_risk_max_orders_filters_by_symbol() -> None:
    """다른 심볼 active는 카운트하지 않음."""
    rm = RiskManager(RiskLimits(max_orders_per_symbol=2))
    ob = OrderBook()
    actives = _add_pending_orders(ob, "ETHUSDT", 5)  # 5 ETH actives
    result = rm.check(
        intent=_intent("BTCUSDT"),
        sized_quantity=Decimal("1"),
        instrument=_btc(),
        ledger=Ledger(initial_equity=10000),
        active_orders=actives,
    )
    assert result.accepted


# ---------- Phase 2 한도는 정의만 (검사 안 함) -----------------------------


def test_risk_phase2_limits_now_enforced() -> None:
    """PR I: max_position_size / max_total_exposure / max_leverage 모두 활성.

    이전엔 Phase 1 에서 무시됐지만, PR I 부터 RiskManager.check 가 사이즈 적용 후
    새 포지션을 추정해 한도 검사한다. ``max_position_size`` 는 ``market_close``
    없이도 검사 가능하므로 가장 먼저 reject.
    """
    limits = RiskLimits(
        max_position_size=Decimal("0.001"),
        max_total_exposure=Decimal("100"),
        max_leverage=Decimal("1"),
    )
    rm = RiskManager(limits)
    result = rm.check(
        intent=_intent(),
        sized_quantity=Decimal("100"),
        instrument=_btc(),
        ledger=Ledger(initial_equity=10000),
        active_orders=[],
    )
    assert not result.accepted
    assert "max_position_size" in result.reason


def test_risk_default_limits_use_max_orders_5() -> None:
    """RiskLimits 기본값: max_orders_per_symbol=5, blacklist=empty."""
    limits = RiskLimits()
    assert limits.max_orders_per_symbol == 5
    assert limits.blacklist_symbols == frozenset()


# ---------- Pytest scope 도우미 ---------------------------------------------


def test_risk_check_returns_risk_check_result_type() -> None:
    rm = RiskManager(RiskLimits())
    result = rm.check(
        intent=_intent(),
        sized_quantity=Decimal("1"),
        instrument=_btc(),
        ledger=Ledger(initial_equity=10000),
        active_orders=[],
    )
    assert isinstance(result, RiskCheckResult)
