"""PR 5 Sizer 테스트."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backtester.core.errors import DataError
from backtester.core.orders import (
    ClosePosition,
    FullPosition,
    OrderIntent,
    ScaleIn,
    TargetNotional,
    TargetUnits,
    TargetWeight,
)
from backtester.core.snapshot import MarketSnapshot
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.position import Position
from backtester.portfolio.sizer import Sizer

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


def _snap(close: str = "50000") -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=TS,
        open=Decimal("50000"),
        high=Decimal("50100"),
        low=Decimal("49900"),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _intent(
    side: str = "buy",
    size_spec: object = TargetUnits(units=Decimal("1")),
) -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
        type="market",
        size_spec=size_spec,  # type: ignore[arg-type]
    )


# ---------- Phase 1 지원 SizeSpec -------------------------------------------


def test_sizer_target_units_buy() -> None:
    sizer = Sizer()
    units = sizer.resolve(
        intent=_intent("buy", TargetUnits(units=Decimal("2.5"))),
        instrument=_btc(),
        equity=Decimal("100000"),
        position=Position(symbol="BTCUSDT"),
        market=_snap(),
    )
    assert units == Decimal("2.5")


def test_sizer_target_notional_converts_via_close_price() -> None:
    sizer = Sizer()
    # notional=$25000 / close=$50000 = 0.5 units
    units = sizer.resolve(
        intent=_intent("buy", TargetNotional(notional=Decimal("25000"))),
        instrument=_btc(),
        equity=Decimal("100000"),
        position=Position(symbol="BTCUSDT"),
        market=_snap("50000"),
    )
    assert units == Decimal("0.5")


def test_sizer_target_notional_rejects_zero_or_negative() -> None:
    sizer = Sizer()
    for bad in [Decimal("0"), Decimal("-1")]:
        with pytest.raises(DataError, match="notional must be > 0"):
            sizer.resolve(
                intent=_intent("buy", TargetNotional(notional=bad)),
                instrument=_btc(),
                equity=Decimal("100000"),
                position=Position(symbol="BTCUSDT"),
                market=_snap(),
            )


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1"), Decimal("-0.5")])
def test_sizer_target_units_rejects_zero_or_negative(bad: Decimal) -> None:
    """Sizer 계약: 절대 수량 반환. TargetUnits.units가 0/음수면 DataError로 차단."""
    sizer = Sizer()
    with pytest.raises(DataError, match="must be > 0"):
        sizer.resolve(
            intent=_intent("buy", TargetUnits(units=bad)),
            instrument=_btc(),
            equity=Decimal("100000"),
            position=Position(symbol="BTCUSDT"),
            market=_snap(),
        )


def test_sizer_target_notional_rejects_non_positive_close() -> None:
    sizer = Sizer()
    with pytest.raises(DataError, match="non-positive close"):
        sizer.resolve(
            intent=_intent("buy", TargetNotional(notional=Decimal("1000"))),
            instrument=_btc(),
            equity=Decimal("100000"),
            position=Position(symbol="BTCUSDT"),
            market=_snap("0"),
        )


def test_sizer_close_position_returns_abs_size() -> None:
    sizer = Sizer()
    position = Position(symbol="BTCUSDT", size=Decimal("1.5"))
    units = sizer.resolve(
        intent=_intent("sell", ClosePosition()),
        instrument=_btc(),
        equity=Decimal("100000"),
        position=position,
        market=_snap(),
    )
    assert units == Decimal("1.5")


def test_sizer_close_position_on_flat_returns_zero() -> None:
    sizer = Sizer()
    units = sizer.resolve(
        intent=_intent("sell", ClosePosition()),
        instrument=_btc(),
        equity=Decimal("100000"),
        position=Position(symbol="BTCUSDT"),
        market=_snap(),
    )
    assert units == Decimal("0")


# ---------- Phase 2 SizeSpec — NotImplementedError --------------------------


@pytest.mark.parametrize(
    "spec",
    [
        TargetWeight(weight=Decimal("0.5")),
        FullPosition(),
        ScaleIn(by=Decimal("0.5")),
    ],
)
def test_sizer_phase2_specs_raise(spec: object) -> None:
    sizer = Sizer()
    with pytest.raises(NotImplementedError, match="Phase 2"):
        sizer.resolve(
            intent=_intent("buy", spec),
            instrument=_btc(),
            equity=Decimal("100000"),
            position=Position(symbol="BTCUSDT"),
            market=_snap(),
        )


# ---------- Phase 1 short 차단 ----------------------------------------------


def test_sizer_blocks_short_entry_target_units() -> None:
    """flat에서 sell 시도 → short — NotImplementedError."""
    sizer = Sizer()
    with pytest.raises(NotImplementedError, match="short not supported in Phase 1"):
        sizer.resolve(
            intent=_intent("sell", TargetUnits(units=Decimal("1"))),
            instrument=_btc(),
            equity=Decimal("100000"),
            position=Position(symbol="BTCUSDT"),  # flat
            market=_snap(),
        )


def test_sizer_blocks_oversell_target_units() -> None:
    """long 1단위 보유, 2단위 매도 시도 → short — NotImplementedError."""
    sizer = Sizer()
    with pytest.raises(NotImplementedError, match="short not supported"):
        sizer.resolve(
            intent=_intent("sell", TargetUnits(units=Decimal("2"))),
            instrument=_btc(),
            equity=Decimal("100000"),
            position=Position(symbol="BTCUSDT", size=Decimal("1")),
            market=_snap(),
        )


def test_sizer_blocks_short_entry_target_notional() -> None:
    sizer = Sizer()
    with pytest.raises(NotImplementedError, match="short"):
        sizer.resolve(
            intent=_intent("sell", TargetNotional(notional=Decimal("50000"))),
            instrument=_btc(),
            equity=Decimal("100000"),
            position=Position(symbol="BTCUSDT"),  # flat
            market=_snap("50000"),
        )


def test_sizer_partial_close_via_target_units_allowed() -> None:
    """long 2단위 보유, 1단위 매도 → 1단위 long 잔존 — 정상 처리."""
    sizer = Sizer()
    units = sizer.resolve(
        intent=_intent("sell", TargetUnits(units=Decimal("1"))),
        instrument=_btc(),
        equity=Decimal("100000"),
        position=Position(symbol="BTCUSDT", size=Decimal("2")),
        market=_snap(),
    )
    assert units == Decimal("1")


def test_sizer_full_close_via_target_units_allowed() -> None:
    """long 1단위 보유, 1단위 매도 (정확히 청산) — 정상 처리."""
    sizer = Sizer()
    units = sizer.resolve(
        intent=_intent("sell", TargetUnits(units=Decimal("1"))),
        instrument=_btc(),
        equity=Decimal("100000"),
        position=Position(symbol="BTCUSDT", size=Decimal("1")),
        market=_snap(),
    )
    assert units == Decimal("1")
