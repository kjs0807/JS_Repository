"""멀티 거래소 폴링 스케줄러.

현재 열린 거래소의 상품만 폴링하여 율 효율적으로 데이터 수집.
DESIGN.md 8.2절 스케줄러 동작 구현.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from collector.realtime_poller import RealtimePoller
from config.products import PRODUCTS
from scheduler.exchange_hours import get_open_exchanges, is_exchange_open

logger = logging.getLogger(__name__)


class PollScheduler:
    """멀티 거래소 폴링 스케줄러.

    매 사이클마다 현재 열린 거래소를 판별하고 해당 상품만 폴링한다.
    bar_resampler 콜백과 연동하여 봉 완성 이벤트를 전달할 수 있다.

    Attributes:
        poller: RealtimePoller 인스턴스
        on_bar_callbacks: 심볼별 봉 완성 콜백 dict {symbol: callback}
        _running: 스케줄러 실행 여부
        _thread: 백그라운드 스레드
    """

    def __init__(
        self,
        poller: RealtimePoller,
        on_bar_callbacks: Optional[Dict[str, callable]] = None,
    ) -> None:
        """
        Args:
            poller: RealtimePoller 인스턴스
            on_bar_callbacks: 심볼별 봉 완성 콜백. {symbol: callback(bar)} 형태.
        """
        self.poller = poller
        self.on_bar_callbacks: Dict[str, callable] = on_bar_callbacks or {}
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────

    def _build_poll_list(self) -> List[dict]:
        """현재 열린 거래소의 상품만 폴링 리스트 생성.

        PRODUCTS 딕셔너리를 순회하여 is_exchange_open()이 True인
        거래소의 상품만 반환한다.

        Returns:
            [{symbol, kis_code, exchange}, ...] 리스트
        """
        open_exchanges = set(get_open_exchanges())
        poll_list = []
        for symbol, product in PRODUCTS.items():
            if product.exchange in open_exchanges:
                poll_list.append({
                    "symbol":   symbol,
                    "kis_code": product.kis_code,
                    "exchange": product.exchange,
                })
        return poll_list

    def _poll_cycle(self) -> None:
        """한 사이클: 열린 거래소 상품 순차 폴링.

        폴링 결과를 on_bar_callbacks에 전달하지는 않는다
        (bar_resampler는 poller의 on_tick_callback에서 처리).
        """
        poll_list = self._build_poll_list()
        if not poll_list:
            logger.debug("현재 열린 거래소 없음 — 폴링 건너뜀")
            return

        open_exchanges = sorted(set(item["exchange"] for item in poll_list))
        logger.debug(
            "폴링 사이클: 거래소=%s, 상품=%d개",
            open_exchanges, len(poll_list),
        )
        self.poller.poll_all(poll_list)

    def _scheduler_loop(self, interval_sec: float) -> None:
        """스케줄러 루프 (별도 스레드 실행).

        Args:
            interval_sec: 사이클 간격 (초)
        """
        logger.info("PollScheduler 루프 시작 (간격=%.1f초)", interval_sec)
        while self._running:
            start = time.monotonic()
            try:
                self._poll_cycle()
            except Exception as exc:
                logger.error("_poll_cycle 예외: %s", exc)

            elapsed = time.monotonic() - start
            sleep_time = max(0.0, interval_sec - elapsed)
            deadline = time.monotonic() + sleep_time
            while self._running and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))

        logger.info("PollScheduler 루프 종료")

    # ── 공개 메서드 ──────────────────────────────────────────────────────

    def start(self, interval_sec: float = 30.0) -> None:
        """스케줄러 시작 (백그라운드 스레드).

        이미 실행 중이면 무시.

        Args:
            interval_sec: 폴링 간격 (초, 기본 30)
        """
        if self._running:
            logger.warning("PollScheduler가 이미 실행 중입니다.")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            args=(interval_sec,),
            daemon=True,
            name="PollScheduler",
        )
        self._thread.start()
        logger.info("PollScheduler 시작")

    def stop(self) -> None:
        """스케줄러 정지.

        실행 중인 스레드가 완료될 때까지 최대 5초 대기.
        """
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("PollScheduler 정지")

    def get_status(self) -> dict:
        """현재 스케줄러 상태 조회.

        Returns:
            {
                "running": bool,
                "open_exchanges": List[str],
                "poll_count": int,       # 현재 폴링 대상 상품 수
                "all_products": int,     # 전체 상품 수
                "timestamp": str,        # ISO 8601 KST
            }
        """
        poll_list = self._build_poll_list()
        open_exchanges = sorted(set(item["exchange"] for item in poll_list))

        return {
            "running":        self._running,
            "open_exchanges": open_exchanges,
            "poll_count":     len(poll_list),
            "all_products":   len(PRODUCTS),
            "timestamp":      datetime.now().isoformat(),
        }
