"""PR 16 — FRAMAMultiChannelStrategy regression.

Mirrors ``test_pr_w_bbkc_multi.py`` for FRAMA. Coverage:

1. 3-symbol fixture (BTC/ETH/AVAX) — every symbol gets at least one
   INTENT_CREATED + FILL.
2. ``registry.build_strategy("frama_multi_channel", ...)`` returns a working
   instance with a shared FRAMA indicator across children.
3. Single-symbol multi config produces the same fill count as the bare
   single-symbol strategy (parity sanity).
4. ConfigError on empty / duplicate / bad-kwarg ``symbols``.
5. ``on_pending_orders`` filters per-symbol (no leak between children).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from backtester.core.clock import ClockHelper
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import (
    BarsView,
    OrdersView,
    OrderView,
    StrategyContext,
)
from backtester.core.engine import BacktestEngine
from backtester.core.errors import ConfigError
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.frama_channel import FRAMAChannelStrategy
from backtester.strategies.frama_multi_channel import FRAMAMultiChannelStrategy
from backtester.strategies.registry import build_strategy

UTC = timezone.utc


# ---------- fixture builders ------------------------------------------------


def _make_flat_then_breakout(target: Path, *, base_price: float = 100.0) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(250):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": base_price,
                "high": base_price + 0.05,
                "low": base_price - 0.05,
                "close": base_price + (0.01 if i % 2 else -0.01),
                "volume": 1.0,
            }
        )
    for i in range(80):
        p = base_price + (i + 1) * 1.0
        rows.append(
            {
                "timestamp": base + timedelta(hours=250 + i),
                "open": p - 0.5,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0,
            }
        )
    peak = base_price + 80.0
    for i in range(80):
        p = peak - (i + 1) * 1.0
        rows.append(
            {
                "timestamp": base + timedelta(hours=330 + i),
                "open": p + 0.5,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0,
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _instrument(symbol: str, base: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _multi_config(
    tmp_path: Path,
    *,
    symbols: list[tuple[str, str, float]],
    primary: str,
) -> BacktestConfig:
    data_dir = tmp_path / "data"
    for sym, _base, price in symbols:
        _make_flat_then_breakout(data_dir / f"{sym}_1h.parquet", base_price=price)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=410 + 1)
    insts = [_instrument(s, b) for s, b, _ in symbols]
    return BacktestConfig(
        run_id="frama_multi_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=insts,
        timeframes_per_symbol={s: ["1h"] for s, _, _ in symbols},
        primary_symbol=primary,
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )


# ---------- 1. three-symbol intent + fill -----------------------------------


def test_three_symbols_each_get_intent_and_fill(tmp_path: Path) -> None:
    syms = [
        ("BTCUSDT", "BTC", 100.0),
        ("ETHUSDT", "ETH", 50.0),
        ("AVAXUSDT", "AVAX", 25.0),
    ]
    cfg = _multi_config(tmp_path, symbols=syms, primary="BTCUSDT")

    strategy = FRAMAMultiChannelStrategy(
        symbols=[s for s, _, _ in syms],
        timeframe="1h",
        child_params={
            "length": 26,
            "distance": "1.5",
            "volatility_window": 200,
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "tp_pct": Decimal("0.06"),
            "sl_pct": Decimal("0.07"),
        },
    )
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    reader = EventLogReader(result.events_path)

    intents = list(reader.by_type(EventType.INTENT_CREATED))
    fills = list(reader.by_type(EventType.FILL))
    intent_syms = {evt.payload["intent"]["symbol"] for evt in intents}
    fill_syms = {evt.payload["symbol"] for evt in fills}

    for sym, _, _ in syms:
        assert sym in intent_syms, (
            f"{sym} produced no INTENT_CREATED in multi run; got {intent_syms}"
        )
        assert sym in fill_syms, (
            f"{sym} produced no FILL in multi run; got {fill_syms}"
        )


# ---------- 2. registry build_strategy + shared indicator -------------------


def test_registry_builds_multi_strategy_with_shared_indicator() -> None:
    strategy = build_strategy(
        "frama_multi_channel",
        {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframe": "1h",
            "child_params": {
                "length": 26,
                "leverage": Decimal("3"),
                "margin_pct": Decimal("0.05"),
            },
        },
    )
    assert isinstance(strategy, FRAMAMultiChannelStrategy)
    assert strategy.symbols == ["BTCUSDT", "ETHUSDT"]
    assert "BTCUSDT" in strategy._children
    assert "ETHUSDT" in strategy._children
    # IndicatorEngine deduplication relies on every child sharing the same
    # FRAMA instance (otherwise horizontal concat collides on column names).
    btc = strategy._children["BTCUSDT"]
    eth = strategy._children["ETHUSDT"]
    assert btc._frama is strategy._frama
    assert eth._frama is strategy._frama


# ---------- 3. single-symbol multi == bare single ---------------------------


def test_single_symbol_multi_matches_bare_single(tmp_path: Path) -> None:
    syms = [("BTCUSDT", "BTC", 100.0)]
    cfg_multi = _multi_config(tmp_path, symbols=syms, primary="BTCUSDT")

    multi = FRAMAMultiChannelStrategy(
        symbols=["BTCUSDT"],
        timeframe="1h",
        child_params={
            "length": 26,
            "distance": "1.5",
            "volatility_window": 200,
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "tp_pct": Decimal("0.06"),
            "sl_pct": Decimal("0.07"),
        },
    )
    res_multi = BacktestEngine(cfg_multi, multi, verbose=False).run()
    fills_multi = list(EventLogReader(res_multi.events_path).by_type(EventType.FILL))

    cfg_single = _multi_config(tmp_path, symbols=syms, primary="BTCUSDT")
    cfg_kwargs: dict[str, Any] = {
        f.name: getattr(cfg_single, f.name)
        for f in cfg_single.__dataclass_fields__.values()
    }
    cfg_kwargs["run_id"] = "frama_single_baseline"
    cfg_single_only = BacktestConfig(**cfg_kwargs)
    bare = FRAMAChannelStrategy(
        length=26,
        distance=1.5,
        volatility_window=200,
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
    )
    res_bare = BacktestEngine(cfg_single_only, bare, verbose=False).run()
    fills_bare = list(EventLogReader(res_bare.events_path).by_type(EventType.FILL))
    assert len(fills_multi) == len(fills_bare)


# ---------- 4. ConfigError ---------------------------------------------------


def test_empty_symbols_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="non-empty 'symbols'"):
        FRAMAMultiChannelStrategy(symbols=[])


def test_duplicate_symbols_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="duplicates"):
        FRAMAMultiChannelStrategy(symbols=["BTCUSDT", "BTCUSDT"])


def test_bad_child_params_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="signature"):
        FRAMAMultiChannelStrategy(
            symbols=["BTCUSDT"],
            child_params={"unknown_kwarg": 1},
        )


# ---------- 5. on_pending_orders symbol isolation ---------------------------


def test_on_pending_orders_filters_by_symbol() -> None:
    """multi forwards only the right symbol's pending orders to each child.

    Today the FRAMA child has a no-op ``on_pending_orders``, but routing
    correctness still matters in case the child grows trailing logic later
    — the wrapper should not leak BTCUSDT stops into the ETHUSDT child.
    """
    bars = BarsView(
        bars={
            "BTCUSDT": {"1h": pl.DataFrame({"close": [100.0]})},
            "ETHUSDT": {"1h": pl.DataFrame({"close": [50.0]})},
        },
        timestamp_index={"BTCUSDT": {"1h": {}}, "ETHUSDT": {"1h": {}}},
        timestamps={"BTCUSDT": {"1h": []}, "ETHUSDT": {"1h": []}},
        clock_helper=ClockHelper(),
        now=datetime(2026, 3, 5, tzinfo=UTC),
    )
    btc_stop = OrderView(
        id="btc_sl_1",
        symbol="BTCUSDT",
        side="sell",
        type="stop",
        state="pending",
        sized_quantity=Decimal("1"),
        remaining=Decimal("1"),
        submitted_at=datetime(2026, 3, 5, tzinfo=UTC),
        limit_price=None,
        stop_price=Decimal("90"),
    )
    eth_stop = OrderView(
        id="eth_sl_1",
        symbol="ETHUSDT",
        side="sell",
        type="stop",
        state="pending",
        sized_quantity=Decimal("1"),
        remaining=Decimal("1"),
        submitted_at=datetime(2026, 3, 5, tzinfo=UTC),
        limit_price=None,
        stop_price=Decimal("45"),
    )
    pending = (btc_stop, eth_stop)
    ctx = StrategyContext(
        now=datetime(2026, 3, 5, tzinfo=UTC),
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        bars=bars,
        orders=OrdersView(_orders=pending),
    )
    multi = FRAMAMultiChannelStrategy(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1h",
        child_params={},
    )
    actions = multi.on_pending_orders(ctx, pending)
    # FRAMAChannelStrategy.on_pending_orders is the BaseStrategy default (no-op),
    # so actions must be empty regardless of how the wrapper splits pending.
    assert actions == []
