"""PR J — Reduce-Only / Close Semantics 회귀.

검증:
1. ``reduce_only=True`` + flat → ValueError.
2. ``reduce_only=True`` + same-direction (long+buy, short+sell) → ValueError.
3. ``reduce_only=True`` + oversize → ValueError (1차 reject 정책).
4. ``reduce_only=True`` + 정상 close (long+sell ≤ size, short+buy ≤ abs(size)) → 통과.
5. ``ClosePosition`` + side mismatch → DataError.
6. ``ClosePosition`` + flat → 0 (noop).
7. ``ClosePosition`` 이 long/short 적절히 처리.
8. EventLog: reduce_only intent 가 payload 에 보존.
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
    ClosePosition,
    OrderIntent,
    TargetUnits,
)
from backtester.core.snapshot import MarketSnapshot
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.position import Position
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


def _intent(side: str, units: str = "1", reduce_only: bool = False) -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
        type="market",
        size_spec=TargetUnits(units=Decimal(units)),
        reason="test",
        reduce_only=reduce_only,
    )


# ---------- 1. reduce_only flat reject ---------------------------------------


def test_reduce_only_flat_rejects() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT")
    with pytest.raises(ValueError, match="flat"):
        sizer.resolve(_intent("buy", reduce_only=True), _btc(), Decimal("100000"), pos, _market())


# ---------- 2. same-direction reject ----------------------------------------


def test_reduce_only_long_buy_rejects() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    with pytest.raises(ValueError, match="extend"):
        sizer.resolve(_intent("buy", reduce_only=True), _btc(), Decimal("100000"), pos, _market())


def test_reduce_only_short_sell_rejects() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-1"), avg_price=Decimal("100"))
    with pytest.raises(ValueError, match="extend"):
        sizer.resolve(_intent("sell", reduce_only=True), _btc(), Decimal("100000"), pos, _market())


# ---------- 3. oversize reject (1차 정책) -----------------------------------


def test_reduce_only_oversize_long_rejects() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    with pytest.raises(ValueError, match="oversize"):
        sizer.resolve(
            _intent("sell", "2", reduce_only=True), _btc(), Decimal("100000"), pos, _market()
        )


def test_reduce_only_oversize_short_rejects() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-1"), avg_price=Decimal("100"))
    with pytest.raises(ValueError, match="oversize"):
        sizer.resolve(
            _intent("buy", "2", reduce_only=True), _btc(), Decimal("100000"), pos, _market()
        )


# ---------- 4. reduce_only 정상 close ---------------------------------------


def test_reduce_only_long_sell_partial() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("2"), avg_price=Decimal("100"))
    out = sizer.resolve(
        _intent("sell", "1", reduce_only=True), _btc(), Decimal("100000"), pos, _market()
    )
    assert out == Decimal("1")


def test_reduce_only_short_buy_partial() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-2"), avg_price=Decimal("100"))
    out = sizer.resolve(
        _intent("buy", "1", reduce_only=True), _btc(), Decimal("100000"), pos, _market()
    )
    assert out == Decimal("1")


def test_reduce_only_long_full_close() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("2"), avg_price=Decimal("100"))
    out = sizer.resolve(
        _intent("sell", "2", reduce_only=True), _btc(), Decimal("100000"), pos, _market()
    )
    assert out == Decimal("2")


# ---------- 5/6. ClosePosition side mismatch + flat -------------------------


def test_close_position_side_mismatch_long_buy_raises() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=ClosePosition(),
        reason="bad",
    )
    with pytest.raises(DataError, match="side='sell'"):
        sizer.resolve(intent, _btc(), Decimal("100000"), pos, _market())


def test_close_position_side_mismatch_short_sell_raises() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-1"), avg_price=Decimal("100"))
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="sell",
        type="market",
        size_spec=ClosePosition(),
        reason="bad",
    )
    with pytest.raises(DataError, match="side='buy'"):
        sizer.resolve(intent, _btc(), Decimal("100000"), pos, _market())


def test_close_position_flat_returns_zero() -> None:
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="sell",
        type="market",
        size_spec=ClosePosition(),
        reason="noop",
    )
    out = sizer.resolve(intent, _btc(), Decimal("100000"), pos, _market())
    assert out == Decimal("0")


# ---------- 7. ClosePosition long/short 정상 처리 ---------------------------


def test_close_position_long_returns_size() -> None:
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT", size=Decimal("3"), avg_price=Decimal("100"))
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="sell",
        type="market",
        size_spec=ClosePosition(),
    )
    assert sizer.resolve(intent, _btc(), Decimal("100000"), pos, _market()) == Decimal("3")


def test_close_position_short_returns_abs_size() -> None:
    sizer = Sizer(allow_short=True)
    pos = Position(symbol="BTCUSDT", size=Decimal("-3"), avg_price=Decimal("100"))
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=ClosePosition(),
    )
    assert sizer.resolve(intent, _btc(), Decimal("100000"), pos, _market()) == Decimal("3")


# ---------- 8. Engine 통합 ----------------------------------------------------


def _make_parquet(target: Path, n_bars: int = 6) -> None:
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


class _LongThenReduceOnlySell(BaseStrategy):
    def __init__(self) -> None:
        self._step = 0

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        self._step += 1
        if self._step == 1:
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("2")),
                    reason="entry",
                )
            ]
        if self._step == 3 and ctx.has_position("BTCUSDT"):
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="sell",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="reduce",
                    reduce_only=True,
                )
            ]
        return []


def test_engine_reduce_only_intent_flows_to_event_log(tmp_path: Path) -> None:
    """reduce_only=True 가 INTENT_CREATED payload 에 보존."""
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="reduce_only_smoke",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=6),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _LongThenReduceOnlySell(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    intents = list(reader.by_type(EventType.INTENT_CREATED))
    # 두 intent: 진입 (reduce_only=False) + reduce-only sell (True)
    assert len(intents) >= 2
    reduce_intents = [
        e for e in intents if e.payload["intent"].get("reduce_only") is True
    ]
    assert len(reduce_intents) == 1
    assert reduce_intents[0].payload["intent"]["reason"] == "reduce"
