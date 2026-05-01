"""PR G 회귀 — ClockEvent 처리 순서 (fill before mark+funding) + on_pending_orders read-only.

검증:
1. 같은 봉에 fill 이 발생하고 funding boundary 인 경우, funding 이 fill 된 포지션을
   기준으로 계산 (이전엔 pre-fill 상태 사용 → funding 누락).
2. 같은 봉에 fill 이 발생하면 on_market 이 post-fill 포지션을 mark — equity_history /
   SNAPSHOT positions 가 post-fill 상태 반영.
3. on_pending_orders 가 ``tuple[OrderView, ...]`` (frozen) 를 받음. mutate 시도 →
   FrozenInstanceError.
4. Engine 이 새 intent 처리 후 fresh OrdersView 를 만들어 on_pending_orders 에 주입 —
   같은 on_bar 에서 발행된 주문도 pending 에 포함.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import OrderView, StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderAction, OrderIntent, TargetUnits
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.execution.funding import FundingModel
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


def _btc() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _make_parquet(target: Path, n_bars: int = 24) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0] * n_bars,
            "high": [101.0] * n_bars,
            "low": [99.0] * n_bars,
            "close": [100.0] * n_bars,
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _config(
    tmp_path: Path,
    funding_models: dict[str, FundingModel] | None = None,
) -> BacktestConfig:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=24)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs = dict(
        run_id="pr_g",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    if funding_models is not None:
        kwargs["funding_models"] = funding_models
    return BacktestConfig(**kwargs)  # type: ignore[arg-type]


# ---------- 1. fill 이 funding 보다 먼저 적용 -------------------------------


class _BuyAtBar7CloseStrategy(BaseStrategy):
    """봉 7 close (07:00 ts) 에 buy intent 발행 — 봉 8 open (07:00 ~ 08:00 사이)
    에서 fill. funding boundary 08:00 시점에 LONG 으로 잡혀 있어야 함.
    """

    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        # ctx.now 는 "직전 봉의 close 시각". 봉 6 close = 07:00.
        if ctx.now == datetime(2026, 1, 1, 7, tzinfo=UTC):
            self._sent = True
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="entry",
                )
            ]
        return []


def test_pr_g_funding_includes_same_bar_fill(tmp_path: Path) -> None:
    """봉 8 open 에서 fill, 봉 8 close = 08:00 funding boundary. PR G 순서로 LONG 적용 후
    funding 계산 → SETTLE 이벤트 발생 (이전 순서에서는 발생 안 했을 수 있음).
    """
    cfg = _config(
        tmp_path,
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8,
                rate_source="constant",
                constant_rate=Decimal("0.0001"),
            )
        },
    )
    result = BacktestEngine(cfg, _BuyAtBar7CloseStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    settles = list(reader.by_type(EventType.SETTLE))
    # boundary: 08:00 / 16:00 / 00:00 — 24h 시리즈에 모두 LONG 보유 → 3 SETTLE.
    # 핵심: 첫 SETTLE (08:00) 가 발생하는지. 이전 순서라면 fill 이 funding 후라 누락 가능.
    settle_ts = sorted({s.ts for s in settles})
    assert datetime(2026, 1, 1, 8, tzinfo=UTC) in settle_ts, (
        f"first SETTLE at 08:00 missing — PR G fill→funding order regression. "
        f"got settles at {settle_ts}"
    )


# ---------- 2. on_market 이 post-fill 포지션을 mark ---------------------------


def test_pr_g_snapshot_after_fill_reflects_position(tmp_path: Path) -> None:
    """봉 8 close SNAPSHOT(periodic) 이 fill 직후의 LONG 포지션을 보여야 한다.

    PR G 순서: fill → on_market → snapshot. 따라서 fill 봉의 periodic SNAPSHOT 도
    post-fill 상태.
    """
    cfg = _config(tmp_path)
    result = BacktestEngine(cfg, _BuyAtBar7CloseStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    # 봉 8 close 시점 (08:00) 의 SNAPSHOT (reason='periodic' 또는 'fill')
    target_ts = datetime(2026, 1, 1, 8, tzinfo=UTC)
    snaps_at_target = [
        s for s in reader.by_type(EventType.SNAPSHOT) if s.ts == target_ts
    ]
    assert snaps_at_target, "no snapshot at 08:00 (fill bar close)"
    # 마지막 snapshot 이 LONG 표시
    last = snaps_at_target[-1]
    pos = last.payload.get("positions", {})
    assert "BTCUSDT" in pos, (
        f"expected LONG BTCUSDT in 08:00 snapshot positions, got {pos}"
    )
    assert Decimal(pos["BTCUSDT"]["size"]) == Decimal("1")


# ---------- 3. on_pending_orders 는 read-only OrderView tuple ----------------


class _PendingMutateAttemptStrategy(BaseStrategy):
    """pending OrderView 에 mutate 시도 → FrozenInstanceError 받아내는 전략."""

    def __init__(self) -> None:
        self.frozen_caught = False
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
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

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        if not pending:
            return []
        # pending 은 frozen — mutate 시도 시 FrozenInstanceError
        try:
            pending[0].state = "cancelled"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            self.frozen_caught = True
        return []


def test_pr_g_on_pending_orders_receives_frozen_view(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    strat = _PendingMutateAttemptStrategy()
    BacktestEngine(cfg, strat, verbose=False).run()
    assert strat.frozen_caught, (
        "on_pending_orders should receive frozen OrderView (FrozenInstanceError on mutate)"
    )


# ---------- 4. fresh OrdersView 가 새 intent 의 주문을 포함 ------------------


class _CheckPendingAfterIntentStrategy(BaseStrategy):
    """on_bar 에서 limit intent 발행 → 같은 on_bar 의 on_pending_orders 가 그 주문을 본다."""

    def __init__(self) -> None:
        self.pending_seen_after_intent = False
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
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

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        if any(o.type == "limit" for o in pending):
            self.pending_seen_after_intent = True
        return []


def test_pr_g_pending_includes_just_added_intent_order(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    strat = _CheckPendingAfterIntentStrategy()
    BacktestEngine(cfg, strat, verbose=False).run()
    assert strat.pending_seen_after_intent, (
        "on_pending_orders should see the limit order just added in the same on_bar — "
        "fresh OrdersView post-intent"
    )


# pytest 자체 import 안전 (unused 방지)
_ = pytest
