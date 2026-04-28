"""Bybit WebSocket v5 실시간 Kline 클라이언트.

asyncio 이벤트 루프를 데몬 스레드에서 실행하여 동기 코드와 통합 가능.
kline.{interval}.{symbol} 토픽을 구독하고 봉 확정(confirm=true) 이벤트를 콜백으로 전달한다.
지수 백오프 자동 재연결을 지원한다.
"""

import asyncio
import concurrent.futures
import json
import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

try:
    import websocket as _websocket_lib
    _WEBSOCKET_CLIENT_AVAILABLE = True
except ImportError:
    _WEBSOCKET_CLIENT_AVAILABLE = False
    logger.warning("websocket-client 패키지가 없습니다. pip install websocket-client")


class BybitWebSocketClient:
    """Bybit WebSocket v5 실시간 Kline 클라이언트.

    asyncio 이벤트 루프를 별도 데몬 스레드에서 실행하여
    tkinter GUI 등 동기 코드와 통합 가능.

    구독 토픽: kline.{interval}.{symbol}
    봉 확정 이벤트(confirm=true)만 on_kline_closed 콜백으로 전달.

    Attributes:
        ws_url: Bybit WebSocket 서버 URL
        on_kline_closed: 봉 확정 콜백 Callable[[symbol, interval, bar_dict], None]
        reconnect_delay: 재연결 초기 대기 시간 (초)
        max_reconnect_attempts: 최대 재연결 시도 횟수
    """

    def __init__(
        self,
        ws_url: Optional[str] = None,
        on_kline_closed: Optional[Callable[[str, str, Dict], None]] = None,
        on_kline_update: Optional[Callable[[str, str, Dict], None]] = None,
        on_permanent_failure: Optional[Callable[[], None]] = None,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 20,
    ) -> None:
        """BybitWebSocketClient 초기화.

        Args:
            ws_url: WebSocket URL. None이면 AppSettings에서 로드.
            on_kline_closed: 봉 확정 콜백.
                인자: (symbol: str, interval: str, bar: dict)
                bar 딕셔너리 키: start, end, interval, open, close, high, low,
                                  volume, turnover, confirm, timestamp
            on_kline_update: 미확정 봉 콜백 (confirm=False 틱 전달용).
                인자: (symbol: str, interval: str, bar: dict)
            on_permanent_failure: 최대 재연결 횟수 초과 시 호출되는 콜백.
            reconnect_delay: 초기 재연결 대기 시간 초 (기본 5)
            max_reconnect_attempts: 최대 재연결 횟수 (기본 20)
        """
        if ws_url is None:
            from config.settings import settings
            ws_url = settings.ws_url
        self.ws_url = ws_url
        self.on_kline_closed = on_kline_closed
        self.on_kline_update = on_kline_update
        self.on_permanent_failure = on_permanent_failure
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts

        self._subscriptions: List[str] = []   # 구독할 토픽 목록
        self._running: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ws = None

        # 통계
        self._kline_count: int = 0
        self._connected_at: Optional[float] = None

    # ── 토픽 구성 ────────────────────────────────────────────────────────

    @staticmethod
    def make_topic(symbol: str, interval: str) -> str:
        """kline 구독 토픽 문자열 생성.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            interval: 타임프레임 (예: "15", "60", "240", "D")

        Returns:
            "kline.15.BTCUSDT" 형태의 토픽 문자열
        """
        return f"kline.{interval}.{symbol}"

    def _make_subscribe_msg(self, topics: List[str]) -> str:
        """구독 요청 메시지 JSON 생성.

        Args:
            topics: 구독할 토픽 리스트

        Returns:
            JSON 문자열
        """
        return json.dumps({
            "op": "subscribe",
            "args": topics,
        })

    def _make_ping_msg(self) -> str:
        """Ping 메시지 생성."""
        return json.dumps({"op": "ping"})

    # ── 메시지 파싱 ──────────────────────────────────────────────────────

    def _handle_message(self, raw: str) -> None:
        """수신 메시지 파싱 및 콜백 호출.

        Args:
            raw: 수신된 원시 JSON 문자열
        """
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return

        # Pong 응답 처리
        if msg.get("op") == "pong":
            return

        # 구독 확인 메시지
        if "success" in msg:
            if msg.get("success"):
                logger.info("구독 확인: %s", msg.get("ret_msg", ""))
            else:
                logger.warning("구독 실패: %s", msg)
            return

        # kline 데이터 메시지
        topic = msg.get("topic", "")
        if not topic.startswith("kline."):
            return

        # 토픽에서 interval, symbol 추출: "kline.15.BTCUSDT"
        parts = topic.split(".", 2)
        if len(parts) < 3:
            return
        interval = parts[1]
        symbol = parts[2]

        data_list = msg.get("data", [])
        if not isinstance(data_list, list):
            data_list = [data_list]

        for bar in data_list:
            if not isinstance(bar, dict):
                continue

            # NaN 방어: 필수 필드 없으면 스킵
            if bar.get("start") is None:
                continue

            is_confirm = bar.get("confirm", False)
            self._kline_count += 1

            if is_confirm and self.on_kline_closed is not None:
                # 봉 확정 이벤트 콜백
                try:
                    self.on_kline_closed(symbol, interval, bar)
                except Exception as e:
                    logger.error(
                        "on_kline_closed 콜백 예외 (symbol=%s, interval=%s): %s",
                        symbol, interval, e,
                    )
            elif not is_confirm and self.on_kline_update is not None:
                # 미확정 봉(틱) 콜백
                try:
                    self.on_kline_update(symbol, interval, bar)
                except Exception as e:
                    logger.debug(
                        "on_kline_update 콜백 예외 (symbol=%s, interval=%s): %s",
                        symbol, interval, e,
                    )

    # ── asyncio 핵심 루프 ─────────────────────────────────────────────────

    async def _connect_and_run(self) -> None:
        """WebSocket 접속 → 구독 → 수신 루프 (지수 백오프 자동 재연결)."""
        try:
            import websockets as _ws_lib
        except ImportError:
            logger.error("websockets 패키지가 없습니다. pip install websockets")
            return

        attempt = 0

        while self._running and attempt < self.max_reconnect_attempts:
            try:
                logger.info(
                    "WebSocket 접속 시도 (%d/%d): %s",
                    attempt + 1, self.max_reconnect_attempts, self.ws_url,
                )
                async with _ws_lib.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=None,
                ) as ws:
                    self._ws = ws
                    self._connected_at = time.time()
                    attempt = 0  # 연결 성공 시 재연결 카운터 초기화
                    logger.info("WebSocket 연결 성공: %s", self.ws_url)

                    # 구독 요청 (최대 10개 토픽씩 배치)
                    batch_size = 10
                    for i in range(0, len(self._subscriptions), batch_size):
                        batch = self._subscriptions[i:i + batch_size]
                        await ws.send(self._make_subscribe_msg(batch))
                        await asyncio.sleep(0.1)
                    logger.info("%d개 토픽 구독 요청 완료", len(self._subscriptions))

                    # 수신 루프 + 주기적 Ping
                    last_ping = time.time()
                    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
                    async for raw in ws:
                        if not self._running:
                            break

                        # 동기 콜백을 스레드풀에서 실행하여 asyncio 루프 블로킹 방지
                        loop = asyncio.get_event_loop()
                        loop.run_in_executor(_executor, self._handle_message, raw)

                        # 20초마다 Ping 전송
                        now = time.time()
                        if now - last_ping > 20:
                            await ws.send(self._make_ping_msg())
                            last_ping = now

            except Exception as e:
                logger.warning("WebSocket 오류: %s", e)

            self._ws = None
            self._connected_at = None

            if self._running:
                attempt += 1
                delay = min(self.reconnect_delay * (2 ** (attempt - 1)), 60.0)
                logger.info(
                    "%.1f초 후 재연결 (%d/%d)...",
                    delay, attempt, self.max_reconnect_attempts,
                )
                await asyncio.sleep(delay)

        if attempt >= self.max_reconnect_attempts:
            logger.critical(
                "최대 재연결 횟수(%d) 초과 -- WebSocket 영구 실패",
                self.max_reconnect_attempts,
            )
            if self.on_permanent_failure is not None:
                try:
                    self.on_permanent_failure()
                except Exception as e:
                    logger.error("on_permanent_failure 콜백 예외: %s", e)

    def _run_loop(self) -> None:
        """asyncio 이벤트 루프 (별도 데몬 스레드에서 실행)."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_run())
        finally:
            self._loop.close()
            self._loop = None

    # ── 공개 메서드 ──────────────────────────────────────────────────────

    def start(
        self,
        symbols: List[str],
        intervals: Optional[List[str]] = None,
    ) -> None:
        """WebSocket 수신 시작 (백그라운드 데몬 스레드).

        Args:
            symbols: 구독할 심볼 리스트 (예: ["BTCUSDT", "ETHUSDT"])
            intervals: 구독할 타임프레임 리스트 (예: ["15", "60"]).
                None이면 ["15"] 기본값.
        """
        if self._running:
            logger.warning("WebSocket이 이미 실행 중입니다.")
            return

        if intervals is None:
            intervals = ["15"]

        # 토픽 목록 구성
        self._subscriptions = []
        for symbol in symbols:
            for interval in intervals:
                self._subscriptions.append(self.make_topic(symbol, interval))

        self._running = True
        self._kline_count = 0

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="BybitWebSocket",
        )
        self._thread.start()
        logger.info(
            "BybitWebSocketClient 시작: %d개 심볼 × %d개 타임프레임 = %d토픽",
            len(symbols), len(intervals), len(self._subscriptions),
        )

    def stop(self) -> None:
        """WebSocket 수신 정지 및 리소스 정리."""
        self._running = False

        # asyncio 루프에서 ws.close() 호출
        if self._ws is not None and self._loop is not None and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except Exception as exc:
                logger.warning("WS close 전송 실패: %s", exc)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            self._thread = None

        self._subscriptions.clear()
        self._ws = None
        self._connected_at = None
        logger.info(
            "BybitWebSocketClient 정지 (총 kline 수신: %d개)",
            self._kline_count,
        )

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return (
            self._running
            and self._thread is not None
            and self._thread.is_alive()
        )

    def get_stats(self) -> Dict:
        """실시간 통계 반환.

        Returns:
            running, connected_at, subscriptions, kline_count 포함 딕셔너리
        """
        return {
            "running": self.is_running,
            "connected_at": self._connected_at,
            "subscriptions": list(self._subscriptions),
            "kline_count": self._kline_count,
            "ws_url": self.ws_url,
        }

    def __repr__(self) -> str:
        status = "실행 중" if self.is_running else "정지"
        return (
            f"BybitWebSocketClient({status}, "
            f"topics={len(self._subscriptions)}개, "
            f"kline={self._kline_count}건)"
        )


__all__ = ["BybitWebSocketClient"]
