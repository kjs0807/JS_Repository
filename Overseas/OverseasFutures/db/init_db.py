"""DB 초기화 모듈.

schema.sql로 테이블을 생성하고 config.products.PRODUCTS에서
14개 상품 마스터를 자동으로 INSERT OR REPLACE한다.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)


def get_db_connection(db_path: str = None) -> sqlite3.Connection:
    """SQLite 커넥션 반환 (row_factory=Row 설정).

    Args:
        db_path: SQLite DB 파일 경로

    Returns:
        sqlite3.Connection 객체 (sqlite3.Row factory 설정됨)
    """
    if db_path is None:
        from config import DB_PATH
        db_path = DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """커넥션 컨텍스트 매니저 (자동 close).

    Args:
        db_path: SQLite DB 파일 경로

    Yields:
        sqlite3.Connection 객체
    """
    conn = get_db_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _load_schema(schema_path: str) -> str:
    """schema.sql 파일 내용을 읽어 반환.

    Args:
        schema_path: schema.sql 파일 경로

    Returns:
        SQL 문자열

    Raises:
        FileNotFoundError: schema_path가 존재하지 않을 경우
    """
    path = Path(schema_path)
    if not path.exists():
        raise FileNotFoundError(f"schema.sql not found: {schema_path}")
    return path.read_text(encoding="utf-8")


def _insert_products(conn: sqlite3.Connection) -> int:
    """config.products.PRODUCTS 딕셔너리에서 14개 상품을 INSERT OR REPLACE.

    Args:
        conn: 활성 SQLite 커넥션

    Returns:
        삽입/갱신된 상품 수
    """
    from config.products import PRODUCTS

    sql = """
        INSERT OR REPLACE INTO products_master (
            symbol, asset_class, exchange, exch_cd,
            name_en, name_kr,
            contract_size, tick_size, tick_value, currency,
            kis_code, kis_code_current,
            expiry_date, margin, point_value,
            is_core
        ) VALUES (
            :symbol, :asset_class, :exchange, :exch_cd,
            :name_en, :name_kr,
            :contract_size, :tick_size, :tick_value, :currency,
            :kis_code, :kis_code_current,
            :expiry_date, :margin, :point_value,
            :is_core
        )
    """

    rows = []
    for product in PRODUCTS.values():
        rows.append({
            "symbol":          product.symbol,
            "asset_class":     product.asset_class,
            "exchange":        product.exchange,
            "exch_cd":         product.exch_cd,
            "name_en":         product.name_en,
            "name_kr":         product.name_kr,
            "contract_size":   product.contract_size,
            "tick_size":       product.tick_size,
            "tick_value":      product.tick_value,
            "currency":        product.currency,
            "kis_code":        product.kis_code,
            "kis_code_current": product.kis_code,  # 초기값: 현재 근월물 코드와 동일
            "expiry_date":     product.expiry_date,
            "margin":          product.margin,
            "point_value":     product.point_value,
            "is_core":         1,
        })

    conn.executemany(sql, rows)
    return len(rows)


def initialize_database(
    db_path: Optional[str] = None,
    schema_path: Optional[str] = None,
    force_recreate: bool = False,
) -> None:
    """DB를 초기화하고 상품 마스터를 적재한다.

    1. DB 파일 디렉토리 생성 (없으면)
    2. schema.sql 실행으로 테이블/인덱스 생성
    3. PRODUCTS에서 14개 상품 INSERT OR REPLACE

    Args:
        db_path: SQLite DB 파일 경로 (예: "db/futures.db")
        schema_path: schema.sql 경로.
            None이면 db_path와 같은 디렉토리의 schema.sql을 사용.
        force_recreate: True이면 기존 DB 파일을 삭제 후 재생성.

    Raises:
        FileNotFoundError: schema.sql을 찾을 수 없을 때
        sqlite3.Error: DB 작업 실패 시
    """
    if db_path is None:
        from config import DB_PATH
        db_path = DB_PATH

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    if force_recreate and db_file.exists():
        db_file.unlink()
        logger.info("기존 DB 삭제: %s", db_path)

    # schema_path 기본값: db_path와 같은 폴더의 schema.sql
    if schema_path is None:
        schema_path = str(db_file.parent / "schema.sql")

    schema_sql = _load_schema(schema_path)

    with get_db_connection(db_path) as conn:
        # 테이블 및 인덱스 생성
        conn.executescript(schema_sql)
        logger.info("스키마 적용 완료: %s", schema_path)

        # 상품 마스터 삽입
        count = _insert_products(conn)
        conn.commit()
        logger.info("상품 마스터 %d개 INSERT OR REPLACE 완료", count)

    logger.info("DB 초기화 완료: %s", db_path)


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db = sys.argv[1] if len(sys.argv) > 1 else "db/futures.db"
    force = "--force" in sys.argv

    initialize_database(db_path=db, force_recreate=force)
