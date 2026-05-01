"""PR I — Leverage / Futures Sizing 회귀.

검증:
1. ``TargetMarginPct``: notional = equity * margin_pct * leverage, units = notional / mark.
2. ``TargetNotionalPct``: notional = equity * notional_pct.
3. ``FullEquityNotional``: notional = equity * leverage.
4. RiskLimits.max_position_size 검사.
5. RiskLimits.max_total_exposure 검사.
6. RiskLimits.max_leverage 검사.
7. Engine 통합 — risk reject EventLog.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.errors import DataError
from backtester.core.orders import (
    FullEquityNotional,
    OrderIntent,
    TargetMarginPct,
    TargetNotionalPct,
)
from backtester.core.snapshot import MarketSnapshot
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.ledger import Ledger
from backtester.portfolio.position import Position
from backtester.portfolio.risk import RiskLimits, RiskManager
from backtester.portfolio.sizer import Sizer
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


def _market(close: float = 100.0) -> MarketSnapshot:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    d = Decimal(str(close))
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=base,
        open=d,
        high=d + Decimal("1"),
        low=d - Decimal("1"),
        close=d,
        volume=Decimal("1"),
    )


def _intent_with(spec) -> OrderIntent:  # type: ignore[no-untyped-def]
    return OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=spec,
        reason="test",
    )


# ---------- 1. TargetMarginPct -----------------------------------------------


def test_sizer_target_margin_pct_computes_notional() -> None:
    """equity=100k, margin_pct=0.1, leverage=5, mark=100 → notional=50k → 500 units."""
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    spec = TargetMarginPct(margin_pct=Decimal("0.1"), leverage=Decimal("5"))
    out = sizer.resolve(_intent_with(spec), _btc(), Decimal("100000"), pos, _market(100))
    assert out == Decimal("500")


def test_sizer_target_margin_pct_rejects_non_positive() -> None:
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    with pytest.raises(DataError, match="margin_pct"):
        sizer.resolve(
            _intent_with(TargetMarginPct(margin_pct=Decimal("0"), leverage=Decimal("5"))),
            _btc(),
            Decimal("100000"),
            pos,
            _market(100),
        )
    with pytest.raises(DataError, match="leverage"):
        sizer.resolve(
            _intent_with(
                TargetMarginPct(margin_pct=Decimal("0.1"), leverage=Decimal("0"))
            ),
            _btc(),
            Decimal("100000"),
            pos,
            _market(100),
        )


# ---------- 2. TargetNotionalPct ---------------------------------------------


def test_sizer_target_notional_pct() -> None:
    """equity=100k, notional_pct=0.5, mark=100 → notional=50k → 500 units."""
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    spec = TargetNotionalPct(notional_pct=Decimal("0.5"))
    out = sizer.resolve(_intent_with(spec), _btc(), Decimal("100000"), pos, _market(100))
    assert out == Decimal("500")


# ---------- 3. FullEquityNotional --------------------------------------------


def test_sizer_full_equity_notional() -> None:
    """equity=10k, leverage=3, mark=50 → notional=30k → 600 units."""
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    spec = FullEquityNotional(leverage=Decimal("3"))
    out = sizer.resolve(_intent_with(spec), _btc(), Decimal("10000"), pos, _market(50))
    assert out == Decimal("600")


# ---------- 4. RiskLimits.max_position_size ---------------------------------


def test_risk_blocks_max_position_size_exceeded() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    rm = RiskManager(RiskLimits(max_position_size=Decimal("10")))
    intent = _intent_with(TargetNotionalPct(notional_pct=Decimal("0.5")))
    result = rm.check(
        intent=intent,
        sized_quantity=Decimal("15"),  # > max 10
        instrument=_btc(),
        ledger=led,
        active_orders=[],
        market_close=Decimal("100"),
    )
    assert not result.accepted
    assert "max_position_size" in result.reason


def test_risk_allows_max_position_size_within_limit() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    rm = RiskManager(RiskLimits(max_position_size=Decimal("10")))
    intent = _intent_with(TargetNotionalPct(notional_pct=Decimal("0.5")))
    result = rm.check(
        intent=intent,
        sized_quantity=Decimal("5"),
        instrument=_btc(),
        ledger=led,
        active_orders=[],
        market_close=Decimal("100"),
    )
    assert result.accepted


# ---------- 5. RiskLimits.max_total_exposure --------------------------------


def test_risk_blocks_max_total_exposure_exceeded() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    rm = RiskManager(RiskLimits(max_total_exposure=Decimal("50000")))
    intent = _intent_with(TargetNotionalPct(notional_pct=Decimal("0.5")))
    # 600 units * mark 100 = 60k notional > 50k
    result = rm.check(
        intent=intent,
        sized_quantity=Decimal("600"),
        instrument=_btc(),
        ledger=led,
        active_orders=[],
        market_close=Decimal("100"),
    )
    assert not result.accepted
    assert "max_total_exposure" in result.reason


# ---------- 6. RiskLimits.max_leverage --------------------------------------


def test_risk_blocks_max_leverage_exceeded() -> None:
    led = Ledger(initial_equity=Decimal("10000"))
    rm = RiskManager(RiskLimits(max_leverage=Decimal("3")))
    intent = _intent_with(FullEquityNotional(leverage=Decimal("5")))
    # 500 units * mark 100 = 50k notional / 10k equity = 5x > 3x
    result = rm.check(
        intent=intent,
        sized_quantity=Decimal("500"),
        instrument=_btc(),
        ledger=led,
        active_orders=[],
        market_close=Decimal("100"),
    )
    assert not result.accepted
    assert "max_leverage" in result.reason


def test_risk_allows_max_leverage_within() -> None:
    led = Ledger(initial_equity=Decimal("10000"))
    rm = RiskManager(RiskLimits(max_leverage=Decimal("3")))
    intent = _intent_with(FullEquityNotional(leverage=Decimal("2")))
    result = rm.check(
        intent=intent,
        sized_quantity=Decimal("200"),  # 2x leverage
        instrument=_btc(),
        ledger=led,
        active_orders=[],
        market_close=Decimal("100"),
    )
    assert result.accepted


# ---------- 7. Engine 통합 ---------------------------------------------------


def _make_parquet(target: Path, n_bars: int = 5) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0] * n_bars,
            "high": [101.0] * n_bars,
            "low": [99.0] * n_bars,
            "close": [100.0] * n_bars,
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


class _LeveragedEntryStrategy(BaseStrategy):
    def __init__(self) -> None:
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
                size_spec=FullEquityNotional(leverage=Decimal("10")),
                reason="overleveraged",
            )
        ]


def test_engine_rejects_overleveraged_entry(tmp_path: Path) -> None:
    """전략이 leverage=10 으로 사이즈 했지만 max_leverage=3 → REJECTED."""
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="lev_reject",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=5),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        risk_limits=RiskLimits(max_leverage=Decimal("3")),
    )
    result = BacktestEngine(cfg, _LeveragedEntryStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    rejected = list(reader.by_type(EventType.ORDER_REJECTED))
    assert any("max_leverage" in str(e.payload.get("reason", "")) for e in rejected)


def test_engine_target_margin_pct_smoke(tmp_path: Path) -> None:
    """TargetMarginPct 가 deterministic payload 생성."""
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet")
    base = datetime(2026, 1, 1, tzinfo=UTC)

    class _Strat(BaseStrategy):
        def __init__(self) -> None:
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
                    size_spec=TargetMarginPct(
                        margin_pct=Decimal("0.05"),
                        leverage=Decimal("2"),
                    ),
                    reason="entry",
                )
            ]

    cfg = BacktestConfig(
        run_id="margin_pct",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=5),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _Strat(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    assert len(fills) == 1
    # equity=100k, margin_pct=0.05, leverage=2, mark=100 → notional=10k → 100 units
    assert Decimal(fills[0].payload["size"]) == Decimal("100")
