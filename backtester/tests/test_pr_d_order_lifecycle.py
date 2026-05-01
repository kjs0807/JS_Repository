"""PR D 회귀 — Engine OrderAction (cancel / modify) + expires_at lifecycle.

본 파일은 OrderBook 단위 테스트가 아니라 Engine 통합:
- 전략이 ``OrderAction(type='cancel', order_id=...)`` 반환 → ORDER_CANCELLED 이벤트
- 전략이 ``OrderAction(type='modify', order_id=..., modify_limit_price=...)`` 반환 →
  ORDER_MODIFIED 이벤트 + 가격 변경 후 fill 시나리오에 반영
- ``expires_at`` 가 도래하면 ORDER_EXPIRED 이벤트 + SNAPSHOT(reason='expire')
- expired 주문은 fill 되지 않음
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import OrderView, StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderAction, OrderIntent, TargetUnits
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


def _btc() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _make_parquet(target: Path, n_bars: int = 10, base_price: float = 100.0) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [base_price + i * 0.1 for i in range(n_bars)],
            "high": [base_price + 1.0 + i * 0.1 for i in range(n_bars)],
            "low": [base_price - 1.0 + i * 0.1 for i in range(n_bars)],
            "close": [base_price + 0.5 + i * 0.1 for i in range(n_bars)],
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _config(tmp_path: Path, n_bars: int = 10) -> BacktestConfig:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=n_bars, base_price=100.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return BacktestConfig(
        run_id="pr_d_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=n_bars),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )


# ---------- Cancel ----------------------------------------------------------


class _CancelOnNextBarStrategy(BaseStrategy):
    """첫 봉에 limit buy 발행, 다음 봉에 그 주문 cancel."""

    def __init__(self) -> None:
        self._submitted_id: str | None = None
        self._sent_entry = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if not self._sent_entry:
            self._sent_entry = True
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="limit",
                    size_spec=TargetUnits(units=Decimal("1")),
                    limit_price=Decimal("50.0"),  # 매우 낮음 → 체결 안 됨
                    reason="entry_limit",
                )
            ]
        return []

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        if self._submitted_id is None:
            for o in pending:
                if o.type == "limit":
                    self._submitted_id = o.id
                    break
            return []
        # 다음 봉에 cancel
        oid = self._submitted_id
        self._submitted_id = None
        return [OrderAction(type="cancel", order_id=oid)]


def test_engine_handles_cancel_action_emits_order_cancelled(tmp_path: Path) -> None:
    cfg = _config(tmp_path, n_bars=5)
    strat = _CancelOnNextBarStrategy()
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    cancelled = list(reader.by_type(EventType.ORDER_CANCELLED))
    assert len(cancelled) == 1
    assert cancelled[0].payload["order_id"] == "ord_0"


# ---------- Modify ----------------------------------------------------------


class _ModifyLimitStrategy(BaseStrategy):
    """첫 봉 limit buy(매우 높은 가격, 미체결) 발행 → 다음 봉에 limit_price 인하."""

    def __init__(self) -> None:
        self._submitted_id: str | None = None
        self._sent_entry = False
        self._modified = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if not self._sent_entry:
            self._sent_entry = True
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="limit",
                    size_spec=TargetUnits(units=Decimal("1")),
                    limit_price=Decimal("50.0"),
                    reason="entry",
                )
            ]
        return []

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        if self._submitted_id is None:
            for o in pending:
                if o.type == "limit":
                    self._submitted_id = o.id
                    break
            return []
        if not self._modified:
            self._modified = True
            return [
                OrderAction(
                    type="modify",
                    order_id=self._submitted_id,
                    modify_limit_price=Decimal("75.0"),
                )
            ]
        return []


def test_engine_handles_modify_action_emits_order_modified(tmp_path: Path) -> None:
    cfg = _config(tmp_path, n_bars=5)
    strat = _ModifyLimitStrategy()
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    modified = list(reader.by_type(EventType.ORDER_MODIFIED))
    assert len(modified) == 1
    p = modified[0].payload
    assert p["order_id"] == "ord_0"
    assert p["limit_price"] == "75.0"


# ---------- Expire ----------------------------------------------------------


class _ExpiringLimitStrategy(BaseStrategy):
    """첫 봉에 ``expires_at = first_bar + 2h`` limit buy 발행 (매우 낮은 가격, 미체결)."""

    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        # 봉 ts 기준 2 시간 뒤 만료
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="limit",
                size_spec=TargetUnits(units=Decimal("1")),
                limit_price=Decimal("50.0"),
                expires_at=ctx.now + timedelta(hours=2),
                reason="entry",
            )
        ]


def test_engine_expires_order_emits_event_and_snapshot(tmp_path: Path) -> None:
    cfg = _config(tmp_path, n_bars=10)
    strat = _ExpiringLimitStrategy()
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    expired = list(reader.by_type(EventType.ORDER_EXPIRED))
    assert len(expired) == 1
    assert expired[0].payload["order_id"] == "ord_0"

    # SNAPSHOT(reason='expire') 가 expired event 와 같은 ts 에 발행됨
    expire_ts = expired[0].ts
    same_ts_snapshots = [
        s for s in reader.by_type(EventType.SNAPSHOT)
        if s.ts == expire_ts
        and s.payload.get("snapshot_reason") == "expire"
    ]
    assert len(same_ts_snapshots) == 1


def test_engine_expired_order_does_not_fill(tmp_path: Path) -> None:
    """만료된 주문은 이후 fill 되지 않음."""
    cfg = _config(tmp_path, n_bars=10)
    strat = _ExpiringLimitStrategy()
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    assert fills == []


# ---------- Cancel of unknown id is silent ----------------------------------


class _CancelUnknownStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        if self._sent:
            return []
        self._sent = True
        return [OrderAction(type="cancel", order_id="ord_999")]


def test_engine_cancel_unknown_id_no_event(tmp_path: Path) -> None:
    """존재하지 않는 order_id cancel 은 silently no-op (ORDER_CANCELLED 이벤트 없음)."""
    cfg = _config(tmp_path, n_bars=3)
    strat = _CancelUnknownStrategy()
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    cancelled = list(reader.by_type(EventType.ORDER_CANCELLED))
    assert cancelled == []


# ---------- Modify on market order rejected ---------------------------------


class _ModifyMarketStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._submitted_id: str | None = None
        self._sent_entry = False
        self._tried_modify = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if not self._sent_entry:
            self._sent_entry = True
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="limit",
                    size_spec=TargetUnits(units=Decimal("1")),
                    limit_price=Decimal("50.0"),
                    reason="entry",
                )
            ]
        return []

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        if self._submitted_id is None:
            for o in pending:
                if o.type == "limit":
                    self._submitted_id = o.id
                    break
            return []
        if not self._tried_modify:
            self._tried_modify = True
            # limit 주문에 stop_price modify 시도 → ValueError → ORDER_REJECTED 이벤트
            return [
                OrderAction(
                    type="modify",
                    order_id=self._submitted_id,
                    modify_stop_price=Decimal("60"),
                )
            ]
        return []


def test_engine_modify_invalid_field_emits_order_rejected(tmp_path: Path) -> None:
    """limit 에 stop_price modify → ValueError → ORDER_REJECTED 이벤트로 변환."""
    cfg = _config(tmp_path, n_bars=4)
    strat = _ModifyMarketStrategy()
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    rejected = list(reader.by_type(EventType.ORDER_REJECTED))
    assert any("modify" in str(r.payload.get("reason", "")) for r in rejected)


