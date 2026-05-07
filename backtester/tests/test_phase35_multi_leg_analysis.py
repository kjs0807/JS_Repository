"""Phase 3.5 — analysis / viz multi-leg awareness.

Drives a one-shot ``MultiBracketSpec`` entry through a real backtest run
and verifies that downstream readers (``analysis.export``, ``viz.trade_review``)
surface the multi-leg structure correctly.

Coverage:

1. ``intents.csv`` carries ``bracket_kind="multi"`` + ``tp_legs_n`` +
   ``tp_legs_prices``; closest TP price is in ``tp_price``.
2. ``orders.csv`` populates ``bracket_group_id`` / ``bracket_role`` /
   ``tp_leg_index`` for spawned children, and includes ``ORDER_RESIZED``
   rows with old→new resize info in the ``reason`` cell.
3. ``identify_trades`` annotates the closed multi-leg trade with
   ``bracket_kind="multi"``, ``exit_legs=["tp1","tp2","tp3"]``,
   and a volume-weighted exit price = (tp1+tp2+tp3)/3 for the SATS-
   style 1/3 split. ``realized_pnl_pct`` uses that weighted price, not
   the legacy last-fill ``exit_price``.
4. Regression: single ``BracketSpec`` runs still produce ``bracket_kind
   ="single"`` and ``weighted_exit_price`` equal to the single exit
   fill's price.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from backtester.analysis.export import export_run_data
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.orders import (
    BracketSpec,
    MultiBracketSpec,
    OrderIntent,
    TakeProfitLeg,
    TargetUnits,
)
from backtester.events.reader import EventLogReader
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy
from backtester.viz.trade_review import identify_trades

UTC = timezone.utc


# ---------- helpers ---------------------------------------------------------


class _OneShotEntryStrategy(BaseStrategy):
    """Emit one entry intent on the first ``on_bar`` call."""

    def __init__(
        self,
        *,
        symbol: str,
        size: Decimal,
        bracket: BracketSpec | MultiBracketSpec,
    ) -> None:
        self.symbol = symbol
        self.size = size
        self.bracket = bracket
        self._fired = False

    def on_bar(self, ctx: Any) -> list[OrderIntent]:
        if self._fired:
            return []
        self._fired = True
        return [
            OrderIntent(
                symbol=self.symbol,
                side="buy",
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


def _run_engine(
    tmp_path: Path,
    bars: list[dict[str, float]],
    bracket: BracketSpec | MultiBracketSpec,
    *,
    size: Decimal = Decimal("3"),  # divisible by 3 for clean 1/3 splits
) -> Path:
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    _write_bars(data_dir / f"{sym}_1h.parquet", bars)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=len(bars) + 1)
    cfg = BacktestConfig(
        run_id="phase35_test",
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
    )
    strategy = _OneShotEntryStrategy(symbol=sym, size=size, bracket=bracket)
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    return Path(result.run_dir)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        return list(reader)


def _multi_bracket_3legs() -> MultiBracketSpec:
    return MultiBracketSpec(
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


def _bars_all_tps_hit() -> list[dict[str, float]]:
    """Warmup → entry @100 → TP1 hit → TP2 hit → TP3 hit (one per bar)."""
    return [
        {"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0},  # warmup
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},   # entry, no TP/SL
        {"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},   # TP1
        {"open": 110.0, "high": 121.0, "low": 109.0, "close": 120.0},  # TP2
        {"open": 120.0, "high": 131.0, "low": 119.0, "close": 130.0},  # TP3
    ]


def _bars_single_tp_hit() -> list[dict[str, float]]:
    return [
        {"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},  # TP hit
    ]


# ---------- 1. intents.csv multi-bracket ------------------------------------


def test_export_intents_records_multi_bracket_columns(tmp_path: Path) -> None:
    bracket = _multi_bracket_3legs()
    bars = _bars_all_tps_hit()
    run_dir = _run_engine(tmp_path, bars, bracket)
    paths = export_run_data(run_dir)
    rows = _read_csv(paths["intents"])
    # Exactly one INTENT_CREATED for our entry.
    entries = [r for r in rows if r["reason"] == "test_entry"]
    assert len(entries) == 1
    e = entries[0]
    assert e["bracket_kind"] == "multi"
    assert e["tp_legs_n"] == "3"
    # Closest TP first → tp_price column gets the closest leg.
    assert e["tp_price"] == "110"
    assert e["sl_price"] == "95"
    # tp_legs_prices is semicolon-joined, in spec order.
    assert e["tp_legs_prices"] == "110;120;130"


# ---------- 2. orders.csv populates bracket_* + ORDER_RESIZED ---------------


def test_export_orders_includes_bracket_metadata_and_resizes(
    tmp_path: Path,
) -> None:
    bracket = _multi_bracket_3legs()
    bars = _bars_all_tps_hit()
    run_dir = _run_engine(tmp_path, bars, bracket)
    paths = export_run_data(run_dir)
    rows = _read_csv(paths["orders"])

    added = [r for r in rows if r["event_type"] == "order_added"]
    # 1 entry + 3 TP legs + 1 SL = 5 ORDER_ADDED.
    assert len(added) == 5
    tp_legs = [r for r in added if r["bracket_role"] == "tp_leg"]
    sl_rows = [r for r in added if r["bracket_role"] == "protector_sl"]
    assert len(tp_legs) == 3
    assert len(sl_rows) == 1
    # Leg indices preserved 0/1/2.
    assert sorted(int(r["tp_leg_index"]) for r in tp_legs) == [0, 1, 2]
    # Shared bracket_group_id across all 4 children.
    gids = {r["bracket_group_id"] for r in (*tp_legs, *sl_rows)}
    assert len(gids) == 1 and next(iter(gids)) != ""

    # ORDER_RESIZED — TP1/TP2 cause resize; TP3 cancels (full split).
    resized = [r for r in rows if r["event_type"] == "order_resized"]
    assert len(resized) == 2
    for r in resized:
        assert r["bracket_role"] == "protector_sl"
        # reason carries old->new info for analysts.
        assert "->" in r["reason"]
        assert "tp_leg_filled" in r["reason"]
        # parent_order_id repurposed as trigger TP leg's id.
        assert r["parent_order_id"] != ""

    # Final SL cancelled with bracket_position_closed reason.
    cancels = [
        r
        for r in rows
        if r["event_type"] == "order_cancelled"
        and "bracket_position_closed" in r["reason"]
    ]
    assert len(cancels) == 1


# ---------- 3. identify_trades multi-leg annotation -------------------------


def test_identify_trades_annotates_multi_leg(tmp_path: Path) -> None:
    bracket = _multi_bracket_3legs()
    bars = _bars_all_tps_hit()
    run_dir = _run_engine(tmp_path, bars, bracket)

    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "long"
    assert t.open is False
    assert t.bracket_kind == "multi"
    # Three TP legs in spec order.
    assert t.exit_legs == ["tp1", "tp2", "tp3"]
    # Volume-weighted: (110*1 + 120*1 + 130*1) / 3 = 120.0
    expected = Decimal("120")
    assert t.weighted_exit_price is not None
    assert abs(t.weighted_exit_price - expected) < Decimal("0.5")
    # realized_pnl_pct uses weighted price, not the last fill (tp3=130).
    pnl = t.realized_pnl_pct
    assert pnl is not None
    legacy = (Decimal("130") - Decimal("100")) / Decimal("100")  # 0.30
    weighted = (expected - Decimal("100")) / Decimal("100")  # 0.20
    assert abs(pnl - weighted) < Decimal("0.005")
    assert abs(pnl - legacy) > Decimal("0.05"), (
        "weighted PnL must differ from legacy last-fill PnL on multi-leg"
    )


# ---------- 4. single-bracket regression ------------------------------------


def test_identify_trades_single_bracket_marks_kind_single(
    tmp_path: Path,
) -> None:
    bracket = BracketSpec(
        take_profit_price=Decimal("110"),
        stop_loss_price=Decimal("95"),
    )
    bars = _bars_single_tp_hit()
    run_dir = _run_engine(tmp_path, bars, bracket, size=Decimal("1"))

    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    assert len(trades) == 1
    t = trades[0]
    assert t.bracket_kind == "single"
    # Single TP fires once → exit_legs is one entry.
    assert t.exit_legs == ["tp"]
    # weighted = single fill price = 110.
    assert t.weighted_exit_price == Decimal("110")
    # realized_pnl_pct weighted == legacy in single-leg case.
    assert t.realized_pnl_pct is not None
    assert abs(t.realized_pnl_pct - Decimal("0.10")) < Decimal("0.005")


# ---------- 5. side direction parity for short multi-leg --------------------


def test_export_short_multi_bracket_intent_columns(tmp_path: Path) -> None:
    """Short multi-leg also needs ``bracket_kind="multi"`` plus correct
    leg ordering in tp_legs_prices."""
    sym = "BTCUSDT"
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(
                price=Decimal("90"), size_fraction=Decimal("0.5"), label="tp1"
            ),
            TakeProfitLeg(
                price=Decimal("80"), size_fraction=Decimal("0.5"), label="tp2"
            ),
        ),
        stop_loss_price=Decimal("110"),
    )

    class _ShortStrat(BaseStrategy):
        def __init__(self) -> None:
            self._fired = False

        def on_bar(self, ctx: Any) -> list[OrderIntent]:
            if self._fired:
                return []
            self._fired = True
            return [
                OrderIntent(
                    symbol=sym,
                    side="sell",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("2")),
                    bracket=bracket,
                    reason="short_entry",
                )
            ]

    data_dir = tmp_path / "data"
    bars: list[dict[str, float]] = [
        {"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 100.0, "low": 89.0, "close": 90.0},   # TP1
        {"open": 90.0, "high": 91.0, "low": 79.0, "close": 80.0},     # TP2
    ]
    _write_bars(data_dir / f"{sym}_1h.parquet", bars)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="phase35_short",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(sym)],
        timeframes_per_symbol={sym: ["1h"]},
        primary_symbol=sym,
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=len(bars) + 1),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
        allow_short=True,
    )
    result = BacktestEngine(cfg, _ShortStrat(), verbose=False).run()
    paths = export_run_data(Path(result.run_dir))
    rows = _read_csv(paths["intents"])
    e = next(r for r in rows if r["reason"] == "short_entry")
    assert e["bracket_kind"] == "multi"
    assert e["side"] == "sell"
    assert e["tp_legs_n"] == "2"
    assert e["tp_legs_prices"] == "90;80"  # spec-order, closest first


# ---------- 6. weighted_exit_price ignores entry fill -----------------------


def test_weighted_exit_price_excludes_entry_fill(tmp_path: Path) -> None:
    """A long trade's weighted_exit_price must come from the SELL fills only,
    not include the BUY entry fill in the average."""
    bracket = _multi_bracket_3legs()
    bars = _bars_all_tps_hit()
    run_dir = _run_engine(tmp_path, bars, bracket)
    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    t = trades[0]
    # If the entry (price=100, size=3) had been included, the weighted avg
    # would skew toward 100. With three exits at 110/120/130 only:
    # ~120. Confirm by checking it's strictly above 115.
    assert t.weighted_exit_price is not None
    assert t.weighted_exit_price > Decimal("115")


# ---------- 7. realized_pnl_pct on open trade falls back to exit_price ------


def test_realized_pnl_pct_open_trade_returns_none(tmp_path: Path) -> None:
    bracket = _multi_bracket_3legs()
    # Short window — entry fills, no TP/SL hit by run end → trade stays open.
    bars: list[dict[str, float]] = [
        {"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
    ]
    run_dir = _run_engine(tmp_path, bars, bracket)
    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    assert len(trades) == 1
    t = trades[0]
    assert t.open is True
    # Open trade — no exit yet, so realized_pnl_pct must be None.
    assert t.exit_price is None
    assert t.weighted_exit_price is None
    assert t.realized_pnl_pct is None


# ---------- 8. open trade with partial TP fills must not report realized PnL --


def test_realized_pnl_pct_is_none_while_partial_close_in_progress(
    tmp_path: Path,
) -> None:
    """Partial-sum bracket (0.3+0.3+0.3 = 0.9): all 3 TPs fire but 10% of the
    position stays under the SL, so the trade record stays ``open=True``.

    Previously :attr:`TradeRecord.realized_pnl_pct` returned a weighted PnL
    even on open trades, which would surface a "completed" percentage in
    the trade-review chart title for a position that hasn't actually
    closed. Now it must be ``None`` while open. The partial PnL is still
    available via :attr:`TradeRecord.partial_pnl_pct` for callers that
    want to show in-progress diagnostics.
    """
    bracket = MultiBracketSpec(
        take_profits=(
            TakeProfitLeg(price=Decimal("110"), size_fraction=Decimal("0.3")),
            TakeProfitLeg(price=Decimal("120"), size_fraction=Decimal("0.3")),
            TakeProfitLeg(price=Decimal("130"), size_fraction=Decimal("0.3")),
        ),
        stop_loss_price=Decimal("95"),
    )
    bars = [
        {"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},   # TP1
        {"open": 110.0, "high": 121.0, "low": 109.0, "close": 120.0},  # TP2
        {"open": 120.0, "high": 131.0, "low": 119.0, "close": 130.0},  # TP3
    ]
    run_dir = _run_engine(tmp_path, bars, bracket, size=Decimal("10"))
    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    assert len(trades) == 1
    t = trades[0]
    # 10 units × 0.3 each leg = 9 units exited; 1 unit left under SL →
    # trade stays open.
    assert t.open is True
    # Three TP legs fired and were captured.
    assert t.exit_legs == ["tp1", "tp2", "tp3"]
    # weighted_exit_price IS computed (so partial diagnostics work) …
    assert t.weighted_exit_price is not None
    assert abs(t.weighted_exit_price - Decimal("120")) < Decimal("0.5")
    # …but realized_pnl_pct must be None while open.
    assert t.realized_pnl_pct is None
    # partial_pnl_pct gives the in-progress weighted PnL on the closed part.
    assert t.partial_pnl_pct is not None
    expected = (Decimal("120") - Decimal("100")) / Decimal("100")  # 0.20
    assert abs(t.partial_pnl_pct - expected) < Decimal("0.01")


def test_partial_pnl_pct_equals_realized_when_finalized(
    tmp_path: Path,
) -> None:
    """For a fully closed multi-leg trade, ``partial_pnl_pct`` and
    ``realized_pnl_pct`` agree — partial is the more general accessor."""
    bracket = _multi_bracket_3legs()
    bars = _bars_all_tps_hit()
    run_dir = _run_engine(tmp_path, bars, bracket)
    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    t = trades[0]
    assert t.open is False
    assert t.realized_pnl_pct is not None
    assert t.partial_pnl_pct is not None
    assert t.realized_pnl_pct == t.partial_pnl_pct


