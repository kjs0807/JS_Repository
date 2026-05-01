"""PR K — Bracket Orders (TP / SL) 회귀.

검증:
1. ``BracketSpec`` dataclass + ``has_any``.
2. long entry + bracket → TP/SL reduce-only sell child 자동 생성.
3. short entry + bracket → TP/SL reduce-only buy child 생성.
4. child orders 가 같은 ``oco_group_id`` + parent_order_id 기록.
5. ORDER_ADDED EventLog payload 에 parent_order_id / oco_group_id 보존.
6. partial filling 후에도 children 은 parent fill size 만큼.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import (
    BracketSpec,
    OrderIntent,
    TargetUnits,
)
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
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


def _make_parquet(target: Path, n_bars: int = 8) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0 + i * 0.1 for i in range(n_bars)],
            "high": [101.0 + i * 0.1 for i in range(n_bars)],
            "low": [99.0 + i * 0.1 for i in range(n_bars)],
            "close": [100.5 + i * 0.1 for i in range(n_bars)],
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _config(
    tmp_path: Path,
    *,
    allow_short: bool = False,
    n_bars: int = 8,
) -> BacktestConfig:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=n_bars)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return BacktestConfig(
        run_id="bracket_smoke",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=n_bars),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        allow_short=allow_short,
    )


# ---------- 1. BracketSpec dataclass ----------------------------------------


def test_bracket_spec_has_any() -> None:
    assert BracketSpec().has_any() is False
    assert BracketSpec(take_profit_price=Decimal("110")).has_any() is True
    assert BracketSpec(stop_loss_price=Decimal("90")).has_any() is True
    assert BracketSpec(time_stop_bars=10).has_any() is True


# ---------- 2. long entry + bracket → reduce-only sell child ---------------


class _LongWithBracket(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="market",
                size_spec=TargetUnits(units=Decimal("1")),
                reason="long_entry",
                bracket=BracketSpec(
                    take_profit_price=Decimal("110"),
                    stop_loss_price=Decimal("95"),
                ),
            )
        ]


def test_long_entry_spawns_tp_sl_children(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    result = BacktestEngine(cfg, _LongWithBracket(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    # entry + TP + SL = 3 ORDER_ADDED 이벤트
    assert len(added) == 3
    parent = added[0]
    assert parent.payload["parent_order_id"] is None
    assert parent.payload["oco_group_id"] is None
    assert parent.payload["intent"]["side"] == "buy"

    tp_sl = added[1:]
    for child in tp_sl:
        assert child.payload["parent_order_id"] == parent.payload["order_id"]
        assert child.payload["oco_group_id"] == f"oco_{parent.payload['order_id']}"
        assert child.payload["intent"]["side"] == "sell"
        assert child.payload["intent"]["reduce_only"] is True

    types = {c.payload["intent"]["type"] for c in tp_sl}
    assert types == {"limit", "stop"}


# ---------- 3. short entry + bracket → reduce-only buy child ---------------


class _ShortWithBracket(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="sell",
                type="market",
                size_spec=TargetUnits(units=Decimal("1")),
                reason="short_entry",
                bracket=BracketSpec(
                    take_profit_price=Decimal("90"),
                    stop_loss_price=Decimal("110"),
                ),
            )
        ]


def test_short_entry_spawns_tp_sl_buy_children(tmp_path: Path) -> None:
    cfg = _config(tmp_path, allow_short=True)
    result = BacktestEngine(cfg, _ShortWithBracket(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    assert len(added) == 3
    children = added[1:]
    for c in children:
        assert c.payload["intent"]["side"] == "buy"
        assert c.payload["intent"]["reduce_only"] is True


# ---------- 4. children 의 가격 필드 확인 -----------------------------------


def test_long_bracket_tp_is_limit_sl_is_stop(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    result = BacktestEngine(cfg, _LongWithBracket(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    children = added[1:]
    by_type = {c.payload["intent"]["type"]: c for c in children}
    assert "limit" in by_type
    assert "stop" in by_type
    # limit child = TP at 110 (sell limit)
    assert Decimal(by_type["limit"].payload["intent"]["limit_price"]) == Decimal("110")
    # stop child = SL at 95 (sell stop)
    assert Decimal(by_type["stop"].payload["intent"]["stop_price"]) == Decimal("95")


# ---------- 5. bracket 없으면 child 없음 ------------------------------------


class _LongNoBracket(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
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


def test_no_bracket_no_children(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    result = BacktestEngine(cfg, _LongNoBracket(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    assert len(added) == 1
    assert added[0].payload["parent_order_id"] is None


# ---------- 6. TP only / SL only 부분 bracket -------------------------------


class _LongTPOnly(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="market",
                size_spec=TargetUnits(units=Decimal("1")),
                reason="entry",
                bracket=BracketSpec(take_profit_price=Decimal("110")),
            )
        ]


def test_tp_only_spawns_one_child(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    result = BacktestEngine(cfg, _LongTPOnly(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    assert len(added) == 2  # entry + TP only
    assert added[1].payload["intent"]["type"] == "limit"


# ---------- 7. children 도 ctx.open_orders 에 보임 -------------------------


class _LongBracketCheckOrders(BaseStrategy):
    def __init__(self) -> None:
        self._step = 0
        self.children_seen_count = 0

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        self._step += 1
        if self._step == 1:
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="entry",
                    bracket=BracketSpec(
                        take_profit_price=Decimal("110"),
                        stop_loss_price=Decimal("95"),
                    ),
                )
            ]
        # bar 3 이후엔 entry fill 이 끝나고 TP/SL active
        if self._step >= 3:
            self.children_seen_count = len(ctx.open_orders("BTCUSDT"))
        return []


def test_bracket_children_visible_in_ctx_open_orders(tmp_path: Path) -> None:
    cfg = _config(tmp_path, n_bars=10)
    strat = _LongBracketCheckOrders()
    BacktestEngine(cfg, strat, verbose=False).run()
    # 봉 3 이후 ctx.open_orders 에서 TP + SL 두 개가 보여야 함
    assert strat.children_seen_count == 2
