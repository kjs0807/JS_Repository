"""PR 16 — FRAMAChannelStrategy single-symbol regression.

Coverage:
1. ``required_indicators`` exposes a single FRAMAChannel that the IndicatorEngine
   can precompute.
2. break_up fixture → long market intent with bracket TP/SL using
   ``tp_pct/leverage`` price-level pct (BBKC parity).
3. break_dn fixture → short market intent when ``allow_short=True``.
4. ``allow_short=False`` → break_dn is ignored.
5. Already-in-position → no duplicate entry intent.
6. ``drop_tp=True`` → bracket has SL but no TP.
7. End-to-end: BacktestEngine produces FILL events from the FRAMA strategy on a
   simple flat-then-breakout fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import polars as pl

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
from backtester.core.orders import (
    BracketSpec,
    TargetMarginPct,
)
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.frama_channel import FRAMAChannelStrategy

UTC = timezone.utc


# ---------- helpers ---------------------------------------------------------


def _make_flat_then_breakout(target: Path, *, base_price: float = 100.0) -> None:
    """250 flat bars + 80 strong-up + 80 strong-down. Long enough for both
    a break_up early on and a break_dn after the trend reverses.
    """
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


def _ctx_from_indicator_df(
    *,
    bars_df: pl.DataFrame,
    ind_df: pl.DataFrame,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    has_position: bool = False,
    position_size: Decimal = Decimal("1"),
) -> StrategyContext:
    """Build a minimal StrategyContext that exposes both bars and indicators
    truncated up to the current ``now`` (= last timestamp in ``bars_df``).

    The Engine normally builds these views; this helper lets us drive
    ``on_bar`` directly with hand-shaped indicator values without spinning a
    full engine — much cheaper than running a backtest per behavioural test.
    """
    ts_list = bars_df["timestamp"].to_list()
    idx_map = {t: i for i, t in enumerate(ts_list)}
    # ``now`` must be the *bar close* time, which for a 1h bar starting at
    # ``ts`` is ``ts + 1h``. ClockHelper.last_closed_time then resolves back
    # to ``ts`` and the views expose all bars including the final one.
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
        portfolio_positions[symbol] = PositionView(
            symbol=symbol,
            size=position_size,
            avg_price=Decimal(str(bars_df["close"][-1])),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
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


def _bars_from_close(prices: list[float]) -> pl.DataFrame:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
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


def _make_indicator_df(
    n: int,
    *,
    last_break_up: bool = False,
    last_break_dn: bool = False,
) -> pl.DataFrame:
    """Hand-shaped indicator DataFrame with a controlled last-bar signal.

    Most rows have null/false signals; only the final row gets the requested
    break flag — exactly what the strategy reads.
    """
    breaks_up = [False] * n
    breaks_dn = [False] * n
    if last_break_up:
        breaks_up[-1] = True
    if last_break_dn:
        breaks_dn[-1] = True
    return pl.DataFrame(
        {
            "frama": [100.0] * n,
            "frama_upper": [101.0] * n,
            "frama_lower": [99.0] * n,
            "frama_alpha": [0.5] * n,
            "frama_dimension": [1.5] * n,
            "frama_break_up": breaks_up,
            "frama_break_dn": breaks_dn,
        }
    )


# ---------- 1. required_indicators ------------------------------------------


def test_required_indicators_returns_single_frama_instance() -> None:
    s = FRAMAChannelStrategy(length=26, distance=1.5)
    inds = s.required_indicators()
    assert len(inds) == 1
    assert isinstance(inds[0], FRAMAChannel)
    assert inds[0].length == 26


# ---------- 2. break_up → long ----------------------------------------------


def test_break_up_emits_long_market_intent_with_bracket() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_indicator_df(30, last_break_up=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)
    s = FRAMAChannelStrategy(
        length=26,
        distance=1.5,
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
    )
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "buy"
    assert intent.type == "market"
    assert isinstance(intent.size_spec, TargetMarginPct)
    assert intent.size_spec.margin_pct == Decimal("0.05")
    assert intent.size_spec.leverage == Decimal("3")
    # BBKC parity: price-level pct = pct/leverage. tp_pct=0.06, leverage=3
    # → +2% above entry for buy.
    assert intent.bracket is not None
    expected_tp = Decimal("100.0") * (Decimal("1") + Decimal("0.06") / Decimal("3"))
    expected_sl = Decimal("100.0") * (Decimal("1") - Decimal("0.07") / Decimal("3"))
    assert intent.bracket.take_profit_price == expected_tp
    assert intent.bracket.stop_loss_price == expected_sl
    assert intent.reason == "frama_channel_break_up"


# ---------- 3. break_dn → short when allowed --------------------------------


def test_break_dn_emits_short_market_intent_when_allow_short() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_indicator_df(30, last_break_dn=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)
    s = FRAMAChannelStrategy(
        length=26,
        distance=1.5,
        allow_short=True,
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
    )
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "sell"
    # Short bracket: TP below entry, SL above entry.
    assert intent.bracket is not None
    expected_tp = Decimal("100.0") * (Decimal("1") - Decimal("0.06") / Decimal("3"))
    expected_sl = Decimal("100.0") * (Decimal("1") + Decimal("0.07") / Decimal("3"))
    assert intent.bracket.take_profit_price == expected_tp
    assert intent.bracket.stop_loss_price == expected_sl
    assert intent.reason == "frama_channel_break_dn"


# ---------- 4. allow_short=False blocks short -------------------------------


def test_break_dn_blocked_when_allow_short_false() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_indicator_df(30, last_break_dn=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)
    s = FRAMAChannelStrategy(allow_short=False)
    assert s.on_bar(ctx) == []


# ---------- 5. existing position blocks duplicate entry ---------------------


def test_no_duplicate_entry_while_in_position() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_indicator_df(30, last_break_up=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind, has_position=True)
    s = FRAMAChannelStrategy()
    assert s.on_bar(ctx) == []


# ---------- 6. drop_tp removes only the TP ----------------------------------


def test_drop_tp_emits_bracket_with_sl_only() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_indicator_df(30, last_break_up=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)
    s = FRAMAChannelStrategy(
        leverage=Decimal("3"),
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
        drop_tp=True,
    )
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    bracket = intents[0].bracket
    assert isinstance(bracket, BracketSpec)
    assert bracket.take_profit_price is None
    assert bracket.stop_loss_price is not None


def test_no_tp_no_sl_yields_no_bracket() -> None:
    bars = _bars_from_close([100.0] * 30)
    ind = _make_indicator_df(30, last_break_up=True)
    ctx = _ctx_from_indicator_df(bars_df=bars, ind_df=ind)
    s = FRAMAChannelStrategy(tp_pct=None, sl_pct=None)
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].bracket is None


# ---------- 7. end-to-end engine smoke --------------------------------------


def test_engine_run_produces_fills(tmp_path: Path) -> None:
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    _make_flat_then_breakout(data_dir / f"{sym}_1h.parquet", base_price=100.0)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=410 + 1)
    cfg = BacktestConfig(
        run_id="frama_single_test",
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
    strategy = FRAMAChannelStrategy(
        length=26,
        distance=1.5,
        volatility_window=200,
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
    )
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    fills = list(EventLogReader(result.events_path).by_type(EventType.FILL))
    intents = list(
        EventLogReader(result.events_path).by_type(EventType.INTENT_CREATED)
    )
    # FRAMA on a synthetic uptrend after 200 flat bars should fire at least
    # one entry intent (and therefore at least one fill).
    assert len(intents) >= 1, "no FRAMA entry intent on uptrend fixture"
    assert len(fills) >= 1, "no FILL on uptrend fixture"
    sides: set[Literal["buy", "sell"]] = {f.payload["side"] for f in fills}
    # At least one buy (the entry).
    assert "buy" in sides
