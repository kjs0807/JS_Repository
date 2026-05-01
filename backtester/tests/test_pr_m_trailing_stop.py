"""PR M — Trailing / Break-Even Stop ratchet 회귀.

검증:
1. ``OrderBook.modify`` 의 reduce_only stop ratchet:
   - long 보호 stop (intent.side='sell'): 위로만, 아래로 또는 같은 가격 → ValueError
   - short 보호 stop (intent.side='buy'): 아래로만, 위로 또는 같은 가격 → ValueError
2. ``reduce_only=False`` stop 은 ratchet 미적용 (자유 변경).
3. ``stop_limit`` reduce_only stop 도 동일 ratchet.
4. Engine 통합: 전략이 trailing 로직으로 OrderAction(type='modify') 발행 →
   ratchet violation 시 ORDER_REJECTED, 정상이면 ORDER_MODIFIED + 새 가격 적용.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orderbook import OrderBook
from backtester.core.orders import (
    BracketSpec,
    OrderAction,
    OrderIntent,
    TargetUnits,
)
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc
TS = datetime(2026, 1, 1, tzinfo=UTC)


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


def _stop_intent(
    side: str,
    stop_price: str,
    *,
    reduce_only: bool = False,
    type: str = "stop",
    limit_price: str | None = None,
) -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
        type=type,  # type: ignore[arg-type]
        size_spec=TargetUnits(units=Decimal("1")),
        stop_price=Decimal(stop_price),
        limit_price=Decimal(limit_price) if limit_price else None,
        reduce_only=reduce_only,
    )


# ---------- 1. Long protecting stop ratchet ---------------------------------


def test_modify_long_stop_up_ok() -> None:
    ob = OrderBook()
    order = ob.add(
        _stop_intent("sell", "95", reduce_only=True), Decimal("1"), TS
    )
    assert ob.modify(order.id, stop_price=Decimal("100")) is True
    assert order.intent.stop_price == Decimal("100")


def test_modify_long_stop_down_rejected() -> None:
    ob = OrderBook()
    order = ob.add(
        _stop_intent("sell", "95", reduce_only=True), Decimal("1"), TS
    )
    with pytest.raises(ValueError, match="long-protecting"):
        ob.modify(order.id, stop_price=Decimal("90"))


def test_modify_long_stop_same_rejected() -> None:
    ob = OrderBook()
    order = ob.add(
        _stop_intent("sell", "95", reduce_only=True), Decimal("1"), TS
    )
    with pytest.raises(ValueError, match="long-protecting"):
        ob.modify(order.id, stop_price=Decimal("95"))


# ---------- 2. Short protecting stop ratchet --------------------------------


def test_modify_short_stop_down_ok() -> None:
    ob = OrderBook()
    order = ob.add(
        _stop_intent("buy", "110", reduce_only=True), Decimal("1"), TS
    )
    assert ob.modify(order.id, stop_price=Decimal("105")) is True


def test_modify_short_stop_up_rejected() -> None:
    ob = OrderBook()
    order = ob.add(
        _stop_intent("buy", "110", reduce_only=True), Decimal("1"), TS
    )
    with pytest.raises(ValueError, match="short-protecting"):
        ob.modify(order.id, stop_price=Decimal("115"))


# ---------- 3. reduce_only=False 자유 변경 ----------------------------------


def test_modify_non_reduce_only_stop_free() -> None:
    ob = OrderBook()
    order = ob.add(_stop_intent("sell", "95"), Decimal("1"), TS)
    # 아래로 이동도 OK
    assert ob.modify(order.id, stop_price=Decimal("80")) is True
    assert order.intent.stop_price == Decimal("80")


# ---------- 4. stop_limit reduce_only ratchet ------------------------------


def test_modify_stop_limit_reduce_only_ratchet() -> None:
    ob = OrderBook()
    order = ob.add(
        _stop_intent(
            "sell",
            "95",
            reduce_only=True,
            type="stop_limit",
            limit_price="94",
        ),
        Decimal("1"),
        TS,
    )
    assert ob.modify(order.id, stop_price=Decimal("100")) is True
    with pytest.raises(ValueError, match="long-protecting"):
        ob.modify(order.id, stop_price=Decimal("99"))


# ---------- 5. Engine 통합 — trailing strategy ------------------------------


def _make_parquet(target: Path, bars: list[dict[str, Any]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(bars).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


class _TrailingLong(BaseStrategy):
    """long entry + bracket SL @95. Bar 마다 close 가 상승하면 stop 도 함께 위로 trail."""

    def __init__(self) -> None:
        self._step = 0
        self._sl_id: str | None = None
        self._last_trail: Decimal | None = None

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
                    bracket=BracketSpec(stop_loss_price=Decimal("95")),
                )
            ]
        return []

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[Any, ...],
    ) -> list[OrderAction]:
        # Identify SL
        if self._sl_id is None:
            for o in pending:
                if o.type == "stop" and o.side == "sell":
                    self._sl_id = o.id
                    break
            if self._sl_id is None:
                return []
        # Trailing — current bar close 의 5 아래로 stop 갱신.
        bars = ctx.bars["BTCUSDT"]["1h"]
        if bars.height == 0:
            return []
        close = Decimal(str(bars["close"][-1]))
        new_stop = close - Decimal("5")
        if self._last_trail is None or new_stop > self._last_trail:
            self._last_trail = new_stop
            return [
                OrderAction(
                    type="modify",
                    order_id=self._sl_id,
                    modify_stop_price=new_stop,
                )
            ]
        return []


def test_engine_trailing_long_modifies_stop_upward(tmp_path: Path) -> None:
    """close 가 100 → 110 → 120 으로 상승할 때 stop 이 95 → 105 → 115 로 trail."""
    base = TS
    bars = [
        {"timestamp": base + timedelta(hours=i), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0 + 10.0 * i, "volume": 1.0}
        for i in range(5)
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="trail_long",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=5),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _TrailingLong(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    modified = list(reader.by_type(EventType.ORDER_MODIFIED))
    # 적어도 한 번은 modify 발행됨
    assert len(modified) >= 1
    # stop_price 가 단조 증가
    stops = [Decimal(m.payload["stop_price"]) for m in modified]
    # 단조 증가 (ratchet invariant 가 강제)
    for prev, curr in zip(stops, stops[1:], strict=False):
        assert curr > prev


def test_engine_modify_long_stop_downward_emits_rejected(tmp_path: Path) -> None:
    """전략이 잘못된 방향으로 stop 내리면 ORDER_REJECTED."""

    class _BadTrail(BaseStrategy):
        def __init__(self) -> None:
            self._step = 0
            self._sl_id: str | None = None
            self._tried = False

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
                        bracket=BracketSpec(stop_loss_price=Decimal("95")),
                    )
                ]
            return []

        def on_pending_orders(
            self,
            ctx: StrategyContext,
            pending: tuple[Any, ...],
        ) -> list[OrderAction]:
            if self._sl_id is None:
                for o in pending:
                    if o.type == "stop" and o.side == "sell":
                        self._sl_id = o.id
                        break
                if self._sl_id is None:
                    return []
            if not self._tried and self._sl_id is not None:
                self._tried = True
                return [
                    OrderAction(
                        type="modify",
                        order_id=self._sl_id,
                        modify_stop_price=Decimal("90"),  # below 95 — violates ratchet
                    )
                ]
            return []

    base = TS
    bars = [
        {"timestamp": base + timedelta(hours=i), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1.0}
        for i in range(5)
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="bad_trail",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=5),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _BadTrail(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    rejected = list(reader.by_type(EventType.ORDER_REJECTED))
    assert any("ratchet" in str(e.payload.get("reason", "")) for e in rejected)
