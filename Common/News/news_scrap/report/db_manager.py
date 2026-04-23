"""
뉴스 DB 관리자
==============
SQLite CRUD 전담. articles / market_cache / indicators_cache 테이블 관리.
"""

import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class NewsDB:
    """뉴스 DB 관리 클래스. db/news.db에 연결."""

    def __init__(self, db_path: str = "db/news.db") -> None:
        """SQLite 연결 + 테이블 자동 생성.

        Args:
            db_path: DB 파일 경로 (news_scrap/ 기준 상대 경로).
        """
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._migrate_excluded_column()

    # ── 테이블 생성 ──

    def _create_tables(self) -> None:
        """articles, market_cache, indicators_cache 3개 테이블 생성."""
        cur = self.conn.cursor()

        cur.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                source TEXT NOT NULL,
                lang TEXT DEFAULT 'ko',
                published TIMESTAMP,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category TEXT,
                importance TEXT DEFAULT 'UNSCORED',
                z_score REAL,
                matched_keywords TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_articles_published
                ON articles(published);
            CREATE INDEX IF NOT EXISTS idx_articles_category
                ON articles(category);
            CREATE INDEX IF NOT EXISTS idx_articles_importance
                ON articles(importance);
            CREATE INDEX IF NOT EXISTS idx_articles_source
                ON articles(source);

            -- 수동 제외 플래그 (기존 테이블에 컬럼 추가)
            -- SQLite는 ALTER TABLE ADD COLUMN IF NOT EXISTS 미지원 → 별도 처리

            CREATE TABLE IF NOT EXISTS market_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                date DATE NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_market_ticker_date
                ON market_cache(ticker, date);
            CREATE INDEX IF NOT EXISTS idx_market_date
                ON market_cache(date);

            CREATE TABLE IF NOT EXISTS indicators_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                series_id TEXT NOT NULL,
                date DATE NOT NULL,
                value REAL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_indicators_series_date
                ON indicators_cache(provider, series_id, date);
        """)
        self.conn.commit()

    def _migrate_excluded_column(self) -> None:
        """articles 테이블에 excluded 컬럼 추가 (마이그레이션)."""
        try:
            cur = self.conn.execute("PRAGMA table_info(articles)")
            columns = [row["name"] for row in cur.fetchall()]
            if "excluded" not in columns:
                self.conn.execute(
                    "ALTER TABLE articles ADD COLUMN excluded INTEGER DEFAULT 0"
                )
                self.conn.commit()
                logger.info("articles 테이블에 excluded 컬럼 추가 완료")
        except Exception as e:
            logger.error(f"excluded 컬럼 마이그레이션 실패: {e}")

    # ── 기사 CRUD ──

    @staticmethod
    def _prepare_article_row(article: dict[str, Any]) -> tuple:
        """기사 dict를 DB INSERT용 tuple로 변환.

        category/matched_keywords는 JSON 배열로, published는 ISO 문자열로 직렬화.

        Args:
            article: 기사 dict.

        Returns:
            (url, title, description, source, lang, published, category, matched_keywords) tuple.
        """
        category = article.get("category")
        if isinstance(category, list):
            category = json.dumps(category, ensure_ascii=False)
        elif category is None:
            category = "[]"

        matched_kw = article.get("matched_keywords")
        if isinstance(matched_kw, list):
            matched_kw = json.dumps(matched_kw, ensure_ascii=False)
        elif matched_kw is None:
            matched_kw = "[]"

        published = article.get("published")
        if isinstance(published, datetime):
            published = published.isoformat()
        elif isinstance(published, date):
            published = datetime.combine(published, datetime.min.time()).isoformat()

        return (
            article.get("url", ""),
            article.get("title", ""),
            article.get("description", ""),
            article.get("source", ""),
            article.get("lang", "ko"),
            published,
            category,
            matched_kw,
        )

    def insert_article(self, article: dict[str, Any]) -> bool:
        """단일 기사 INSERT OR IGNORE (URL UNIQUE).

        Args:
            article: 기사 dict (url, title, description, source, lang,
                     published, category, matched_keywords).

        Returns:
            삽입 성공 여부.
        """
        try:
            row = self._prepare_article_row(article)
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO articles
                   (url, title, description, source, lang, published,
                    category, matched_keywords)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
            self.conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.error(f"기사 삽입 실패: {e}")
            return False

    def insert_articles(self, articles: list[dict[str, Any]]) -> int:
        """복수 기사 배치 INSERT (단일 트랜잭션).

        Args:
            articles: 기사 dict 리스트.

        Returns:
            실제 삽입된 건수.
        """
        if not articles:
            return 0

        rows: list[tuple] = []
        for article in articles:
            rows.append(self._prepare_article_row(article))

        try:
            cur = self.conn.executemany(
                """INSERT OR IGNORE INTO articles
                   (url, title, description, source, lang, published,
                    category, matched_keywords)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.conn.commit()
            return cur.rowcount
        except Exception as e:
            logger.error(f"배치 기사 삽입 실패: {e}")
            return 0

    def get_articles(
        self,
        start_date: datetime,
        end_date: datetime,
        category: str | None = None,
        importance: str | None = None,
        source: str | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """기간 + 필터 조건으로 기사 조회.

        Args:
            start_date: 시작 일시.
            end_date: 종료 일시.
            category: 카테고리 필터 (JSON 배열 내 검색).
            importance: 중요도 필터.
            source: 출처 필터.
            limit: 최대 건수 (0이면 제한 없음).

        Returns:
            기사 dict 리스트 (published 역순 정렬).
        """
        query = "SELECT * FROM articles WHERE published >= ? AND published <= ? AND COALESCE(excluded, 0) = 0"
        params: list[Any] = [start_date.isoformat(), end_date.isoformat()]

        if category:
            query += (" AND EXISTS"
                       " (SELECT 1 FROM json_each(category) WHERE value = ?)")
            params.append(category)

        if importance:
            query += " AND importance = ?"
            params.append(importance)

        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY published DESC"

        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        try:
            cur = self.conn.execute(query, params)
            rows = cur.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                # JSON 배열 복원
                if d.get("category"):
                    try:
                        d["category"] = json.loads(d["category"])
                    except (json.JSONDecodeError, TypeError):
                        d["category"] = []
                else:
                    d["category"] = []

                if d.get("matched_keywords"):
                    try:
                        d["matched_keywords"] = json.loads(d["matched_keywords"])
                    except (json.JSONDecodeError, TypeError):
                        d["matched_keywords"] = []
                else:
                    d["matched_keywords"] = []

                # published 문자열 → datetime
                if d.get("published") and isinstance(d["published"], str):
                    try:
                        d["published"] = datetime.fromisoformat(d["published"])
                    except ValueError:
                        pass

                results.append(d)
            return results
        except Exception as e:
            logger.error(f"기사 조회 실패: {e}")
            return []

    def get_article_count_by_date(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, int]:
        """날짜별 기사 건수 반환.

        Args:
            start_date: 시작 일시.
            end_date: 종료 일시.

        Returns:
            {"2026-03-01": 45, "2026-03-02": 12, ...}
        """
        try:
            cur = self.conn.execute(
                """SELECT DATE(published) as dt, COUNT(*) as cnt
                   FROM articles
                   WHERE published >= ? AND published <= ?
                   GROUP BY DATE(published)
                   ORDER BY dt""",
                (start_date.isoformat(), end_date.isoformat()),
            )
            return {row["dt"]: row["cnt"] for row in cur.fetchall()}
        except Exception as e:
            logger.error(f"날짜별 기사 건수 조회 실패: {e}")
            return {}

    def update_importance(
        self,
        article_id: int,
        importance: str,
        z_score: float,
        z_score_details: dict[str, float] | None = None,
    ) -> None:
        """기사의 중요도/Z-score 업데이트.

        Args:
            article_id: 기사 ID.
            importance: "HIGH" / "MEDIUM" / "LOW" / "UNSCORED".
            z_score: 대표 Z-score 값.
            z_score_details: {ticker: z_score_value} 상세.
        """
        try:
            self.conn.execute(
                "UPDATE articles SET importance = ?, z_score = ? WHERE id = ?",
                (importance, z_score, article_id),
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"중요도 업데이트 실패 (id={article_id}): {e}")

    # ── 시장 데이터 캐시 ──

    def cache_market_data(self, ticker: str, df: pd.DataFrame) -> int:
        """yfinance 가격 데이터를 market_cache에 캐시.

        Args:
            ticker: 종목 티커.
            df: DataFrame (index=date, columns=[open, high, low, close, volume]).

        Returns:
            저장된 행 수.
        """
        if df is None or df.empty:
            return 0

        rows: list[tuple] = []
        for idx, row in df.iterrows():
            dt = idx
            if isinstance(dt, pd.Timestamp):
                dt = dt.date()
            elif isinstance(dt, datetime):
                dt = dt.date()

            rows.append((
                ticker,
                str(dt),
                float(row.get("open", 0) or 0),
                float(row.get("high", 0) or 0),
                float(row.get("low", 0) or 0),
                float(row.get("close", 0) or 0),
                int(row.get("volume", 0) or 0),
            ))

        try:
            self.conn.executemany(
                """INSERT OR REPLACE INTO market_cache
                   (ticker, date, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"시장 데이터 캐시 저장 실패 [{ticker}]: {e}")
        return len(rows)

    def get_cached_market_data(
        self, ticker: str, start_date: date, end_date: date,
        ttl_hours: int = 24
    ) -> pd.DataFrame | None:
        """market_cache에서 캐시된 가격 데이터 조회.

        과거 데이터는 영구 유효, 당일 데이터는 TTL 적용.

        Args:
            ticker: 종목 티커.
            start_date: 시작 날짜.
            end_date: 종료 날짜.
            ttl_hours: 당일 데이터 캐시 유효 시간 (기본 24시간).

        Returns:
            DataFrame 또는 캐시 미스 시 None.
        """
        try:
            cutoff = (datetime.now() - timedelta(hours=ttl_hours)).isoformat()
            cur = self.conn.execute(
                """SELECT date, open, high, low, close, volume
                   FROM market_cache
                   WHERE ticker = ? AND date >= ? AND date <= ?
                     AND (date < DATE('now') OR fetched_at >= ?)
                   ORDER BY date""",
                (ticker, str(start_date), str(end_date), cutoff),
            )
            rows = cur.fetchall()
            if not rows:
                return None

            data = []
            for row in rows:
                data.append(
                    {
                        "date": row["date"],
                        "open": row["open"],
                        "high": row["high"],
                        "low": row["low"],
                        "close": row["close"],
                        "volume": row["volume"],
                    }
                )
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            return df
        except Exception as e:
            logger.error(f"시장 데이터 캐시 조회 실패 [{ticker}]: {e}")
            return None

    # ── 경제지표 캐시 ──

    def cache_indicator(
        self, provider: str, series_id: str, dt: date, value: float
    ) -> None:
        """ECOS/FRED 경제지표 캐시 저장.

        Args:
            provider: "ECOS" 또는 "FRED".
            series_id: 지표 코드/시리즈 ID.
            dt: 날짜.
            value: 지표 값.
        """
        try:
            self.conn.execute(
                """INSERT OR REPLACE INTO indicators_cache
                   (provider, series_id, date, value, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (provider, series_id, str(dt), value, datetime.now().isoformat()),
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"지표 캐시 저장 실패 [{provider}/{series_id}]: {e}")

    def get_cached_indicator(
        self, provider: str, series_id: str, ttl_hours: int = 24
    ) -> pd.Series | None:
        """indicators_cache에서 캐시된 지표 조회.

        Args:
            provider: "ECOS" 또는 "FRED".
            series_id: 지표 코드/시리즈 ID.
            ttl_hours: 캐시 유효 시간 (기본 24시간).

        Returns:
            pd.Series(index=date, values=float) 또는 TTL 초과/미스 시 None.
        """
        try:
            cutoff = (datetime.now() - timedelta(hours=ttl_hours)).isoformat()
            cur = self.conn.execute(
                """SELECT date, value FROM indicators_cache
                   WHERE provider = ? AND series_id = ? AND fetched_at >= ?
                   ORDER BY date""",
                (provider, series_id, cutoff),
            )
            rows = cur.fetchall()
            if not rows:
                return None

            dates = []
            values = []
            for row in rows:
                dates.append(pd.to_datetime(row["date"]))
                values.append(row["value"])

            return pd.Series(data=values, index=dates, name=series_id)
        except Exception as e:
            logger.error(f"지표 캐시 조회 실패 [{provider}/{series_id}]: {e}")
            return None

    # ── 종료 ──

    def set_article_excluded(self, article_id: int, excluded: bool) -> None:
        """기사의 제외 상태를 설정.

        Args:
            article_id: 기사 ID.
            excluded: True이면 제외, False이면 복원.
        """
        flag = 1 if excluded else 0
        action = "제외" if excluded else "복원"
        try:
            self.conn.execute(
                "UPDATE articles SET excluded = ? WHERE id = ?",
                (flag, article_id),
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"기사 {action} 실패 (id={article_id}): {e}")

    def exclude_article(self, article_id: int) -> None:
        """기사를 수동 제외 처리 (보고서에서 숨김).

        Args:
            article_id: 기사 ID.
        """
        self.set_article_excluded(article_id, True)

    def restore_article(self, article_id: int) -> None:
        """제외된 기사를 복원.

        Args:
            article_id: 기사 ID.
        """
        self.set_article_excluded(article_id, False)

    def close(self) -> None:
        """DB 연결 종료."""
        try:
            self.conn.close()
        except Exception as e:
            logger.debug(f"DB 연결 종료 중 오류: {e}")
