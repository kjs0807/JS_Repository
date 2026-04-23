"""일봉 OHLCV 수집기.

DESIGN.md 3.4절 수정된 API 파라미터 사용:
- exchange 파라미터 필수 (EXCH_CD)
- 응답 필드: output2[].data_date, open_price, high_price, low_price, last_price, vol
- UPSERT into ohlcv_daily table
"""

import logging
from datetime import date, timedelta
from typing import Optional

from api.rest_client import KISRestClient, KISAPIError
from config import DB_PATH
from config.products import PRODUCTS
from db.init_db import get_db_connection

logger = logging.getLogger(__name__)


class DailyCollector:
    """해외선물 일봉 OHLCV 수집기.

    KIS API get_futures_daily_ohlcv()를 호출하여 ohlcv_daily 테이블에
    UPSERT (INSERT OR REPLACE)한다. exchange 파라미터는 필수.

    Attributes:
        client: KIS REST 클라이언트
        db_path: SQLite DB 경로
    """

    def __init__(self, client: KISRestClient, db_path: str = None) -> None:
        """
        Args:
            client: KISRestClient 인스턴스
            db_path: SQLite DB 경로. None이면 config.DB_PATH 사용.
        """
        self.client = client
        self.db_path = db_path or DB_PATH

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────

    def _parse_record(self, record: dict) -> Optional[dict]:
        """KIS 응답 record → ohlcv_daily 삽입용 dict 변환.

        DESIGN.md 3.4절 응답 필드:
          data_date, open_price, high_price, low_price, last_price, vol

        레거시 필드명(date_time, stck_oprc 등)도 시도하여 호환성 유지.

        Args:
            record: output2 배열의 원소 dict

        Returns:
            {date, open, high, low, close, volume} dict.
            파싱 실패 또는 날짜 누락 시 None.
        """
        if not record:
            return None

        # 날짜 — 신규 필드명 우선, 레거시 fallback
        date_val = (
            record.get("data_date")
            or record.get("date_time")
            or record.get("stck_bsop_date")
            or ""
        )
        if not date_val:
            return None
        date_val = date_val[:8]  # YYYYMMDD만 취득

        def _to_float(val: Optional[str]) -> Optional[float]:
            if val is None:
                return None
            try:
                return float(str(val).replace(",", ""))
            except (ValueError, TypeError):
                return None

        def _to_int(val: Optional[str]) -> Optional[int]:
            if val is None:
                return None
            try:
                return int(str(val).replace(",", ""))
            except (ValueError, TypeError):
                return None

        # 가격 — 신규 필드명 우선, 레거시 fallback
        open_val = _to_float(
            record.get("open_price") or record.get("stck_oprc")
        )
        high_val = _to_float(
            record.get("high_price") or record.get("stck_hgpr")
        )
        low_val = _to_float(
            record.get("low_price") or record.get("stck_lwpr")
        )
        close_val = _to_float(
            record.get("last_price") or record.get("stck_clpr") or record.get("prdy_clpr")
        )
        volume_val = _to_int(
            record.get("vol") or record.get("acml_vol") or record.get("last_qntt")
        )

        return {
            "date": date_val,
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "volume": volume_val,
        }

    def _upsert_bars(self, symbol: str, bars: list) -> int:
        """파싱된 봉 리스트를 ohlcv_daily에 UPSERT.

        Args:
            symbol: 상품 심볼 (예: "VG")
            bars: _parse_record() 결과 dict 리스트

        Returns:
            삽입/갱신된 행 수
        """
        if not bars:
            return 0

        sql = """
            INSERT OR REPLACE INTO ohlcv_daily
                (symbol, date, open, high, low, close, volume)
            VALUES
                (:symbol, :date, :open, :high, :low, :close, :volume)
        """
        rows = [dict(symbol=symbol, **b) for b in bars if b]
        if not rows:
            return 0

        conn = get_db_connection(self.db_path)
        try:
            conn.executemany(sql, rows)
            conn.commit()
        finally:
            conn.close()

        return len(rows)

    # ── 공개 메서드 ──────────────────────────────────────────────────────

    def collect_symbol(
        self,
        symbol: str,
        kis_code: str,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> int:
        """단일 종목 일봉 수집.

        Args:
            symbol: 루트 심볼 (예: "VG") — DB 저장 키
            kis_code: KIS 종목코드 (예: "VGM26") — API 호출 키
            exchange: 거래소 코드 (예: "EUREX") — API 필수 파라미터
            start_date: 조회 시작일 YYYYMMDD
            end_date: 조회 종료일 YYYYMMDD

        Returns:
            DB에 삽입/갱신된 행 수. 오류 발생 시 0.
        """
        logger.info(
            "일봉 수집 시작: symbol=%s kis_code=%s exchange=%s %s~%s",
            symbol, kis_code, exchange, start_date, end_date,
        )
        try:
            raw_records = self.client.get_futures_daily_ohlcv(
                symbol=kis_code,
                exchange=exchange,
                start_date=start_date,
                end_date=end_date,
            )
        except KISAPIError as exc:
            logger.error("API 오류 [%s/%s]: %s", symbol, kis_code, exc)
            return 0
        except Exception as exc:
            logger.error("예외 발생 [%s/%s]: %s", symbol, kis_code, exc)
            return 0

        if not raw_records:
            logger.warning("일봉 데이터 없음: %s/%s %s~%s",
                           symbol, kis_code, start_date, end_date)
            return 0

        bars = [self._parse_record(r) for r in raw_records]
        bars = [b for b in bars if b is not None]

        count = self._upsert_bars(symbol, bars)
        logger.info("일봉 저장 완료: %s → %d행", symbol, count)
        return count

    def collect_all(
        self,
        start_date: str = None,
        end_date: str = None,
    ) -> None:
        """PRODUCTS 딕셔너리의 모든 종목 일봉 수집.

        Args:
            start_date: 조회 시작일 YYYYMMDD. None이면 오늘 기준 60일 전.
            end_date: 조회 종료일 YYYYMMDD. None이면 오늘.
        """
        today = date.today()
        if end_date is None:
            end_date = today.strftime("%Y%m%d")
        if start_date is None:
            start_date = (today - timedelta(days=60)).strftime("%Y%m%d")

        total = 0
        for symbol, product in PRODUCTS.items():
            count = self.collect_symbol(
                symbol=symbol,
                kis_code=product.kis_code,
                exchange=product.exchange,
                start_date=start_date,
                end_date=end_date,
            )
            total += count

        logger.info(
            "전체 일봉 수집 완료: %d개 상품, 총 %d행 저장",
            len(PRODUCTS), total,
        )
