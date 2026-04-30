"""Ledger (spec §3.13).

심볼별 Position과 cash, equity 추적. Fill / Market 이벤트로 갱신.

Phase 1 범위:
- on_fill: long-only 진입/청산. 매수 시 avg_price 가중평균, 매도 시 realized_pnl 갱신.
- on_market: snapshot의 close로 mark-to-market unrealized PnL 갱신.
- equity: cash + Σ(position.size * avg_price + unrealized_pnl) = cash + 보유자산 시가
- equity_curve: on_market 호출마다 (timestamp, equity) 적재.
- snapshot: SNAPSHOT 이벤트 payload용 dict (str 직렬화).

Phase 1 미구현:
- on_settle: NotImplementedError("Phase 1.5") — settlement/funding 도입 후.
- on_expired: noop. expire_pending이 항상 []이라 호출되어도 영향 없음 (Phase 1.5+).
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
    """Settlement/funding으로 발생하는 현금 흐름 (Phase 1.5+).

    Phase 1에서는 정의만 두고 사용하지 않음.
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
        """체결 반영. Phase 1: long-only 진입(buy)/청산(sell).

        외부 입력(Fill의 price/size/fee)은 to_decimal로 강제 변환 — float 혼입 방지
        (spec §11 Decimal 가드).

        unrealized_pnl은 호출 끝에서 fill.price 기준으로 재계산해 stale 방지:
        예) 2단위 매수 → on_market(가격↑)로 unrealized 갱신 → 1단위 부분 매도 →
            남은 1단위에 대한 unrealized가 이전 mark 기준으로 stale될 수 있음. 따라서
            fill 직후 fill.price를 새로운 마크로 보고 재계산.
        """
        del instrument  # Phase 1 미사용 (Phase 2 contract_multiplier 등 활용)
        size = to_decimal(fill.size)
        price = to_decimal(fill.price)
        fee = to_decimal(fill.fee)

        position = self._positions.setdefault(fill.symbol, Position(symbol=fill.symbol))

        if fill.side == "buy":
            # 가중평균 avg_price 갱신
            new_size = position.size + size
            if new_size > 0:
                position.avg_price = (
                    position.size * position.avg_price + size * price
                ) / new_size
            position.size = new_size
            self._cash -= size * price + fee
        else:  # sell
            # Phase 1: long-only이므로 size <= position.size여야 함 (Sizer가 보장)
            if size > position.size:
                raise ValueError(
                    f"Sell fill of {size} exceeds position size {position.size} "
                    f"for {fill.symbol!r} (Phase 1 long-only invariant violated)"
                )
            realized = (price - position.avg_price) * size
            position.realized_pnl += realized
            position.size -= size
            if position.size == 0:
                position.avg_price = Decimal("0")
            self._cash += size * price - fee

        # Stale unrealized 방지 — fill.price 기준으로 재계산
        if position.size > 0:
            position.unrealized_pnl = (price - position.avg_price) * position.size
        else:
            position.unrealized_pnl = Decimal("0")

        position.last_update = fill.timestamp

    def on_market(self, snapshots: dict[str, MarketSnapshot]) -> None:
        """mark-to-market — unrealized_pnl 갱신 + equity_curve에 (ts, equity) 적재."""
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

    def on_settle(self, cashflow: CashFlow) -> None:
        """Phase 1 미구현. Settlement/funding 도입은 Phase 1.5 PR 9."""
        del cashflow
        raise NotImplementedError(
            "Ledger.on_settle is Phase 1.5 (settlement/funding 도입 후 활성화)"
        )

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
