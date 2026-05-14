"""AlertManager — 텔레그램 알림."""
from __future__ import annotations

import logging
import time
from typing import Dict

import requests

from src.core.config import AlertConfig

logger = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class AlertManager:
    def __init__(self, config: AlertConfig, throttle_seconds: int = 60) -> None:
        self.config = config
        self.throttle_seconds = throttle_seconds
        self._last_sent: Dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return (
            self.config.telegram_enabled
            and bool(self.config.telegram_token)
            and bool(self.config.telegram_chat_id)
        )

    def notify(self, level: str, message: str, bypass_throttle: bool = False) -> None:
        """Send an alert. Throttle applies per-level unless ``bypass_throttle``.

        Trade entry/exit alerts bypass throttle so every fill is reported even
        when multiple trades fire inside the throttle window. Other levels
        (ERROR / DAILY / SYSTEM) stay throttled to prevent spam loops.
        """
        if not self.enabled:
            return
        if not bypass_throttle:
            now = time.time()
            if now - self._last_sent.get(level, 0) < self.throttle_seconds:
                return
            self._last_sent[level] = now
        self._send_telegram(f"[{level}] {message}")

    def on_trade_entry(
        self, symbol: str, side: str, qty: float, price: float, strategy: str
    ) -> None:
        if not self.config.alert_on_trade:
            return
        self.notify(
            "TRADE",
            f"진입: {symbol} {side}\n가격: {price:,.1f} | 수량: {qty}\n전략: {strategy}",
            bypass_throttle=True,
        )

    def on_trade_exit(
        self, symbol: str, side: str, pnl: float, exit_reason: str, strategy: str
    ) -> None:
        if not self.config.alert_on_trade:
            return
        self.notify(
            "TRADE_EXIT",
            f"청산: {symbol} {side} ({exit_reason})\n"
            f"PnL: {'+' if pnl >= 0 else ''}{pnl:,.2f} USDT\n전략: {strategy}",
            bypass_throttle=True,
        )

    def on_error(self, error_msg: str) -> None:
        if not self.config.alert_on_error:
            return
        self.notify("ERROR", error_msg)

    def on_daily_summary(
        self, pnl: float, trades: int, wins: int, equity: float, mdd: float
    ) -> None:
        if not self.config.alert_on_daily_summary:
            return
        self.notify(
            "DAILY",
            f"일일 요약\nPnL: {pnl:+,.2f}\n거래: {trades}건 (승{wins})\n"
            f"에퀴티: {equity:,.0f}\nMDD: {mdd:.1%}",
        )

    def on_system_event(self, event: str) -> None:
        self.notify("SYSTEM", event)

    def _send_telegram(self, text: str) -> None:
        url = TELEGRAM_API.format(token=self.config.telegram_token)
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning("텔레그램 실패: %d", resp.status_code)
        except Exception as exc:
            logger.warning("텔레그램 예외: %s", exc)


__all__ = ["AlertManager"]
