"""Position dataclass (spec §3.12).

mutable: size / avg_price / pnl / last_update이 fill·mark에 따라 변한다.

Phase 1: long/flat만 사용. short 진입은 Sizer가 차단하므로 size < 0인 Position은
정상 흐름에서 만들어지지 않는다 (`direction` 메서드는 short도 처리하지만 Phase 1에서는
사용되지 않음).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass
class Position:
    """심볼별 포지션 추적."""

    symbol: str
    size: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    last_update: datetime | None = None

    @property
    def is_flat(self) -> bool:
        """size가 정확히 0인지. 누적 부동소수 오차가 없는 Decimal 환산이므로 안전."""
        return self.size == 0

    def is_effectively_flat(self, tick_size: Decimal) -> bool:
        """tick_size 미만이면 사실상 flat (spec §3.12).

        Decimal 직접 비교 대신 권장되는 방식 (spec §11).
        """
        return abs(self.size) < tick_size

    @property
    def direction(self) -> Literal["long", "short", "flat"]:
        if self.size > 0:
            return "long"
        if self.size < 0:
            return "short"
        return "flat"
