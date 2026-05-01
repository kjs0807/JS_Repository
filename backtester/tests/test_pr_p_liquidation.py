"""PR P — Margin / Liquidation 회귀.

검증:
1. ``MarginModel`` 검증 — out-of-range mmr/fee reject.
2. Position.liquidation_price 가 flat→open 시 계산.
3. 비-leverage 포지션 (notional ≤ equity) 의 liq_price 는 0 근처 (도달 어려움).
4. Long liquidation: low <= liq_price → 강제 close + LIQUIDATION 이벤트.
5. Short liquidation: high >= liq_price.
6. liquidation 이후 bracket child cancel.
7. liquidation_fee 가 cash 에서 차감.
8. Position close 후 liquidation_price 클리어.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import (
    BracketSpec,
    FullEquityNotional,
    OrderIntent,
)
from backtester.core.types import Fill
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument, MarginModel
from backtester.portfolio.ledger import Ledger
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc
TS = datetime(2026, 1, 1, tzinfo=UTC)


def _btc(margin_model: MarginModel | None = None) -> Instrument:
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
        margin_model=margin_model,
    )


def _fill(side: str, size: str, price: str) -> Fill:
    return Fill(
        timestamp=TS,
        symbol="BTCUSDT",
        price=Decimal(price),
        size=Decimal(size),
        side=side,  # type: ignore[arg-type]
        fee=Decimal("0"),
        fee_currency="USDT",
        order_id="ord_test",
        intent_reason="test",
    )


# ---------- 1. MarginModel validation ---------------------------------------


def test_margin_model_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="maintenance_margin_rate"):
        MarginModel(maintenance_margin_rate=Decimal("1.0"))
    with pytest.raises(ValueError, match="liquidation_fee_rate"):
        MarginModel(
            maintenance_margin_rate=Decimal("0.005"),
            liquidation_fee_rate=Decimal("-0.1"),
        )


# ---------- 2. liquidation_price 자동 계산 -----------------------------------


def test_position_liq_price_long_isolated_5x() -> None:
    """equity=10k, 50 units * avg=200 = 10k notional → L=1 (no leverage).
    liq = 200 * (1 - 1/1 + 0.005) = 200 * 0.005 = 1. 거의 0 — 비-leverage 안전.
    """
    led = Ledger(initial_equity=Decimal("10000"))
    inst = _btc(MarginModel(maintenance_margin_rate=Decimal("0.005")))
    led.on_fill(_fill("buy", "50", "200"), inst)
    pos = led.positions["BTCUSDT"]
    assert pos.liquidation_price == Decimal("1.000")


def test_position_liq_price_long_5x_leverage() -> None:
    """equity=10k. notional=50k (50 units * 1000) = 5x leverage.
    liq = 1000 * (1 - 1/5 + 0.005) = 1000 * 0.805 = 805.
    """
    led = Ledger(initial_equity=Decimal("10000"))
    inst = _btc(MarginModel(maintenance_margin_rate=Decimal("0.005")))
    led.on_fill(_fill("buy", "50", "1000"), inst)
    pos = led.positions["BTCUSDT"]
    assert pos.liquidation_price == Decimal("805.000")


def test_position_liq_price_short_5x_leverage() -> None:
    """equity=10k, 50 units short @1000 = 5x. liq = 1000 * (1 + 0.2 - 0.005) = 1195."""
    led = Ledger(initial_equity=Decimal("10000"))
    inst = _btc(MarginModel(maintenance_margin_rate=Decimal("0.005")))
    led.on_fill(_fill("sell", "50", "1000"), inst)
    pos = led.positions["BTCUSDT"]
    assert pos.liquidation_price == Decimal("1195.000")


def test_position_liq_price_none_without_margin_model() -> None:
    led = Ledger(initial_equity=Decimal("10000"))
    inst = _btc(None)
    led.on_fill(_fill("buy", "1", "100"), inst)
    pos = led.positions["BTCUSDT"]
    assert pos.liquidation_price is None


# ---------- 3. 완전 close → liquidation_price 클리어 ------------------------


def test_position_liq_price_cleared_on_full_close() -> None:
    led = Ledger(initial_equity=Decimal("10000"))
    inst = _btc(MarginModel(maintenance_margin_rate=Decimal("0.005")))
    led.on_fill(_fill("buy", "50", "1000"), inst)
    led.on_fill(_fill("sell", "50", "1100"), inst)
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("0")
    assert pos.liquidation_price is None


# ---------- 4. Engine 통합 — Long liquidation -------------------------------


def _make_parquet(target: Path, bars: list[dict[str, Any]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(bars).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _bar(t: datetime, o: float, h: float, low: float, c: float) -> dict[str, Any]:
    return {
        "timestamp": t,
        "open": float(o),
        "high": float(h),
        "low": float(low),
        "close": float(c),
        "volume": 1.0,
    }


class _LongLeveragedStrategy(BaseStrategy):
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
                size_spec=FullEquityNotional(leverage=Decimal("5")),
                reason="entry",
            )
        ]


def test_engine_long_liquidation_fires_when_low_hits_liq_price(tmp_path: Path) -> None:
    """5x long entry @1000 → liq ≈ 805. Bar 3 low=800 → liquidation."""
    base = TS
    bars = [
        _bar(base, 1000, 1001, 999, 1000),
        _bar(base + timedelta(hours=1), 1000, 1001, 999, 1000),  # entry fills
        _bar(base + timedelta(hours=2), 1000, 1001, 800, 850),  # low hits liq
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="long_liq",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc(MarginModel(maintenance_margin_rate=Decimal("0.005")))],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=3),
        initial_equity=Decimal("10000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _LongLeveragedStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    liqs = list(reader.by_type(EventType.LIQUIDATION))
    assert len(liqs) == 1
    assert Decimal(liqs[0].payload["liquidation_price"]) == Decimal("805.000")
    assert liqs[0].payload["side"] == "sell"


# ---------- 5. Short liquidation --------------------------------------------


class _ShortLeveragedStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="sell",
                type="market",
                size_spec=FullEquityNotional(leverage=Decimal("5")),
                reason="entry",
            )
        ]


def test_engine_short_liquidation_fires_when_high_hits_liq_price(
    tmp_path: Path,
) -> None:
    """5x short entry @1000 → liq ≈ 1195. Bar 3 high=1200 → liquidation."""
    base = TS
    bars = [
        _bar(base, 1000, 1001, 999, 1000),
        _bar(base + timedelta(hours=1), 1000, 1001, 999, 1000),
        _bar(base + timedelta(hours=2), 1000, 1200, 999, 1150),
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="short_liq",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc(MarginModel(maintenance_margin_rate=Decimal("0.005")))],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=3),
        initial_equity=Decimal("10000"),
        output_dir=tmp_path / "runs",
        allow_short=True,
    )
    result = BacktestEngine(cfg, _ShortLeveragedStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    liqs = list(reader.by_type(EventType.LIQUIDATION))
    assert len(liqs) == 1
    assert liqs[0].payload["side"] == "buy"


# ---------- 6. Bracket children cancelled on liquidation -------------------


class _LongLevWithBracket(BaseStrategy):
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
                size_spec=FullEquityNotional(leverage=Decimal("5")),
                reason="entry",
                bracket=BracketSpec(
                    take_profit_price=Decimal("1200"),  # 멀리 — 미체결
                    stop_loss_price=Decimal("700"),  # liq=805 보다 더 낮음 — 미체결
                ),
            )
        ]


def test_engine_liquidation_cancels_bracket_children(tmp_path: Path) -> None:
    base = TS
    bars = [
        _bar(base, 1000, 1001, 999, 1000),
        _bar(base + timedelta(hours=1), 1000, 1001, 999, 1000),
        _bar(base + timedelta(hours=2), 1000, 1001, 800, 850),  # liquidation
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="liq_bracket",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc(MarginModel(maintenance_margin_rate=Decimal("0.005")))],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=3),
        initial_equity=Decimal("10000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _LongLevWithBracket(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    cancels = [
        c for c in reader.by_type(EventType.ORDER_CANCELLED)
        if c.payload.get("reason") == "liquidation"
    ]
    # 2 cancels (TP + SL)
    assert len(cancels) == 2


# ---------- 7. liquidation_fee 가 cash 에서 차감 ----------------------------


def test_engine_liquidation_fee_reduces_cash(tmp_path: Path) -> None:
    base = TS
    bars = [
        _bar(base, 1000, 1001, 999, 1000),
        _bar(base + timedelta(hours=1), 1000, 1001, 999, 1000),
        _bar(base + timedelta(hours=2), 1000, 1001, 800, 850),
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="liq_fee",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[
            _btc(
                MarginModel(
                    maintenance_margin_rate=Decimal("0.005"),
                    liquidation_fee_rate=Decimal("0.001"),
                )
            )
        ],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=3),
        initial_equity=Decimal("10000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _LongLeveragedStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    liqs = list(reader.by_type(EventType.LIQUIDATION))
    assert len(liqs) == 1
    fee = Decimal(liqs[0].payload["fee"])
    # close size 50 * liq_price 805 * fee_rate 0.001 = 40.25
    assert fee == Decimal("40.250")
