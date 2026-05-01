"""Ledger (spec §3.13).

심볼별 Position과 cash, equity 추적. Fill / Market / Settle 이벤트로 갱신.

범위:
- on_fill: long-only 진입/청산. 매수 시 avg_price 가중평균, 매도 시 realized_pnl 갱신.
- on_market: snapshot의 close로 mark-to-market unrealized PnL 갱신.
- on_settle (PR E 활성): funding / settlement cashflow 를 cash 에 적용.
- on_expired: noop (Phase 2 expire_pending 활성 후, 마진 해제 등 후속 PR).
- equity: cash + Σ(position.size * avg_price + unrealized_pnl) = cash + 보유자산 시가
- equity_curve: on_market 호출마다 (timestamp, equity) 적재.
- snapshot: SNAPSHOT 이벤트 payload용 dict (str 직렬화).

본 모듈의 ``CashFlow`` 는 funding/settlement 호환을 위해 ``execution.funding.CashFlow``
와 동일 시그니처로 정의 — 두 클래스 모두 (symbol, ts, amount, kind|reason) 형태로
서로 호환되도록 ``on_settle`` 가 ``amount`` 만 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import polars as pl

from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill, to_decimal
from backtester.instruments.base import Instrument
from backtester.portfolio.position import Position


@dataclass(frozen=True)
class CashFlow:
    """Settlement/funding으로 발생하는 현금 흐름 (PR E 활성).

    호환을 위해 ``execution.funding.CashFlow`` 와 같은 ``symbol`` / ``ts`` / ``amount``
    필드를 보유. 이 모듈의 ``CashFlow`` 는 ``reason`` (legacy 명명), 상위 모듈은
    ``kind``. ``Ledger.on_settle`` 는 두 변종 모두 받는다 (Protocol).
    """

    timestamp: datetime
    symbol: str
    amount: Decimal
    reason: str  # 'funding' | 'settlement' | ...


class Ledger:
    """포트폴리오 회계 — cash + positions + equity 추적."""

    def __init__(self, initial_equity: Decimal | int | str | float) -> None:
        cash = to_decimal(initial_equity)
        if cash <= 0:
            raise ValueError(f"initial_equity must be > 0, got {cash}")
        self._cash: Decimal = cash
        self._initial_equity: Decimal = cash
        self._positions: dict[str, Position] = {}
        self._equity_history: list[tuple[datetime, Decimal]] = []

    # ---------- Properties --------------------------------------------------

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions

    @property
    def realized_pnl(self) -> Decimal:
        total = Decimal("0")
        for p in self._positions.values():
            total += p.realized_pnl
        return total

    @property
    def unrealized_pnl(self) -> Decimal:
        total = Decimal("0")
        for p in self._positions.values():
            total += p.unrealized_pnl
        return total

    @property
    def equity(self) -> Decimal:
        """equity = cash + Σ position market_value
                   = cash + Σ (size * avg_price + unrealized_pnl)
                   = cash + Σ size * mark
        """
        market_value = Decimal("0")
        for p in self._positions.values():
            market_value += p.size * p.avg_price + p.unrealized_pnl
        return self._cash + market_value

    # ---------- Event handlers ---------------------------------------------

    def on_fill(self, fill: Fill, instrument: Instrument) -> None:
        """체결 반영 — long/short/flat 양방향 (PR H 일반화).

        외부 입력(Fill의 price/size/fee)은 to_decimal로 강제 변환 — float 혼입 방지.

        Cases (signed delta = +size for buy, -size for sell):
        1. flat → 새 포지션 (long 또는 short). avg_price = fill.price.
        2. 같은 방향 추가: avg_price 가중평균.
        3. 반대 방향 부분 청산: realize PnL on closed_size, size 만큼 줄어듦. avg_price 유지.
        4. 반대 방향 완전 청산 (delta 가 정확히 |position.size|): realize 전체, size=0,
           avg_price=0.
        5. 반대 방향 초과 (= flip): allow_flip=True 일 때만 도달 (Sizer 가 차단). 기존
           포지션 모두 청산 + 잔여로 새 반대 포지션 개시. realize 는 청산분만.

        unrealized_pnl 재계산은 fill.price 기준으로 stale 방지. signed 공식 사용:
        ``(price - avg) * size`` — long size>0 / short size<0 모두 양수 PnL 일관.

        Cash:
        - buy: cash -= size * price + fee.
        - sell: cash += size * price - fee.
        (short open/close 도 동일 부호 — sell 은 cash 들어옴, buy 는 cash 나감.)
        """
        size = to_decimal(fill.size)
        price = to_decimal(fill.price)
        fee = to_decimal(fill.fee)

        position = self._positions.setdefault(fill.symbol, Position(symbol=fill.symbol))
        delta = size if fill.side == "buy" else -size  # signed
        prev_size = position.size
        prev_avg = position.avg_price
        new_size = prev_size + delta

        # PR P — open from flat / flip 시 liquidation_price 계산용 equity (pre-fill).
        equity_before = self.equity

        if prev_size == 0:
            # Case 1: open from flat
            position.size = new_size
            position.avg_price = price
            # PR N — 새 포지션 ts 기록.
            position.opened_at = fill.timestamp
            # PR P — liquidation_price 계산 (instrument.margin_model 있을 때만).
            position.liquidation_price = self._compute_liquidation_price(
                instrument, new_size, price, equity_before
            )
        elif (prev_size > 0 and delta > 0) or (prev_size < 0 and delta < 0):
            # Case 2: same direction extend → 가중평균. opened_at 유지.
            position.avg_price = (
                abs(prev_size) * prev_avg + abs(delta) * price
            ) / abs(new_size)
            position.size = new_size
        else:
            # Case 3/4/5: reduce / close / flip (반대 방향)
            close_size = min(abs(prev_size), abs(delta))
            if prev_size > 0:
                # closing long
                realized = (price - prev_avg) * close_size
            else:
                # closing short
                realized = (prev_avg - price) * close_size
            position.realized_pnl += realized

            if abs(delta) <= abs(prev_size):
                # Case 3 or 4: pure reduce/close
                position.size = new_size
                if new_size == 0:
                    position.avg_price = Decimal("0")
                    position.liquidation_price = None  # PR P
                    # opened_at 은 일부러 그대로 — 다음 open 이 덮어쓴다.
                # else avg_price stays (liquidation_price 도 부분 reduce 시 이론상 갱신
                # 가능하지만, 1차에서는 entry 시점 가격 유지 — 보수적 방향).
            else:
                # Case 5: flip. Sizer 는 allow_flip=False 면 여기 도달 안 시킴.
                # 기존 포지션 전부 청산 + 잔여로 반대 방향 신규 개시.
                position.size = new_size
                position.avg_price = price  # 신규 포지션의 avg = fill price
                # PR N — 새 (반대) 포지션 ts 기록.
                position.opened_at = fill.timestamp
                # PR P — 새 포지션 liquidation_price 갱신.
                position.liquidation_price = self._compute_liquidation_price(
                    instrument, new_size, price, equity_before
                )

        # Cash 반영
        if fill.side == "buy":
            self._cash -= size * price + fee
        else:
            self._cash += size * price - fee

        # Stale unrealized 방지 — signed 공식.
        if position.size != 0:
            position.unrealized_pnl = (price - position.avg_price) * position.size
        else:
            position.unrealized_pnl = Decimal("0")

        position.last_update = fill.timestamp

    @staticmethod
    def _compute_liquidation_price(
        instrument: Instrument,
        new_size: Decimal,
        avg_price: Decimal,
        equity_before: Decimal,
    ) -> Decimal | None:
        """PR P: isolated-margin 근사 liquidation_price.

        ``new_size`` 는 signed (long > 0, short < 0). leverage L = notional /
        equity_at_open. mmr = margin_model.maintenance_margin_rate.
        long  liq = avg * (1 - 1/L + mmr)
        short liq = avg * (1 + 1/L - mmr)
        equity_before <= 0 또는 margin_model 미설정 → None.
        """
        if instrument.margin_model is None:
            return None
        if equity_before <= 0 or new_size == 0 or avg_price <= 0:
            return None
        notional = abs(new_size) * avg_price
        leverage = notional / equity_before
        if leverage <= 0:
            return None
        mmr = instrument.margin_model.maintenance_margin_rate
        if new_size > 0:
            liq = avg_price * (Decimal("1") - Decimal("1") / leverage + mmr)
        else:
            liq = avg_price * (Decimal("1") + Decimal("1") / leverage - mmr)
        # 음수 가격은 의미 없음
        if liq < 0:
            return Decimal("0")
        return liq

    def on_market(self, snapshots: dict[str, MarketSnapshot]) -> None:
        """mark-to-market — unrealized_pnl 갱신 + equity_curve에 (ts, equity) 적재.

        signed 공식 ``(close - avg) * size`` — long size>0 / short size<0 모두
        양수 PnL 일관 (PR H short 지원).
        """
        if not snapshots:
            return
        for symbol, snap in snapshots.items():
            position = self._positions.get(symbol)
            if position is None:
                continue
            if position.is_flat:
                position.unrealized_pnl = Decimal("0")
            else:
                position.unrealized_pnl = (snap.close - position.avg_price) * position.size
            position.last_update = snap.timestamp
        # 모든 snapshot은 동일 ClockEvent ts라고 가정 (Engine이 보장)
        any_ts = next(iter(snapshots.values())).timestamp
        self._equity_history.append((any_ts, self.equity))

    def on_settle(self, cashflow: Any) -> None:
        """Settlement / funding cashflow 를 cash 에 적용 (PR E 활성).

        ``cashflow.amount`` 는 signed Decimal. 양수=수령, 음수=지불. ``execution.funding
        .CashFlow`` (kind 필드) 와 본 모듈의 ``CashFlow`` (reason 필드) 모두 수용 —
        duck-typed (``cashflow.amount`` attribute 만 사용).
        """
        self._cash += to_decimal(cashflow.amount)

    def on_expired(self, expired: list[Any]) -> None:
        """Phase 1: noop. expire_pending이 항상 []이라 호출되어도 영향 없음.

        Phase 1.5+에서 expires_at 도입 시 만료 주문이 cash에 영향을 주지 않음을
        명시적으로 보장하기 위해 인터페이스만 정의.
        """
        del expired

    # ---------- Outputs -----------------------------------------------------

    def equity_curve(self) -> pl.DataFrame:
        """on_market 호출마다 적재된 (timestamp, equity) 시리즈."""
        if not self._equity_history:
            return pl.DataFrame(
                schema={
                    "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
                    "equity": pl.Float64,
                }
            )
        return pl.DataFrame(
            {
                "timestamp": [ts for ts, _ in self._equity_history],
                "equity": [float(eq) for _, eq in self._equity_history],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))

    def snapshot(self) -> dict[str, Any]:
        """SNAPSHOT 이벤트 payload용 (spec §3.13).

        snapshot_reason은 호출자(_emit_snapshot)가 추가. 모든 Decimal은 str로 직렬화.
        flat 포지션은 positions에서 제외.
        """
        return {
            "equity": str(self.equity),
            "cash": str(self._cash),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "positions": {
                sym: {
                    "size": str(p.size),
                    "avg_price": str(p.avg_price),
                    "unrealized_pnl": str(p.unrealized_pnl),
                }
                for sym, p in self._positions.items()
                if not p.is_flat
            },
        }
