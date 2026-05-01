"""Bybit linear perp preset 회귀.

검증:
1. 상위 10 심볼 모두 ``bybit_linear_perp(symbol)`` 으로 생성 가능.
2. unknown symbol → ValueError.
3. preset Instrument 가 FeeModel + ExchangeRule + MarginModel 모두 포함.
4. 전용 편의 함수 (``bybit_btcusdt_perp()`` 등) 가 동일 결과.
5. ExchangeRule.qty_step floor / min_notional reject 가 PR O Sizer 와 연결.
6. ``available_bybit_linear_symbols()`` 가 sorted list.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtester.instruments import (
    available_bybit_linear_symbols,
    bybit_adausdt_perp,
    bybit_avaxusdt_perp,
    bybit_bnbusdt_perp,
    bybit_btcusdt_perp,
    bybit_dogeusdt_perp,
    bybit_ethusdt_perp,
    bybit_linear_perp,
    bybit_linkusdt_perp,
    bybit_solusdt_perp,
    bybit_tonusdt_perp,
    bybit_xrpusdt_perp,
)

# ---------- 1. 상위 10 심볼 모두 생성 가능 ---------------------------------


@pytest.mark.parametrize(
    "symbol",
    [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "BNBUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "TONUSDT",
    ],
)
def test_bybit_linear_perp_preset_constructs(symbol: str) -> None:
    inst = bybit_linear_perp(symbol)
    assert inst.symbol == symbol
    assert inst.asset_class == "crypto_perp"
    assert inst.quote_currency == "USDT"
    assert inst.size_unit == "base_asset"
    assert inst.fee_model is not None
    assert inst.exchange_rule is not None
    assert inst.margin_model is not None


# ---------- 2. unknown symbol ---------------------------------------------


def test_bybit_linear_perp_unknown_symbol_raises() -> None:
    with pytest.raises(ValueError, match="unknown Bybit"):
        bybit_linear_perp("UNKNOWNUSDT")


# ---------- 3. spec 값 검증 (DB-driven) ------------------------------------


def test_bybit_btcusdt_preset_values() -> None:
    inst = bybit_btcusdt_perp()
    assert inst.base_currency == "BTC"
    rule = inst.exchange_rule
    assert rule is not None
    assert rule.price_tick == Decimal("0.1")
    assert rule.qty_step == Decimal("0.001")
    assert rule.min_qty == Decimal("0.001")
    assert rule.min_notional == Decimal("5")
    assert rule.max_leverage == Decimal("100")


def test_bybit_ethusdt_preset_values() -> None:
    inst = bybit_ethusdt_perp()
    assert inst.base_currency == "ETH"
    rule = inst.exchange_rule
    assert rule is not None
    assert rule.price_tick == Decimal("0.01")
    assert rule.qty_step == Decimal("0.01")
    assert rule.min_qty == Decimal("0.01")


def test_bybit_dogeusdt_preset_has_tiny_tick() -> None:
    """DOGE 의 tick=0.00001 — DB 값."""
    inst = bybit_dogeusdt_perp()
    rule = inst.exchange_rule
    assert rule is not None
    assert rule.price_tick == Decimal("0.00001")
    assert rule.qty_step == Decimal("1")


def test_bybit_xrpusdt_preset_values() -> None:
    inst = bybit_xrpusdt_perp()
    rule = inst.exchange_rule
    assert rule is not None
    assert rule.price_tick == Decimal("0.0001")
    assert rule.qty_step == Decimal("0.1")


# ---------- 4. fee / margin 표준값 -----------------------------------------


def test_preset_fee_model_default_values() -> None:
    inst = bybit_btcusdt_perp()
    assert inst.fee_model.taker == Decimal("0.0006")
    assert inst.fee_model.maker == Decimal("0.0001")
    assert inst.fee_model.type == "flat"


def test_preset_margin_model_default_values() -> None:
    inst = bybit_btcusdt_perp()
    mm = inst.margin_model
    assert mm is not None
    assert mm.maintenance_margin_rate == Decimal("0.005")
    assert mm.liquidation_fee_rate == Decimal("0.0006")


# ---------- 5. 편의 함수 = generic 결과 동일 -------------------------------


def test_convenience_functions_match_generic() -> None:
    pairs = [
        (bybit_btcusdt_perp, "BTCUSDT"),
        (bybit_ethusdt_perp, "ETHUSDT"),
        (bybit_solusdt_perp, "SOLUSDT"),
        (bybit_xrpusdt_perp, "XRPUSDT"),
        (bybit_bnbusdt_perp, "BNBUSDT"),
        (bybit_dogeusdt_perp, "DOGEUSDT"),
        (bybit_adausdt_perp, "ADAUSDT"),
        (bybit_avaxusdt_perp, "AVAXUSDT"),
        (bybit_linkusdt_perp, "LINKUSDT"),
        (bybit_tonusdt_perp, "TONUSDT"),
    ]
    for fn, sym in pairs:
        assert fn().symbol == sym
        assert fn() == bybit_linear_perp(sym)


# ---------- 6. available_bybit_linear_symbols ------------------------------


def test_available_symbols_sorted_and_includes_top_10() -> None:
    syms = available_bybit_linear_symbols()
    assert syms == sorted(syms)
    expected = {
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "BNBUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "TONUSDT",
    }
    assert expected.issubset(set(syms))


# ---------- 7. ExchangeRule + Sizer 연결 ----------------------------------


def test_preset_rule_floor_quantize_via_sizer() -> None:
    """preset 의 ExchangeRule 이 Sizer floor quantize 적용 시 절삭 동작."""
    from datetime import datetime, timezone

    from backtester.core.orders import OrderIntent, TargetUnits
    from backtester.core.snapshot import MarketSnapshot
    from backtester.portfolio.position import Position
    from backtester.portfolio.sizer import Sizer

    inst = bybit_btcusdt_perp()
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    market = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=Decimal("100"),
        high=Decimal("100.5"),
        low=Decimal("99.5"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        # 0.0567 → floor 0.001 → 0.056. 0.056 * 100 = 5.6 ≥ min_notional 5.
        size_spec=TargetUnits(units=Decimal("0.0567")),
    )
    out = sizer.resolve(intent, inst, Decimal("100000"), pos, market)
    # qty_step=0.001 → floor 0.0567 → 0.056
    assert out == Decimal("0.056")


def test_preset_rule_min_qty_rejection() -> None:
    """preset min_qty 미달 → ValueError."""
    from datetime import datetime, timezone

    from backtester.core.orders import OrderIntent, TargetUnits
    from backtester.core.snapshot import MarketSnapshot
    from backtester.portfolio.position import Position
    from backtester.portfolio.sizer import Sizer

    inst = bybit_btcusdt_perp()  # min_qty=0.001
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    market = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=Decimal("100"),
        high=Decimal("100.5"),
        low=Decimal("99.5"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("0.0001")),  # < 0.001
    )
    with pytest.raises(ValueError, match="min_qty"):
        sizer.resolve(intent, inst, Decimal("100000"), pos, market)
