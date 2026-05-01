"""PR L — OCO + Same-Bar TP/SL Policy 회귀.

검증:
1. TP fill → SL cancel + reason="oco_sibling_filled".
2. SL fill → TP cancel + reason="oco_sibling_filled".
3. Same-bar TP/SL 양쪽 도달 — PESSIMISTIC: SL 우선.
4. Same-bar TP/SL 양쪽 도달 — OPTIMISTIC: TP 우선.
5. 명시적 user cancel 의 ORDER_CANCELLED reason="user_cancel".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import (
    BracketSpec,
    OrderIntent,
    TargetUnits,
)
from backtester.core.types import BarPathModel
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


def _make_parquet_with_bars(target: Path, bars: list[dict[str, Any]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(bars).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _config(
    tmp_path: Path,
    *,
    bars: list[dict[str, Any]],
    bar_path_model: BarPathModel = BarPathModel.PESSIMISTIC,
) -> BacktestConfig:
    data_dir = tmp_path / "data"
    _make_parquet_with_bars(data_dir / "BTCUSDT_1h.parquet", bars)
    base = bars[0]["timestamp"]
    end = bars[-1]["timestamp"] + timedelta(hours=1)
    return BacktestConfig(
        run_id="oco_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        bar_path_model=bar_path_model,
    )


class _LongWithBracket(BaseStrategy):
    def __init__(self, tp: str, sl: str) -> None:
        self.tp = Decimal(tp)
        self.sl = Decimal(sl)
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
                bracket=BracketSpec(
                    take_profit_price=self.tp,
                    stop_loss_price=self.sl,
                ),
            )
        ]


def _bar(t: datetime, o: float, h: float, low: float, c: float) -> dict[str, Any]:
    return {
        "timestamp": t,
        "open": float(o),
        "high": float(h),
        "low": float(low),
        "close": float(c),
        "volume": 1.0,
    }


# ---------- 1. TP fill → SL cancel ------------------------------------------


def test_tp_fill_cancels_sl(tmp_path: Path) -> None:
    """long entry @100, TP=110, SL=95. Bar 2: high 가 TP 도달, low 는 SL 미도달.

    봉 0: entry intent. 봉 1: open=100 entry fill, child 생성. 봉 2: TP fill,
    SL cancelled.
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        _bar(base, 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=1), 100, 100.5, 99.5, 100),  # entry @100
        _bar(base + timedelta(hours=2), 100, 115, 99, 110),  # high=115 → TP 도달
    ]
    cfg = _config(tmp_path, bars=bars)
    result = BacktestEngine(cfg, _LongWithBracket("110", "95"), verbose=False).run()
    reader = EventLogReader(result.events_path)
    cancels = list(reader.by_type(EventType.ORDER_CANCELLED))
    assert len(cancels) == 1
    assert cancels[0].payload["reason"] == "oco_sibling_filled"


# ---------- 2. SL fill → TP cancel ------------------------------------------


def test_sl_fill_cancels_tp(tmp_path: Path) -> None:
    """Bar 2: low=85 SL 도달, high=105 TP 미도달."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        _bar(base, 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=1), 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=2), 100, 105, 85, 90),
    ]
    cfg = _config(tmp_path, bars=bars)
    result = BacktestEngine(cfg, _LongWithBracket("110", "95"), verbose=False).run()
    reader = EventLogReader(result.events_path)
    cancels = list(reader.by_type(EventType.ORDER_CANCELLED))
    assert len(cancels) == 1
    assert cancels[0].payload["reason"] == "oco_sibling_filled"
    fills = list(reader.by_type(EventType.FILL))
    # entry + SL = 2 fill
    assert len(fills) == 2
    # 두 번째 fill 이 SL (sell at stop_price)
    assert fills[1].payload["side"] == "sell"


# ---------- 3. Same-bar both touch — PESSIMISTIC: SL 먼저 -------------------


def test_same_bar_both_touch_pessimistic_picks_sl(tmp_path: Path) -> None:
    """Bar 2: high=115 (TP 도달) AND low=85 (SL 도달). PESSIMISTIC → SL 우선."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        _bar(base, 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=1), 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=2), 100, 115, 85, 100),
    ]
    cfg = _config(tmp_path, bars=bars, bar_path_model=BarPathModel.PESSIMISTIC)
    result = BacktestEngine(cfg, _LongWithBracket("110", "95"), verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    assert len(fills) == 2  # entry + SL
    sl_fill = fills[1]
    # SL = sell at stop_price 95
    assert sl_fill.payload["side"] == "sell"
    assert Decimal(sl_fill.payload["price"]) == Decimal("95")


# ---------- 4. Same-bar both touch — OPTIMISTIC: TP 먼저 --------------------


def test_same_bar_both_touch_optimistic_picks_tp(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        _bar(base, 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=1), 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=2), 100, 115, 85, 100),
    ]
    cfg = _config(tmp_path, bars=bars, bar_path_model=BarPathModel.OPTIMISTIC)
    result = BacktestEngine(cfg, _LongWithBracket("110", "95"), verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    assert len(fills) == 2  # entry + TP
    tp_fill = fills[1]
    # TP = sell at limit_price 110
    assert tp_fill.payload["side"] == "sell"
    assert Decimal(tp_fill.payload["price"]) == Decimal("110")


# ---------- 5. user explicit cancel reason ---------------------------------


class _CancelLimitStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._step = 0
        self._target_id: str | None = None

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        self._step += 1
        if self._step == 1:
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
        pending: tuple[Any, ...],
    ) -> list[Any]:
        from backtester.core.orders import OrderAction

        if self._target_id is None:
            for o in pending:
                if o.type == "limit":
                    self._target_id = o.id
                    break
            return []
        oid = self._target_id
        self._target_id = None
        return [OrderAction(type="cancel", order_id=oid)]


def test_user_cancel_emits_user_cancel_reason(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        _bar(base + timedelta(hours=i), 100, 101, 99, 100) for i in range(5)
    ]
    cfg = _config(tmp_path, bars=bars)
    result = BacktestEngine(cfg, _CancelLimitStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    cancels = list(reader.by_type(EventType.ORDER_CANCELLED))
    assert len(cancels) == 1
    assert cancels[0].payload["reason"] == "user_cancel"
