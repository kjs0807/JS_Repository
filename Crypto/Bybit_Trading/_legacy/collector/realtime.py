"""실시간 OHLCV 수집기 (WebSocket kline 스트림).

Bybit WebSocket kline 스트림을 구독하여 봉 확정 시 SQLite에 UPSERT한다.
"""

import logging
import time
from typing import Callable, Dict, List, Optional

from api.ws_client import BybitWebSocketClient
from db.db_manager import DBManager

logger = logging.getLogger(__name__)


class RealtimeCollector:
    """Bybit WebSocket 실시간 OHLCV 수집기.

    봉 확정(confirm=true) 이벤트를 수신하여 DB에 자동 저장한다.
    외부 콜백을 등록하여 전략 엔진과 연동 가능하다.

    Attributes:
        ws_client: BybitWebSocketClient 인스턴스
        db: DBManager 인스턴스
        on_bar: 봉 확정 시 호출할 외부 콜백
    """

    def __init__(
        self,
        ws_client: Optional[BybitWebSocketClient] = None,
        db: Optional[DBManager] = None,
        on_bar: Optional[Callable[[str, str, Dict], None]] = None,
    ) -> None:
        """RealtimeCollector 초기화.

        Args:
            ws_client: BybitWebSocketClient. None이면 기본 설정으로 생성.
            db: DBManager. None이면 기본 DB_PATH로 생성.
            on_bar: 봉 확정 콜백 Callable[[symbol, interval, bar_dict], None].
                bar_dict 키: start, end, interval, open, close, high, low,
                              volume, turnover, confirm, timestamp
        """
        self.on_bar = on_bar

        if db is None:
            db = DBManager()
        self.db = db

        if ws_client is None:
            ws_client = BybitWebSocketClient(
                on_kline_closed=self._on_kline_closed,
            )
        else:
            # 기존 클라이언트에 콜백 주입
            ws_client.on_kline_closed = self._on_kline_closed
        self.ws_client = ws_client

        # 통계
        self._bar_count: int = 0
        self._save_count: int = 0

    # ── 내부 콜백 ────────────────────────────────────────────────────────

    def _on_kline_closed(self, symbol: str, interval: str, bar: Dict) -> None:
        """봉 확정 이벤트 처리.

        WebSocket에서 confirm=true 봉을 수신하면 DB에 저장하고
        외부 on_bar 콜백을 호출한다.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            interval: 타임프레임 (예: "15")
            bar: Bybit kline 봉 딕셔너리
        """
        self._bar_count += 1

        # DB 테이블 결정
        from db.db_manager import TIMEFRAME_TABLE
        table = TIMEFRAME_TABLE.get(interval)
        if table is None:
            logger.debug("미지원 interval — DB 저장 스킵: %s", interval)
        else:
            # NaN 방어: 필수 필드 검증
            try:
                open_time = int(bar.get("start", 0))
                close_val = float(bar.get("close", 0))
                if open_time > 0 and close_val > 0:
                    row = {
                        "symbol": symbol,
                        "open_time": open_time,
                        "open": float(bar.get("open", 0)) or None,
                        "high": float(bar.get("high", 0)) or None,
                        "low": float(bar.get("low", 0)) or None,
                        "close": close_val,
                        "volume": float(bar.get("volume", 0)) or 0.0,
                        "turnover": float(bar.get("turnover", 0)) or None,
                    }
                    saved = self.db.upsert_ohlcv(table, [row])
                    if saved > 0:
                        self._save_count += 1
                        logger.debug(
                            "봉 저장: %s %s @ %d (close=%.4f)",
                            symbol, interval, open_time, close_val,
                        )
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(
                    "봉 파싱 오류 (symbol=%s, interval=%s): %s", symbol, interval, e
                )

        # 외부 콜백 호출
        if self.on_bar is not None:
            try:
                self.on_bar(symbol, interval, bar)
            except Exception as e:
                logger.error(
                    "on_bar 콜백 예외 (symbol=%s, interval=%s): %s",
                    symbol, interval, e,
                )

    # ── 공개 메서드 ──────────────────────────────────────────────────────

    def start(
        self,
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
    ) -> None:
        """실시간 수집 시작.

        Args:
            symbols: 구독할 심볼 리스트. None이면 AppSettings.symbols.
            intervals: 구독할 타임프레임 리스트. None이면 ["15", "60"].
        """
        if symbols is None:
            from config.settings import settings
            symbols = settings.symbols

        if intervals is None:
            intervals = ["15", "60"]

        self.db.initialize()
        self.ws_client.start(symbols=symbols, intervals=intervals)
        logger.info(
            "RealtimeCollector 시작: %d개 심볼 × %d개 타임프레임",
            len(symbols), len(intervals),
        )

    def stop(self) -> None:
        """실시간 수집 정지."""
        self.ws_client.stop()
        logger.info(
            "RealtimeCollector 정지 (수신=%d봉, 저장=%d봉)",
            self._bar_count, self._save_count,
        )

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return self.ws_client.is_running

    def get_stats(self) -> Dict:
        """수집 통계 반환.

        Returns:
            bar_count, save_count, ws_stats 포함 딕셔너리
        """
        return {
            "bar_count": self._bar_count,
            "save_count": self._save_count,
            "ws_stats": self.ws_client.get_stats(),
        }

    def __repr__(self) -> str:
        status = "실행 중" if self.is_running else "정지"
        return (
            f"RealtimeCollector({status}, "
            f"bar={self._bar_count}, save={self._save_count})"
        )


__all__ = ["RealtimeCollector"]
