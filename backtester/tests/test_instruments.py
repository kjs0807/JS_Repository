"""PR 2 instruments 테스트."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtester.core.errors import InstrumentError
from backtester.instruments import FeeModel, Instrument, InstrumentRegistry

# ---------- FeeModel --------------------------------------------------------


def test_fee_model_flat_taker() -> None:
    fm = FeeModel(type="flat", taker=Decimal("0.001"))
    fee = fm.compute_fee(fill_notional=Decimal("10000"))
    assert fee == Decimal("10.000")


def test_fee_model_negative_notional_uses_abs() -> None:
    fm = FeeModel(type="flat", taker=Decimal("0.001"))
    # 청산(short close 등)으로 음수 notional이 들어와도 fee는 양수
    fee = fm.compute_fee(fill_notional=Decimal("-10000"))
    assert fee == Decimal("10.000")


def test_fee_model_uses_taker_when_not_maker() -> None:
    """Phase 2 PR 15a: is_maker=False (default) → taker rate."""
    fm = FeeModel(type="flat", taker=Decimal("0.001"), maker=Decimal("0.0005"))
    assert fm.compute_fee(Decimal("1000"), is_maker=False) == Decimal("1.000")
    # default 는 is_maker=False
    assert fm.compute_fee(Decimal("1000")) == Decimal("1.000")


def test_fee_model_uses_maker_when_is_maker_true() -> None:
    """Phase 2 PR 15a: is_maker=True → maker rate."""
    fm = FeeModel(type="flat", taker=Decimal("0.001"), maker=Decimal("0.0005"))
    assert fm.compute_fee(Decimal("1000"), is_maker=True) == Decimal("0.5000")


# ---------- Instrument ------------------------------------------------------


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


def test_instrument_creation() -> None:
    inst = _btc_perp()
    assert inst.symbol == "BTCUSDT"
    assert inst.size_unit == "base_asset"
    assert inst.fee_model.taker == Decimal("0.0006")


def test_instrument_is_frozen() -> None:
    import dataclasses

    inst = _btc_perp()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inst.symbol = "ETHUSDT"  # type: ignore[misc]


def test_instrument_phase2_5_optional_fields_present() -> None:
    """PR O / PR P 활성: ``exchange_rule`` / ``margin_model`` 은 Optional 필드로 추가.
    ``funding_model`` / ``trading_hours`` 는 여전히 Instrument 외부 (BacktestConfig
    .funding_models / 별도 trading session) 에 둔다.
    """
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(Instrument)}
    assert "exchange_rule" in field_names  # PR O
    assert "margin_model" in field_names  # PR P
    assert "funding_model" not in field_names  # config.funding_models 로 분리
    assert "trading_hours" not in field_names


# ---------- InstrumentRegistry ----------------------------------------------


def test_registry_register_and_get() -> None:
    reg = InstrumentRegistry()
    inst = _btc_perp()
    reg.register(inst)
    assert reg.has("BTCUSDT")
    assert reg.get("BTCUSDT") is inst


def test_registry_duplicate_register_raises() -> None:
    reg = InstrumentRegistry()
    reg.register(_btc_perp())
    with pytest.raises(InstrumentError, match="already registered"):
        reg.register(_btc_perp())


def test_registry_get_unknown_raises() -> None:
    reg = InstrumentRegistry()
    with pytest.raises(InstrumentError, match="not registered"):
        reg.get("ETHUSDT")


def test_registry_all_symbols_sorted() -> None:
    reg = InstrumentRegistry()
    reg.register(_btc_perp())
    eth = Instrument(
        symbol="ETHUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="ETH",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )
    reg.register(eth)
    assert reg.all_symbols() == ["BTCUSDT", "ETHUSDT"]
    assert len(reg) == 2
