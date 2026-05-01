"""PR S — Forward / Paper Runner 회귀.

검증:
1. ForwardRunner 가 같은 데이터로 BacktestEngine 과 byte-identical events.jsonl 생성.
2. ForwardRunner run_dir artifact (events.jsonl, config.yaml, results) 모두 생성.
3. ForwardRunner 결과로 chart / report / rebuild 가능.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.analysis.rebuild import rebuild_equity_curve
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.runtime import ForwardRunner, PaperBroker
from backtester.strategies.base import BaseStrategy
from backtester.viz.run_chart import build_run_chart

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


def _make_parquet(target: Path, n_bars: int = 24) -> None:
    df = pl.DataFrame(
        {
            "timestamp": [TS + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0 + i * 0.1 for i in range(n_bars)],
            "high": [101.0 + i * 0.1 for i in range(n_bars)],
            "low": [99.0 + i * 0.1 for i in range(n_bars)],
            "close": [100.5 + i * 0.1 for i in range(n_bars)],
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


class _BuyHoldSell(BaseStrategy):
    def __init__(self) -> None:
        self._step = 0

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        self._step += 1
        if self._step == 2:
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="entry",
                )
            ]
        if self._step == 10 and ctx.has_position("BTCUSDT"):
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="sell",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="exit",
                    reduce_only=True,
                )
            ]
        return []


def _config(tmp_path: Path, run_id: str) -> BacktestConfig:
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet")
    return BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=TS,
        end=TS + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        random_seed=42,
        allow_short=True,
    )


# ---------- 1. forward run = backtest byte-identical ------------------------


def test_forward_runner_matches_backtest_byte_identical(tmp_path: Path) -> None:
    """같은 config + 데이터 + 전략 → ForwardRunner 와 BacktestEngine 결과 events.jsonl
    이 byte-identical.
    """
    bt_cfg = _config(tmp_path, "shared_run_a")
    fwd_cfg = _config(tmp_path, "shared_run_b")

    bt_result = BacktestEngine(bt_cfg, _BuyHoldSell(), verbose=False).run()
    fwd_result = ForwardRunner(verbose=False).run(fwd_cfg, _BuyHoldSell())

    bt_bytes = bt_result.events_path.read_bytes()
    fwd_bytes = fwd_result.events_path.read_bytes()
    assert bt_bytes == fwd_bytes


# ---------- 2. run_dir artifact -------------------------------------------


def test_forward_runner_creates_run_dir_artifacts(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "fwd_artifacts")
    result = ForwardRunner(verbose=False).run(cfg, _BuyHoldSell())
    assert (result.run_dir / "events.jsonl").exists()
    assert (result.run_dir / "config.yaml").exists()
    assert (result.run_dir / "results").exists()


# ---------- 3. chart / report / rebuild smoke ------------------------------


def test_forward_runner_chart_and_rebuild_smoke(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "fwd_smoke")
    result = ForwardRunner(verbose=False).run(cfg, _BuyHoldSell())
    fig = build_run_chart(result.run_dir)
    assert fig is not None
    out = rebuild_equity_curve(result.run_dir)
    eq = pl.read_parquet(out)
    assert eq.height > 0


# ---------- 4. event surface 정합성 (특정 이벤트 타입 비교) ------------------


def test_forward_runner_event_surface_same_as_backtest(tmp_path: Path) -> None:
    bt_cfg = _config(tmp_path, "surface_bt")
    fwd_cfg = _config(tmp_path, "surface_fwd")
    bt = BacktestEngine(bt_cfg, _BuyHoldSell(), verbose=False).run()
    fwd = ForwardRunner(verbose=False).run(fwd_cfg, _BuyHoldSell())
    bt_reader = EventLogReader(bt.events_path)
    fwd_reader = EventLogReader(fwd.events_path)
    bt_fills = list(bt_reader.by_type(EventType.FILL))
    fwd_fills = list(fwd_reader.by_type(EventType.FILL))
    assert len(bt_fills) == len(fwd_fills) >= 2
    for a, b in zip(bt_fills, fwd_fills, strict=True):
        assert a.payload["side"] == b.payload["side"]
        assert a.payload["size"] == b.payload["size"]
        assert a.payload["price"] == b.payload["price"]


# ---------- 5. PaperBroker placeholder --------------------------------------


def test_paper_broker_placeholder_constructs() -> None:
    pb = PaperBroker()
    assert pb is not None
