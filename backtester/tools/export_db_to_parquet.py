"""SQLite OHLCV → Parquet export 도구.

Bybit_Trading 등 외부 SQLite DB의 OHLCV 테이블을 새 backtester가 소비할 수 있는 표준
Parquet 포맷으로 변환한다. ``ParquetDataSource``의 schema/sort 검증을 통과해야 한다.

출력 파일명 (spec §6.5): ``{output_dir}/{sanitize_symbol(symbol)}_{timeframe}.parquet``

출력 스키마 (spec §3.1):
    timestamp: pl.Datetime("us", time_zone="UTC")
    open / high / low / close / volume: pl.Float64

CLI 사용 예 (Bybit_Trading DB)::

    python tools/export_db_to_parquet.py \\
        --db ../Crypto/Bybit_Trading/db/bybit_data.db \\
        --table ohlcv_1h \\
        --symbol BTCUSDT \\
        --timeframe 1h \\
        --output-dir tests/fixtures \\
        --start 2026-03-01 --end 2026-04-29

``--start``/``--end``는 선택. 생략 시 해당 symbol의 모든 봉을 export.

**Date-only 입력의 의미**: ``YYYY-MM-DD`` 형식만 적으면 ``YYYY-MM-DD T00:00:00+00:00``
으로 해석된다 (UTC tz-aware). 따라서 ``--end 2026-04-29``는 **2026-04-29 00:00 (UTC) inclusive
까지만** export 하며, 해당 날짜 전체를 받으려면 ``--end 2026-04-30`` 또는 ``--end
2026-04-29T23:00:00+00:00``처럼 명시한다.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

# DB 컬럼 -> 표준 OHLCV 컬럼 매핑. Bybit_Trading 스키마(``open_time`` epoch ms) 기본.
DEFAULT_COLUMN_MAP: dict[str, str] = {
    "open_time": "timestamp",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}


def sanitize_symbol(symbol: str) -> str:
    """파일명 안전 심볼 (data/base.py의 sanitize_symbol과 동일 규약)."""
    return symbol.replace("/", "_").replace("\\", "_")


def _detect_timestamp_unit(sample: int) -> str:
    """epoch 정수의 단위(s/ms/us)를 휴리스틱으로 판별.

    1e12 < ms < 1e13 (대략 2001-09 ~ 2286-11)
    1e9  < s  < 1e10
    1e15 < us < 1e16
    """
    if sample > 10**14:
        return "us"
    if sample > 10**11:
        return "ms"
    return "s"


def _epoch_to_utc(values: list[int]) -> list[datetime]:
    if not values:
        return []
    unit = _detect_timestamp_unit(values[0])
    if unit == "ms":
        scale = 1000.0
    elif unit == "us":
        scale = 1_000_000.0
    else:
        scale = 1.0
    return [datetime.fromtimestamp(v / scale, tz=timezone.utc) for v in values]


def export_one(
    *,
    db_path: Path,
    table: str,
    symbol: str,
    timeframe: str,
    output_dir: Path,
    start: datetime | None = None,
    end: datetime | None = None,
    column_map: dict[str, str] | None = None,
) -> Path:
    """SQLite의 OHLCV 테이블에서 ``symbol`` 데이터를 1개 parquet으로 export."""
    cmap = dict(column_map or DEFAULT_COLUMN_MAP)
    ts_db_col = next((k for k, v in cmap.items() if v == "timestamp"), None)
    if ts_db_col is None:
        raise ValueError(
            f"column_map must include a key mapping to 'timestamp', got {cmap!r}"
        )
    needed_db_cols = [
        ts_db_col,
        next(k for k, v in cmap.items() if v == "open"),
        next(k for k, v in cmap.items() if v == "high"),
        next(k for k, v in cmap.items() if v == "low"),
        next(k for k, v in cmap.items() if v == "close"),
        next(k for k, v in cmap.items() if v == "volume"),
    ]

    select_cols = ", ".join(needed_db_cols)
    sql = (
        f"SELECT {select_cols} FROM {table} WHERE symbol = ? "
        f"ORDER BY {ts_db_col} ASC"
    )
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(sql, (symbol,)).fetchall()
    finally:
        conn.close()

    if not rows:
        raise ValueError(f"No rows for symbol={symbol!r} in table={table!r}")

    raw_ts = [int(r[0]) for r in rows]
    timestamps = _epoch_to_utc(raw_ts)
    df = pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
    )

    # ParquetDataSource는 strictly increasing timestamp + duplicate 0건을 요구.
    df = df.unique(subset=["timestamp"]).sort("timestamp")

    if start is not None:
        df = df.filter(pl.col("timestamp") >= start)
    if end is not None:
        df = df.filter(pl.col("timestamp") <= end)

    if df.height == 0:
        raise ValueError(
            f"No rows after applying filters (symbol={symbol}, table={table}, "
            f"start={start}, end={end})"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{sanitize_symbol(symbol)}_{timeframe}.parquet"
    df.write_parquet(target)
    return target


def _parse_iso(value: str) -> datetime:
    """``YYYY-MM-DD`` 또는 ISO8601 datetime 문자열을 UTC tz-aware datetime으로 변환."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime: {value!r} ({e})"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite OHLCV → Parquet export.",
    )
    parser.add_argument("--db", type=Path, required=True, help="SQLite DB path")
    parser.add_argument("--table", required=True, help="OHLCV table name")
    parser.add_argument("--symbol", required=True, help="e.g. BTCUSDT")
    parser.add_argument("--timeframe", required=True, help="e.g. 1h, 4h, 1d")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory (will be created if missing).",
    )
    parser.add_argument(
        "--start",
        type=_parse_iso,
        default=None,
        help=(
            "Optional inclusive start (YYYY-MM-DD or ISO8601, UTC). "
            "Date-only is parsed as 00:00:00 UTC."
        ),
    )
    parser.add_argument(
        "--end",
        type=_parse_iso,
        default=None,
        help=(
            "Optional inclusive end (YYYY-MM-DD or ISO8601, UTC). "
            "Date-only is parsed as 00:00:00 UTC: only the bar at midnight "
            "of that day is included. Use the next day or an explicit time "
            "(e.g. '2026-04-29T23:00:00+00:00') to include the whole day."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        path = export_one(
            db_path=args.db,
            table=args.table,
            symbol=args.symbol,
            timeframe=args.timeframe,
            output_dir=args.output_dir,
            start=args.start,
            end=args.end,
        )
    except (ValueError, sqlite3.Error) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
