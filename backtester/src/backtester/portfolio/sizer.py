"""Sizer (spec §3.13).

OrderIntent의 SizeSpec을 실제 거래 단위(Decimal)로 변환.

Phase 1 지원:
- TargetUnits(units): 그대로 반환
- TargetNotional(notional): notional / market.close
- ClosePosition: abs(position.size). flat이면 Decimal('0') 반환.

Phase 1 미지원 (NotImplementedError):
- TargetWeight, FullPosition, ScaleIn

short 차단 (단일 위치 강제 — spec §3.13 Sizer reminder):
- 결과 size를 적용했을 때 포지션이 음수가 되면
  `NotImplementedError("short not supported in Phase 1")`.
- Position/OrderBook/Risk/Ledger는 이 정책에 관여하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal

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
from backtester.instruments.base import Instrument
from backtester.portfolio.position import Position


class Sizer:
    """SizeSpec → 실제 거래 단위 변환."""

    def resolve(
        self,
        intent: OrderIntent,
        instrument: Instrument,
        equity: Decimal,
        position: Position,
        market: MarketSnapshot,
    ) -> Decimal:
        """반환: 거래할 절대 단위 수 (음수 아님). 0이면 거래 안 함을 의미.

        intent.side가 거래 방향. Phase 1은 long-only이므로 sell 결과로 음수
        포지션이 만들어지면 NotImplementedError.
        """
        del instrument, equity  # Phase 1 미사용 (Phase 2 leverage/exposure 검사 시 활용)
        spec = intent.size_spec

        if isinstance(spec, TargetUnits):
            # Sizer 계약: 절대 거래 수량 (양수) 반환. 0/음수는 입력 자체를 거부.
            if spec.units <= 0:
                raise DataError(
                    f"TargetUnits.units must be > 0 (Sizer returns absolute size); "
                    f"got {spec.units}"
                )
            return self._validate_long_only(intent, position, spec.units)

        if isinstance(spec, TargetNotional):
            if spec.notional <= 0:
                raise DataError(
                    f"TargetNotional.notional must be > 0, got {spec.notional}"
                )
            if market.close <= 0:
                raise DataError(
                    f"Cannot resolve TargetNotional with non-positive close: {market.close}"
                )
            units = spec.notional / market.close
            return self._validate_long_only(intent, position, units)

        if isinstance(spec, ClosePosition):
            if position.is_flat:
                return Decimal("0")
            return abs(position.size)

        if isinstance(spec, (TargetWeight, FullPosition, ScaleIn)):
            raise NotImplementedError(
                f"{type(spec).__name__} is Phase 2 "
                f"(Phase 1 supports TargetUnits / TargetNotional / ClosePosition)"
            )

        raise NotImplementedError(  # pragma: no cover — 모든 SizeSpec 분기 처리됨
            f"Unknown SizeSpec: {type(spec).__name__}"
        )

    def _validate_long_only(
        self,
        intent: OrderIntent,
        position: Position,
        units: Decimal,
    ) -> Decimal:
        """short 진입 차단 (Phase 1).

        sell 주문이 현재 long 포지션(또는 flat)을 음수로 만들 수 있으면 raise.
        """
        if intent.side == "sell" and units > position.size:
            raise NotImplementedError(
                f"short not supported in Phase 1: sell of {units} units would "
                f"reduce position from {position.size} to negative for {intent.symbol!r}"
            )
        return units
