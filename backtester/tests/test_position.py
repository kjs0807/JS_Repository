"""PR 5 Position 테스트."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backtester.portfolio.position import Position

UTC = timezone.utc


def test_position_default_is_flat() -> None:
    p = Position(symbol="BTCUSDT")
    assert p.is_flat
    assert p.direction == "flat"
    assert p.size == Decimal("0")
    assert p.avg_price == Decimal("0")
    assert p.realized_pnl == Decimal("0")
    assert p.unrealized_pnl == Decimal("0")
    assert p.last_update is None


def test_position_long_direction() -> None:
    p = Position(symbol="BTCUSDT", size=Decimal("1.5"))
    assert not p.is_flat
    assert p.direction == "long"


def test_position_short_direction() -> None:
    """Phase 1에서는 short 포지션이 만들어지지 않지만 direction 메서드 자체는 검사."""
    p = Position(symbol="BTCUSDT", size=Decimal("-0.5"))
    assert p.direction == "short"


def test_position_is_effectively_flat_under_tick() -> None:
    """size가 tick_size 미만이면 effectively flat (spec §3.12)."""
    p = Position(symbol="BTCUSDT", size=Decimal("0.0001"))
    assert not p.is_flat  # exactly 0이 아니므로 is_flat은 False
    assert p.is_effectively_flat(tick_size=Decimal("0.001"))
    assert not p.is_effectively_flat(tick_size=Decimal("0.00001"))


def test_position_is_effectively_flat_negative_dust() -> None:
    """음수 더스트도 abs로 처리."""
    p = Position(symbol="BTCUSDT", size=Decimal("-0.0001"))
    assert p.is_effectively_flat(tick_size=Decimal("0.001"))


def test_position_last_update_assignable() -> None:
    """mutable dataclass — Ledger가 last_update를 갱신할 수 있어야 함."""
    p = Position(symbol="BTCUSDT")
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    p.last_update = ts
    assert p.last_update == ts
