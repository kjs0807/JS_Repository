"""Funding model + processor (Phase 1.5 PR 9, spec §3.14, §16).

Phase 1.5 PR 9 범위 — **계산 골격**.

- ``FundingModel`` (instrument 부착, 8h interval 등) + ``CashFlow`` 정의
- ``FundingProcessor`` 가 ``(symbol, ts, instrument, position, market)`` 으로부터
  funding cashflow 발행 여부와 금액 계산
- ``rate_source="constant"`` 만 지원. ``"from_data_source"`` (DB ``funding_rate`` 테이블
  연동) 는 후속 PR 에서 활성

Engine wiring (SETTLE 이벤트 + ``Ledger.on_settle`` 활성 + ``ClockEvent.settlements``
주입) 은 후속 PR 에서 도입한다 — 본 모듈은 그때 그대로 import 해서 쓸 수 있도록 단위로
완결시켜 둔다.

부호 규약 (perpetual swap funding):
- LONG 보유 + rate > 0 → LONG 이 SHORT 에게 funding 지불 → ``amount < 0``
- SHORT 보유 + rate > 0 → SHORT 가 LONG 에게서 받음 → ``amount > 0``
- amount = ``-position.size × mark × rate`` (``position.size`` 양수=LONG, 음수=SHORT)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from backtester.core.snapshot import MarketSnapshot
from backtester.instruments.base import Instrument
from backtester.portfolio.position import Position

CashFlowKind = Literal["funding", "settlement"]
RateSource = Literal["constant", "from_data_source"]


@dataclass(frozen=True)
class FundingModel:
    """심볼별 funding 정책 (spec §3.14).

    - ``interval_hours``: funding 주기. 일반 perp 는 8h.
    - ``rate_source``: ``"constant"`` (단순 회귀 테스트) 또는 ``"from_data_source"``
      (DB ``funding_rate`` 테이블 연동, Phase 1.5+ 후속 PR).
    - ``constant_rate``: ``rate_source="constant"`` 일 때 봉당 적용 rate (예: ``0.0001``
      = 0.01%).
    """

    interval_hours: int
    rate_source: RateSource = "constant"
    constant_rate: Decimal | None = None

    def __post_init__(self) -> None:
        if self.interval_hours <= 0:
            raise ValueError(
                f"interval_hours must be > 0, got {self.interval_hours}"
            )
        if 24 % self.interval_hours != 0:
            raise ValueError(
                f"interval_hours must divide 24 (1/2/3/4/6/8/12/24), "
                f"got {self.interval_hours}"
            )
        if self.rate_source not in ("constant", "from_data_source"):
            raise ValueError(
                f"rate_source must be 'constant' or 'from_data_source', "
                f"got {self.rate_source!r}"
            )
        if self.rate_source == "constant" and self.constant_rate is None:
            raise ValueError(
                "constant_rate must be set when rate_source='constant'"
            )


@dataclass(frozen=True)
class CashFlow:
    """Settlement / funding 발생 시 ledger 에 적용되는 단위 cashflow (spec §3.14).

    - ``amount``: signed Decimal. 양수=수령, 음수=지불.
    - ``rate``: 감사용 — 산출에 쓰인 rate (constant 모드에서는 ``FundingModel.constant_rate``).
    """

    symbol: str
    ts: datetime
    amount: Decimal
    kind: CashFlowKind = "funding"
    rate: Decimal | None = None


def is_funding_boundary(model: FundingModel, ts: datetime) -> bool:
    """``ts`` 가 ``model.interval_hours`` 경계인지 (UTC 자정 기준 정렬).

    예: ``interval_hours=8`` → ``00:00 / 08:00 / 16:00`` UTC 만 ``True``.
    """
    return (
        ts.hour % model.interval_hours == 0
        and ts.minute == 0
        and ts.second == 0
        and ts.microsecond == 0
    )


class FundingProcessor:
    """심볼별 ``FundingModel`` 을 받아 funding cashflow 발행.

    Phase 1.5 PR 9 — ``rate_source="constant"`` 만. ``"from_data_source"`` 는 Engine
    wiring 시점에 ``data_source`` 인자를 통해 주입.
    """

    def __init__(self, models: dict[str, FundingModel]) -> None:
        self.models = dict(models)

    def process(
        self,
        symbol: str,
        ts: datetime,
        instrument: Instrument,
        position: Position,
        market: MarketSnapshot,
    ) -> CashFlow | None:
        """``ts`` 시점에 해당 ``symbol`` 에 대한 funding cashflow 가 있으면 반환.

        ``None`` 반환 케이스:
        - 모델 미등록 / interval 경계 아님 / position flat
        """
        del instrument  # 현 phase 에서는 instrument 메타데이터 미사용

        model = self.models.get(symbol)
        if model is None:
            return None
        if not is_funding_boundary(model, ts):
            return None
        if position.is_flat:
            return None

        if model.rate_source == "constant":
            assert model.constant_rate is not None  # __post_init__ 검증
            rate = model.constant_rate
        else:
            raise NotImplementedError(
                "rate_source='from_data_source' is wired in subsequent Phase 1.5 PRs"
            )

        # mark price 우선, 없으면 close
        mark = market.mark_price if market.mark_price is not None else market.close
        amount = -position.size * mark * rate
        return CashFlow(
            symbol=symbol,
            ts=ts,
            amount=amount,
            kind="funding",
            rate=rate,
        )
