"""FRAMA + EMA regime strategy tests."""

from __future__ import annotations

from decimal import Decimal

import polars as pl

from backtester.core.orders import ClosePosition, TargetMarginPct
from backtester.indicators.stateless.ema import EMA
from backtester.strategies.frama_ema200_channel import (
    FRAMAEMA200ChannelStrategy,
    FRAMAMultiEMA200ChannelStrategy,
)
from tests.test_pr16_frama_strategy import _bars_from_close, _ctx_from_indicator_df


def _indicator_df(
    n: int,
    *,
    ema_value: float,
    break_up: bool = False,
    break_dn: bool = False,
) -> pl.DataFrame:
    up = [False] * n
    dn = [False] * n
    up[-1] = break_up
    dn[-1] = break_dn
    return pl.DataFrame(
        {
            "frama": [100.0] * n,
            "frama_upper": [101.0] * n,
            "frama_lower": [99.0] * n,
            "frama_alpha": [0.5] * n,
            "frama_dimension": [1.5] * n,
            "frama_break_up": up,
            "frama_break_dn": dn,
            "ema_200": [ema_value] * n,
        }
    )


def test_ema_indicator_outputs_stable_column() -> None:
    bars = _bars_from_close([100.0, 101.0, 102.0])
    out = EMA(period=3).compute(bars)
    assert out.columns == ["ema_3"]
    assert out.height == 3
    assert out["ema_3"][-1] is not None


def test_long_entry_requires_break_up_and_close_above_ema() -> None:
    bars = _bars_from_close([100.0] * 29 + [110.0])
    ind = _indicator_df(30, ema_value=105.0, break_up=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)

    intents = FRAMAEMA200ChannelStrategy(
        margin_pct=Decimal("0.03"),
        leverage=Decimal("3"),
    ).on_bar(ctx)

    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert isinstance(intents[0].size_spec, TargetMarginPct)
    assert intents[0].size_spec.margin_pct == Decimal("0.03")
    assert intents[0].bracket is None
    assert intents[0].reason == "frama_ema200_long_break_up"


def test_short_entry_requires_break_down_and_close_below_ema() -> None:
    bars = _bars_from_close([100.0] * 29 + [90.0])
    ind = _indicator_df(30, ema_value=95.0, break_dn=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)

    intents = FRAMAEMA200ChannelStrategy().on_bar(ctx)

    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert intents[0].reason == "frama_ema200_short_break_dn"


def test_regime_filter_blocks_wrong_side_signal() -> None:
    bars = _bars_from_close([100.0] * 29 + [90.0])
    ind = _indicator_df(30, ema_value=95.0, break_up=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)

    assert FRAMAEMA200ChannelStrategy().on_bar(ctx) == []


def test_long_exits_on_opposite_frama_break() -> None:
    bars = _bars_from_close([100.0] * 29 + [110.0])
    ind = _indicator_df(30, ema_value=105.0, break_dn=True)
    ctx = _ctx_from_indicator_df(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        position_size=Decimal("1"),
    )

    intents = FRAMAEMA200ChannelStrategy().on_bar(ctx)

    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert isinstance(intents[0].size_spec, ClosePosition)
    assert intents[0].reduce_only is True
    assert intents[0].reason == "frama_ema200_exit_long_break_dn"


def test_short_exits_on_opposite_frama_break() -> None:
    bars = _bars_from_close([100.0] * 29 + [90.0])
    ind = _indicator_df(30, ema_value=95.0, break_up=True)
    ctx = _ctx_from_indicator_df(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        position_size=Decimal("-1"),
    )

    intents = FRAMAEMA200ChannelStrategy().on_bar(ctx)

    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert isinstance(intents[0].size_spec, ClosePosition)
    assert intents[0].reduce_only is True
    assert intents[0].reason == "frama_ema200_exit_short_break_up"


def test_multi_strategy_shares_frama_and_ema_instances() -> None:
    strategy = FRAMAMultiEMA200ChannelStrategy(
        symbols=["BTCUSDT", "ETHUSDT"],
        child_params={"ema_period": 200},
    )
    indicators = strategy.required_indicators()
    assert len(indicators) == 2
    assert isinstance(indicators[1], EMA)
    assert strategy._children["BTCUSDT"]._ema is strategy._ema
    assert strategy._children["ETHUSDT"]._frama is strategy._frama
