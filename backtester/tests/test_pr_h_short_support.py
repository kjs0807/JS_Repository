"""PR H — Short Position Support 회귀.

검증:
1. Sizer: ``allow_short=False`` (default) → sell from flat / oversize sell → NotImplementedError.
2. Sizer: ``allow_short=True`` → short open / extend / close 허용.
3. Sizer: flip (allow_flip=False) → ValueError.
4. Ledger.on_fill: short open / extend / partial close / full close.
5. Ledger 부호 일관: long PnL = (price-avg)*size, short PnL = (avg-price)*|size|.
6. Engine 통합: short 라이프사이클 + funding 부호.
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
from backtester.core.orders import (
    ClosePosition,
    OrderIntent,
    TargetUnits,
)
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.execution.funding import FundingModel
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.ledger import Ledger
from backtester.portfolio.position import Position
from backtester.portfolio.sizer import Sizer
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


# ---------- fixtures --------------------------------------------------------


def _btc(taker: str = "0") -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal(taker)),
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


def _intent(side: str, units: str = "1") -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
        type="market",
        size_spec=TargetUnits(units=Decimal(units)),
        reason="test",
    )


# ---------- 1. Sizer allow_short=False ---------------------------------------


def test_sizer_default_blocks_sell_from_flat() -> None:
    sizer = Sizer()  # allow_short=False
    pos = Position(symbol="BTCUSDT")  # flat
    with pytest.raises(NotImplementedError, match="short"):
        sizer.resolve(_intent("sell"), _btc(), Decimal("100000"), pos, _market())


def test_sizer_default_blocks_sell_oversize() -> None:
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    # sell 2 → flip into short. allow_flip=False 가 먼저 잡음.
    with pytest.raises(ValueError, match="flip"):
        sizer.resolve(_intent("sell", "2"), _btc(), Decimal("100000"), pos, _market())


def test_sizer_default_allows_long_close() -> None:
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT", size=Decimal("2"), avg_price=Decimal("100"))
    out = sizer.resolve(_intent("sell", "2"), _btc(), Decimal("100000"), pos, _market())
    assert out == Decimal("2")


# ---------- 2. Sizer allow_short=True ----------------------------------------


def test_sizer_allow_short_opens_from_flat() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT")
    out = sizer.resolve(_intent("sell"), _btc(), Decimal("100000"), pos, _market())
    assert out == Decimal("1")


def test_sizer_allow_short_extends_short() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-1"), avg_price=Decimal("100"))
    out = sizer.resolve(_intent("sell", "0.5"), _btc(), Decimal("100000"), pos, _market())
    assert out == Decimal("0.5")


def test_sizer_allow_short_close_via_buy() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-1"), avg_price=Decimal("100"))
    out = sizer.resolve(_intent("buy", "1"), _btc(), Decimal("100000"), pos, _market())
    assert out == Decimal("1")


def test_sizer_close_position_on_short_returns_abs_size() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-2"), avg_price=Decimal("100"))
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=ClosePosition(),
        reason="close_short",
    )
    out = sizer.resolve(intent, _btc(), Decimal("100000"), pos, _market())
    assert out == Decimal("2")


# ---------- 3. flip 정책 ------------------------------------------------------


def test_sizer_flip_blocked_by_default() -> None:
    sizer = Sizer(allow_short=True)  # short OK 지만 flip 은 reject
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    with pytest.raises(ValueError, match="flip"):
        sizer.resolve(_intent("sell", "2"), _btc(), Decimal("100000"), pos, _market())


def test_sizer_flip_allowed_explicitly() -> None:
    sizer = Sizer(allow_short=True, allow_flip=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    out = sizer.resolve(_intent("sell", "2"), _btc(), Decimal("100000"), pos, _market())
    assert out == Decimal("2")


def test_sizer_buy_flip_short_to_long_blocked() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-1"), avg_price=Decimal("100"))
    with pytest.raises(ValueError, match="flip"):
        sizer.resolve(_intent("buy", "2"), _btc(), Decimal("100000"), pos, _market())


# ---------- 4. Ledger on_fill short -------------------------------------------


def _fill(side: str, size: str, price: str) -> Fill:
    return Fill(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        symbol="BTCUSDT",
        price=Decimal(price),
        size=Decimal(size),
        side=side,  # type: ignore[arg-type]
        fee=Decimal("0"),
        fee_currency="USDT",
        order_id="ord_test",
        intent_reason="test",
    )


def test_ledger_short_open_decreases_size_and_increases_cash() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill("sell", "1", "100"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("-1")
    assert pos.avg_price == Decimal("100")
    assert led.cash == Decimal("100100")  # cash += 1 * 100


def test_ledger_short_extend_weighted_avg() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill("sell", "1", "100"), _btc())
    led.on_fill(_fill("sell", "1", "120"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("-2")
    assert pos.avg_price == Decimal("110")  # (1*100 + 1*120) / 2


def test_ledger_short_partial_close_realizes_pnl() -> None:
    """short open at 100, buy back 0.5 at 90 → realized = (100-90)*0.5 = 5."""
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill("sell", "1", "100"), _btc())
    led.on_fill(_fill("buy", "0.5", "90"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("-0.5")
    assert pos.avg_price == Decimal("100")  # 잔여는 entry price 유지
    assert pos.realized_pnl == Decimal("5.0")


def test_ledger_short_full_close_clears_avg_price() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill("sell", "1", "100"), _btc())
    led.on_fill(_fill("buy", "1", "90"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("0")
    assert pos.avg_price == Decimal("0")
    assert pos.realized_pnl == Decimal("10")


def test_ledger_short_unrealized_signed_formula() -> None:
    """short size=-2, avg=100, mark=90 → unrealized = (90-100)*(-2) = 20 (winning short)."""
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill("sell", "2", "100"), _btc())
    snap = _market(90.0)
    led.on_market({"BTCUSDT": snap})
    pos = led.positions["BTCUSDT"]
    assert pos.unrealized_pnl == Decimal("20")
    # equity = cash + market_value = 100200 + (-2*100 + 20) = 100200 - 180 = 100020
    assert led.equity == Decimal("100020")


def test_ledger_long_unchanged_default_path() -> None:
    """기존 long-only 회귀 — long open/close PnL 동일."""
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill("buy", "2", "100"), _btc())
    led.on_fill(_fill("sell", "1", "110"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("1")
    assert pos.avg_price == Decimal("100")
    assert pos.realized_pnl == Decimal("10")


# ---------- 5. flip via Ledger (allow_flip=True 가정) -----------------------


def test_ledger_flip_long_to_short_realizes_full_long_then_opens_short() -> None:
    """long 1 @ 100, sell 3 @ 110 → realize (110-100)*1 = 10, short 2 @ 110."""
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill("buy", "1", "100"), _btc())
    led.on_fill(_fill("sell", "3", "110"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("-2")
    assert pos.avg_price == Decimal("110")
    assert pos.realized_pnl == Decimal("10")


# ---------- 6. Engine integration: short lifecycle + funding -----------------


def _make_parquet(target: Path, n_bars: int = 24) -> None:
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


class _ShortAndHoldStrategy(BaseStrategy):
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
                size_spec=TargetUnits(units=Decimal("1")),
                reason="short_open",
            )
        ]


def test_engine_short_open_with_allow_short(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="short_smoke",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        allow_short=True,
    )
    result = BacktestEngine(cfg, _ShortAndHoldStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    assert len(fills) == 1
    assert fills[0].payload["side"] == "sell"


def test_engine_short_blocked_when_allow_short_false(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="short_blocked",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        # allow_short=False (default)
    )
    result = BacktestEngine(cfg, _ShortAndHoldStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    rejected = list(reader.by_type(EventType.ORDER_REJECTED))
    assert any("short" in str(e.payload.get("reason", "")) for e in rejected)


def test_engine_short_funding_sign(tmp_path: Path) -> None:
    """SHORT + rate>0 → funding cash 수령 (positive amount)."""
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="short_funding",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        allow_short=True,
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8,
                rate_source="constant",
                constant_rate=Decimal("0.0001"),
            )
        },
    )
    result = BacktestEngine(cfg, _ShortAndHoldStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    settles = list(reader.by_type(EventType.SETTLE))
    # SHORT + rate>0 → all amounts positive
    assert settles
    for s in settles:
        amt = Decimal(s.payload["amount"])
        assert amt > 0, f"SHORT funding should be positive (받음), got {amt}"


# ---------- 7. Config YAML round-trip for allow_short ----------------------


def test_config_yaml_round_trip_allow_short(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="rt",
        data_source=DataSourceConfig(base_dir=tmp_path),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        allow_short=True,
        allow_flip=True,
    )
    yaml_path = tmp_path / "cfg.yaml"
    cfg.to_yaml(yaml_path)
    text = yaml_path.read_text(encoding="utf-8")
    assert "allow_short: true" in text
    assert "allow_flip: true" in text
    restored = BacktestConfig.from_yaml(yaml_path)
    assert restored.allow_short is True
    assert restored.allow_flip is True
