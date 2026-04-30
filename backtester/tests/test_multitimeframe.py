"""PR 13 멀티 timeframe 테스트 (Phase 2, spec §7).

두 축:
1. MultiTimeframeClock 단위 — bar_close 합집합 시간 순 emit, 같은 ts 그룹화, 검증.
2. Engine 통합 — 1h primary + 4h secondary 시나리오에서 strategy 가 primary close 시점에만
   호출되고 BarsView 가 multi-TF 데이터를 lookahead 없이 노출.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.clock import MultiTimeframeClock, SimpleClock
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent
from backtester.indicators.base import Indicator
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


# ---------- MultiTimeframeClock 단위 ---------------------------------------


def test_mtf_clock_single_tf_equivalent_to_simple_clock() -> None:
    """단일 (symbol, tf) 입력 시 SimpleClock 과 동일한 ClockEvent 시퀀스."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    starts = [base + timedelta(hours=i) for i in range(5)]
    mtf = MultiTimeframeClock({("BTCUSDT", "1h"): starts})
    simple = SimpleClock(["BTCUSDT"], "1h", starts)

    mtf_evts = list(mtf)
    simple_evts = list(simple)
    assert len(mtf_evts) == len(simple_evts) == 5
    for m, s in zip(mtf_evts, simple_evts, strict=True):
        assert m.timestamp == s.timestamp
        assert m.bar_closes == s.bar_closes
        assert m.settlements == s.settlements


def test_mtf_clock_groups_coincident_closes() -> None:
    """1h 와 4h 가 동일 ts 에 닫히면 한 ClockEvent 의 bar_closes 에 모두 담긴다."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    h1_starts = [base + timedelta(hours=i) for i in range(8)]
    h4_starts = [base, base + timedelta(hours=4)]
    mtf = MultiTimeframeClock(
        {("BTCUSDT", "1h"): h1_starts, ("BTCUSDT", "4h"): h4_starts}
    )
    evts = list(mtf)

    # 마감 시각: 1h 마감 = 01:00, 02:00, ..., 08:00. 4h 마감 = 04:00, 08:00.
    # 합집합 = {01:00, 02:00, 03:00, 04:00, 05:00, 06:00, 07:00, 08:00} (8 개).
    assert len(evts) == 8

    by_ts = {e.timestamp: e for e in evts}
    # 04:00 시점에 1h + 4h 모두 닫힘
    e_04 = by_ts[base + timedelta(hours=4)]
    assert sorted(e_04.bar_closes["BTCUSDT"]) == ["1h", "4h"]
    # 02:00 시점에는 1h 만
    e_02 = by_ts[base + timedelta(hours=2)]
    assert e_02.bar_closes["BTCUSDT"] == ["1h"]
    # 08:00 시점에 1h + 4h 모두
    e_08 = by_ts[base + timedelta(hours=8)]
    assert sorted(e_08.bar_closes["BTCUSDT"]) == ["1h", "4h"]


def test_mtf_clock_multi_symbol_at_same_ts() -> None:
    """다른 symbol 이 같은 ts 에 닫히면 한 ClockEvent 의 bar_closes 에 둘 다 등장."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    starts = [base, base + timedelta(hours=1)]
    mtf = MultiTimeframeClock(
        {("BTCUSDT", "1h"): starts, ("ETHUSDT", "1h"): starts}
    )
    evts = list(mtf)
    assert len(evts) == 2
    assert sorted(evts[0].bar_closes.keys()) == ["BTCUSDT", "ETHUSDT"]


def test_mtf_clock_rejects_naive_datetime() -> None:
    base_naive = datetime(2026, 3, 1)
    with pytest.raises(ValueError, match="timezone-aware"):
        MultiTimeframeClock({("BTCUSDT", "1h"): [base_naive]})


def test_mtf_clock_rejects_non_utc_offset() -> None:
    base_kst = datetime(2026, 3, 1, tzinfo=timezone(timedelta(hours=9)))
    with pytest.raises(ValueError, match="UTC"):
        MultiTimeframeClock({("BTCUSDT", "1h"): [base_kst]})


def test_mtf_clock_rejects_non_increasing_timestamps() -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="strictly increasing"):
        MultiTimeframeClock(
            {("BTCUSDT", "1h"): [base, base + timedelta(hours=1), base]}
        )


def test_mtf_clock_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        MultiTimeframeClock({})


def test_mtf_clock_len_equals_unique_close_count() -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    h1 = [base + timedelta(hours=i) for i in range(4)]
    h4 = [base]  # 마감 04:00 → 이미 h1 시퀀스에 포함
    mtf = MultiTimeframeClock(
        {("BTCUSDT", "1h"): h1, ("BTCUSDT", "4h"): h4}
    )
    # h1 마감: 01:00, 02:00, 03:00, 04:00. h4 마감: 04:00. 합집합 = 4 개.
    assert len(mtf) == 4
    assert len(list(mtf)) == 4


# ---------- Engine multi-TF 통합 -------------------------------------------


def _write_btc_parquet(target: Path, n_bars: int, tf_hours: int) -> None:
    """``tf_hours`` 시간 간격으로 ``n_bars`` 개의 OHLCV bar (close = 100 + i)."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": base + timedelta(hours=i * tf_hours),
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 1.0,
        }
        for i in range(n_bars)
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _btc_instrument() -> Instrument:
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


class _RecordingStrategy(BaseStrategy):
    """on_bar 호출마다 (now, primary_close_ts, h4_view_height) 를 기록만 하는 전략."""

    def __init__(self) -> None:
        self.calls: list[tuple[datetime, int, int]] = []

    def required_indicators(self) -> list[Indicator]:
        return []

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        h1_view = ctx.bars[ctx.primary_symbol]["1h"]
        h4_view: pl.DataFrame
        try:
            h4_view = ctx.bars[ctx.primary_symbol]["4h"]
        except KeyError:
            h4_view = pl.DataFrame()
        self.calls.append((ctx.now, h1_view.height, h4_view.height))
        return []


def test_engine_multitf_strategy_fires_only_on_primary_close(tmp_path: Path) -> None:
    """primary=1h, secondary=4h. strategy.on_bar 는 매 1h 마감마다 호출 (총 1h 봉 수 -
    warmup). 4h 단독 마감 시점은 (1h 와 항상 일치하므로) 별도로 fire 되지 않는다."""
    data_dir = tmp_path / "data"
    _write_btc_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=24, tf_hours=1)
    _write_btc_parquet(data_dir / "BTCUSDT_4h.parquet", n_bars=6, tf_hours=4)

    cfg = BacktestConfig(
        run_id="mtf_smoke",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h", "4h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 2, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = _RecordingStrategy()
    engine = BacktestEngine(cfg, strat, verbose=False)
    engine.run()

    # 24 1h bar - warmup(0 indicators) - 1 (primary close 가 첫 bar 마감인지 후 bar_count
    # > warmup 가드). 정확한 호출 수는 바뀔 수 있으니 핵심: 모든 호출 시각이 매 hour:00 (1h
    # 마감 경계) 와 정확히 일치한다.
    assert strat.calls, "strategy must be invoked at least once"
    for now, _h1_height, _h4_height in strat.calls:
        assert now.minute == 0
        assert now.second == 0
        # 1h 마감 시각만 (전부 hourly). polars 로 로드된 datetime tzinfo 는
        # zoneinfo.ZoneInfo("UTC") 라서 stdlib timezone.utc 와 != 이지만 offset 동일.
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)


def test_engine_multitf_h4_view_no_lookahead(tmp_path: Path) -> None:
    """1h primary 시점 ``now=HH:00`` 에서 4h BarsView 가 ``floor((HH-1)/4)*4`` 마감
    봉까지만 노출. ``now=04:00`` 에서 4h close 도 포함, ``now=05:00`` 에서는 04:00 마감
    봉까지만 (다음 4h close 는 08:00)."""
    data_dir = tmp_path / "data"
    _write_btc_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=24, tf_hours=1)
    _write_btc_parquet(data_dir / "BTCUSDT_4h.parquet", n_bars=6, tf_hours=4)

    cfg = BacktestConfig(
        run_id="mtf_lookahead",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h", "4h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 2, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = _RecordingStrategy()
    BacktestEngine(cfg, strat, verbose=False).run()

    # polars zoneinfo vs stdlib UTC 차이를 피하기 위해 epoch microsecond 키로 정규화.
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    base = datetime(2026, 3, 1, tzinfo=UTC)

    def _us(dt: datetime) -> int:
        return int((dt - epoch) / timedelta(microseconds=1))

    by_us = {_us(now): h4 for now, _h1, h4 in strat.calls}

    # now=02:00 / 03:00 → 4h 마감 안 됨 → 4h view height = 0
    assert by_us[_us(base + timedelta(hours=2))] == 0
    assert by_us[_us(base + timedelta(hours=3))] == 0
    # now=04:00 → 4h 첫 봉 (open=00:00) 마감 → height = 1
    assert by_us[_us(base + timedelta(hours=4))] == 1
    # now=05:00 ~ 07:00 → 여전히 1 개 (다음 4h 마감은 08:00)
    assert by_us[_us(base + timedelta(hours=5))] == 1
    assert by_us[_us(base + timedelta(hours=7))] == 1
    # now=08:00 → 4h 두 번째 봉 (open=04:00) 마감 → height = 2
    assert by_us[_us(base + timedelta(hours=8))] == 2


def test_engine_multitf_indicators_persisted_per_tf(tmp_path: Path) -> None:
    """IndicatorEngine 이 (symbol, tf) 별로 indicators parquet 영속화."""
    from backtester.indicators.stateless.bb import BollingerBands

    class _BBStrategy(_RecordingStrategy):
        def required_indicators(self) -> list[Indicator]:
            return [BollingerBands(period=5, num_std=2.0)]

    data_dir = tmp_path / "data"
    _write_btc_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=24, tf_hours=1)
    _write_btc_parquet(data_dir / "BTCUSDT_4h.parquet", n_bars=6, tf_hours=4)

    cfg = BacktestConfig(
        run_id="mtf_indicators",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h", "4h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 2, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _BBStrategy(), verbose=False).run()

    h1_path = result.run_dir / "indicators" / "BTCUSDT_1h.parquet"
    h4_path = result.run_dir / "indicators" / "BTCUSDT_4h.parquet"
    assert h1_path.exists() and h4_path.exists()

    h1_df = pl.read_parquet(h1_path)
    h4_df = pl.read_parquet(h4_path)
    assert h1_df.height == 24
    assert h4_df.height == 6
    # BB 컬럼 노출
    assert any("bb_" in c for c in h1_df.columns)
    assert any("bb_" in c for c in h4_df.columns)
