"""Phase 3 — multi-leg bracket engine integration.

Drives a one-shot test strategy that emits a single ``MultiBracketSpec``
entry, then runs the engine over hand-shaped OHLCV bars to exercise the
spawn / TP-fill / SL-resize / SL-cancel paths.

Coverage:

1. Spawn — entry fill spawns N TP legs + 1 SL stop with shared
   ``bracket_group_id``, distinct roles + tp_leg_index, sizes per fraction.
2. TP1 fill (later bar) → SL ``sized_quantity`` shrunk by leg size,
   TP2/TP3 still active, ``ORDER_RESIZED`` event payload correct.
3. SL fill (no TPs yet) → all TP legs cancelled with reason
   ``bracket_sl_filled``.
4. All TPs fill across bars → SL cancelled with reason
   ``bracket_position_closed``.
5. Same-bar PESSIMISTIC: entry bar reaches SL + every TP → SL fills first
   (BarPathModel ordering), TPs cancelled.
6. Same-bar OPTIMISTIC: entry bar reaches every TP and SL → only TP1 fills
   this bar (E1 one-TP-per-bar rule); TP2 / TP3 / resized SL stay active.
7. Partial sum (0.3+0.3+0.3 = 0.9): all TPs fill → SL stays active with
   ``remaining`` = parent_qty * 0.1 (not cancelled).
8. Side-aware ordering: long with descending TP prices (and short with
   ascending) is rejected at intent processing time via ORDER_REJECTED
   so the position never opens — the engine refuses to fill an entry
   whose bracket spec would violate the side-aware TP order invariant.
9. Regression: existing single-TP ``BracketSpec`` path still works (one
   ORDER_RESIZED test).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import (
    BracketSpec,
    MultiBracketSpec,
    OrderIntent,
    TakeProfitLeg,
    TargetUnits,
)
from backtester.core.types import BarPathModel
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


# ---------- helpers ---------------------------------------------------------


class _OneShotEntryStrategy(BaseStrategy):
    """Emit one entry intent on the first ``on_bar`` call. No indicators."""

    def __init__(
        self,
        *,
        symbol: str,
        size: Decimal,
        bracket: BracketSpec | MultiBracketSpec,
        side: Literal["buy", "sell"] = "buy",
    ) -> None:
        self.symbol = symbol
        self.size = size
        self.bracket = bracket
        self.side = side
        self._fired = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._fired:
            return []
        self._fired = True
        return [
            OrderIntent(
                symbol=self.symbol,
                side=self.side,
                type="market",
                size_spec=TargetUnits(units=self.size),
                bracket=self.bracket,
                reason="test_entry",
            )
        ]


def _instrument(symbol: str = "BTCUSDT") -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _write_bars(target: Path, bars: list[dict[str, float]]) -> None:
    """Write hourly OHLCV bars to a Parquet file. ``bars[i]`` provides
    open/high/low/close/volume for hour ``i`` from a fixed base timestamp."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    for i, b in enumerate(bars):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b.get("volume", 1.0),
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _run(
    tmp_path: Path,
    bars: list[dict[str, float]],
    bracket: BracketSpec | MultiBracketSpec,
    *,
    side: Literal["buy", "sell"] = "buy",
    bar_path_model: BarPathModel = BarPathModel.PESSIMISTIC,
    size: Decimal = Decimal("1"),
) -> Path:
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    _write_bars(data_dir / f"{sym}_1h.parquet", bars)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=len(bars) + 1)
    cfg = BacktestConfig(
        run_id="multibracket_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(sym)],
        timeframes_per_symbol={sym: ["1h"]},
        primary_symbol=sym,
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
        bar_path_model=bar_path_model,
        # Short open via Sizer requires allow_short=True; tests that send a
        # ``side="sell"`` entry would otherwise be rejected before reaching
        # the bracket-spawn validation path.
        allow_short=(side == "sell"),
    )
    strategy = _OneShotEntryStrategy(
        symbol=sym, size=size, bracket=bracket, side=side
    )
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    return result.events_path


def _events(events_path: Path, ev_type: EventType) -> list[Any]:
    return list(EventLogReader(events_path).by_type(ev_type))


def _flat_bar(price: float) -> dict[str, float]:
    return {
        "open": price,
        "high": price + 0.05,
        "low": price - 0.05,
        "close": price,
    }


# ---------- 1. Spawn structure ---------------------------------------------


def test_spawn_creates_n_tp_legs_plus_one_sl_with_shared_group(
    tmp_path: Path,
) -> None:
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(
                price=Decimal("110"),
                size_fraction=Decimal("0.3333"),
                label="tp1",
            ),
            TakeProfitLeg(
                price=Decimal("120"),
                size_fraction=Decimal("0.3333"),
                label="tp2",
            ),
            TakeProfitLeg(
                price=Decimal("130"),
                size_fraction=Decimal("0.3334"),
                label="tp3",
            ),
        ),
        stop_loss_price=Decimal("95"),
    )
    # 3 bars: warmup → entry-bar (no TP/SL hit) → flat.
    bars = [_flat_bar(100.0), _flat_bar(100.0), _flat_bar(100.0)]
    events_path = _run(tmp_path, bars, bracket)

    added = _events(events_path, EventType.ORDER_ADDED)
    # 1 entry + 3 TP legs + 1 SL = 5
    assert len(added) == 5
    bracket_children = [
        e for e in added if e.payload.get("bracket_group_id") is not None
    ]
    assert len(bracket_children) == 4
    gids = {e.payload["bracket_group_id"] for e in bracket_children}
    assert len(gids) == 1, f"expected one shared bracket_group_id, got {gids}"
    roles = [e.payload["bracket_role"] for e in bracket_children]
    assert roles.count("tp_leg") == 3
    assert roles.count("protector_sl") == 1
    # leg indices preserved
    leg_idxs = sorted(
        e.payload["tp_leg_index"]
        for e in bracket_children
        if e.payload["bracket_role"] == "tp_leg"
    )
    assert leg_idxs == [0, 1, 2]


# ---------- 2. TP fill resizes SL ------------------------------------------


def test_tp_fill_resizes_sl_and_emits_order_resized(tmp_path: Path) -> None:
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("120"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.3334")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),  # warmup
        # bar 1: entry fills at open=100, range 99-101 (no TP/SL hit)
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        # bar 2: hits TP1=110 (high=111), no SL touch
        {"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},
    ]
    events_path = _run(tmp_path, bars, bracket)

    resized = _events(events_path, EventType.ORDER_RESIZED)
    assert len(resized) == 1, "TP1 fill should emit exactly one ORDER_RESIZED"
    payload = resized[0].payload
    assert payload["reason"] == "tp_leg_filled"
    # SL was sized at 1.0, TP1 took 0.3333 → new sized = 0.6667
    old = Decimal(str(payload["old_sized_quantity"]))
    new = Decimal(str(payload["new_sized_quantity"]))
    assert old == Decimal("1")
    assert new == Decimal("1") - Decimal("0.3333")
    # Other TPs and resized SL stay active.
    cancelled = _events(events_path, EventType.ORDER_CANCELLED)
    assert all(
        c.payload.get("reason") != "bracket_sl_filled" for c in cancelled
    )


# ---------- 3. SL fill cancels remaining TPs --------------------------------


def test_sl_fill_cancels_all_remaining_tp_legs(tmp_path: Path) -> None:
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("120"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.3334")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),  # warmup
        # bar 1: entry fills (no SL hit on entry bar — open=100, range 99-101)
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        # bar 2: SL=95 hit (low=94)
        {"open": 99.0, "high": 100.0, "low": 94.0, "close": 95.0},
    ]
    events_path = _run(tmp_path, bars, bracket)

    cancelled = _events(events_path, EventType.ORDER_CANCELLED)
    bracket_cancels = [
        c for c in cancelled if c.payload.get("reason") == "bracket_sl_filled"
    ]
    assert len(bracket_cancels) == 3, (
        f"expected 3 TP cancels on SL fill, got {len(bracket_cancels)}"
    )


# ---------- 4. All TPs fill cancels SL --------------------------------------


def test_all_tps_fill_cancels_sl(tmp_path: Path) -> None:
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("120"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.3334")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),  # warmup
        # bar 1: entry, no TP/SL
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        # bar 2: TP1=110 hit
        {"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},
        # bar 3: TP2=120 hit
        {"open": 110.0, "high": 121.0, "low": 109.0, "close": 120.0},
        # bar 4: TP3=130 hit
        {"open": 120.0, "high": 131.0, "low": 119.0, "close": 130.0},
    ]
    events_path = _run(tmp_path, bars, bracket)

    resized = _events(events_path, EventType.ORDER_RESIZED)
    # TP1 and TP2 emit RESIZED, TP3 fills the remainder → SL cancelled instead
    # of resized (resize cannot reach 0 by invariant). Two RESIZED events.
    assert len(resized) == 2

    cancelled = _events(events_path, EventType.ORDER_CANCELLED)
    pos_closed = [
        c
        for c in cancelled
        if c.payload.get("reason") == "bracket_position_closed"
    ]
    assert len(pos_closed) == 1, (
        f"expected 1 SL cancel after all TPs filled, got {len(pos_closed)}"
    )


# ---------- 5. Same-bar PESSIMISTIC: SL wins -------------------------------


def test_same_bar_pessimistic_sl_wins_over_tps(tmp_path: Path) -> None:
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("120"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.3334")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),  # warmup
        # bar 1 (entry bar): entry fills at open=100, range 80-130 covers SL + every TP
        {"open": 100.0, "high": 130.0, "low": 80.0, "close": 100.0},
    ]
    events_path = _run(
        tmp_path, bars, bracket, bar_path_model=BarPathModel.PESSIMISTIC
    )

    fills = _events(events_path, EventType.FILL)
    # Entry + SL only.
    fill_reasons = [f.payload["intent_reason"] for f in fills]
    assert any(r == "test_entry" for r in fill_reasons)
    assert any(r.startswith("bracket_sl") for r in fill_reasons)
    assert not any("bracket_tp" in r for r in fill_reasons), (
        "PESSIMISTIC should not fill any TP when SL is also reachable"
    )
    cancelled = _events(events_path, EventType.ORDER_CANCELLED)
    sl_cancels = [
        c for c in cancelled if c.payload.get("reason") == "bracket_sl_filled"
    ]
    assert len(sl_cancels) == 3


# ---------- 6. Same-bar OPTIMISTIC: only one TP fills ----------------------


def test_same_bar_optimistic_only_first_tp_fills(tmp_path: Path) -> None:
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("120"), size_fraction=Decimal("0.3333")),
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.3334")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),  # warmup
        # bar 1 (entry bar): every TP and SL reached
        {"open": 100.0, "high": 130.0, "low": 80.0, "close": 100.0},
    ]
    events_path = _run(
        tmp_path, bars, bracket, bar_path_model=BarPathModel.OPTIMISTIC
    )

    fills = _events(events_path, EventType.FILL)
    # Entry + exactly one TP (the closest).
    tp_fills = [
        f for f in fills if "bracket_tp" in f.payload["intent_reason"]
    ]
    sl_fills = [
        f for f in fills if "bracket_sl" in f.payload["intent_reason"]
    ]
    assert len(tp_fills) == 1, (
        f"OPTIMISTIC E1 should fill one TP per bar, got {len(tp_fills)}"
    )
    assert len(sl_fills) == 0, (
        "OPTIMISTIC: SL must not fill while TPs were reachable"
    )
    # ORDER_RESIZED emitted on TP fill.
    resized = _events(events_path, EventType.ORDER_RESIZED)
    assert len(resized) == 1


# ---------- 7. Partial-sum bracket leaves SL active ------------------------


def test_partial_sum_bracket_leaves_sl_active_after_all_tps(
    tmp_path: Path,
) -> None:
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.3")),
            TakeProfitLeg(price=Decimal("120"), size_fraction=Decimal("0.3")),
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.3")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},  # TP1
        {"open": 110.0, "high": 121.0, "low": 109.0, "close": 120.0},  # TP2
        {"open": 120.0, "high": 131.0, "low": 119.0, "close": 130.0},  # TP3
    ]
    events_path = _run(tmp_path, bars, bracket)

    cancelled = _events(events_path, EventType.ORDER_CANCELLED)
    pos_closed = [
        c
        for c in cancelled
        if c.payload.get("reason") == "bracket_position_closed"
    ]
    assert len(pos_closed) == 0, (
        "SL should NOT be cancelled while size_fraction sum < 1"
    )
    # 3 TP fills → 3 ORDER_RESIZED.
    resized = _events(events_path, EventType.ORDER_RESIZED)
    assert len(resized) == 3
    # Final remaining = parent_qty * 0.1
    last_payload = resized[-1].payload
    new_sized = Decimal(str(last_payload["new_sized_quantity"]))
    assert abs(new_sized - Decimal("0.1")) < Decimal("0.0001")


# ---------- 8. Side-aware ordering invariant -------------------------------


def test_long_with_descending_tp_prices_is_rejected(tmp_path: Path) -> None:
    """Long entry with TP prices sorted descending must be rejected at
    intent processing time so the entry never fills — TP1 should be the
    closest above entry, not the farthest."""
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.5")),
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.5")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
    ]
    events_path = _run(tmp_path, bars, bracket, side="buy")

    rejected = _events(events_path, EventType.ORDER_REJECTED)
    assert len(rejected) == 1
    reason = rejected[0].payload["reason"]
    assert "bracket" in reason and "ascending" in reason
    # No fills, no order_added (entry never opened).
    fills = _events(events_path, EventType.FILL)
    assert fills == []
    added = _events(events_path, EventType.ORDER_ADDED)
    assert added == []


def test_short_with_ascending_tp_prices_is_rejected(tmp_path: Path) -> None:
    """Short entry: TP prices must be descending so TP1 is closest below
    entry. Bad spec is rejected via ORDER_REJECTED, no entry fills."""
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("85"), size_fraction=Decimal("0.5")),
            TakeProfitLeg(price=Decimal("90"), size_fraction=Decimal("0.5")),
        ),
        stop_loss_price=Decimal("105"),
    )
    bars = [
        _flat_bar(100.0),
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
    ]
    events_path = _run(tmp_path, bars, bracket, side="sell")

    rejected = _events(events_path, EventType.ORDER_REJECTED)
    assert len(rejected) == 1
    reason = rejected[0].payload["reason"]
    assert "bracket" in reason and "descending" in reason
    fills = _events(events_path, EventType.FILL)
    assert fills == []


# ---------- 9. Regression — single-TP BracketSpec still works ----------------


def test_single_bracket_spec_path_still_works(tmp_path: Path) -> None:
    bracket = BracketSpec(
        take_profit_price=Decimal("110"),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        _flat_bar(100.0),
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},  # TP hit
    ]
    events_path = _run(tmp_path, bars, bracket)

    # Old OCO path — no ORDER_RESIZED emitted.
    resized = _events(events_path, EventType.ORDER_RESIZED)
    assert len(resized) == 0
    # TP fill cancels SL via OCO sibling path.
    cancelled = _events(events_path, EventType.ORDER_CANCELLED)
    oco_cancels = [
        c for c in cancelled if c.payload.get("reason") == "oco_sibling_filled"
    ]
    assert len(oco_cancels) == 1
