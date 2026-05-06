"""SATSStrategy regression — Phase 2 single-symbol entry + single TP bracket.

Coverage:

1. ``required_indicators`` exposes a single ``SATSIndicator`` reflecting
   the constructor params.
2. signal=+1 + ready=True + flat → long market intent with ``BracketSpec``
   carrying TP3/SL prices from the indicator row, sized via
   ``TargetNotionalPct(margin_pct)``.
3. signal=-1 + ready=True + flat → short market intent (mirror).
4. ``allow_short=False`` → short signals are dropped silently.
5. Already-in-position → no entry intent (Phase 1 ``ignore_while_position``).
6. ``sats_ready=False`` → no entry intent (warmup gate).
7. ``signal=0`` → no entry intent.
8. ``single_tp_level="tp1"`` reads ``sats_tp1_price`` for the bracket TP.
9. Time stop fires when ``ctx.bars_held() >= trade_max_age_bars`` and emits
   a reduce-only ``ClosePosition`` intent.
10. Time stop disabled (``trade_max_age_bars=None``) leaves position alone
    and the strategy continues to ignore signals while in position.
11. Time stop wins over a fresh signal in the same bar — the strategy
    closes first (entry comes only after flat).
12. Registry: ``build_strategy("sats", {...})`` returns SATSStrategy;
    invalid params raise ``ConfigError``.
13. Constructor validation rejects bad ``preset``/``tp_mode``/
    ``single_tp_level``/non-positive ``margin_pct``.
14. End-to-end smoke: BacktestEngine on a synthetic flat-then-breakout
    parquet produces at least one fill via the ``"sats"`` registry name.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import polars as pl
import pytest

from backtester.core.clock import ClockHelper
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import (
    BarsView,
    IndicatorsView,
    OrdersView,
    PortfolioView,
    PositionView,
    StrategyContext,
)
from backtester.core.engine import BacktestEngine
from backtester.core.errors import ConfigError
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    TargetNotionalPct,
)
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.indicators.stateful.sats import SATSIndicator
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.registry import build_strategy
from backtester.strategies.sats import SATSStrategy

UTC = timezone.utc


# ---------- helpers ---------------------------------------------------------


def _bars_from_close(prices: list[float]) -> pl.DataFrame:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    for i, p in enumerate(prices):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": p,
                "high": p + 0.1,
                "low": p - 0.1,
                "close": p,
                "volume": 1.0,
            }
        )
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )


def _make_sats_df(
    n: int,
    *,
    last_signal: int = 0,
    last_ready: bool = True,
    sl_price: float = 95.0,
    tp1_price: float = 105.0,
    tp2_price: float = 110.0,
    tp3_price: float = 115.0,
    short: bool = False,
) -> pl.DataFrame:
    """Hand-shaped indicator DataFrame matching the 25-column schema.

    Most rows carry no signal; the final row gets ``last_signal`` and the
    SL/TP plan. ``short=True`` shifts SL above and TPs below entry, mirroring
    the indicator's short branch.
    """
    if short:
        sl_p = 105.0
        tp1, tp2, tp3 = 95.0, 90.0, 85.0
    else:
        sl_p = sl_price
        tp1, tp2, tp3 = tp1_price, tp2_price, tp3_price

    sig = [0] * n
    sig[-1] = int(last_signal)
    ready = [False] * n
    if last_ready:
        for i in range(n):
            ready[i] = True

    null_floats = [None] * n
    return pl.DataFrame(
        {
            "sats_atr": null_floats,
            "sats_raw_atr": null_floats,
            "sats_er": null_floats,
            "sats_vol_ratio": null_floats,
            "sats_tqi": null_floats,
            "sats_tqi_er": null_floats,
            "sats_tqi_vol": null_floats,
            "sats_tqi_struct": null_floats,
            "sats_tqi_mom": null_floats,
            "sats_active_mult": null_floats,
            "sats_passive_mult": null_floats,
            "sats_lower_band": null_floats,
            "sats_upper_band": null_floats,
            "sats_trend": [1] * n,
            "sats_st_line": null_floats,
            "sats_signal": sig,
            "sats_entry_price": [None] * (n - 1) + [100.0],
            "sats_sl_price": [None] * (n - 1) + [sl_p],
            "sats_tp1_price": [None] * (n - 1) + [tp1],
            "sats_tp2_price": [None] * (n - 1) + [tp2],
            "sats_tp3_price": [None] * (n - 1) + [tp3],
            "sats_tp1_r": [None] * (n - 1) + [1.0],
            "sats_tp2_r": [None] * (n - 1) + [2.0],
            "sats_tp3_r": [None] * (n - 1) + [3.0],
            "sats_ready": ready,
        },
        schema_overrides={
            "sats_trend": pl.Int8,
            "sats_signal": pl.Int8,
            "sats_ready": pl.Boolean,
        },
    )


def _ctx(
    *,
    bars_df: pl.DataFrame,
    ind_df: pl.DataFrame,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    has_position: bool = False,
    position_size: Decimal = Decimal("1"),
    bars_held: int | None = None,
) -> StrategyContext:
    """Minimal StrategyContext exposing both bars and indicators truncated up
    to the last bar in ``bars_df``. ``bars_held`` controls the synthetic
    position's ``opened_at`` so ``ctx.bars_held()`` returns the desired age.
    """
    ts_list = bars_df["timestamp"].to_list()
    idx_map = {t: i for i, t in enumerate(ts_list)}
    now = ts_list[-1] + timedelta(hours=1)
    bars = BarsView(
        bars={symbol: {timeframe: bars_df}},
        timestamp_index={symbol: {timeframe: idx_map}},
        timestamps={symbol: {timeframe: ts_list}},
        clock_helper=ClockHelper(),
        now=now,
    )
    indicators = IndicatorsView(
        cache={(symbol, timeframe): ind_df},
        timestamp_index={symbol: {timeframe: idx_map}},
        timestamps={symbol: {timeframe: ts_list}},
        clock_helper=ClockHelper(),
        now=now,
    )
    portfolio_positions: dict[str, PositionView] = {}
    if has_position:
        opened_at = now - timedelta(hours=bars_held) if bars_held else now
        portfolio_positions[symbol] = PositionView(
            symbol=symbol,
            size=position_size,
            avg_price=Decimal(str(bars_df["close"][-1])),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            opened_at=opened_at,
        )
    portfolio = PortfolioView(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        positions=portfolio_positions,
    )
    return StrategyContext(
        now=now,
        primary_symbol=symbol,
        primary_timeframe=timeframe,
        bars=bars,
        indicators=indicators,
        portfolio=portfolio,
        orders=OrdersView(_orders=()),
    )


# ---------- 1. required_indicators ------------------------------------------


def test_required_indicators_returns_single_sats_instance() -> None:
    s = SATSStrategy(preset="Custom", atr_len=11)
    inds = s.required_indicators()
    assert len(inds) == 1
    assert isinstance(inds[0], SATSIndicator)
    assert inds[0].cfg.atr_len == 11
    assert inds[0].cfg.preset == "Custom"


# ---------- 2. long entry ---------------------------------------------------


def test_long_signal_emits_long_market_intent_with_bracket() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=1)
    ctx = _ctx(bars_df=bars, ind_df=ind)
    s = SATSStrategy(margin_pct=Decimal("0.07"), single_tp_level="tp3")
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "buy"
    assert intent.type == "market"
    assert isinstance(intent.size_spec, TargetNotionalPct)
    assert intent.size_spec.notional_pct == Decimal("0.07")
    assert intent.bracket is not None
    assert intent.bracket.take_profit_price == Decimal("115.0")
    assert intent.bracket.stop_loss_price == Decimal("95.0")
    assert intent.bracket.time_stop_bars is None  # no double-source-of-truth
    assert intent.reason == "sats_buy"


# ---------- 3. short entry --------------------------------------------------


def test_short_signal_emits_short_market_intent() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=-1, short=True)
    ctx = _ctx(bars_df=bars, ind_df=ind)
    s = SATSStrategy(allow_short=True, single_tp_level="tp3")
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "sell"
    assert intent.bracket is not None
    assert intent.bracket.stop_loss_price == Decimal("105.0")
    assert intent.bracket.take_profit_price == Decimal("85.0")
    assert intent.reason == "sats_sell"


# ---------- 4. allow_short=False --------------------------------------------


def test_short_signal_blocked_when_allow_short_false() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=-1, short=True)
    ctx = _ctx(bars_df=bars, ind_df=ind)
    s = SATSStrategy(allow_short=False)
    assert s.on_bar(ctx) == []


# ---------- 5. existing position blocks duplicate entry --------------------


def test_no_duplicate_entry_while_in_position() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=1)
    ctx = _ctx(bars_df=bars, ind_df=ind, has_position=True)
    s = SATSStrategy()
    assert s.on_bar(ctx) == []


# ---------- 6. warmup gate --------------------------------------------------


def test_not_ready_blocks_entry() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=1, last_ready=False)
    ctx = _ctx(bars_df=bars, ind_df=ind)
    s = SATSStrategy()
    assert s.on_bar(ctx) == []


# ---------- 7. signal=0 -----------------------------------------------------


def test_zero_signal_emits_nothing() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=0)
    ctx = _ctx(bars_df=bars, ind_df=ind)
    s = SATSStrategy()
    assert s.on_bar(ctx) == []


# ---------- 8. single_tp_level chooses correct column ----------------------


@pytest.mark.parametrize(
    ("level", "expected_tp"),
    [
        ("tp1", Decimal("105.0")),
        ("tp2", Decimal("110.0")),
        ("tp3", Decimal("115.0")),
    ],
)
def test_single_tp_level_picks_correct_price(
    level: str, expected_tp: Decimal
) -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=1)
    ctx = _ctx(bars_df=bars, ind_df=ind)
    s = SATSStrategy(single_tp_level=level)
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].bracket is not None
    assert intents[0].bracket.take_profit_price == expected_tp


# ---------- 9. time stop fires ---------------------------------------------


def test_time_stop_emits_close_position_intent() -> None:
    bars = _bars_from_close([100.0] * 50)
    # No signal — only the time-stop branch should run.
    ind = _make_sats_df(50, last_signal=0)
    ctx = _ctx(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        position_size=Decimal("1"),
        bars_held=12,
    )
    s = SATSStrategy(trade_max_age_bars=10)
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "sell"  # long position → sell to close
    assert intent.type == "market"
    assert isinstance(intent.size_spec, ClosePosition)
    assert intent.reduce_only is True
    assert intent.reason == "sats_time_stop"
    assert intent.bracket is None


def test_time_stop_handles_short_position() -> None:
    bars = _bars_from_close([100.0] * 50)
    ind = _make_sats_df(50, last_signal=0)
    ctx = _ctx(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        position_size=Decimal("-1"),
        bars_held=15,
    )
    s = SATSStrategy(trade_max_age_bars=10)
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].side == "buy"  # short → buy to close
    assert intents[0].reason == "sats_time_stop"


# ---------- 10. time stop disabled ------------------------------------------


def test_time_stop_disabled_keeps_position() -> None:
    bars = _bars_from_close([100.0] * 50)
    ind = _make_sats_df(50, last_signal=0)
    ctx = _ctx(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        bars_held=999,
    )
    s = SATSStrategy(trade_max_age_bars=None)
    assert s.on_bar(ctx) == []


# ---------- 11. time stop wins over fresh signal ----------------------------


def test_time_stop_takes_precedence_over_new_signal() -> None:
    """Held >= cap and a fresh +1 signal arrive on the same bar.

    Strategy closes first (next bar will see flat and act on the next
    signal that fires from then on). We never want a time-stop ClosePosition
    racing with an entry intent in the same on_bar call.
    """
    bars = _bars_from_close([100.0] * 50)
    ind = _make_sats_df(50, last_signal=1)
    ctx = _ctx(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        bars_held=12,
    )
    s = SATSStrategy(trade_max_age_bars=10)
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].reason == "sats_time_stop"
    assert isinstance(intents[0].size_spec, ClosePosition)


# ---------- 12. registry ----------------------------------------------------


def test_registry_builds_sats_with_primitive_params() -> None:
    s = build_strategy(
        "sats",
        {
            "preset": "Custom",
            "timeframe_minutes": 60,
            "atr_len": 12,
            "single_tp_level": "tp2",
            "margin_pct": "0.05",
        },
    )
    assert isinstance(s, SATSStrategy)
    assert s.single_tp_level == "tp2"
    assert s.margin_pct == Decimal("0.05")


def test_registry_rejects_unknown_kwarg_with_config_error() -> None:
    with pytest.raises(ConfigError):
        build_strategy("sats", {"nonexistent_param": 7})


def test_registry_rejects_invalid_preset_with_config_error() -> None:
    # SATSStrategy raises ValueError for bad preset; build_strategy wraps
    # TypeError into ConfigError, but ValueError propagates as-is.
    with pytest.raises(ValueError):
        build_strategy("sats", {"preset": "BadPreset"})


# ---------- 13. constructor validation --------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"preset": "Nonsense"},
        {"tp_mode": "Whatever"},
        {"single_tp_level": "tp9"},
        {"margin_pct": "0"},
        {"margin_pct": "-0.01"},
    ],
)
def test_constructor_rejects_invalid(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SATSStrategy(**kwargs)


def test_constructor_treats_zero_or_negative_max_age_as_disabled() -> None:
    s_zero = SATSStrategy(trade_max_age_bars=0)
    s_neg = SATSStrategy(trade_max_age_bars=-5)
    assert s_zero.trade_max_age_bars is None
    assert s_neg.trade_max_age_bars is None


# ---------- 14. end-to-end engine smoke -------------------------------------


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


def _make_v_shape_bars(target: Path, *, base_price: float = 100.0) -> None:
    """100 flat + 100 sharp down + 150 sharp up.

    SATS initial trend is +1 (Pine ``var int stTrend = 1``), so a pure
    uptrend never flips — no signals. The down leg drives a flip-down past
    warmup (warmup ~60 bars), and the recovery leg drives the flip-up that
    we want to assert as an entry intent.
    """
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    # Flat noise — establishes ATR / band state before the regime shift.
    for i in range(100):
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
    # Down leg — forces trend to flip to -1.
    for i in range(100):
        p = base_price - (i + 1) * 0.5
        rows.append(
            {
                "timestamp": base + timedelta(hours=100 + i),
                "open": p + 0.25,
                "high": p + 0.25,
                "low": p - 0.25,
                "close": p,
                "volume": 1.0,
            }
        )
    trough = base_price - 100 * 0.5  # = 50
    # Recovery leg — forces flip back to +1, generating a buy signal.
    for i in range(150):
        p = trough + (i + 1) * 0.5
        rows.append(
            {
                "timestamp": base + timedelta(hours=200 + i),
                "open": p - 0.25,
                "high": p + 0.25,
                "low": p - 0.25,
                "close": p,
                "volume": 1.0,
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def test_engine_run_produces_fills_via_registry(tmp_path: Path) -> None:
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    _make_v_shape_bars(data_dir / f"{sym}_1h.parquet", base_price=100.0)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=350 + 1)

    cfg = BacktestConfig(
        run_id="sats_smoke",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(sym, "BTC")],
        timeframes_per_symbol={sym: ["1h"]},
        primary_symbol=sym,
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = build_strategy(
        "sats",
        {
            "preset": "Custom",
            "timeframe_minutes": 60,
            "atr_len": 14,
            "base_mult": 2.0,
            "er_length": 20,
            "rsi_len": 14,
            "sl_atr_mult": 1.5,
            "single_tp_level": "tp3",
            "margin_pct": "0.05",
        },
    )
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    intents = list(
        EventLogReader(result.events_path).by_type(EventType.INTENT_CREATED)
    )
    fills = list(EventLogReader(result.events_path).by_type(EventType.FILL))
    assert len(intents) >= 1, "no SATS entry intent on uptrend fixture"
    assert len(fills) >= 1, "no FILL on uptrend fixture"
    sides: set[Literal["buy", "sell"]] = {f.payload["side"] for f in fills}
    assert "buy" in sides


# ---------- BracketSpec sanity ---------------------------------------------


def test_long_bracket_carries_no_time_stop_bars() -> None:
    """SATS spec recommends NOT setting BracketSpec.time_stop_bars (engine
    ignores it; strategy owns timeout). Defensive guard."""
    bars = _bars_from_close([100.0] * 30)
    ind = _make_sats_df(30, last_signal=1)
    ctx = _ctx(bars_df=bars, ind_df=ind)
    s = SATSStrategy(trade_max_age_bars=50)
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    bracket = intents[0].bracket
    assert isinstance(bracket, BracketSpec)
    assert bracket.time_stop_bars is None
