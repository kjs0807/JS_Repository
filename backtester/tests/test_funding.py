"""PR 9 FundingModel + FundingProcessor 테스트 (Phase 1.5 ``execution/funding.py``)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backtester.core.snapshot import MarketSnapshot
from backtester.execution.funding import (
    CashFlow,
    FundingModel,
    FundingProcessor,
    is_funding_boundary,
)
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.position import Position

UTC = timezone.utc


def _btc_perp() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _snapshot(close: Decimal, mark: Decimal | None = None) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("0"),
        mark_price=mark,
    )


# ---------- FundingModel 검증 ----------------------------------------------


def test_funding_model_accepts_valid_constant() -> None:
    m = FundingModel(
        interval_hours=8,
        rate_source="constant",
        constant_rate=Decimal("0.0001"),
    )
    assert m.interval_hours == 8
    assert m.constant_rate == Decimal("0.0001")


def test_funding_model_rejects_zero_or_negative_interval() -> None:
    with pytest.raises(ValueError, match="interval_hours"):
        FundingModel(interval_hours=0, rate_source="constant", constant_rate=Decimal("0.0001"))
    with pytest.raises(ValueError, match="interval_hours"):
        FundingModel(interval_hours=-1, rate_source="constant", constant_rate=Decimal("0.0001"))


def test_funding_model_rejects_interval_not_dividing_24() -> None:
    """5h 등 24의 약수가 아닌 interval은 hour-aligned boundary 가 매일 다른 시각에
    떨어져 백테스트 재현성에 위협 → 명시적으로 거부."""
    with pytest.raises(ValueError, match="must divide 24"):
        FundingModel(
            interval_hours=5, rate_source="constant", constant_rate=Decimal("0.0001")
        )


def test_funding_model_rejects_constant_without_rate() -> None:
    with pytest.raises(ValueError, match="constant_rate"):
        FundingModel(interval_hours=8, rate_source="constant")


def test_funding_model_rejects_unknown_rate_source() -> None:
    with pytest.raises(ValueError, match="rate_source"):
        FundingModel(interval_hours=8, rate_source="bogus")  # type: ignore[arg-type]


def test_funding_model_from_data_source_constant_rate_optional() -> None:
    """rate_source='from_data_source' 일 때는 constant_rate 없어도 OK."""
    m = FundingModel(interval_hours=8, rate_source="from_data_source")
    assert m.constant_rate is None


# ---------- is_funding_boundary --------------------------------------------


def test_is_funding_boundary_8h_matches_00_08_16() -> None:
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    assert is_funding_boundary(m, datetime(2026, 3, 1, 0, 0, tzinfo=UTC)) is True
    assert is_funding_boundary(m, datetime(2026, 3, 1, 8, 0, tzinfo=UTC)) is True
    assert is_funding_boundary(m, datetime(2026, 3, 1, 16, 0, tzinfo=UTC)) is True


def test_is_funding_boundary_8h_rejects_other_hours() -> None:
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    for hour in (1, 4, 7, 9, 15, 17, 23):
        assert is_funding_boundary(m, datetime(2026, 3, 1, hour, 0, tzinfo=UTC)) is False


def test_is_funding_boundary_rejects_minute_second() -> None:
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    assert is_funding_boundary(m, datetime(2026, 3, 1, 0, 1, tzinfo=UTC)) is False
    assert is_funding_boundary(m, datetime(2026, 3, 1, 0, 0, 1, tzinfo=UTC)) is False
    assert is_funding_boundary(m, datetime(2026, 3, 1, 0, 0, 0, 1, tzinfo=UTC)) is False


# ---------- FundingProcessor.process ----------------------------------------


def test_processor_returns_none_when_no_model_for_symbol() -> None:
    proc = FundingProcessor(models={})
    cf = proc.process(
        symbol="BTCUSDT",
        ts=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        instrument=_btc_perp(),
        position=Position(symbol="BTCUSDT", size=Decimal("1")),
        market=_snapshot(close=Decimal("50000")),
    )
    assert cf is None


def test_processor_returns_none_off_boundary() -> None:
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    proc = FundingProcessor(models={"BTCUSDT": m})
    cf = proc.process(
        symbol="BTCUSDT",
        ts=datetime(2026, 3, 1, 1, 0, tzinfo=UTC),  # off-boundary
        instrument=_btc_perp(),
        position=Position(symbol="BTCUSDT", size=Decimal("1")),
        market=_snapshot(close=Decimal("50000")),
    )
    assert cf is None


def test_processor_returns_none_when_position_flat() -> None:
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    proc = FundingProcessor(models={"BTCUSDT": m})
    cf = proc.process(
        symbol="BTCUSDT",
        ts=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        instrument=_btc_perp(),
        position=Position(symbol="BTCUSDT"),  # default size=0 → flat
        market=_snapshot(close=Decimal("50000")),
    )
    assert cf is None


def test_processor_long_position_pays_when_rate_positive() -> None:
    """LONG 보유 + rate>0 → LONG 이 funding 지불 (amount < 0)."""
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    proc = FundingProcessor(models={"BTCUSDT": m})
    cf = proc.process(
        symbol="BTCUSDT",
        ts=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        instrument=_btc_perp(),
        position=Position(symbol="BTCUSDT", size=Decimal("1")),
        market=_snapshot(close=Decimal("50000")),
    )
    assert cf is not None
    assert cf.kind == "funding"
    assert cf.symbol == "BTCUSDT"
    assert cf.rate == Decimal("0.0001")
    # amount = -1 * 50000 * 0.0001 = -5.0
    assert cf.amount == Decimal("-5.0000")


def test_processor_short_position_receives_when_rate_positive() -> None:
    """SHORT 보유 + rate>0 → SHORT 가 funding 수령 (amount > 0)."""
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    proc = FundingProcessor(models={"BTCUSDT": m})
    cf = proc.process(
        symbol="BTCUSDT",
        ts=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        instrument=_btc_perp(),
        position=Position(symbol="BTCUSDT", size=Decimal("-1")),
        market=_snapshot(close=Decimal("50000")),
    )
    assert cf is not None
    assert cf.amount == Decimal("5.0000")


def test_processor_uses_mark_price_when_available() -> None:
    """mark_price 가 주어지면 close 대신 그것으로 계산."""
    m = FundingModel(interval_hours=8, rate_source="constant", constant_rate=Decimal("0.0001"))
    proc = FundingProcessor(models={"BTCUSDT": m})
    cf = proc.process(
        symbol="BTCUSDT",
        ts=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        instrument=_btc_perp(),
        position=Position(symbol="BTCUSDT", size=Decimal("1")),
        market=_snapshot(close=Decimal("50000"), mark=Decimal("50100")),
    )
    assert cf is not None
    # mark 50100 우선
    assert cf.amount == Decimal("-5.0100")


def test_processor_from_data_source_raises_until_wired() -> None:
    """rate_source='from_data_source' 는 후속 PR 에서 wiring 예정이므로 명시적 NIE."""
    m = FundingModel(interval_hours=8, rate_source="from_data_source")
    proc = FundingProcessor(models={"BTCUSDT": m})
    with pytest.raises(NotImplementedError, match="from_data_source"):
        proc.process(
            symbol="BTCUSDT",
            ts=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
            instrument=_btc_perp(),
            position=Position(symbol="BTCUSDT", size=Decimal("1")),
            market=_snapshot(close=Decimal("50000")),
        )


def test_cashflow_immutable_dataclass() -> None:
    cf = CashFlow(
        symbol="BTCUSDT",
        ts=datetime(2026, 3, 1, tzinfo=UTC),
        amount=Decimal("-5"),
        rate=Decimal("0.0001"),
    )
    assert cf.kind == "funding"  # default
    with pytest.raises(FrozenInstanceError):  # frozen dataclass
        cf.amount = Decimal("10")  # type: ignore[misc]
