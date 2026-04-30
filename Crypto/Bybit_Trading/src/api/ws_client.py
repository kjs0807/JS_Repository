"""Bybit WebSocket client for realtime kline events."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import websocket

logger = logging.getLogger(__name__)
DEFAULT_WS_URL = "wss://stream.bybit.com/v5/public/linear"


class BybitWebSocketClient:
    def __init__(
        self,
        ws_url: str = DEFAULT_WS_URL,
        reconnect_delay: float = 5.0,
    ) -> None:
        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._subscriptions: List[str] = []
        self._lock = threading.RLock()
        self.on_kline_closed: Optional[Callable[[str, str, Dict], None]] = None

    def _build_subscribe_args(
        self, symbols: List[str], intervals: List[str],
    ) -> List[str]:
        return [f"kline.{iv}.{sym}" for sym in symbols for iv in intervals]

    def _parse_kline(self, msg: Dict) -> Optional[Tuple[str, str, Dict]]:
        topic = msg.get("topic", "")
        if not topic.startswith("kline."):
            return None
        parts = topic.split(".")
        if len(parts) < 3:
            return None
        interval, symbol = parts[1], parts[2]
        data_list = msg.get("data", [])
        if not data_list:
            return None
        raw = data_list[0]
        kline = {
            "start": int(raw.get("start", 0)),
            "open": float(raw.get("open", 0)),
            "high": float(raw.get("high", 0)),
            "low": float(raw.get("low", 0)),
            "close": float(raw.get("close", 0)),
            "volume": float(raw.get("volume", 0)),
            "turnover": float(raw.get("turnover", 0)),
            "confirm": bool(raw.get("confirm", False)),
        }
        return symbol, interval, kline

    def _handle_message(self, raw_msg: str) -> None:
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            return
        if "op" in msg:
            return
        result = self._parse_kline(msg)
        if result is None:
            return
        symbol, interval, kline = result
        if kline.get("confirm") and self.on_kline_closed:
            self.on_kline_closed(symbol, interval, kline)

    def start(self, symbols: List[str], intervals: List[str]) -> None:
        with self._lock:
            if self.is_running:
                logger.warning("WS already running")
                return
            self._subscriptions = self._build_subscribe_args(symbols, intervals)
            self._running = True
            self._connected = False
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="BybitWebSocket",
        )
        self._thread.start()

    def _run_loop(self) -> None:
        while self._running:
            subscribe_args = list(self._subscriptions)

            def on_open(ws):
                with self._lock:
                    self._connected = True
                ws.send(json.dumps({"op": "subscribe", "args": subscribe_args}))
                logger.info("WS connected; subscribed topics=%d", len(subscribe_args))

            def on_message(ws, msg):
                self._handle_message(msg)

            def on_error(ws, error):
                with self._lock:
                    self._connected = False
                logger.error("WS error: %s", error)

            def on_close(ws, code, reason):
                with self._lock:
                    self._connected = False
                logger.warning("WS closed: code=%s reason=%s", code, reason)

            with self._lock:
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                ws = self._ws

            ws.run_forever(ping_interval=20, ping_timeout=10)

            with self._lock:
                self._connected = False
                should_reconnect = self._running
            if should_reconnect:
                logger.warning(
                    "WS disconnected; reconnecting in %.1fs",
                    self.reconnect_delay,
                )
                time.sleep(self.reconnect_delay)

        with self._lock:
            self._ws = None
            self._connected = False

    def stop(self) -> None:
        with self._lock:
            self._running = False
            ws = self._ws
        if ws:
            ws.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    @property
    def is_running(self) -> bool:
        return bool(self._running and self._thread and self._thread.is_alive())

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_stats(self) -> Dict:
        return {
            "running": self.is_running,
            "connected": self.is_connected,
            "subscriptions": list(self._subscriptions),
            "ws_url": self.ws_url,
        }


__all__ = ["BybitWebSocketClient"]
