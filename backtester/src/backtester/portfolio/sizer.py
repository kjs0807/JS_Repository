"""Sizer (spec §3.13, PR H 확장).

OrderIntent의 SizeSpec을 실제 거래 단위(Decimal)로 변환.

지원:
- TargetUnits(units): 그대로 반환
- TargetNotional(notional): notional / market.close
- ClosePosition: abs(position.size). flat이면 Decimal('0') 반환.

미지원 (NotImplementedError):
- TargetWeight, FullPosition, ScaleIn (PR I 후속에서 leverage 변종 도입)

PR H Short 정책 (Sizer 단일 위치 강제 — spec §3.13 Sizer reminder):
- ``allow_short=False`` (default, Phase 1 호환): sell 결과 포지션이 음수가 되면
  ``NotImplementedError``. flat 에서 sell 도 차단.
- ``allow_short=True``: short open / extend / close 모두 허용. 단 ``allow_flip=False``
  (default) 면 long↔short 한 fill 전환은 ``ValueError`` 로 차단 — 전략은 close →
  open 두 단계로 표현해야 함.
- Position/OrderBook/Risk/Ledger는 이 정책에 관여하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal

from backtester.core.errors import DataError
from backtester.core.orders import (
    ClosePosition,
    FullEquityNotional,
    FullPosition,
    OrderIntent,
    ScaleIn,
    TargetMarginPct,
    TargetNotional,
    TargetNotionalPct,
    TargetUnits,
    TargetWeight,
)
from backtester.core.snapshot import MarketSnapshot
from backtester.instruments.base import Instrument
from backtester.portfolio.position import Position


class Sizer:
    """SizeSpec → 실제 거래 단위 변환."""

    def __init__(
        self,
        *,
        allow_short: bool = False,
        allow_flip: bool = False,
    ) -> None:
        self.allow_short = allow_short
        self.allow_flip = allow_flip

    def resolve(
        self,
        intent: OrderIntent,
        instrument: Instrument,
        equity: Decimal,
        position: Position,
        market: MarketSnapshot,
    ) -> Decimal:
        """반환: 거래할 절대 단위 수 (음수 아님). 0이면 거래 안 함을 의미.

        intent.side가 거래 방향. ``allow_short`` 가 False 면 sell 결과 음수 포지션 →
        NotImplementedError. allow_flip 이 False 면 한 fill 으로 long↔short 전환도
        ValueError.

        PR J: ``intent.reduce_only=True`` 또는 ``ClosePosition`` SizeSpec 은 새 반대
        포지션을 열지 않도록 추가 검사 (flat/extend/oversize reject).

        PR O: ``instrument.exchange_rule`` 이 설정돼 있으면:
        1. price_tick 검증 — limit/stop intent 의 가격 필드가 tick 정수배가 아니면
           ValueError.
        2. qty_step quantize (floor down) — 자동 round 금지, 결과만 floor.
        3. min_qty / min_notional 검증 — 미달 시 ValueError.
        """
        spec = intent.size_spec

        # PR O — price_tick 검증 (sizing 전에 차단). limit/stop 의 limit_price /
        # stop_price 가 tick 정수배가 아니면 reject.
        self._validate_price_tick(intent, instrument)

        if isinstance(spec, TargetUnits):
            # Sizer 계약: 절대 거래 수량 (양수) 반환. 0/음수는 입력 자체를 거부.
            if spec.units <= 0:
                raise DataError(
                    f"TargetUnits.units must be > 0 (Sizer returns absolute size); "
                    f"got {spec.units}"
                )
            units = self._apply_exchange_qty_rules(
                intent, instrument, market, spec.units
            )
            return self._enforce_short_policy(intent, position, units)

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
            units = self._apply_exchange_qty_rules(intent, instrument, market, units)
            return self._enforce_short_policy(intent, position, units)

        if isinstance(spec, ClosePosition):
            if position.is_flat:
                # ClosePosition + flat → 거래 없음 (정상 noop). reduce_only=True 명시
                # intent 와 달리 ClosePosition 은 의미상 "있으면 닫고 없으면 패스".
                return Decimal("0")
            # side mismatch 검사 — long 보유 + side='buy' 또는 short 보유 + side='sell'
            # 은 ClosePosition 의 본질을 위반.
            if position.size > 0 and intent.side != "sell":
                raise DataError(
                    f"ClosePosition on long position requires side='sell', "
                    f"got side={intent.side!r} for {intent.symbol!r}"
                )
            if position.size < 0 and intent.side != "buy":
                raise DataError(
                    f"ClosePosition on short position requires side='buy', "
                    f"got side={intent.side!r} for {intent.symbol!r}"
                )
            # ClosePosition 은 exchange_rule quantize / min 적용 안 함 — 보유 수량
            # 그대로 전부 닫는다 (이미 거래소가 받아준 수량). 잔여물 round 회피.
            return abs(position.size)

        # PR I — Futures sizing 변종.
        if isinstance(spec, TargetMarginPct):
            if spec.margin_pct <= 0:
                raise DataError(
                    f"TargetMarginPct.margin_pct must be > 0, got {spec.margin_pct}"
                )
            if spec.leverage <= 0:
                raise DataError(
                    f"TargetMarginPct.leverage must be > 0, got {spec.leverage}"
                )
            if market.close <= 0:
                raise DataError(
                    f"TargetMarginPct requires positive market.close, got {market.close}"
                )
            notional = equity * spec.margin_pct * spec.leverage
            units = notional / market.close
            units = self._apply_exchange_qty_rules(intent, instrument, market, units)
            return self._enforce_short_policy(intent, position, units)

        if isinstance(spec, TargetNotionalPct):
            if spec.notional_pct <= 0:
                raise DataError(
                    f"TargetNotionalPct.notional_pct must be > 0, got {spec.notional_pct}"
                )
            if market.close <= 0:
                raise DataError(
                    f"TargetNotionalPct requires positive market.close, got {market.close}"
                )
            notional = equity * spec.notional_pct
            units = notional / market.close
            units = self._apply_exchange_qty_rules(intent, instrument, market, units)
            return self._enforce_short_policy(intent, position, units)

        if isinstance(spec, FullEquityNotional):
            if spec.leverage <= 0:
                raise DataError(
                    f"FullEquityNotional.leverage must be > 0, got {spec.leverage}"
                )
            if market.close <= 0:
                raise DataError(
                    f"FullEquityNotional requires positive market.close, got {market.close}"
                )
            notional = equity * spec.leverage
            units = notional / market.close
            units = self._apply_exchange_qty_rules(intent, instrument, market, units)
            return self._enforce_short_policy(intent, position, units)

        if isinstance(spec, (TargetWeight, FullPosition, ScaleIn)):
            raise NotImplementedError(
                f"{type(spec).__name__} is Phase 2+ "
                f"(현재 TargetUnits / TargetNotional / ClosePosition / TargetMarginPct / "
                f"TargetNotionalPct / FullEquityNotional 만 지원)"
            )

        raise NotImplementedError(  # pragma: no cover — 모든 SizeSpec 분기 처리됨
            f"Unknown SizeSpec: {type(spec).__name__}"
        )

    def _enforce_short_policy(
        self,
        intent: OrderIntent,
        position: Position,
        units: Decimal,
    ) -> Decimal:
        # PR J: reduce_only 검사 우선. 새 반대 포지션 열기를 절대 차단.
        if intent.reduce_only:
            self._enforce_reduce_only(intent, position, units)
        return self._check_short_flip(intent, position, units)

    # ---------- PR O — Exchange rule helpers --------------------------------

    def _validate_price_tick(
        self,
        intent: OrderIntent,
        instrument: Instrument,
    ) -> None:
        rule = instrument.exchange_rule
        if rule is None:
            return
        if intent.limit_price is not None and not rule.is_price_aligned(
            intent.limit_price
        ):
            raise ValueError(
                f"limit_price {intent.limit_price} not aligned to price_tick "
                f"{rule.price_tick} for {intent.symbol!r}"
            )
        if intent.stop_price is not None and not rule.is_price_aligned(
            intent.stop_price
        ):
            raise ValueError(
                f"stop_price {intent.stop_price} not aligned to price_tick "
                f"{rule.price_tick} for {intent.symbol!r}"
            )

    def _apply_exchange_qty_rules(
        self,
        intent: OrderIntent,
        instrument: Instrument,
        market: MarketSnapshot,
        units: Decimal,
    ) -> Decimal:
        """qty_step quantize (floor) + min_qty / min_notional 검증.

        ClosePosition 은 호출하지 않음 (보유 수량 그대로 닫음).
        """
        rule = instrument.exchange_rule
        if rule is None:
            return units
        quantized = rule.quantize_qty_floor(units)
        if quantized < rule.min_qty:
            raise ValueError(
                f"computed units {units} (quantized {quantized}) below "
                f"min_qty {rule.min_qty} for {intent.symbol!r}"
            )
        ref_price = (
            intent.limit_price
            if intent.limit_price is not None
            else (intent.stop_price if intent.stop_price is not None else market.close)
        )
        if ref_price is not None and ref_price > 0:
            notional = quantized * ref_price
            if notional < rule.min_notional:
                raise ValueError(
                    f"computed notional {notional} below min_notional "
                    f"{rule.min_notional} for {intent.symbol!r}"
                )
        return quantized

    def _enforce_reduce_only(
        self,
        intent: OrderIntent,
        position: Position,
        units: Decimal,
    ) -> None:
        """PR J reduce_only 정책: 새 포지션 / extend / 반대 방향 초과 모두 reject.

        Allowed:
        - long > 0 + sell + units <= long_size
        - short < 0 + buy + units <= abs(short_size)
        Rejected:
        - flat (nothing to reduce)
        - same direction (long + buy, short + sell — extend)
        - oversize (close > position) — flip to opposite would happen
        """
        side = intent.side
        size = position.size
        if size == 0:
            raise ValueError(
                f"reduce_only=True but position is flat for {intent.symbol!r} "
                f"(nothing to reduce)"
            )
        if size > 0:
            if side == "buy":
                raise ValueError(
                    f"reduce_only=True with side='buy' on long position {size} "
                    f"would extend, not reduce, for {intent.symbol!r}"
                )
            if units > size:
                raise ValueError(
                    f"reduce_only=True oversize sell {units} > long {size} "
                    f"for {intent.symbol!r} (would flip to short)"
                )
        else:  # size < 0
            if side == "sell":
                raise ValueError(
                    f"reduce_only=True with side='sell' on short position {size} "
                    f"would extend, not reduce, for {intent.symbol!r}"
                )
            if units > abs(size):
                raise ValueError(
                    f"reduce_only=True oversize buy {units} > short {abs(size)} "
                    f"for {intent.symbol!r} (would flip to long)"
                )

    def _check_short_flip(
        self,
        intent: OrderIntent,
        position: Position,
        units: Decimal,
    ) -> Decimal:
        """PR H short / flip 정책 강제.

        Cases:
        - sell + position.size > 0 (long 보유):
            * units <= position.size → reduce/close, 항상 OK.
            * units > position.size → flip 시도. allow_flip=False 면 ValueError.
              allow_flip=True 인데 allow_short=False 면 NotImplementedError.
        - sell + position.size <= 0 (flat / short 보유):
            * allow_short=False → NotImplementedError.
            * allow_short=True → short open/extend, OK.
        - buy + position.size < 0 (short 보유):
            * units <= abs(short_size) → reduce/close, 항상 OK.
            * units > abs(short_size) → flip. allow_flip=False 면 ValueError.
        - buy + position.size >= 0 (flat / long): always OK (long 진입 또는 추가).
        """
        side = intent.side
        size = position.size
        if side == "sell":
            if size > 0:
                if units > size and not self.allow_flip:
                    raise ValueError(
                        f"flip not allowed: sell of {units} units would convert long "
                        f"position {size} into short for {intent.symbol!r} "
                        f"(use close → open two intents, or set allow_flip=True)"
                    )
                if units > size and not self.allow_short:
                    raise NotImplementedError(
                        f"short not allowed: sell of {units} would flip long "
                        f"position {size} into short for {intent.symbol!r} "
                        f"(set allow_short=True to enable)"
                    )
            else:
                # flat or already short
                if not self.allow_short:
                    raise NotImplementedError(
                        f"short not allowed: sell of {units} units from "
                        f"position {size} for {intent.symbol!r} "
                        f"(set allow_short=True to enable)"
                    )
        else:  # buy
            if size < 0:
                abs_short = abs(size)
                if units > abs_short and not self.allow_flip:
                    raise ValueError(
                        f"flip not allowed: buy of {units} units would convert short "
                        f"position {size} into long for {intent.symbol!r} "
                        f"(use close → open two intents, or set allow_flip=True)"
                    )
        return units
