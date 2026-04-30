"""MarketSnapshot — 봉 마감 시점의 시장 상태 (spec §3.11).

ClockEvent마다 활성 심볼별로 생성되어 ExecutionModel/Ledger의 입력이 된다.

Phase 1 범위:
- open / high / low / close / volume 필수
- mark_price / funding_rate / open_interest는 항상 None
  (Funding/Settlement 도입은 Phase 1.5 PR 9)
- bid / ask는 호가 데이터 도입 시점(Phase 2+)에 활성. Phase 1은 None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class MarketSnapshot:
    """봉 마감 시점의 시장 상태."""

    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    mark_price: Decimal | None = None  # Phase 1: 항상 None
    funding_rate: Decimal | None = None  # Phase 1.5+
    open_interest: Decimal | None = None  # Phase 2+
    metadata: dict[str, Any] = field(default_factory=dict)
