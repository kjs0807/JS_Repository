"""ExecutionModel Protocol (spec §3.10).

Phase 1: 단일 시그니처 try_fill(order, snapshot, instrument). BarPathModel은 enum
정의만 두고 사용하지 않으므로 시그니처에 포함하지 않는다 (Phase 2에서 추가).

체결 결과 Fill을 반환하거나 미체결 시 None.
"""

from __future__ import annotations

from typing import Protocol

from backtester.core.orderbook import Order
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill
from backtester.instruments.base import Instrument


class ExecutionModel(Protocol):
    """주문을 시장 데이터로 체결하는 모델."""

    def try_fill(
        self,
        order: Order,
        snapshot: MarketSnapshot,
        instrument: Instrument,
    ) -> Fill | None: ...
