"""Bybit WebSocket 클라이언트. 실시간 kline 수신."""
from __future__ import annotations
import json
import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple
import websocket

logger = logging.getLogger(__name__)
DEFAULT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

class BybitWebSocketClient:
    def __init__(self, ws_url: str = DEFAULT_WS_URL) -> None:
        self.ws_url = ws_url
        self._ws = None
        self._thread = None
        self._running = False
        self.on_kline_closed: Optional[Callable[[str, str, Dict], None]] = None

    def _build_subscribe_args(self, symbols: List[str], intervals: List[str]) -> List[str]:
        return [f"kline.{iv}.{sym}" for sym in symbols for iv in intervals]

    def _parse_kline(self, msg: Dict) -> Optional[Tuple[str, str, Dict]]:
        topic = msg.get("topic", "")
        if not topic.startswith("kline."): return None
        parts = topic.split(".")
        if len(parts) < 3: return None
        interval, symbol = parts[1], parts[2]
        data_list = msg.get("data", [])
        if not data_list: return None
        raw = data_list[0]
        kline = {"start": int(raw.get("start",0)), "open": float(raw.get("open",0)),
                 "high": float(raw.get("high",0)), "low": float(raw.get("low",0)),
                 "close": float(raw.get("close",0)), "volume": float(raw.get("volume",0)),
                 "turnover": float(raw.get("turnover",0)),
                 "confirm": bool(raw.get("confirm", False))}
        return symbol, interval, kline

    def _handle_message(self, raw_msg: str) -> None:
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            return
        if "op" in msg: return
        result = self._parse_kline(msg)
        if result is None: return
        symbol, interval, kline = result
        if kline.get("confirm") and self.on_kline_closed:
            self.on_kline_closed(symbol, interval, kline)

    def start(self, symbols: List[str], intervals: List[str]) -> None:
        subscribe_args = self._build_subscribe_args(symbols, intervals)
        def on_open(ws):
            ws.send(json.dumps({"op": "subscribe", "args": subscribe_args}))
        def on_message(ws, msg):
            self._handle_message(msg)
        def on_error(ws, error):
            logger.error("WS 오류: %s", error)
        def on_close(ws, code, reason):
            self._running = False
        self._ws = websocket.WebSocketApp(self.ws_url, on_open=on_open,
            on_message=on_message, on_error=on_error, on_close=on_close)
        self._running = True
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ws: self._ws.close()

    @property
    def is_running(self) -> bool:
        return self._running

__all__ = ["BybitWebSocketClient"]
