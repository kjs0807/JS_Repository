"""PR 2 데이터 레이어 테스트 (spec §20 PR 2 acceptance)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from backtester.core.errors import DataError
from backtester.data import (
    OHLCV_SCHEMA,
    GapReport,
    ParquetDataSource,
    parse_timeframe,
    sanitize_symbol,
    validate_ohlcv_schema,
)
from backtester.data.base import compute_gap_report

UTC = timezone.utc  # Python 3.10 호환 (datetime.UTC는 3.11+)

# ---------- helpers ---------------------------------------------------------


def _make_ohlcv_df(timestamps: list[datetime]) -> pl.DataFrame:
    n = len(timestamps)
    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": [50000.0] * n,
            "high": [50100.0] * n,
            "low": [49900.0] * n,
            "close": [50050.0] * n,
            "volume": [1.0] * n,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))


def _hourly(start: datetime, hours: int) -> list[datetime]:
    return [start + timedelta(hours=i) for i in range(hours)]


# ---------- parse_timeframe -------------------------------------------------


@pytest.mark.parametrize(
    ("tf", "expected"),
    [
        ("1m", timedelta(minutes=1)),
        ("15m", timedelta(minutes=15)),
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
        ("1d", timedelta(days=1)),
        ("1w", timedelta(weeks=1)),
        ("30s", timedelta(seconds=30)),
    ],
)
def test_parse_timeframe_valid(tf: str, expected: timedelta) -> None:
    assert parse_timeframe(tf) == expected


@pytest.mark.parametrize("bad", ["", "1", "h", "1y", "abc", "0h", "-1h", "1.5h"])
def test_parse_timeframe_invalid(bad: str) -> None:
    with pytest.raises(DataError):
        parse_timeframe(bad)


# ---------- sanitize_symbol -------------------------------------------------


def test_sanitize_symbol() -> None:
    assert sanitize_symbol("BTCUSDT") == "BTCUSDT"
    assert sanitize_symbol("BTC/USDT") == "BTC_USDT"
    assert sanitize_symbol("BTC\\USDT") == "BTC_USDT"


# ---------- validate_ohlcv_schema -------------------------------------------


def test_validate_ohlcv_schema_passes() -> None:
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 5))
    validate_ohlcv_schema(df)  # no raise


def test_validate_ohlcv_schema_missing_column() -> None:
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 5)).drop("volume")
    with pytest.raises(DataError, match="missing column"):
        validate_ohlcv_schema(df)


def test_validate_ohlcv_schema_wrong_dtype() -> None:
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 5)).with_columns(
        pl.col("close").cast(pl.Int64)
    )
    with pytest.raises(DataError, match="close"):
        validate_ohlcv_schema(df)


def test_validate_ohlcv_schema_naive_timestamp_rejected() -> None:
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 5)).with_columns(
        pl.col("timestamp").dt.replace_time_zone(None)
    )
    with pytest.raises(DataError, match="timestamp"):
        validate_ohlcv_schema(df)


@pytest.mark.parametrize("col", ["open", "high", "low", "close", "volume"])
def test_validate_ohlcv_schema_rejects_null_in_numeric_column(col: str) -> None:
    """OHLCV 숫자 컬럼에 null이 있으면 거부 (Clock/BarsView/Ledger 보호)."""
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 5)).with_columns(
        pl.when(pl.int_range(pl.len()) == 2)
        .then(None)
        .otherwise(pl.col(col))
        .alias(col)
    )
    with pytest.raises(DataError, match=f"{col}.*null"):
        validate_ohlcv_schema(df)


def test_validate_ohlcv_schema_rejects_null_timestamp() -> None:
    """timestamp에 null이 있으면 거부."""
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 5)).with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(None)
        .otherwise(pl.col("timestamp"))
        .alias("timestamp")
    )
    with pytest.raises(DataError, match="timestamp.*null"):
        validate_ohlcv_schema(df)


# ---------- GapReport -------------------------------------------------------


def test_gap_report_no_gaps() -> None:
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 10))
    rpt = compute_gap_report(df, "BTCUSDT", "1h")
    assert rpt.gaps == []
    assert rpt.total_missing_bars == 0
    assert not rpt.is_significant()


def test_gap_report_detects_single_gap() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 13:00 14:00 [missing 15:00, 16:00] 17:00
    timestamps = [base, base + timedelta(hours=1), base + timedelta(hours=4)]
    df = _make_ohlcv_df(timestamps)
    rpt = compute_gap_report(df, "BTCUSDT", "1h")
    assert len(rpt.gaps) == 1
    assert rpt.total_missing_bars == 2


def test_gap_report_is_significant_threshold() -> None:
    rpt = GapReport(
        symbol="X",
        timeframe="1h",
        expected_interval=timedelta(hours=1),
        gaps=[],
        total_missing_bars=15,
    )
    assert rpt.is_significant(threshold=10)
    assert not rpt.is_significant(threshold=20)


# ---------- ParquetDataSource -----------------------------------------------


@pytest.fixture()
def parquet_dir(tmp_path: Path) -> Path:
    """샘플 BTCUSDT 1h parquet (10시간)을 tmp_path에 작성."""
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 10))
    df.write_parquet(tmp_path / "BTCUSDT_1h.parquet")
    return tmp_path


def test_parquet_source_init_invalid_dir(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="does not exist"):
        ParquetDataSource(tmp_path / "nonexistent")


def test_parquet_source_init_not_a_dir(tmp_path: Path) -> None:
    file_path = tmp_path / "x.txt"
    file_path.write_text("hello")
    with pytest.raises(DataError, match="not a directory"):
        ParquetDataSource(file_path)


def test_parquet_source_fetch_returns_df_and_report(parquet_dir: Path) -> None:
    src = ParquetDataSource(parquet_dir)
    df, rpt = src.fetch(
        "BTCUSDT",
        "1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 9, tzinfo=UTC),
    )
    assert df.height == 10
    validate_ohlcv_schema(df)
    assert isinstance(rpt, GapReport)
    assert rpt.symbol == "BTCUSDT"
    assert rpt.timeframe == "1h"
    assert rpt.total_missing_bars == 0


def test_parquet_source_fetch_filters_range(parquet_dir: Path) -> None:
    src = ParquetDataSource(parquet_dir)
    df, _ = src.fetch(
        "BTCUSDT",
        "1h",
        start=datetime(2026, 1, 1, 3, tzinfo=UTC),
        end=datetime(2026, 1, 1, 6, tzinfo=UTC),
    )
    # 3,4,5,6시 = 4개 (양 끝 포함)
    assert df.height == 4
    first_ts = df["timestamp"][0]
    last_ts = df["timestamp"][-1]
    assert first_ts == datetime(2026, 1, 1, 3, tzinfo=UTC)
    assert last_ts == datetime(2026, 1, 1, 6, tzinfo=UTC)


def test_parquet_source_fetch_missing_file(parquet_dir: Path) -> None:
    src = ParquetDataSource(parquet_dir)
    with pytest.raises(DataError, match="Parquet file not found"):
        src.fetch(
            "ETHUSDT",
            "1h",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, 9, tzinfo=UTC),
        )


def test_parquet_source_fetch_invalid_range(parquet_dir: Path) -> None:
    src = ParquetDataSource(parquet_dir)
    with pytest.raises(DataError, match="start must be"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 1, 1, 5, tzinfo=UTC),
            end=datetime(2026, 1, 1, 3, tzinfo=UTC),
        )


def test_parquet_source_fetch_unsorted_raises(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 의도적으로 역순
    df = _make_ohlcv_df([base + timedelta(hours=2), base + timedelta(hours=1), base])
    df.write_parquet(tmp_path / "BTCUSDT_1h.parquet")
    src = ParquetDataSource(tmp_path)
    with pytest.raises(DataError, match="not sorted"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=base,
            end=base + timedelta(hours=10),
        )


def test_parquet_source_fetch_duplicates_raise(tmp_path: Path) -> None:
    """중복 timestamp는 strictly increasing 위반 → DataError."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = _make_ohlcv_df([base, base + timedelta(hours=1), base + timedelta(hours=1)])
    df.write_parquet(tmp_path / "BTCUSDT_1h.parquet")
    src = ParquetDataSource(tmp_path)
    with pytest.raises(DataError, match="duplicates"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=base,
            end=base + timedelta(hours=5),
        )


def test_parquet_source_fetch_rejects_naive_start(parquet_dir: Path) -> None:
    src = ParquetDataSource(parquet_dir)
    with pytest.raises(DataError, match="start must be timezone-aware"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 1, 1),  # naive
            end=datetime(2026, 1, 1, 9, tzinfo=UTC),
        )


def test_parquet_source_fetch_rejects_naive_end(parquet_dir: Path) -> None:
    src = ParquetDataSource(parquet_dir)
    with pytest.raises(DataError, match="end must be timezone-aware"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, 9),  # naive
        )


def test_parquet_source_fetch_rejects_non_utc(parquet_dir: Path) -> None:
    """KST(UTC+9) 등 비-UTC tz는 거부."""
    kst = timezone(timedelta(hours=9))
    src = ParquetDataSource(parquet_dir)
    with pytest.raises(DataError, match="must be UTC"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 1, 1, 9, tzinfo=kst),
            end=datetime(2026, 1, 1, 18, tzinfo=kst),
        )


def test_ohlcv_schema_constant_matches() -> None:
    """모듈 상수와 실제 fixture 데이터의 dtype이 정확히 같아야 한다."""
    df = _make_ohlcv_df(_hourly(datetime(2026, 1, 1, tzinfo=UTC), 3))
    for col, expected in OHLCV_SCHEMA.items():
        assert df.schema[col] == expected
