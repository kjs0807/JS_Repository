"""PR 9 CSVDataSource 테스트 (spec §3.1, Phase 1.5 ``data/csv_source.py``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from backtester.core.errors import DataError
from backtester.data import CSVDataSource

UTC = timezone.utc


def _write_csv(path: Path, rows: list[tuple[str, float, float, float, float, float]]) -> None:
    lines = ["timestamp,open,high,low,close,volume"]
    for ts, o, h, low_, c, v in rows:
        lines.append(f"{ts},{o},{h},{low_},{c},{v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _generate_rows(n: int, base: datetime) -> list[tuple[str, float, float, float, float, float]]:
    return [
        (
            (base + timedelta(hours=i)).isoformat(),
            100.0 + i,
            101.0 + i,
            99.0 + i,
            100.5 + i,
            1.0,
        )
        for i in range(n)
    ]


# ---------- 기본 동작 -------------------------------------------------------


def test_csv_source_constructor_validates_base_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(DataError, match="does not exist"):
        CSVDataSource(missing)
    file_not_dir = tmp_path / "file.txt"
    file_not_dir.write_text("x")
    with pytest.raises(DataError, match="not a directory"):
        CSVDataSource(file_not_dir)


def test_csv_source_fetch_basic(tmp_path: Path) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    _write_csv(csv_path, _generate_rows(10, base))

    src = CSVDataSource(tmp_path)
    df, gap = src.fetch(
        "BTCUSDT", "1h", start=base, end=base + timedelta(hours=10)
    )
    assert df.height == 10
    assert df["timestamp"][0] == base
    assert df.schema["timestamp"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert df.schema["open"] == pl.Float64
    assert gap.symbol == "BTCUSDT"
    assert gap.timeframe == "1h"
    assert gap.total_missing_bars == 0


def test_csv_source_fetch_filters_range(tmp_path: Path) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    _write_csv(csv_path, _generate_rows(20, base))

    src = CSVDataSource(tmp_path)
    df, _ = src.fetch(
        "BTCUSDT",
        "1h",
        start=base + timedelta(hours=5),
        end=base + timedelta(hours=10),
    )
    # inclusive 양 끝
    assert df.height == 6
    assert df["timestamp"][0] == base + timedelta(hours=5)
    assert df["timestamp"][-1] == base + timedelta(hours=10)


def test_csv_source_symbol_sanitize(tmp_path: Path) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    # BTC/USDT → BTC_USDT 파일명
    csv_path = tmp_path / "BTC_USDT_1h.csv"
    _write_csv(csv_path, _generate_rows(5, base))

    src = CSVDataSource(tmp_path)
    df, _ = src.fetch("BTC/USDT", "1h", start=base, end=base + timedelta(hours=5))
    assert df.height == 5


# ---------- 검증 (DataError) ------------------------------------------------


def test_csv_source_missing_file(tmp_path: Path) -> None:
    src = CSVDataSource(tmp_path)
    with pytest.raises(DataError, match="CSV file not found"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 3, 1, tzinfo=UTC),
            end=datetime(2026, 3, 2, tzinfo=UTC),
        )


def test_csv_source_naive_start_rejected(tmp_path: Path) -> None:
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    _write_csv(csv_path, _generate_rows(3, datetime(2026, 3, 1, tzinfo=UTC)))
    src = CSVDataSource(tmp_path)
    with pytest.raises(DataError, match="timezone-aware"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 3, 1),  # naive
            end=datetime(2026, 3, 2, tzinfo=UTC),
        )


def test_csv_source_start_must_be_before_end(tmp_path: Path) -> None:
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    _write_csv(csv_path, _generate_rows(3, datetime(2026, 3, 1, tzinfo=UTC)))
    src = CSVDataSource(tmp_path)
    t = datetime(2026, 3, 1, tzinfo=UTC)
    with pytest.raises(DataError, match="start must be < end"):
        src.fetch("BTCUSDT", "1h", start=t, end=t)


def test_csv_source_missing_column_rejected(tmp_path: Path) -> None:
    """필수 OHLCV 컬럼이 빠지면 DataError."""
    bad = tmp_path / "BTCUSDT_1h.csv"
    bad.write_text(
        "timestamp,open,high,low,close\n"  # volume missing
        "2026-03-01T00:00:00+00:00,100,101,99,100.5\n",
        encoding="utf-8",
    )
    src = CSVDataSource(tmp_path)
    with pytest.raises(DataError, match="missing required column"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 3, 1, tzinfo=UTC),
            end=datetime(2026, 3, 2, tzinfo=UTC),
        )


def test_csv_source_duplicate_timestamp_rejected(tmp_path: Path) -> None:
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = _generate_rows(3, base)
    rows.append(rows[0])  # 중복
    _write_csv(csv_path, rows)
    src = CSVDataSource(tmp_path)
    with pytest.raises(DataError, match="duplicate"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=base,
            end=base + timedelta(hours=5),
        )


def test_csv_source_unsorted_timestamp_rejected(tmp_path: Path) -> None:
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = _generate_rows(3, base)
    rows[0], rows[1] = rows[1], rows[0]  # swap → unsorted
    _write_csv(csv_path, rows)
    src = CSVDataSource(tmp_path)
    with pytest.raises(DataError, match="not sorted"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=base,
            end=base + timedelta(hours=5),
        )


# ---------- 스키마 (Float64 + UTC Datetime) ---------------------------------


def test_csv_source_returns_correct_schema(tmp_path: Path) -> None:
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_csv(csv_path, _generate_rows(5, base))
    src = CSVDataSource(tmp_path)
    df, _ = src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=5))
    assert df.schema == {
        "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    }


def test_csv_source_gap_detection(tmp_path: Path) -> None:
    """1시간 단위로 정렬된 rows 사이에 한 봉이 빠지면 GapReport에 잡혀야 한다."""
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = _generate_rows(5, base)
    # 인덱스 2를 빼서 갭 만들기 (base + 2h 누락)
    del rows[2]
    _write_csv(csv_path, rows)
    src = CSVDataSource(tmp_path)
    _df, gap = src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=5))
    assert gap.total_missing_bars == 1


# ---------- Engine 통합 (DataSourceConfig type='csv') -----------------------


def test_engine_run_with_csv_data_source(tmp_path: Path) -> None:
    """``DataSourceConfig(type='csv')`` 로 BacktestEngine 이 정상 실행 (PR9 후속 정정)."""
    from decimal import Decimal

    from backtester.core.config import BacktestConfig, DataSourceConfig
    from backtester.core.engine import BacktestEngine
    from backtester.instruments.base import FeeModel, Instrument
    from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

    base = datetime(2026, 3, 1, tzinfo=UTC)
    csv_path = tmp_path / "BTCUSDT_1h.csv"
    _write_csv(csv_path, _generate_rows(60, base))

    instrument = Instrument(
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
    cfg = BacktestConfig(
        run_id="csv_engine_smoke",
        data_source=DataSourceConfig(base_dir=tmp_path, type="csv"),
        instruments=[instrument],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=60),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    result = engine.run()
    assert (result.run_dir / "events.jsonl").exists()
    assert (result.run_dir / "events.parquet").exists()
