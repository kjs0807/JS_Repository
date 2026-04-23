"""현재가 REST 폴링 모듈.

30초 간격으로 HHDFC55010000 API를 호출하여 현재가를 수집하고
realtime_ticks 테이블에 저장한다.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

from api.rest_client import KISRestClient, KISAPIError
from config import DB_PATH
from db.init_db import get_db_connection

logger = logging.getLogger(__name__)


class RealtimePoller:
    """현재가 REST 폴링 → 틱 DB 저장.

    30초 간격으로 지정된 종목 리스트를 순차 폴링하여
    realtime_ticks 테이블에 UPSERT한다.

    Attributes:
        client: KIS REST 클라이언트
        db_path: SQLite DB 경로
        on_tick_callback: 틱 수신 시 호출될 콜백 (symbol, price, volume, timestamp)
        _running: 폴링 루프 실행 여부
        _thread: 백그라운드 폴링 스레드
    """

    def __init__(
        self,
        client: KISRestClient,
        db_path: str = None,
        on_tick_callback: Optional[Callable] = None,
    ) -> None:
        """
        Args:
            client: KISRestClient 인스턴스
            db_path: SQLite DB 경로. None이면 config.DB_PATH 사용.
            on_tick_callback: 틱 수신 시 호출될 콜백.
                signature: callback(symbol: str, price: float,
                                    volume: int, timestamp: str) -> None
        """
        self.client = client
        self.db_path = db_path or DB_PATH
        self.on_tick_callback = on_tick_callback
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────

    def _save_tick(self, symbol: str, tick: dict) -> None:
        """틱 데이터를 realtime_ticks 테이블에 UPSERT.

        Args:
            symbol: 루트 심볼 (예: "VG")
            tick: poll_symbol() 반환 dict
        """
        sql = """
            INSERT OR REPLACE INTO realtime_ticks (
                symbol, timestamp, price, volume,
                bid1, bid1_qty, bid2, bid2_qty, bid3, bid3_qty,
                bid4, bid4_qty, bid5, bid5_qty,
                ask1, ask1_qty, ask2, ask2_qty, ask3, ask3_qty,
                ask4, ask4_qty, ask5, ask5_qty
            ) VALUES (
                :symbol, :timestamp, :price, :volume,
                :bid1, :bid1_qty, :bid2, :bid2_qty, :bid3, :bid3_qty,
                :bid4, :bid4_qty, :bid5, :bid5_qty,
                :ask1, :ask1_qty, :ask2, :ask2_qty, :ask3, :ask3_qty,
                :ask4, :ask4_qty, :ask5, :ask5_qty
            )
        """
        row = {
            "symbol":    symbol,
            "timestamp": tick.get("timestamp", ""),
            "price":     tick.get("price"),
            "volume":    tick.get("volume"),
            "bid1":      tick.get("bid1"),  "bid1_qty": tick.get("bid1_qty"),
            "bid2":      tick.get("bid2"),  "bid2_qty": tick.get("bid2_qty"),
            "bid3":      tick.get("bid3"),  "bid3_qty": tick.get("bid3_qty"),
            "bid4":      tick.get("bid4"),  "bid4_qty": tick.get("bid4_qty"),
            "bid5":      tick.get("bid5"),  "bid5_qty": tick.get("bid5_qty"),
            "ask1":      tick.get("ask1"),  "ask1_qty": tick.get("ask1_qty"),
            "ask2":      tick.get("ask2"),  "ask2_qty": tick.get("ask2_qty"),
            "ask3":      tick.get("ask3"),  "ask3_qty": tick.get("ask3_qty"),
            "ask4":      tick.get("ask4"),  "ask4_qty": tick.get("ask4_qty"),
            "ask5":      tick.get("ask5"),  "ask5_qty": tick.get("ask5_qty"),
        }
        conn = get_db_connection(self.db_path)
        try:
            conn.execute(sql, row)
            conn.commit()
        except Exception as exc:
            logger.error("틱 저장 실패 [%s]: %s", symbol, exc)
        finally:
            conn.close()

    @staticmethod
    def _safe_float(val: Optional[str]) -> Optional[float]:
        """문자열 → float 변환. 실패 시 None."""
        if val is None:
            return None
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val: Optional[str]) -> Optional[int]:
        """문자열 → int 변환. 실패 시 None."""
        if val is None:
            return None
        try:
            return int(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return None

    # ── 공개 메서드 ──────────────────────────────────────────────────────

    def poll_symbol(
        self,
        symbol: str,
        kis_code: str,
        exchange: str,
    ) -> Optional[dict]:
        """한 종목 현재가 조회 → DB 저장.

        KIS HHDFC55010000 API 응답에서 현재가/호가 5단계를 파싱하여
        realtime_ticks에 저장하고 콜백을 호출한다.

        Args:
            symbol: 루트 심볼 (예: "VG") — DB 저장 키
            kis_code: KIS 종목코드 (예: "VGM26") — API 호출 키
            exchange: 거래소 코드 (예: "EUREX")

        Returns:
            {symbol, price, volume, bid1, ask1, timestamp} dict.
            API 오류 또는 파싱 실패 시 None.
        """
        try:
            data = self.client.get_futures_current_price(kis_code, exchange)
        except KISAPIError as exc:
            logger.warning("현재가 조회 실패 [%s/%s]: %s", symbol, kis_code, exc)
            return None
        except Exception as exc:
            logger.error("예외 발생 [%s/%s]: %s", symbol, kis_code, exc)
            return None

        if not data:
            return None

        sf = self._safe_float
        si = self._safe_int

        # KIS 응답 필드명 — last_price 또는 ovrs_futr_last 등 변형 가능
        price = sf(
            data.get("last_price") or data.get("ovrs_futr_last")
            or data.get("stck_prpr")
        )
        volume = si(
            data.get("vol") or data.get("acml_vol") or data.get("cntg_vol")
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        tick = {
            "symbol":    symbol,
            "price":     price,
            "volume":    volume,
            "bid1":      sf(data.get("bid_price1") or data.get("ovrs_futr_bid1")),
            "bid1_qty":  si(data.get("bid_qntt1")),
            "bid2":      sf(data.get("bid_price2") or data.get("ovrs_futr_bid2")),
            "bid2_qty":  si(data.get("bid_qntt2")),
            "bid3":      sf(data.get("bid_price3") or data.get("ovrs_futr_bid3")),
            "bid3_qty":  si(data.get("bid_qntt3")),
            "bid4":      sf(data.get("bid_price4") or data.get("ovrs_futr_bid4")),
            "bid4_qty":  si(data.get("bid_qntt4")),
            "bid5":      sf(data.get("bid_price5") or data.get("ovrs_futr_bid5")),
            "bid5_qty":  si(data.get("bid_qntt5")),
            "ask1":      sf(data.get("ask_price1") or data.get("ovrs_futr_ask1")),
            "ask1_qty":  si(data.get("ask_qntt1")),
            "ask2":      sf(data.get("ask_price2") or data.get("ovrs_futr_ask2")),
            "ask2_qty":  si(data.get("ask_qntt2")),
            "ask3":      sf(data.get("ask_price3") or data.get("ovrs_futr_ask3")),
            "ask3_qty":  si(data.get("ask_qntt3")),
            "ask4":      sf(data.get("ask_price4") or data.get("ovrs_futr_ask4")),
            "ask4_qty":  si(data.get("ask_qntt4")),
            "ask5":      sf(data.get("ask_price5") or data.get("ovrs_futr_ask5")),
            "ask5_qty":  si(data.get("ask_qntt5")),
            "timestamp": timestamp,
        }

        self._save_tick(symbol, tick)

        if self.on_tick_callback is not None:
            try:
                self.on_tick_callback(symbol, price, volume, timestamp)
            except Exception as exc:
                logger.error("on_tick_callback 예외 [%s]: %s", symbol, exc)

        return tick

    def poll_all(self, symbols: List[dict]) -> List[dict]:
        """여러 종목 순차 폴링.

        Args:
            symbols: 종목 정보 리스트. 각 원소는 다음 키를 포함:
                - symbol: 루트 심볼
                - kis_code: KIS 종목코드
                - exchange: 거래소 코드

        Returns:
            성공적으로 수집된 틱 dict 리스트.
        """
        results = []
        for item in symbols:
            tick = self.poll_symbol(
                symbol=item["symbol"],
                kis_code=item["kis_code"],
                exchange=item["exchange"],
            )
            if tick is not None:
                results.append(tick)
        return results

    def _poll_loop(self, symbols: List[dict], interval_sec: float) -> None:
        """폴링 루프 (별도 스레드에서 실행).

        Args:
            symbols: poll_all()에 전달할 종목 리스트
            interval_sec: 폴링 간격 (초)
        """
        logger.info(
            "폴링 루프 시작: %d개 종목, 간격=%.1f초",
            len(symbols), interval_sec,
        )
        while self._running:
            start = time.monotonic()
            try:
                self.poll_all(symbols)
            except Exception as exc:
                logger.error("poll_all 예외: %s", exc)

            elapsed = time.monotonic() - start
            sleep_time = max(0.0, interval_sec - elapsed)
            # 짧은 슬립 단위로 나눠서 stop() 반응성 확보
            deadline = time.monotonic() + sleep_time
            while self._running and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))

        logger.info("폴링 루프 종료")

    def start(self, symbols: List[dict], interval_sec: float = 30.0) -> None:
        """폴링 루프 시작 (백그라운드 스레드).

        이미 실행 중이면 무시.

        Args:
            symbols: 폴링할 종목 리스트
            interval_sec: 폴링 간격 (초, 기본 30)
        """
        if self._running:
            logger.warning("폴링이 이미 실행 중입니다.")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(symbols, interval_sec),
            daemon=True,
            name="RealtimePoller",
        )
        self._thread.start()
        logger.info("RealtimePoller 스레드 시작")

    def stop(self) -> None:
        """폴링 루프 정지.

        실행 중인 스레드가 완료될 때까지 최대 5초 대기.
        """
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("RealtimePoller 정지")
