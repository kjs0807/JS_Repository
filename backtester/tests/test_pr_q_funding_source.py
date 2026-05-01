"""PR Q — Real Funding Rate Source 회귀.

검증:
1. ParquetFundingRateSource 정확 lookup.
2. Missing rate → None.
3. FundingProcessor + from_data_source 정상 흐름.
4. FundingProcessor + from_data_source missing → DataError.
5. Engine 통합 — funding_source_dir 가 funding rate 적용.
6. run_dir/funding/ self-contained 복사.
7. YAML round-trip funding_source_dir.
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
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.snapshot import MarketSnapshot
from backtester.data.funding_source import ParquetFundingRateSource
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.execution.funding import FundingModel, FundingProcessor
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.position import Position
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


def _make_funding_parquet(target: Path, rates: dict[datetime, str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"timestamp": ts, "rate": rate} for ts, rate in rates.items()]
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _make_ohlcv(target: Path, n_bars: int = 24) -> None:
    df = pl.DataFrame(
        {
            "timestamp": [TS + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0] * n_bars,
            "high": [101.0] * n_bars,
            "low": [99.0] * n_bars,
            "close": [100.0] * n_bars,
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


# ---------- 1. ParquetFundingRateSource lookup ------------------------------


def test_funding_source_exact_lookup(tmp_path: Path) -> None:
    rates = {
        TS + timedelta(hours=8): "0.0001",
        TS + timedelta(hours=16): "0.00012",
        TS + timedelta(days=1): "0.00009",
    }
    _make_funding_parquet(tmp_path / "funding_BTCUSDT.parquet", rates)
    src = ParquetFundingRateSource(tmp_path)
    assert src.get_rate("BTCUSDT", TS + timedelta(hours=8)) == Decimal("0.0001")
    assert src.get_rate("BTCUSDT", TS + timedelta(hours=16)) == Decimal("0.00012")


def test_funding_source_missing_returns_none(tmp_path: Path) -> None:
    rates = {TS + timedelta(hours=8): "0.0001"}
    _make_funding_parquet(tmp_path / "funding_BTCUSDT.parquet", rates)
    src = ParquetFundingRateSource(tmp_path)
    assert src.get_rate("BTCUSDT", TS + timedelta(hours=10)) is None


def test_funding_source_unknown_symbol_returns_none(tmp_path: Path) -> None:
    src = ParquetFundingRateSource(tmp_path)
    assert src.get_rate("ETHUSDT", TS) is None


# ---------- 3. FundingProcessor + from_data_source --------------------------


def _market(close: float = 100.0) -> MarketSnapshot:
    d = Decimal(str(close))
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=TS,
        open=d,
        high=d + Decimal("1"),
        low=d - Decimal("1"),
        close=d,
        volume=Decimal("1"),
    )


def test_funding_processor_from_data_source_lookup(tmp_path: Path) -> None:
    rates = {TS + timedelta(hours=8): "0.0002"}
    _make_funding_parquet(tmp_path / "funding_BTCUSDT.parquet", rates)
    src = ParquetFundingRateSource(tmp_path)
    fp = FundingProcessor(
        models={
            "BTCUSDT": FundingModel(
                interval_hours=8, rate_source="from_data_source"
            )
        },
        rate_source=src,
    )
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    cf = fp.process("BTCUSDT", TS + timedelta(hours=8), _btc(), pos, _market())
    assert cf is not None
    assert cf.rate == Decimal("0.0002")
    # 1 unit * 100 mark * -0.0002 = -0.02 (long pays)
    assert cf.amount == Decimal("-0.02")


def test_funding_processor_from_data_source_missing_raises(tmp_path: Path) -> None:
    src = ParquetFundingRateSource(tmp_path)  # 빈 cache
    fp = FundingProcessor(
        models={
            "BTCUSDT": FundingModel(
                interval_hours=8, rate_source="from_data_source"
            )
        },
        rate_source=src,
    )
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    with pytest.raises(DataError, match="funding rate missing"):
        fp.process("BTCUSDT", TS + timedelta(hours=8), _btc(), pos, _market())


def test_funding_processor_from_data_source_no_source_raises() -> None:
    fp = FundingProcessor(
        models={
            "BTCUSDT": FundingModel(
                interval_hours=8, rate_source="from_data_source"
            )
        },
    )
    pos = Position(symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"))
    with pytest.raises(DataError, match="requires a FundingRateSource"):
        fp.process("BTCUSDT", TS + timedelta(hours=8), _btc(), pos, _market())


# ---------- 5. Engine 통합 ---------------------------------------------------


class _BuyAndHold(BaseStrategy):
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
                size_spec=TargetUnits(units=Decimal("1")),
                reason="entry",
            )
        ]


def test_engine_funding_source_applies_rates(tmp_path: Path) -> None:
    """24h 시리즈, 8h boundaries (08:00/16:00/00:00 다음날). funding_source_dir
    에서 parquet 읽어 적용. 누락 없으면 정상 SETTLE 발생.
    """
    funding_dir = tmp_path / "funding_data"
    rates = {
        TS + timedelta(hours=8): "0.0001",
        TS + timedelta(hours=16): "0.0002",
        TS + timedelta(days=1): "0.0003",
    }
    _make_funding_parquet(funding_dir / "funding_BTCUSDT.parquet", rates)
    _make_ohlcv(tmp_path / "data" / "BTCUSDT_1h.parquet")
    cfg = BacktestConfig(
        run_id="funding_q",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=TS,
        end=TS + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8, rate_source="from_data_source"
            )
        },
        funding_source_dir=funding_dir,
    )
    result = BacktestEngine(cfg, _BuyAndHold(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    settles = list(reader.by_type(EventType.SETTLE))
    assert len(settles) == 3
    # rate 가 정확히 매핑됐는지
    expected_rates = ["0.0001", "0.0002", "0.0003"]
    actual_rates = [s.payload["rate"] for s in settles]
    assert actual_rates == expected_rates


def test_engine_funding_source_missing_rate_raises(tmp_path: Path) -> None:
    """funding_source 에 누락된 boundary 가 있으면 DataError → 백테스트 중단."""
    funding_dir = tmp_path / "funding_data"
    rates = {TS + timedelta(hours=8): "0.0001"}  # 16:00 / 00:00 누락
    _make_funding_parquet(funding_dir / "funding_BTCUSDT.parquet", rates)
    _make_ohlcv(tmp_path / "data" / "BTCUSDT_1h.parquet")
    cfg = BacktestConfig(
        run_id="funding_miss",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=TS,
        end=TS + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8, rate_source="from_data_source"
            )
        },
        funding_source_dir=funding_dir,
    )
    with pytest.raises(DataError, match="funding rate missing"):
        BacktestEngine(cfg, _BuyAndHold(), verbose=False).run()


# ---------- 6. self-contained run_dir 복사 ----------------------------------


def test_engine_persists_funding_artifacts(tmp_path: Path) -> None:
    funding_dir = tmp_path / "funding_data"
    rates = {
        TS + timedelta(hours=8): "0.0001",
        TS + timedelta(hours=16): "0.0001",
        TS + timedelta(days=1): "0.0001",
    }
    _make_funding_parquet(funding_dir / "funding_BTCUSDT.parquet", rates)
    _make_ohlcv(tmp_path / "data" / "BTCUSDT_1h.parquet")
    cfg = BacktestConfig(
        run_id="persist_funding",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=TS,
        end=TS + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8, rate_source="from_data_source"
            )
        },
        funding_source_dir=funding_dir,
    )
    result = BacktestEngine(cfg, _BuyAndHold(), verbose=False).run()
    persisted = result.run_dir / "funding" / "funding_BTCUSDT.parquet"
    assert persisted.exists()


# ---------- 7. YAML round-trip ----------------------------------------------


def test_config_yaml_round_trip_funding_source_dir(tmp_path: Path) -> None:
    cfg = BacktestConfig(
        run_id="rt",
        data_source=DataSourceConfig(base_dir=tmp_path),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=TS,
        end=TS + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        funding_source_dir=tmp_path / "funding_data",
    )
    yaml_path = tmp_path / "cfg.yaml"
    cfg.to_yaml(yaml_path)
    text = yaml_path.read_text(encoding="utf-8")
    assert "funding_source_dir" in text
    restored = BacktestConfig.from_yaml(yaml_path)
    assert restored.funding_source_dir == tmp_path / "funding_data"
