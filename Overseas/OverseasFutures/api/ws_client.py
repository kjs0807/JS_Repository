"""KIS WebSocket Client for Overseas Futures Real-time Data.

DESIGN.md Phase 3:
- HDFFF020: 실시간 체결가 (tick-by-tick execution)
- HDFFF010: 실시간 호가 5단계 (bid/ask orderbook)

Based on KIS open-trading-api official sample code.
WebSocket URL: ws://ops.koreainvestment.com:21000 (실전)
"""

import asyncio
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

import requests
import websockets

from config.settings import KISConfig
from config import DB_PATH
from db.init_db import get_db_connection

logger = logging.getLogger(__name__)


# ── HDFFF020 체결가 필드 (25개, ^ 구분) ──────────────────────────────────

HDFFF020_FIELDS: List[str] = [
    "series_cd",        # 0  종목코드
    "bsns_date",        # 1  영업일자
    "mrkt_open_date",   # 2  장개시일자
    "mrkt_open_time",   # 3  장개시시각
    "mrkt_close_date",  # 4  장종료일자
    "mrkt_close_time",  # 5  장종료시각
    "prev_price",       # 6  전일종가
    "recv_date",        # 7  수신일자
    "recv_time",        # 8  수신시각
    "active_flag",      # 9  본장/전산장 구분
    "last_price",       # 10 체결가격
    "last_qntt",        # 11 체결수량
    "prev_diff_price",  # 12 전일대비가
    "prev_diff_rate",   # 13 등락률
    "open_price",       # 14 시가
    "high_price",       # 15 고가
    "low_price",        # 16 저가
    "vol",              # 17 누적거래량
    "prev_sign",        # 18 전일대비부호
    "quotsign",         # 19 체결구분
    "recv_time2",       # 20 수신시각(1/20000초)
    "psttl_price",      # 21 전일정산가
    "psttl_sign",       # 22 전일정산가대비부호
    "psttl_diff_price", # 23 전일정산가대비가격
    "psttl_diff_rate",  # 24 전일정산가대비율
]

# ── HDFFF010 호가 필드 (35개, ^ 구분) ────────────────────────────────────

HDFFF010_FIELDS: List[str] = [
    "series_cd",    # 0  종목코드
    "recv_date",    # 1  수신일자
    "recv_time",    # 2  수신시각
    "prev_price",   # 3  전일종가
    "bid_qntt_1",  # 4  매수1수량
    "bid_num_1",   # 5  매수1건수
    "bid_price_1", # 6  매수1호가
    "ask_qntt_1",  # 7  매도1수량
    "ask_num_1",   # 8  매도1건수
    "ask_price_1", # 9  매도1호가
    "bid_qntt_2",  # 10
    "bid_num_2",   # 11
    "bid_price_2", # 12
    "ask_qntt_2",  # 13
    "ask_num_2",   # 14
    "ask_price_2", # 15
    "bid_qntt_3",  # 16
    "bid_num_3",   # 17
    "bid_price_3", # 18
    "ask_qntt_3",  # 19
    "ask_num_3",   # 20
    "ask_price_3", # 21
    "bid_qntt_4",  # 22
    "bid_num_4",   # 23
    "bid_price_4", # 24
    "ask_qntt_4",  # 25
    "ask_num_4",   # 26
    "ask_price_4", # 27
    "bid_qntt_5",  # 28
    "bid_num_5",   # 29
    "bid_price_5", # 30
    "ask_qntt_5",  # 31
    "ask_num_5",   # 32
    "ask_price_5", # 33
    "sttl_price",  # 34 전일정산가
]


# ── 데이터 클래스 ────────────────────────────────────────────────────────

@dataclass
class TradeData:
    """실시간 체결 데이터 (HDFFF020).

    Attributes:
        symbol: KIS 종목코드 (예: "VGM26")
        price: 체결가
        quantity: 체결수량
        volume: 누적거래량
        open_price: 시가
        high_price: 고가
        low_price: 저가
        prev_price: 전일종가
        recv_date: 수신일자 (YYYYMMDD)
        recv_time: 수신시각 (HHMMSS)
        timestamp: 파싱된 datetime (UTC)
        quotsign: 체결구분 (매수/매도 initiated)
    """
    symbol: str
    price: float
    quantity: int
    volume: int
    open_price: float
    high_price: float
    low_price: float
    prev_price: float
    recv_date: str
    recv_time: str
    timestamp: datetime
    quotsign: str = ""


@dataclass
class OrderbookData:
    """실시간 호가 데이터 (HDFFF010).

    Attributes:
        symbol: KIS 종목코드
        recv_date: 수신일자
        recv_time: 수신시각
        timestamp: 파싱된 datetime (UTC)
        bids: 매수 호가 [{price, qty, count}] x 5
        asks: 매도 호가 [{price, qty, count}] x 5
    """
    symbol: str
    recv_date: str
    recv_time: str
    timestamp: datetime
    bids: List[dict] = field(default_factory=list)
    asks: List[dict] = field(default_factory=list)


# ── WebSocket 클라이언트 ─────────────────────────────────────────────────

class KISWebSocketClient:
    """KIS 해외선물 WebSocket 실시간 클라이언트.

    asyncio 이벤트 루프를 데몬 스레드에서 실행하여
    기존 동기 코드(main.py, dashboard)와 통합 가능.

    Attributes:
        config: KIS API 설정
        on_trade: 체결 수신 콜백
        on_orderbook: 호가 수신 콜백
        db_path: SQLite DB 경로
        save_ticks_to_db: 호가를 realtime_ticks에 자동 저장할지 여부
    """

    WS_URL_REAL = "ws://ops.koreainvestment.com:21000"

    def __init__(
        self,
        config: KISConfig,
        on_trade: Optional[Callable[["TradeData"], None]] = None,
        on_orderbook: Optional[Callable[["OrderbookData"], None]] = None,
        db_path: Optional[str] = None,
        save_ticks_to_db: bool = True,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 10,
    ) -> None:
        self.config = config
        self.on_trade = on_trade
        self.on_orderbook = on_orderbook
        self.db_path = db_path or DB_PATH
        self.save_ticks_to_db = save_ticks_to_db
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts

        self._approval_key: Optional[str] = None
        self._subscriptions: Set[str] = set()
        self._symbols: List[str] = []
        self._running: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ws = None

        # KIS 종목코드 → 루트심볼 역매핑 (DB 저장용)
        self._code_to_root: Dict[str, str] = {}

        # DB 저장 큐 (asyncio 루프 블로킹 방지)
        self._db_queue: queue.Queue = queue.Queue(maxsize=10000)
        self._db_thread: Optional[threading.Thread] = None

        # 종목별 최신 누적거래량 캐시 (체결 → 호가 저장 시 참조)
        self._last_volume: Dict[str, int] = {}

        # 통계
        self._trade_count: int = 0
        self._orderbook_count: int = 0
        self._connected_at: Optional[datetime] = None

    # ── Approval Key ──────────────────────────────────────────────────

    def _get_approval_key(self) -> str:
        """REST API로 WebSocket approval key 발급.

        Returns:
            approval_key 문자열

        Raises:
            RuntimeError: 응답에 approval_key가 없을 때
            requests.RequestException: 네트워크 오류
        """
        url = f"{self.config.base_url}/oauth2/Approval"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "secretkey": self.config.app_secret,
        }
        response = requests.post(
            url,
            headers={"content-type": "application/json"},
            json=body,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        key = data.get("approval_key")
        if not key:
            raise RuntimeError(f"Approval key missing in response: {data}")
        logger.info("WebSocket approval key 발급 성공")
        return key

    # ── 메시지 구성 ───────────────────────────────────────────────────

    def _make_subscribe_msg(
        self, tr_id: str, tr_key: str, subscribe: bool = True
    ) -> str:
        """구독/해제 메시지 JSON 생성.

        Args:
            tr_id: TR_ID ("HDFFF020" 또는 "HDFFF010")
            tr_key: 종목코드 (예: "VGM26")
            subscribe: True=구독, False=해제
        """
        return json.dumps({
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "1" if subscribe else "2",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key,
                }
            }
        })

    # ── 파싱 유틸 ─────────────────────────────────────────────────────

    @staticmethod
    def _safe_float(val: str) -> Optional[float]:
        """문자열 → float. 빈값/파싱실패 시 None."""
        if not val or val.strip() == "":
            return None
        try:
            return float(val.replace(",", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val: str) -> Optional[int]:
        """문자열 → int. 빈값/파싱실패 시 None."""
        if not val or val.strip() == "":
            return None
        try:
            return int(float(val.replace(",", "")))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_timestamp(date_str: str, time_str: str) -> datetime:
        """YYYYMMDD + HHMMSS → UTC datetime."""
        try:
            ts = datetime.strptime(
                f"{date_str}{time_str[:6]}", "%Y%m%d%H%M%S"
            )
            return ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    # ── HDFFF020 체결가 파싱 ──────────────────────────────────────────

    def _parse_trade(self, fields: List[str]) -> Optional[TradeData]:
        """HDFFF020 ^ 구분 필드 → TradeData.

        Args:
            fields: caret-split 필드 리스트 (25개)

        Returns:
            TradeData 또는 파싱 실패 시 None
        """
        if len(fields) < 18:
            logger.warning("HDFFF020 필드 부족: %d개 (최소 18)", len(fields))
            return None

        data = dict(zip(HDFFF020_FIELDS, fields))
        sf, si = self._safe_float, self._safe_int

        price = sf(data.get("last_price", ""))
        if price is None:
            return None

        return TradeData(
            symbol=data.get("series_cd", "").strip(),
            price=price,
            quantity=si(data.get("last_qntt", "")) or 0,
            volume=si(data.get("vol", "")) or 0,
            open_price=sf(data.get("open_price", "")) or 0.0,
            high_price=sf(data.get("high_price", "")) or 0.0,
            low_price=sf(data.get("low_price", "")) or 0.0,
            prev_price=sf(data.get("prev_price", "")) or 0.0,
            recv_date=data.get("recv_date", ""),
            recv_time=data.get("recv_time", ""),
            timestamp=self._parse_timestamp(
                data.get("recv_date", ""), data.get("recv_time", "")
            ),
            quotsign=data.get("quotsign", ""),
        )

    # ── HDFFF010 호가 파싱 ────────────────────────────────────────────

    def _parse_orderbook(self, fields: List[str]) -> Optional[OrderbookData]:
        """HDFFF010 ^ 구분 필드 → OrderbookData.

        Args:
            fields: caret-split 필드 리스트 (35개)

        Returns:
            OrderbookData 또는 파싱 실패 시 None
        """
        if len(fields) < 34:
            logger.warning("HDFFF010 필드 부족: %d개 (최소 34)", len(fields))
            return None

        data = dict(zip(HDFFF010_FIELDS, fields))
        sf, si = self._safe_float, self._safe_int

        bids: List[dict] = []
        asks: List[dict] = []
        for i in range(1, 6):
            bids.append({
                "price": sf(data.get(f"bid_price_{i}", "")) or 0.0,
                "qty":   si(data.get(f"bid_qntt_{i}", "")) or 0,
                "count": si(data.get(f"bid_num_{i}", "")) or 0,
            })
            asks.append({
                "price": sf(data.get(f"ask_price_{i}", "")) or 0.0,
                "qty":   si(data.get(f"ask_qntt_{i}", "")) or 0,
                "count": si(data.get(f"ask_num_{i}", "")) or 0,
            })

        return OrderbookData(
            symbol=data.get("series_cd", "").strip(),
            recv_date=data.get("recv_date", ""),
            recv_time=data.get("recv_time", ""),
            timestamp=self._parse_timestamp(
                data.get("recv_date", ""), data.get("recv_time", "")
            ),
            bids=bids,
            asks=asks,
        )

    # ── DB 큐 워커 (별도 스레드) ────────────────────────────────────

    def _start_db_worker(self) -> None:
        """DB 저장 워커 스레드 시작. 큐에서 SQL+params를 꺼내 배치 실행."""
        self._db_thread = threading.Thread(
            target=self._db_worker_loop,
            daemon=True,
            name="WS-DBWriter",
        )
        self._db_thread.start()
        logger.info("DB 워커 스레드 시작")

    def _db_worker_loop(self) -> None:
        """큐에서 (sql, params) 튜플을 꺼내 배치로 DB에 커밋."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        batch_count = 0
        try:
            while self._running or not self._db_queue.empty():
                batch: list = []
                try:
                    # 최대 0.5초 대기, 한번에 최대 100건 배치
                    item = self._db_queue.get(timeout=0.5)
                    batch.append(item)
                    # 큐에 더 있으면 바로 꺼냄
                    while len(batch) < 100:
                        try:
                            batch.append(self._db_queue.get_nowait())
                        except queue.Empty:
                            break
                except queue.Empty:
                    continue

                for sql, params in batch:
                    try:
                        conn.execute(sql, params)
                    except Exception as exc:
                        logger.error("DB 실행 오류: %s", exc)
                conn.commit()
                batch_count += len(batch)
        except Exception as exc:
            logger.error("DB 워커 예외: %s", exc)
        finally:
            conn.close()
            logger.info("DB 워커 종료 (총 %d건 저장)", batch_count)

    def _enqueue_db(self, sql: str, params: dict) -> None:
        """DB 저장 요청을 큐에 넣기 (논블로킹)."""
        try:
            self._db_queue.put_nowait((sql, params))
        except queue.Full:
            logger.warning("DB 큐 가득 참 — 틱 드랍")

    # ── DB 저장: 체결 → trade_ticks ─────────────────────────────────

    def _save_trade_to_db(self, trade: TradeData) -> None:
        """체결 틱 원본을 DB 큐에 추가 (논블로킹)."""
        root = self._code_to_root.get(trade.symbol, trade.symbol)

        direction = None
        if trade.quotsign == "1":
            direction = "BUY"
        elif trade.quotsign == "2":
            direction = "SELL"

        sql = """
            INSERT OR REPLACE INTO trade_ticks (
                symbol, timestamp, price, quantity, cum_volume,
                direction, open_price, high_price, low_price,
                recv_date, recv_time
            ) VALUES (
                :symbol, :timestamp, :price, :quantity, :cum_volume,
                :direction, :open_price, :high_price, :low_price,
                :recv_date, :recv_time
            )
        """
        row = {
            "symbol":     root,
            "timestamp":  trade.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "price":      trade.price,
            "quantity":   trade.quantity,
            "cum_volume": trade.volume,
            "direction":  direction,
            "open_price": trade.open_price,
            "high_price": trade.high_price,
            "low_price":  trade.low_price,
            "recv_date":  trade.recv_date,
            "recv_time":  trade.recv_time,
        }
        self._enqueue_db(sql, row)

    # ── DB 저장: 호가 → realtime_ticks ────────────────────────────────

    def _save_orderbook_to_db(self, ob: OrderbookData) -> None:
        """호가 5단계 데이터를 DB 큐에 추가 (논블로킹)."""
        root = self._code_to_root.get(ob.symbol, ob.symbol)

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
        mid_price = None
        if ob.bids and ob.asks and ob.bids[0]["price"] and ob.asks[0]["price"]:
            mid_price = (ob.bids[0]["price"] + ob.asks[0]["price"]) / 2

        row: Dict[str, object] = {
            "symbol": root,
            "timestamp": ob.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "price": mid_price,
            "volume": self._last_volume.get(root),
        }
        for i in range(5):
            idx = i + 1
            row[f"bid{idx}"] = ob.bids[i]["price"] if i < len(ob.bids) else None
            row[f"bid{idx}_qty"] = ob.bids[i]["qty"] if i < len(ob.bids) else None
            row[f"ask{idx}"] = ob.asks[i]["price"] if i < len(ob.asks) else None
            row[f"ask{idx}_qty"] = ob.asks[i]["qty"] if i < len(ob.asks) else None

        self._enqueue_db(sql, row)

    # ── 메시지 처리 ───────────────────────────────────────────────────

    def _handle_data_message(self, raw: str) -> None:
        """실시간 데이터 메시지(0|...) 파싱 및 콜백 호출.

        Args:
            raw: pipe-delimited 원시 메시지
        """
        parts = raw.split("|", 3)
        if len(parts) < 4:
            return

        encrypted, tr_id, _count, data_str = parts

        if encrypted == "1":
            logger.debug("암호화 메시지 스킵 (tr_id=%s)", tr_id)
            return

        fields = data_str.split("^")

        if tr_id == "HDFFF020":
            trade = self._parse_trade(fields)
            if trade:
                self._trade_count += 1
                # 종목별 최신 누적거래량 캐시 업데이트
                root = self._code_to_root.get(trade.symbol, trade.symbol)
                if trade.volume:
                    self._last_volume[root] = trade.volume
                if self.save_ticks_to_db:
                    try:
                        self._save_trade_to_db(trade)
                    except Exception as exc:
                        logger.error("체결 DB 저장 예외: %s", exc)
                if self.on_trade:
                    try:
                        self.on_trade(trade)
                    except Exception as exc:
                        logger.error("on_trade 콜백 예외: %s", exc)

        elif tr_id == "HDFFF010":
            ob = self._parse_orderbook(fields)
            if ob:
                self._orderbook_count += 1
                if self.save_ticks_to_db:
                    try:
                        self._save_orderbook_to_db(ob)
                    except Exception as exc:
                        logger.error("호가 DB 저장 예외: %s", exc)
                if self.on_orderbook:
                    try:
                        self.on_orderbook(ob)
                    except Exception as exc:
                        logger.error("on_orderbook 콜백 예외: %s", exc)

    # ── asyncio 핵심 루프 ─────────────────────────────────────────────

    async def _connect_and_run(self) -> None:
        """WebSocket 접속 → 구독 → 수신 루프 (자동 재연결)."""
        attempt = 0

        while self._running and attempt < self.max_reconnect_attempts:
            try:
                self._approval_key = self._get_approval_key()

                async with websockets.connect(
                    self.WS_URL_REAL,
                    ping_interval=None,
                    max_size=None,
                ) as ws:
                    self._ws = ws
                    self._connected_at = datetime.now(timezone.utc)
                    attempt = 0
                    logger.info("WebSocket 연결 성공: %s", self.WS_URL_REAL)

                    # 구독 요청: 각 종목에 체결가 + 호가
                    for symbol in self._symbols:
                        for tr_id in ("HDFFF020", "HDFFF010"):
                            msg = self._make_subscribe_msg(tr_id, symbol)
                            await ws.send(msg)
                            self._subscriptions.add(f"{tr_id}:{symbol}")
                            await asyncio.sleep(0.3)

                    logger.info(
                        "%d개 종목 x 2 TR 구독 완료 (%d건)",
                        len(self._symbols), len(self._subscriptions),
                    )

                    # 수신 루프
                    async for raw in ws:
                        if not self._running:
                            break

                        if not raw:
                            continue

                        # PINGPONG 처리
                        if raw[0] not in ("0", "1"):
                            try:
                                obj = json.loads(raw)
                                header = obj.get("header", {})
                                if header.get("tr_id") == "PINGPONG":
                                    await ws.send(raw)
                                    continue
                                # 구독 확인 등 제어 메시지 로깅
                                msg1 = obj.get("body", {}).get("msg1", "")
                                tr_key = header.get("tr_key", "")
                                logger.info(
                                    "WS [%s/%s]: %s",
                                    header.get("tr_id", ""), tr_key, msg1,
                                )
                            except (json.JSONDecodeError, IndexError, KeyError):
                                pass
                            continue

                        # 실시간 데이터
                        self._handle_data_message(raw)

            except websockets.ConnectionClosed as exc:
                logger.warning("WebSocket 연결 종료: code=%s reason=%s",
                               exc.code, exc.reason)
            except Exception as exc:
                logger.error("WebSocket 오류: %s", exc)

            self._ws = None
            self._connected_at = None

            if self._running:
                attempt += 1
                delay = min(self.reconnect_delay * attempt, 60.0)
                logger.info(
                    "%.1f초 후 재연결 (%d/%d)...",
                    delay, attempt, self.max_reconnect_attempts,
                )
                await asyncio.sleep(delay)

        if attempt >= self.max_reconnect_attempts:
            logger.error("최대 재연결 횟수(%d) 초과 — WebSocket 종료",
                         self.max_reconnect_attempts)

    def _run_loop(self) -> None:
        """asyncio 이벤트 루프 (별도 데몬 스레드에서 실행)."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_run())
        finally:
            self._loop.close()
            self._loop = None

    # ── 공개 메서드 ───────────────────────────────────────────────────

    def set_code_mapping(self, mapping: Dict[str, str]) -> None:
        """KIS 종목코드 → 루트심볼 역매핑 설정 (DB 저장 시 사용).

        Args:
            mapping: {"VGM26": "VG", "BONM26": "BON", ...}
        """
        self._code_to_root = dict(mapping)

    def start(self, symbols: List[str]) -> None:
        """WebSocket 수신 시작 (백그라운드 데몬 스레드).

        Args:
            symbols: 구독할 KIS 종목코드 리스트 (예: ["VGM26", "BONM26"])
        """
        if self._running:
            logger.warning("WebSocket이 이미 실행 중")
            return

        self._symbols = list(symbols)
        self._running = True
        self._trade_count = 0
        self._orderbook_count = 0

        # DB 워커 스레드 시작
        if self.save_ticks_to_db:
            self._start_db_worker()

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="KISWebSocket",
        )
        self._thread.start()
        logger.info("KISWebSocketClient 시작: %d개 종목", len(symbols))

    def stop(self) -> None:
        """WebSocket 수신 정지 및 리소스 정리."""
        self._running = False

        if self._ws and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except Exception:
                pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            self._thread = None

        # DB 워커 종료 대기 (큐 비울 때까지)
        if self._db_thread and self._db_thread.is_alive():
            self._db_thread.join(timeout=5.0)
            self._db_thread = None

        self._subscriptions.clear()
        self._ws = None
        self._connected_at = None
        logger.info(
            "KISWebSocketClient 정지 (체결=%d, 호가=%d)",
            self._trade_count, self._orderbook_count,
        )

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def subscription_count(self) -> int:
        """현재 구독 수."""
        return len(self._subscriptions)

    def get_stats(self) -> dict:
        """실시간 통계 반환."""
        return {
            "running": self.is_running,
            "connected_at": (
                self._connected_at.isoformat() if self._connected_at else None
            ),
            "subscriptions": self.subscription_count,
            "trade_count": self._trade_count,
            "orderbook_count": self._orderbook_count,
            "symbols": list(self._symbols),
        }
