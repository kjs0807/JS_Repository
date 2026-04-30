"""Instrument + FeeModel (spec §3.3).

Phase 1 범위:
- Instrument는 funding_model / margin_model / trading_hours 필드를 **정의하지 않는다**.
  필요해지는 시점(Phase 1.5+)에 추가한다.
- FeeModel은 type='flat' + taker만 사용. maker 필드는 정의하되 Phase 2부터 활성.

Phase 2 PR 15a:
- ``compute_fee(fill_notional, is_maker=False)`` 가 ``is_maker`` 분기 실제 활성.
  market order 는 caller 가 ``is_maker=False`` (= taker) 로 호출. limit maker 판단은
  PR 15b 에서 ExecutionModel 이 결정.

수수료 계산은 ExecutionModel 책임 (compute_fee 호출 시점).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class FeeModel:
    """수수료 모델.

    Phase 1: ``compute_fee`` 가 ``is_maker`` 무시, 항상 taker fee.
    Phase 2 PR 15a: ``is_maker`` 분기 실제 활성. ``maker`` 필드 사용.
    Phase 2 후속: type='tiered' (notional 구간별 차등 fee) — 본 PR 범위 외.
    """

    type: Literal["flat", "tiered"]
    taker: Decimal
    maker: Decimal = Decimal("0")

    def compute_fee(self, fill_notional: Decimal, is_maker: bool = False) -> Decimal:
        """수수료 계산. ``is_maker=True`` 면 maker rate, 아니면 taker rate.

        ``fill_notional`` 부호 무관 (절댓값). type='tiered' 는 후속 PR.
        """
        rate = self.maker if is_maker else self.taker
        return abs(fill_notional) * rate


@dataclass(frozen=True)
class Instrument:
    """거래 대상 명세.

    Phase 1 한정 정의: funding_model / margin_model / trading_hours 필드 없음.
    이들은 Phase 1.5+에서 추가된다.

    `size_unit`은 Sizer가 SizeSpec을 실제 단위로 변환할 때 참조:
    - 'base_asset': BTC 등 base 화폐 단위 (spot crypto)
    - 'contracts': 계약 수 (futures)
    - 'quote_notional': quote 통화 명목 금액
    """

    symbol: str
    asset_class: str
    tick_size: Decimal
    tick_value: Decimal
    contract_multiplier: Decimal
    quote_currency: str
    base_currency: str
    size_unit: Literal["base_asset", "contracts", "quote_notional"]
    fee_model: FeeModel
