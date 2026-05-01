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
class ExchangeRule:
    """거래소 정밀도 / 최소 주문 정책 (PR O).

    Bybit 같은 crypto 거래소가 symbol 별로 부여하는 ``tickSize`` / ``stepSize`` /
    ``minOrderQty`` / ``minOrderAmt`` / ``maxLeverage`` 규약을 backtester 가 그대로
    재현하도록 한다. 누락하면 백테스트가 실제로 못 내는 주문을 체결한 척 — 결과가
    낙관적으로 부풀려진다.

    정책: 자동 round / 자동 clamp 금지 (1차). ``reject`` 가 기본 — 위반 시 ValueError
    → Engine 이 ORDER_REJECTED. 자동 round 옵션은 후속 PR 에서 ``rounding_policy`` 도입.
    """

    symbol: str
    price_tick: Decimal  # 가격 quantum (예: 0.01)
    qty_step: Decimal  # 수량 quantum (예: 0.001)
    min_qty: Decimal  # 최소 주문 수량
    min_notional: Decimal  # 최소 주문 명목 (qty * price)
    max_leverage: Decimal | None = None  # symbol-level 최대 배율

    def __post_init__(self) -> None:
        for name in ("price_tick", "qty_step", "min_qty", "min_notional"):
            v = getattr(self, name)
            if v <= 0:
                raise ValueError(f"ExchangeRule.{name} must be > 0, got {v}")
        if self.max_leverage is not None and self.max_leverage <= 0:
            raise ValueError(
                f"ExchangeRule.max_leverage must be > 0 or None, got {self.max_leverage}"
            )

    def quantize_qty_floor(self, qty: Decimal) -> Decimal:
        """수량을 ``qty_step`` 의 정수배로 floor (보수적 — 자동 round 정책 X)."""
        if qty <= 0:
            return Decimal("0")
        steps = (qty / self.qty_step).to_integral_value(rounding="ROUND_FLOOR")
        return steps * self.qty_step

    def is_price_aligned(self, price: Decimal) -> bool:
        """``price`` 가 ``price_tick`` 의 정수배인지."""
        if price <= 0:
            return False
        return (price / self.price_tick) % 1 == 0


@dataclass(frozen=True)
class MarginModel:
    """Isolated-margin 근사 모델 (PR P).

    Bybit 같은 crypto perp 의 cross/isolated 전체 매트릭스를 완전 재현하지 않고, 단일
    symbol isolated 근사로 liquidation 계산을 활성한다.

    공식 (isolated, mark price 기준):
    - long liq_price = avg * (1 - 1/L + mmr)
    - short liq_price = avg * (1 + 1/L - mmr)
    여기서 L = abs(size) * avg / equity_at_open, mmr = maintenance_margin_rate.

    L <= 1 (no leverage) 이면 liq_price ≈ avg * mmr (long) 또는 avg * (2 - mmr) (short)
    — 사실상 도달하기 어렵다. 누락 효과 없음.
    """

    maintenance_margin_rate: Decimal
    liquidation_fee_rate: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.maintenance_margin_rate < 0 or self.maintenance_margin_rate >= 1:
            raise ValueError(
                f"maintenance_margin_rate must be in [0, 1), got "
                f"{self.maintenance_margin_rate}"
            )
        if self.liquidation_fee_rate < 0 or self.liquidation_fee_rate >= 1:
            raise ValueError(
                f"liquidation_fee_rate must be in [0, 1), got "
                f"{self.liquidation_fee_rate}"
            )


@dataclass(frozen=True)
class Instrument:
    """거래 대상 명세.

    Phase 1 한정 정의: funding_model / margin_model / trading_hours 필드 없음.
    이들은 Phase 1.5+에서 추가된다.

    `size_unit`은 Sizer가 SizeSpec을 실제 단위로 변환할 때 참조:
    - 'base_asset': BTC 등 base 화폐 단위 (spot crypto)
    - 'contracts': 계약 수 (futures)
    - 'quote_notional': quote 통화 명목 금액

    PR O: ``exchange_rule`` (선택) — 거래소 precision/min/leverage 정책. 없으면 검증 없음
    (Phase 1 호환). 있으면 Sizer 가 quantize + min_qty + min_notional + price_tick
    검증, 위반 시 reject.
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
    exchange_rule: ExchangeRule | None = None
    margin_model: MarginModel | None = None  # PR P — liquidation 활성
