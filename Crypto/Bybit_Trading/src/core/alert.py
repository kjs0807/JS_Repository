"""AlertManager - 텔레그램 알림."""
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

    # ------------------------------------------------------------------
    # Stage C-1: operational lifecycle alerts.
    # ``bypass_throttle=True`` on each - these are once-per-event signals
    # the operator must not miss because of a throttle window collision.
    # API keys are passed as fingerprints by the caller; secrets must
    # never reach this method.
    # ------------------------------------------------------------------
    def on_start(
        self, *, mode: str, strategy: str, universe: list, leverage: int,
        equity: float, api_key_fingerprint: str, timeframe: str = "",
        run_id: str = "",
    ) -> None:
        body = (
            f"기동: {strategy} ({mode})\n"
            f"유니버스: {', '.join(universe)}\n"
            f"타임프레임: {timeframe} | 레버리지: {leverage}x\n"
            f"에퀴티: {equity:,.2f} USDT\n"
            f"API key: {api_key_fingerprint}\n"
            f"run_id: {run_id}"
        )
        self.notify("START", body, bypass_throttle=True)

    def on_shutdown(
        self, *, reason: str, equity: float, daily_pnl: float,
        positions: int, bars_seen: int,
    ) -> None:
        body = (
            f"종료: {reason}\n"
            f"에퀴티: {equity:,.2f} USDT (일일 PnL {daily_pnl:+,.2f})\n"
            f"잔여 포지션: {positions} | bars_seen: {bars_seen}"
        )
        self.notify("SHUTDOWN", body, bypass_throttle=True)

    def on_breaker_tripped(
        self, *, rate: float, failures: int, total: int,
        top_category: str, window_minutes: int,
    ) -> None:
        body = (
            f"[!] Circuit breaker TRIPPED\n"
            f"실패율: {rate * 100:.1f}% ({failures}/{total}) "
            f"in {window_minutes}min\n"
            f"top category: {top_category}\n"
            f"신규 진입 자동 차단 - 기존 포지션 관리는 계속"
        )
        self.notify("BREAKER", body, bypass_throttle=True)

    def on_kill_switch_engaged(self, *, reason: str) -> None:
        body = (
            f"[!] Kill switch ENGAGED\n"
            f"사유: {reason}\n"
            f"신규 진입 차단 - close/SL/TP 관리는 계속"
        )
        self.notify("KILL_SWITCH", body, bypass_throttle=True)

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
