"""SQLite DB → Parquet export 스크립트 (PR 8 skeleton).

집 환경에서 backtester/data_cache/ 등으로 OHLCV parquet을 생성하기 위한 CLI 골격.
실제 DB 연결 및 변환 로직은 집 환경(스키마 확정 후)에서 구현한다 — 본 파일은 시그니처와
구조만 제공.

출력 파일명 규약 (spec §6.5): `{output_dir}/{sanitize_symbol(symbol)}_{timeframe}.parquet`
출력 스키마 (spec §3.1):
    timestamp: pl.Datetime("us", time_zone="UTC")
    open / high / low / close / volume: pl.Float64

CLI 사용 예 (구현 완료 후):
    python scripts/export_db_to_parquet.py \\
        --db /path/to/ohlcv.sqlite \\
        --table ohlcv_1h \\
        --symbol BTCUSDT \\
        --timeframe 1h \\
        --output-dir backtester/data_cache/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# polars import는 실제 구현 시 활성. skeleton 단계에서는 미사용 import 경고를 피하기 위해
# 함수 본문 안에서만 사용.

# 컬럼 매핑은 DB 스키마에 따라 다를 수 있어 매개변수화 가능. skeleton 기본값.
DEFAULT_COLUMN_MAP: dict[str, str] = {
    "ts": "timestamp",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}


def sanitize_symbol(symbol: str) -> str:
    """파일명 안전 심볼 (data/base.py의 sanitize_symbol과 동일 규약)."""
    return symbol.replace("/", "_").replace("\\", "_")


def export_one(
    *,
    db_path: Path,
    table: str,
    symbol: str,
    timeframe: str,
    output_dir: Path,
    column_map: dict[str, str] | None = None,
) -> Path:
    """SQLite의 OHLCV 테이블을 1개 parquet으로 export. (skeleton)

    구현 시 단계 (집 환경):
    1. sqlite3 또는 polars.read_database로 `db_path` 연결, `table`에서 SELECT.
    2. WHERE symbol = ? AND timeframe = ? (테이블 스키마 따라).
    3. `column_map`을 사용해 컬럼명을 OHLCV 표준으로 정규화.
    4. timestamp를 `pl.Datetime("us", time_zone="UTC")`로 캐스트
       (DB에 저장된 원본이 epoch millis인지 ISO 문자열인지 확인 후 변환 로직 분기).
    5. open/high/low/close/volume을 `pl.Float64`로 캐스트.
    6. timestamp 오름차순 정렬 + 중복 제거 검증 (data/parquet_source.py가 strictly
       increasing을 강제하므로 export 단계에서 보장).
    7. `{output_dir}/{sanitize_symbol(symbol)}_{timeframe}.parquet`로 write_parquet.
    """
    del db_path, table, column_map  # skeleton 인자 — 구현 시 활용
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{sanitize_symbol(symbol)}_{timeframe}.parquet"
    raise NotImplementedError(
        "export_db_to_parquet.export_one is a skeleton. "
        "Implement DB read + schema cast in home environment "
        f"(target path will be: {target})"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite OHLCV → Parquet export (Phase 1 skeleton).",
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
        )
    except NotImplementedError as e:
        print(f"[skeleton] {e}", file=sys.stderr)
        return 2
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
