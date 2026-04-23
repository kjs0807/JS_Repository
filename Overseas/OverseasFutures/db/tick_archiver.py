"""틱 데이터 아카이브 — 오래된 틱을 Parquet로 백업 후 DB에서 삭제.

사용법:
    from db.tick_archiver import TickArchiver
    archiver = TickArchiver(db_path="db/futures.db", archive_dir="db/archive")
    archiver.archive_old_ticks(keep_days=3)  # 3일 초과 틱을 아카이브
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import pyarrow  # noqa: F401 — Parquet 엔진
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

logger = logging.getLogger(__name__)


class TickArchiver:
    """틱 데이터를 Parquet로 아카이브하고 DB에서 정리."""

    def __init__(self, db_path: str = "db/futures.db", archive_dir: str = "db/archive") -> None:
        self.db_path = db_path
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_old_ticks(self, keep_days: int = 3) -> dict:
        """keep_days일 이전의 틱 데이터를 Parquet로 아카이브 후 DB에서 삭제.

        pyarrow 미설치 시 CSV로 fallback.

        Returns:
            {"trade_ticks": {"archived": N, "file": "path"}, "realtime_ticks": {...}}
        """
        if not HAS_PYARROW:
            logger.info("pyarrow 미설치 → CSV 아카이브로 fallback")

        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        result = {}

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            for table in ["trade_ticks", "realtime_ticks"]:
                # 1. 아카이브 대상 건수 확인
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE timestamp < ?", (cutoff,)
                ).fetchone()[0]

                if count == 0:
                    result[table] = {"archived": 0, "file": None}
                    logger.info(f"[{table}] 아카이브 대상 없음 (cutoff={cutoff})")
                    continue

                # 2. Export (Parquet 우선, 미설치 시 CSV fallback)
                df = pd.read_sql_query(
                    f"SELECT * FROM {table} WHERE timestamp < ?",
                    conn, params=(cutoff,)
                )

                today = datetime.now().strftime("%Y%m%d")
                cutoff_compact = cutoff.replace('-', '')

                if HAS_PYARROW:
                    archive_path = self.archive_dir / f"{table}_{today}_before_{cutoff_compact}.parquet"
                    if archive_path.exists():
                        existing = pd.read_parquet(archive_path)
                        df = pd.concat([existing, df]).drop_duplicates(subset=["symbol", "timestamp"])
                    df.to_parquet(archive_path, index=False, compression="snappy")
                else:
                    archive_path = self.archive_dir / f"{table}_{today}_before_{cutoff_compact}.csv.gz"
                    if archive_path.exists():
                        existing = pd.read_csv(archive_path)
                        df = pd.concat([existing, df]).drop_duplicates(subset=["symbol", "timestamp"])
                    df.to_csv(archive_path, index=False, compression="gzip")

                logger.info(f"[{table}] {count}건 → {archive_path}")

                # 3. DB에서 삭제
                conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
                conn.commit()

                # 4. VACUUM으로 파일 크기 회수 (WAL 체크포인트 포함)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

                result[table] = {"archived": count, "file": str(archive_path)}
                logger.info(f"[{table}] DB에서 {count}건 삭제 완료")

        except Exception as e:
            logger.error(f"틱 아카이브 실패: {e}")
            conn.rollback()
        finally:
            conn.close()

        return result

    def list_archives(self) -> list[dict]:
        """아카이브된 파일 목록 (Parquet + CSV)."""
        files = []
        for pattern in ("*.parquet", "*.csv.gz"):
            for f in sorted(self.archive_dir.glob(pattern)):
                size_mb = f.stat().st_size / (1024 * 1024)
                files.append({"file": f.name, "size_mb": round(size_mb, 2), "path": str(f)})
        return files

    def load_archive(self, path: str) -> pd.DataFrame:
        """아카이브 파일을 DataFrame으로 로드 (Parquet/CSV 자동 감지)."""
        if path.endswith(".parquet"):
            return pd.read_parquet(path)
        elif path.endswith(".csv.gz") or path.endswith(".csv"):
            return pd.read_csv(path)
        else:
            raise ValueError(f"지원하지 않는 파일 형식: {path}")
