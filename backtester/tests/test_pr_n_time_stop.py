"""PR N — Time Stop infrastructure 회귀.

검증:
1. ``Position.opened_at`` — flat → 새 포지션 시 fill ts 기록.
2. 같은 방향 추가 시 ``opened_at`` 유지.
3. 부분 close 시 ``opened_at`` 유지.
4. 완전 close 후 재진입 시 ``opened_at`` 새 ts 로 갱신.
5. flip 시 ``opened_at`` 새 ts 로 갱신.
6. ``ctx.bars_held(symbol)`` — flat → None, 보유 중 → 정수.
7. Engine 통합: 전략이 ``ctx.bars_held`` 기반 time stop 사용 가능.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import (
    ClosePosition,
    OrderIntent,
    TargetUnits,
)
from backtester.core.types import Fill
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.ledger import Ledger
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc
TS = datetime(2026, 1, 1, tzinfo=UTC)


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


def _fill(ts: datetime, side: str, size: str, price: str) -> Fill:
    return Fill(
        timestamp=ts,
        symbol="BTCUSDT",
        price=Decimal(price),
        size=Decimal(size),
        side=side,  # type: ignore[arg-type]
        fee=Decimal("0"),
        fee_currency="USDT",
        order_id="ord_test",
        intent_reason="test",
    )


# ---------- 1. open from flat → opened_at 설정 ------------------------------


def test_position_opened_at_set_on_open_from_flat() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill(TS, "buy", "1", "100"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.opened_at == TS


# ---------- 2. same-direction extend → opened_at 유지 -----------------------


def test_position_opened_at_preserved_on_extend() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill(TS, "buy", "1", "100"), _btc())
    pos = led.positions["BTCUSDT"]
    led.on_fill(_fill(TS + timedelta(hours=2), "buy", "1", "105"), _btc())
    assert pos.opened_at == TS


# ---------- 3. 부분 close → opened_at 유지 ----------------------------------


def test_position_opened_at_preserved_on_partial_close() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill(TS, "buy", "2", "100"), _btc())
    led.on_fill(_fill(TS + timedelta(hours=3), "sell", "1", "110"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.opened_at == TS


# ---------- 4. 완전 close 후 재진입 → opened_at 새 ts -----------------------


def test_position_opened_at_resets_on_reentry() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill(TS, "buy", "1", "100"), _btc())
    led.on_fill(_fill(TS + timedelta(hours=3), "sell", "1", "110"), _btc())
    # 재진입
    new_ts = TS + timedelta(hours=10)
    led.on_fill(_fill(new_ts, "buy", "1", "120"), _btc())
    pos = led.positions["BTCUSDT"]
    assert pos.opened_at == new_ts


# ---------- 5. flip → opened_at 새 ts ---------------------------------------


def test_position_opened_at_resets_on_flip() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_fill(_fill(TS, "buy", "1", "100"), _btc())
    flip_ts = TS + timedelta(hours=5)
    led.on_fill(_fill(flip_ts, "sell", "3", "110"), _btc())  # long → short
    pos = led.positions["BTCUSDT"]
    assert pos.size == Decimal("-2")
    assert pos.opened_at == flip_ts


# ---------- 6. ctx.bars_held(symbol) helper ---------------------------------


def _make_parquet(target: Path, n_bars: int = 6) -> None:
    base = TS
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


class _BarsHeldRecorderStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False
        self.bars_held_log: list[int | None] = []

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        self.bars_held_log.append(ctx.bars_held("BTCUSDT"))
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="market",
                size_spec=TargetUnits(units=Decimal("1")),
                reason="entry",
            )
        ]


def test_engine_bars_held_progression(tmp_path: Path) -> None:
    """첫 봉 flat → None. 봉 2 fill 후부터 단조 증가.

    NextBarOpenExecution 의 ``fill.timestamp = snapshot.timestamp`` (= 봉 시작 ts).
    Engine ClockEvent ts 는 봉 마감 ts (= 봉 시작 + interval). 그래서 fill 직후 첫
    on_bar 의 ``ctx.bars_held = (now - opened_at) / interval = 1``. 이후 단조 증가.
    """
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", n_bars=6)
    cfg = BacktestConfig(
        run_id="bars_held",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=TS,
        end=TS + timedelta(hours=6),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = _BarsHeldRecorderStrategy()
    BacktestEngine(cfg, strat, verbose=False).run()
    log = strat.bars_held_log
    assert log[0] is None  # 첫 봉 flat
    # 두 번째 호출 부터 보유 중. 첫 보유 시점에 bars_held = 1.
    assert log[1] == 1
    # 이후 단조 증가 (1 씩)
    for i in range(2, len(log)):
        assert log[i] is not None
        assert log[i] == log[i - 1] + 1  # type: ignore[operator]


# ---------- 7. Time stop 패턴 — 전략이 ctx.bars_held 로 close ---------------


class _TimeStopStrategy(BaseStrategy):
    """진입 후 N=2 봉 보유하면 reduce-only close."""

    def __init__(self, n: int = 2) -> None:
        self._sent = False
        self._n = n
        self._closed = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if not self._sent:
            self._sent = True
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="entry",
                )
            ]
        if self._closed:
            return []
        held = ctx.bars_held("BTCUSDT")
        if held is not None and held >= self._n:
            self._closed = True
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="sell",
                    type="market",
                    size_spec=ClosePosition(),
                    reason="time_stop",
                    reduce_only=True,
                )
            ]
        return []


def test_engine_time_stop_strategy_closes_after_n_bars(tmp_path: Path) -> None:
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", n_bars=8)
    cfg = BacktestConfig(
        run_id="time_stop",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=TS,
        end=TS + timedelta(hours=8),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _TimeStopStrategy(n=2), verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    # entry + close 2 fills
    assert len(fills) == 2
    assert fills[0].payload["side"] == "buy"
    assert fills[1].payload["side"] == "sell"
    assert fills[1].payload["intent_reason"] == "time_stop"
