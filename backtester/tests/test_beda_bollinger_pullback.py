from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import polars as pl

from backtester.core.clock import ClockHelper
from backtester.core.context import (
    BarsView,
    IndicatorsView,
    OrdersView,
    PortfolioView,
    PositionView,
    StrategyContext,
)
from backtester.core.orders import BracketSpec, ClosePosition, TargetMarginPct
from backtester.indicators.stateful.beda import BedaBand
from backtester.strategies.beda_bollinger_pullback import (
    BedaBollingerPullbackStrategy,
)
from backtester.strategies.registry import build_strategy

UTC = timezone.utc


def _bars(rows: list[tuple[float, float, float, float]]) -> pl.DataFrame:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    data = []
    for i, (open_, high, low, close) in enumerate(rows):
        data.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1.0,
            }
        )
    return pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )


def _ind(strategy: BedaBollingerPullbackStrategy, rows: int, **last) -> pl.DataFrame:
    beda = strategy._beda.name
    bb = strategy._bb.name
    values = {
        f"{beda}_rsi": [50.0] * rows,
        f"{beda}_trend_slow": [49.0] * rows,
        f"{beda}_trend_fast": [49.5] * rows,
        f"{beda}_bull": [False] * rows,
        f"{beda}_bear": [False] * rows,
        f"{beda}_bull_start": [False] * rows,
        f"{beda}_bear_start": [False] * rows,
        f"{bb}_mid": [100.0] * rows,
        f"{bb}_upper": [105.0] * rows,
        f"{bb}_lower": [95.0] * rows,
    }
    for key, value in last.items():
        values[key][-1] = value
    return pl.DataFrame(values)


def _ctx(
    bars_df: pl.DataFrame,
    ind_df: pl.DataFrame,
    *,
    has_position: bool = False,
    position_size: Decimal = Decimal("1"),
) -> StrategyContext:
    symbol = "BTCUSDT"
    tf = "1h"
    ts_list = bars_df["timestamp"].to_list()
    idx_map = {t: i for i, t in enumerate(ts_list)}
    now = ts_list[-1] + timedelta(hours=1)
    bars = BarsView(
        bars={symbol: {tf: bars_df}},
        timestamp_index={symbol: {tf: idx_map}},
        timestamps={symbol: {tf: ts_list}},
        clock_helper=ClockHelper(),
        now=now,
    )
    indicators = IndicatorsView(
        cache={(symbol, tf): ind_df},
        timestamp_index={symbol: {tf: idx_map}},
        timestamps={symbol: {tf: ts_list}},
        clock_helper=ClockHelper(),
        now=now,
    )
    positions = {}
    if has_position:
        positions[symbol] = PositionView(
            symbol=symbol,
            size=position_size,
            avg_price=Decimal("100"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )
    return StrategyContext(
        now=now,
        primary_symbol=symbol,
        primary_timeframe=tf,
        bars=bars,
        indicators=indicators,
        portfolio=PortfolioView(
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            positions=positions,
        ),
        orders=OrdersView(_orders=()),
    )


def test_beda_indicator_emits_state_columns() -> None:
    prices = [100 + i * 0.5 for i in range(40)] + [120 - i for i in range(20)]
    bars = _bars([(p, p + 1, p - 1, p) for p in prices])
    ind = BedaBand().compute(bars)

    prefix = BedaBand().name
    assert {
        f"{prefix}_rsi",
        f"{prefix}_trend_slow",
        f"{prefix}_trend_fast",
        f"{prefix}_bull",
        f"{prefix}_bear",
        f"{prefix}_bull_start",
        f"{prefix}_bear_start",
    }.issubset(set(ind.columns))
    assert ind.height == bars.height
    assert ind[f"{prefix}_bull_start"].drop_nulls().dtype == pl.Boolean


def test_bull_start_sets_pending_then_next_bar_open_below_mid_enters_long() -> None:
    s = BedaBollingerPullbackStrategy(leverage=Decimal("3"), margin_pct=Decimal("0.05"))
    beda = s._beda.name
    first_bars = _bars([(100, 101, 99, 100), (98, 102, 90, 99)])
    first_ind = _ind(s, 2, **{f"{beda}_bull_start": True})

    assert s.on_bar(_ctx(first_bars, first_ind)) == []

    second_bars = _bars(
        [(100, 101, 99, 100), (98, 102, 90, 99), (99, 104, 98, 103)]
    )
    second_ind = _ind(s, 3)
    intents = s.on_bar(_ctx(second_bars, second_ind))

    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "buy"
    assert isinstance(intent.size_spec, TargetMarginPct)
    assert intent.size_spec.margin_pct == Decimal("0.05")
    assert intent.size_spec.leverage == Decimal("3")
    assert isinstance(intent.bracket, BracketSpec)
    assert intent.bracket.stop_loss_price == Decimal("90")
    assert intent.bracket.take_profit_price is None
    assert intent.reason == "beda_bb_bull_start_mid_pullback"


def test_bear_start_sets_pending_then_next_bar_open_above_mid_enters_short() -> None:
    s = BedaBollingerPullbackStrategy(allow_short=True)
    beda = s._beda.name
    first_bars = _bars([(100, 101, 99, 100), (102, 110, 98, 101)])
    first_ind = _ind(s, 2, **{f"{beda}_bear_start": True})

    assert s.on_bar(_ctx(first_bars, first_ind)) == []

    second_bars = _bars(
        [(100, 101, 99, 100), (102, 110, 98, 101), (101, 102, 96, 97)]
    )
    second_ind = _ind(s, 3)
    intents = s.on_bar(_ctx(second_bars, second_ind))

    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert isinstance(intents[0].bracket, BracketSpec)
    assert intents[0].bracket.stop_loss_price == Decimal("110")
    assert intents[0].reason == "beda_bb_bear_start_mid_pullback"


def test_open_filter_rejects_pending_setup() -> None:
    s = BedaBollingerPullbackStrategy()
    beda = s._beda.name
    first_bars = _bars([(100, 101, 99, 100), (98, 102, 90, 99)])
    first_ind = _ind(s, 2, **{f"{beda}_bull_start": True})
    s.on_bar(_ctx(first_bars, first_ind))

    second_bars = _bars(
        [(100, 101, 99, 100), (98, 102, 90, 99), (101, 104, 98, 103)]
    )
    assert s.on_bar(_ctx(second_bars, _ind(s, 3))) == []


def test_rsi_target_closes_long_position() -> None:
    s = BedaBollingerPullbackStrategy(long_rsi_take_profit=65)
    beda = s._beda.name
    bars = _bars([(100, 101, 99, 100), (101, 102, 100, 101)])
    ind = _ind(s, 2, **{f"{beda}_rsi": 65.0})

    intents = s.on_bar(_ctx(bars, ind, has_position=True))

    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert isinstance(intents[0].size_spec, ClosePosition)
    assert intents[0].reduce_only


def test_registry_builds_beda_bollinger_pullback() -> None:
    strategy = build_strategy("beda_bollinger_pullback", {"bb_period": 21})
    assert isinstance(strategy, BedaBollingerPullbackStrategy)
    assert strategy._bb.period == 21
