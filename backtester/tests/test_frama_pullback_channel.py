"""FRAMA pullback strategy regression.

Coverage:
- pending registered on signal bar (no entry on the same bar)
- entry triggers on later pullback to FRAMA mid (long: bar.low <= mid;
  short: bar.high >= mid)
- SL pre-touch invalidates pending before entry
- same-direction new signal refreshes SL + signal_ts
- opposite signal overwrites pending direction
- exit on opposite FRAMA signal while in position (ClosePosition reduce_only)
- ``allow_short=False`` blocks short entry but still consumes the pending
- registry build + multi wrapper share the FRAMA indicator instance
- per-symbol pending isolation in the multi wrapper
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest

from backtester.core.errors import ConfigError
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    OrderIntent,
    TargetMarginPct,
)
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.strategies.frama_pullback_channel import (
    FRAMAChannelPullbackStrategy,
    FRAMAMultiChannelPullbackStrategy,
)
from backtester.strategies.registry import build_strategy
from tests.test_pr16_frama_strategy import _ctx_from_indicator_df

UTC = timezone.utc


# ---------- bar/indicator helpers -------------------------------------------


def _bars_from_hlc(
    rows: list[tuple[float, float, float]],
    *,
    base: datetime | None = None,
) -> pl.DataFrame:
    """Build an OHLCV DataFrame from per-bar (high, low, close) tuples.

    open is set to the previous close (or the first close on bar 0). volume
    is constant 1. Timestamps are 1h apart starting from ``base`` (default
    2026-03-01 UTC).
    """
    if base is None:
        base = datetime(2026, 3, 1, tzinfo=UTC)
    out = []
    for i, (h, low, c) in enumerate(rows):
        prev_close = rows[i - 1][2] if i > 0 else c
        out.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": prev_close,
                "high": h,
                "low": low,
                "close": c,
                "volume": 1.0,
            }
        )
    return pl.DataFrame(out).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )


def _ind(
    n: int,
    *,
    frama: float = 100.0,
    upper: float = 102.0,
    lower: float = 98.0,
    break_up_at: list[int] | None = None,
    break_dn_at: list[int] | None = None,
) -> pl.DataFrame:
    """Hand-shaped indicator DataFrame matching the FRAMA columns the strategy
    reads. ``break_up_at`` / ``break_dn_at`` are lists of bar indices to mark
    True. All other rows are False.
    """
    bu = [False] * n
    bd = [False] * n
    for i in break_up_at or []:
        bu[i] = True
    for i in break_dn_at or []:
        bd[i] = True
    return pl.DataFrame(
        {
            "frama": [frama] * n,
            "frama_upper": [upper] * n,
            "frama_lower": [lower] * n,
            "frama_alpha": [0.5] * n,
            "frama_dimension": [1.5] * n,
            "frama_break_up": bu,
            "frama_break_dn": bd,
        }
    )


def _run_bars(
    strategy: FRAMAChannelPullbackStrategy,
    *,
    bars_df: pl.DataFrame,
    ind_df: pl.DataFrame,
    has_position: bool = False,
    position_size: Decimal = Decimal("1"),
) -> list[list[OrderIntent]]:
    """Drive the strategy bar-by-bar, returning the intent list per bar.

    Builds a fresh ``StrategyContext`` slicing both DataFrames up to bar ``i``
    so the strategy sees the same growing window the engine would expose.
    """
    out: list[list[OrderIntent]] = []
    for i in range(bars_df.height):
        sub_bars = bars_df.head(i + 1)
        sub_ind = ind_df.head(i + 1)
        ctx = _ctx_from_indicator_df(
            bars_df=sub_bars,
            ind_df=sub_ind,
            has_position=has_position,
            position_size=position_size,
        )
        out.append(strategy.on_bar(ctx))
    return out


# ---------- 1. registry / required indicators -------------------------------


def test_required_indicators_returns_single_frama() -> None:
    s = FRAMAChannelPullbackStrategy()
    inds = s.required_indicators()
    assert len(inds) == 1
    assert isinstance(inds[0], FRAMAChannel)


def test_registry_builds_pullback_strategy() -> None:
    s = build_strategy("frama_pullback_channel", {})
    assert isinstance(s, FRAMAChannelPullbackStrategy)


def test_registry_builds_multi_pullback() -> None:
    m = build_strategy(
        "frama_multi_pullback_channel",
        {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframe": "1h",
            "child_params": {},
        },
    )
    assert isinstance(m, FRAMAMultiChannelPullbackStrategy)
    assert m.symbols == ["BTCUSDT", "ETHUSDT"]


# ---------- 2. signal bar arms pending; no same-bar entry -------------------


def test_signal_bar_does_not_emit_entry_even_if_low_at_mid() -> None:
    """Bar 0: break_up with a wide range — low (99.5) reaches BELOW mid
    (would normally trigger a pullback entry). The strategy must NOT enter
    on the signal bar itself, only register the pending.
    """
    bars = _bars_from_hlc([(103.0, 99.5, 102.0)])
    ind = _ind(1, break_up_at=[0])
    s = FRAMAChannelPullbackStrategy()
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents == [[]]
    pending = s._get_pending("BTCUSDT")
    assert pending is not None
    assert pending.direction == "long"
    assert pending.sl_price == Decimal("99.5")


# ---------- 3. pullback entry (long) ----------------------------------------


def test_long_entry_on_pullback_to_mid_after_break_up() -> None:
    """signal candle has wide low (99.5 < mid 100) so SL is *below* mid and
    a later pullback to mid does not pre-touch SL.
    """
    bars = _bars_from_hlc(
        [
            (103.0, 99.5, 102.5),   # signal: signal_low=99.5 (below mid)
            (102.0, 100.0, 101.0),  # pullback bar low touches mid
        ]
    )
    ind = _ind(2, break_up_at=[0])
    s = FRAMAChannelPullbackStrategy(
        margin_pct=Decimal("0.03"),
        leverage=Decimal("3"),
    )
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents[0] == []
    assert len(intents[1]) == 1
    intent = intents[1][0]
    assert intent.side == "buy"
    assert intent.type == "market"
    assert isinstance(intent.size_spec, TargetMarginPct)
    assert intent.bracket == BracketSpec(stop_loss_price=Decimal("99.5"))
    assert intent.reason == "frama_pullback_long_entry"
    assert s._get_pending("BTCUSDT") is None


def test_long_no_entry_when_low_above_mid() -> None:
    """Pullback bar low (101) stays above mid (100); no entry. signal_low
    must be valid (below mid) so the SL pre-touch rule doesn't fire instead.
    """
    bars = _bars_from_hlc(
        [
            (103.0, 99.5, 102.5),
            (102.5, 101.0, 102.0),  # low > mid → no trigger
        ]
    )
    ind = _ind(2, break_up_at=[0])
    intents = _run_bars(FRAMAChannelPullbackStrategy(), bars_df=bars, ind_df=ind)
    assert intents == [[], []]


# ---------- 4. pullback entry (short) ---------------------------------------


def test_short_entry_on_pullback_to_mid_after_break_dn() -> None:
    """Mirror of long fixture: signal_high (100.5) lies above mid (100) so SL
    is above mid and a later pullback up to mid does not pre-touch SL.
    """
    bars = _bars_from_hlc(
        [
            (100.5, 97.0, 97.5),   # signal: signal_high=100.5 (above mid)
            (100.0, 98.5, 99.0),   # pullback up: high == mid 100
        ]
    )
    ind = _ind(2, break_dn_at=[0])
    s = FRAMAChannelPullbackStrategy(
        margin_pct=Decimal("0.03"),
        leverage=Decimal("3"),
    )
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents[0] == []
    assert len(intents[1]) == 1
    intent = intents[1][0]
    assert intent.side == "sell"
    assert intent.bracket == BracketSpec(stop_loss_price=Decimal("100.5"))
    assert intent.reason == "frama_pullback_short_entry"
    assert s._get_pending("BTCUSDT") is None


# ---------- 5. SL pre-touch invalidation ------------------------------------


def test_sl_pre_touch_invalidates_long_pending() -> None:
    """signal_low (99.5) below mid. Bar 1 dips to 99 (below signal_low).
    Even though that low ALSO crosses mid, SL pre-touch fires first and
    drops the pending — no entry.
    """
    bars = _bars_from_hlc(
        [
            (103.0, 99.5, 102.5),   # signal_low = 99.5
            (101.0, 99.0, 100.0),   # low (99) < signal_low (99.5) → invalidate
        ]
    )
    ind = _ind(2, break_up_at=[0])
    s = FRAMAChannelPullbackStrategy()
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents == [[], []]
    assert s._get_pending("BTCUSDT") is None


def test_sl_pre_touch_invalidates_short_pending() -> None:
    bars = _bars_from_hlc(
        [
            (100.5, 97.0, 97.5),    # signal_high = 100.5
            (101.0, 100.0, 100.5),  # high (101) > signal_high (100.5) → invalidate
        ]
    )
    ind = _ind(2, break_dn_at=[0])
    s = FRAMAChannelPullbackStrategy()
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents == [[], []]
    assert s._get_pending("BTCUSDT") is None


# ---------- 6. same-direction new signal refreshes pending ------------------


def test_same_direction_break_up_refreshes_sl_and_signal_ts() -> None:
    """Two consecutive break_up signals — second should overwrite SL with
    the newer signal candle's low (and refresh signal_ts so same-bar entry
    block applies to the newer signal).
    """
    bars = _bars_from_hlc(
        [
            (103.0, 102.0, 102.5),  # signal #1: low 102
            (104.0, 102.5, 103.5),  # signal #2: low 102.5
        ]
    )
    ind = _ind(2, break_up_at=[0, 1])
    s = FRAMAChannelPullbackStrategy()
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents == [[], []]
    pending = s._get_pending("BTCUSDT")
    assert pending is not None
    assert pending.sl_price == Decimal("102.5")
    # signal_ts updated to bar 1.
    expected_ts = bars["timestamp"][1]
    assert pending.signal_ts == expected_ts


# ---------- 7. opposite signal overwrites pending direction -----------------


def test_break_dn_overwrites_long_pending() -> None:
    """break_up arms long. Next bar fires break_dn → pending direction
    flips to short with new SL = that bar's high. The interim long pending
    is cleared either by SL invalidation or opposite-signal preempt
    (whichever fires first); end state is the same.
    """
    bars = _bars_from_hlc(
        [
            (103.0, 99.5, 102.5),  # break_up: valid signal_low (99.5 < mid)
            (101.0, 99.7, 100.0),  # break_dn: opposite preempt drops long;
                                   # signal_high = 101 → new short pending
        ]
    )
    ind = _ind(2, break_up_at=[0], break_dn_at=[1])
    s = FRAMAChannelPullbackStrategy()
    _run_bars(s, bars_df=bars, ind_df=ind)
    pending = s._get_pending("BTCUSDT")
    assert pending is not None
    assert pending.direction == "short"
    assert pending.sl_price == Decimal("101.0")


def test_opposite_signal_preempts_pullback_trigger() -> None:
    """Same-bar pullback condition + opposite FRAMA signal: the opposite
    signal must win — no entry emitted, pending flips to opposite direction.
    Without the preempt step, the long pullback would trigger entry first
    and only THEN register the short pending.
    """
    bars = _bars_from_hlc(
        [
            (103.0, 99.5, 102.5),   # break_up: valid signal_low
            (101.0, 100.0, 100.5),  # bar.low at mid (would normally trigger
                                    # long entry) AND break_dn → preempt.
                                    # signal_high = 101 for new short pending.
        ]
    )
    ind = _ind(2, break_up_at=[0], break_dn_at=[1])
    s = FRAMAChannelPullbackStrategy()
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents == [[], []]
    pending = s._get_pending("BTCUSDT")
    assert pending is not None
    assert pending.direction == "short"
    assert pending.sl_price == Decimal("101.0")


# ---------- 8. exit on opposite signal while in position --------------------


def test_long_position_exits_on_break_dn_AND_arms_short_pending() -> None:
    """Regression for the swing-flip bug: when long + break_dn fires, we
    must (a) emit a ClosePosition reduce_only intent and (b) immediately
    arm a short pending using THIS bar's high. Otherwise the swing-reversal
    setup announced by the very same signal is lost.
    """
    bars = _bars_from_hlc([(101.0, 99.5, 100.5)])
    ind = _ind(1, break_dn_at=[0])
    s = FRAMAChannelPullbackStrategy()
    ctx = _ctx_from_indicator_df(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        position_size=Decimal("1"),
    )
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert isinstance(intents[0].size_spec, ClosePosition)
    assert intents[0].reduce_only is True
    assert intents[0].reason == "frama_pullback_exit_long_break_dn"
    pending = s._get_pending("BTCUSDT")
    assert pending is not None
    assert pending.direction == "short"
    assert pending.sl_price == Decimal("101.0")
    assert pending.signal_ts == bars["timestamp"][0]


def test_short_position_exits_on_break_up_AND_arms_long_pending() -> None:
    """Mirror of the long-flip test."""
    bars = _bars_from_hlc([(101.0, 99.0, 100.5)])
    ind = _ind(1, break_up_at=[0])
    s = FRAMAChannelPullbackStrategy()
    ctx = _ctx_from_indicator_df(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        position_size=Decimal("-1"),
    )
    intents = s.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert isinstance(intents[0].size_spec, ClosePosition)
    pending = s._get_pending("BTCUSDT")
    assert pending is not None
    assert pending.direction == "long"
    assert pending.sl_price == Decimal("99.0")
    assert pending.signal_ts == bars["timestamp"][0]


def test_entry_bar_does_not_register_new_pending_for_same_direction_signal() -> None:
    """Regression for the stale-pending bug: when bar N triggers pullback
    entry from a prior pending AND that same bar also fires a fresh
    same-direction break, we must NOT register a new pending. The old code
    did, which left a stale setup that would fire after the position later
    closed naturally.
    """
    bars = _bars_from_hlc(
        [
            (103.0, 99.5, 102.5),   # break_up #1: arm long pending sl=99.5
            (104.0, 100.0, 103.5),  # bar.low at mid → entry trigger,
                                    # AND break_up #2 fires same bar
        ]
    )
    ind = _ind(2, break_up_at=[0, 1])
    s = FRAMAChannelPullbackStrategy()
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents[0] == []
    assert len(intents[1]) == 1
    assert intents[1][0].side == "buy"
    # CRITICAL: pending must be None after entry, not re-registered from
    # the fresh same-bar break_up signal.
    assert s._get_pending("BTCUSDT") is None


def test_lifecycle_no_stale_pending_after_natural_exit() -> None:
    """Walks: arm → entry → in-position → opposite-signal exit → flat.
    Drives the strategy directly, toggling has_position on the bar after
    the entry intent and back to False after the close intent — exercises
    the full swing-flip lifecycle that ``_run_bars`` (fixed has_position)
    cannot represent.

    Regression target: after the natural exit, the only pending in flight
    should be the fresh short pending armed by the exit signal — NOT a
    stale long pending re-registered on the entry bar.
    """
    bars = _bars_from_hlc(
        [
            (103.0, 99.5, 102.5),   # bar 0: break_up #1 — arm long
            (104.0, 100.0, 103.5),  # bar 1: pullback + break_up #2 — entry
            (104.5, 102.0, 103.0),  # bar 2: position open, no signal
            (102.0, 99.0, 99.5),    # bar 3: position open, break_dn → exit + arm short
        ]
    )
    ind = _ind(4, break_up_at=[0, 1], break_dn_at=[3])
    s = FRAMAChannelPullbackStrategy()
    has_pos = False
    pos_size = Decimal("0")

    intents_per_bar: list[list[OrderIntent]] = []
    for i in range(bars.height):
        ctx = _ctx_from_indicator_df(
            bars_df=bars.head(i + 1),
            ind_df=ind.head(i + 1),
            has_position=has_pos,
            position_size=pos_size,
        )
        out = s.on_bar(ctx)
        intents_per_bar.append(out)
        # Mimic engine fill timing: market intent on bar N fills on bar
        # N+1 open, so position state flips for the NEXT iteration.
        for it in out:
            if isinstance(it.size_spec, ClosePosition):
                has_pos = False
                pos_size = Decimal("0")
            elif isinstance(it.size_spec, TargetMarginPct):
                has_pos = True
                pos_size = Decimal("1") if it.side == "buy" else Decimal("-1")

    # bar 0: arm only — no intent
    assert intents_per_bar[0] == []
    # bar 1: long entry intent
    assert len(intents_per_bar[1]) == 1
    assert intents_per_bar[1][0].side == "buy"
    # bar 2: position open, no signal → no intent
    assert intents_per_bar[2] == []
    # bar 3: opposite signal → close + arm short
    assert len(intents_per_bar[3]) == 1
    assert intents_per_bar[3][0].side == "sell"
    assert isinstance(intents_per_bar[3][0].size_spec, ClosePosition)
    pending = s._get_pending("BTCUSDT")
    assert pending is not None
    assert pending.direction == "short"
    assert pending.sl_price == Decimal("102.0")  # bar 3's high
    assert pending.signal_ts == bars["timestamp"][3]


def test_in_position_no_break_means_no_intent() -> None:
    bars = _bars_from_hlc([(101.0, 99.5, 100.5)])
    ind = _ind(1)
    s = FRAMAChannelPullbackStrategy()
    ctx = _ctx_from_indicator_df(
        bars_df=bars,
        ind_df=ind,
        has_position=True,
        position_size=Decimal("1"),
    )
    assert s.on_bar(ctx) == []


# ---------- 9. allow_short=False --------------------------------------------


def test_allow_short_false_consumes_pending_without_entry() -> None:
    """short setup completes the pullback condition but ``allow_short=False``
    suppresses the entry; the pending must still be cleared so it doesn't
    re-trigger every subsequent pullback bar. Uses a valid signal_high
    (above mid) so SL pre-touch doesn't fire instead.
    """
    bars = _bars_from_hlc(
        [
            (100.5, 97.0, 97.5),   # break_dn, signal_high=100.5 (above mid)
            (100.0, 98.5, 99.0),   # pullback up to mid (high == 100)
        ]
    )
    ind = _ind(2, break_dn_at=[0])
    s = FRAMAChannelPullbackStrategy(allow_short=False)
    intents = _run_bars(s, bars_df=bars, ind_df=ind)
    assert intents == [[], []]
    assert s._get_pending("BTCUSDT") is None


# ---------- 10. multi wrapper -----------------------------------------------


def test_multi_shares_indicator_across_children() -> None:
    m = FRAMAMultiChannelPullbackStrategy(
        symbols=["BTCUSDT", "ETHUSDT", "AVAXUSDT"],
        timeframe="1h",
        child_params={"length": 26, "distance": "1.5"},
    )
    assert len(m.required_indicators()) == 1
    for sym in m.symbols:
        assert m._children[sym]._frama is m._frama


def test_multi_empty_symbols_raises() -> None:
    with pytest.raises(ConfigError, match="non-empty 'symbols'"):
        FRAMAMultiChannelPullbackStrategy(symbols=[])


def test_multi_duplicate_symbols_raises() -> None:
    with pytest.raises(ConfigError, match="duplicates"):
        FRAMAMultiChannelPullbackStrategy(symbols=["BTCUSDT", "BTCUSDT"])


def test_multi_bad_child_params_raises() -> None:
    with pytest.raises(ConfigError, match="signature"):
        FRAMAMultiChannelPullbackStrategy(
            symbols=["BTCUSDT"],
            child_params={"unknown_kwarg": 1},
        )


def test_multi_pending_isolated_per_symbol() -> None:
    """BTC pending should not affect ETH pending — children own their own
    state and the wrapper swaps ``primary_symbol`` on every dispatch.
    """
    m = FRAMAMultiChannelPullbackStrategy(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1h",
        child_params={},
    )
    # Manually set BTC child pending and confirm ETH child sees nothing.
    m._children["BTCUSDT"]._set_pending(
        "BTCUSDT",
        direction="long",
        sl_price=Decimal("99"),
        signal_ts=datetime(2026, 3, 1, tzinfo=UTC),
    )
    assert m._children["BTCUSDT"]._get_pending("BTCUSDT") is not None
    assert m._children["ETHUSDT"]._get_pending("ETHUSDT") is None
    assert m._children["ETHUSDT"]._get_pending("BTCUSDT") is None


# ---------- 11. validation --------------------------------------------------


def test_margin_pct_not_positive_raises() -> None:
    with pytest.raises(ValueError, match="margin_pct"):
        FRAMAChannelPullbackStrategy(margin_pct=Decimal("0"))


def test_leverage_not_positive_raises() -> None:
    with pytest.raises(ValueError, match="leverage"):
        FRAMAChannelPullbackStrategy(leverage=Decimal("0"))
