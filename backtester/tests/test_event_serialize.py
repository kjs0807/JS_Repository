"""PR 6 serialize_event_payload 테스트 (spec §20 PR 6 acceptance)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

import pytest

from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.types import Fill
from backtester.events.serialize import serialize_event_payload
from backtester.events.types import EventType, IntentCreatedPayload

UTC = timezone.utc


# ---------- 기본 타입 -------------------------------------------------------


def test_serialize_none() -> None:
    assert serialize_event_payload(None) is None


def test_serialize_bool() -> None:
    assert serialize_event_payload(True) is True
    assert serialize_event_payload(False) is False


def test_serialize_int() -> None:
    assert serialize_event_payload(42) == 42
    assert serialize_event_payload(-7) == -7


def test_serialize_str() -> None:
    assert serialize_event_payload("hello") == "hello"


def test_serialize_float() -> None:
    assert serialize_event_payload(3.14) == 3.14


# ---------- Decimal ---------------------------------------------------------


def test_serialize_decimal_to_str() -> None:
    """spec §3.15: Decimal → str (정확성 보존)."""
    assert serialize_event_payload(Decimal("1.5")) == "1.5"
    assert serialize_event_payload(Decimal("0")) == "0"
    assert serialize_event_payload(Decimal("-50000.123")) == "-50000.123"


# ---------- datetime / date -------------------------------------------------


def test_serialize_datetime_to_isoformat() -> None:
    ts = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
    assert serialize_event_payload(ts) == ts.isoformat()


def test_serialize_date_to_isoformat() -> None:
    d = date(2026, 1, 1)
    assert serialize_event_payload(d) == "2026-01-01"


# ---------- Enum ------------------------------------------------------------


def test_serialize_str_enum_uses_value() -> None:
    """EventType은 (str, Enum) — Enum 분기에 먼저 걸려 .value 반환."""
    assert serialize_event_payload(EventType.FILL) == "fill"
    assert serialize_event_payload(EventType.SNAPSHOT) == "snapshot"


def test_serialize_plain_enum_uses_value() -> None:
    class Color(Enum):
        RED = 1
        BLUE = 2

    assert serialize_event_payload(Color.RED) == 1
    assert serialize_event_payload(Color.BLUE) == 2


# ---------- dataclass -------------------------------------------------------


def test_serialize_simple_dataclass() -> None:
    spec = TargetUnits(units=Decimal("1.5"))
    assert serialize_event_payload(spec) == {"units": "1.5"}


def test_serialize_full_fill_dataclass() -> None:
    fill = Fill(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        symbol="BTCUSDT",
        price=Decimal("50000"),
        size=Decimal("1"),
        side="buy",
        fee=Decimal("0.5"),
        fee_currency="USDT",
        order_id="ord_0",
        intent_reason="entry",
    )
    result = serialize_event_payload(fill)
    assert result["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert result["price"] == "50000"
    assert result["size"] == "1"
    assert result["fee"] == "0.5"
    assert result["side"] == "buy"
    assert result["order_id"] == "ord_0"


def test_serialize_intent_created_payload_nested() -> None:
    """OrderIntent + size_spec(TargetUnits dataclass) + datetime + Decimal 모두 round-trip."""
    payload = IntentCreatedPayload(
        intent=OrderIntent(
            symbol="BTCUSDT",
            side="buy",
            type="market",
            size_spec=TargetUnits(units=Decimal("1.5")),
        ),
        decision_ts=datetime(2026, 1, 1, 14, tzinfo=UTC),
        bar_timestamp=datetime(2026, 1, 1, 13, tzinfo=UTC),
        bar_close_price=Decimal("50000"),
    )
    result = serialize_event_payload(payload)
    assert result["bar_close_price"] == "50000"
    assert result["decision_ts"] == "2026-01-01T14:00:00+00:00"
    assert result["bar_timestamp"] == "2026-01-01T13:00:00+00:00"
    # nested intent
    assert result["intent"]["symbol"] == "BTCUSDT"
    assert result["intent"]["side"] == "buy"
    assert result["intent"]["type"] == "market"
    # nested size_spec dataclass
    assert result["intent"]["size_spec"] == {"units": "1.5"}
    assert result["intent"]["limit_price"] is None
    assert result["intent"]["expires_at"] is None


# ---------- 컨테이너 -------------------------------------------------------


def test_serialize_dict_recurses() -> None:
    data = {"price": Decimal("50000"), "size": Decimal("1")}
    assert serialize_event_payload(data) == {"price": "50000", "size": "1"}


def test_serialize_list_recurses() -> None:
    data: list[Any] = [Decimal("1"), Decimal("2"), Decimal("3")]
    assert serialize_event_payload(data) == ["1", "2", "3"]


def test_serialize_tuple_returns_list() -> None:
    """tuple은 list로 변환 (JSON에 tuple 없음)."""
    assert serialize_event_payload((1, "a", Decimal("3"))) == [1, "a", "3"]


def test_serialize_set_returns_list() -> None:
    result = serialize_event_payload({Decimal("1"), Decimal("2")})
    assert isinstance(result, list)
    assert sorted(result) == ["1", "2"]


def test_serialize_frozenset_returns_list() -> None:
    result = serialize_event_payload(frozenset({"a", "b"}))
    assert isinstance(result, list)
    assert sorted(result) == ["a", "b"]


# ---------- 미지원 타입 -----------------------------------------------------


def test_serialize_unsupported_type_raises() -> None:
    class Foo:
        pass

    with pytest.raises(TypeError, match="Cannot serialize"):
        serialize_event_payload(Foo())


# ---------- Round-trip + 깊이 -----------------------------------------------


def test_serialize_dataclass_with_default_factory() -> None:
    """default_factory로 생성된 dict 필드도 정상 처리."""

    @dataclass
    class WithMeta:
        name: str
        meta: dict[str, Any] = field(default_factory=dict)

    obj = WithMeta(name="x", meta={"k": Decimal("1")})
    result = serialize_event_payload(obj)
    assert result == {"name": "x", "meta": {"k": "1"}}
