"""core/alert.py 단위 테스트."""
import pytest
from unittest.mock import patch, MagicMock
from src.core.config import AlertConfig
from src.core.alert import AlertManager

class TestAlertManager:
    def test_init_disabled(self):
        assert AlertManager(AlertConfig(telegram_enabled=False)).enabled is False

    def test_init_enabled(self):
        assert AlertManager(AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c")).enabled is True

    @patch("src.core.alert.requests.post")
    def test_notify_sends_telegram(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(AlertConfig(telegram_enabled=True, telegram_token="bot123", telegram_chat_id="chat456"))
        mgr.notify("INFO", "테스트 메시지")
        mock_post.assert_called_once()
        assert "chat456" in str(mock_post.call_args)

    def test_notify_disabled_no_call(self):
        AlertManager(AlertConfig(telegram_enabled=False)).notify("INFO", "should not send")

    @patch("src.core.alert.requests.post")
    def test_throttling(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c"), throttle_seconds=60)
        mgr.notify("INFO", "first")
        mgr.notify("INFO", "second")
        assert mock_post.call_count == 1

    @patch("src.core.alert.requests.post")
    def test_different_levels_not_throttled(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c"), throttle_seconds=60)
        mgr.notify("INFO", "info")
        mgr.notify("WARNING", "warning")
        assert mock_post.call_count == 2

    @patch("src.core.alert.requests.post")
    def test_on_trade_entry(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c", alert_on_trade=True), throttle_seconds=0)
        mgr.on_trade_entry(symbol="BTCUSDT", side="LONG", qty=0.01, price=65000.0, strategy="Test")
        assert mock_post.call_count == 1

    @patch("src.core.alert.requests.post")
    def test_on_trade_exit(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c", alert_on_trade=True), throttle_seconds=0)
        mgr.on_trade_exit(symbol="BTCUSDT", side="LONG", pnl=150.0, exit_reason="TP", strategy="Test")
        assert mock_post.call_count == 1

    @patch("src.core.alert.requests.post")
    def test_on_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c", alert_on_error=True), throttle_seconds=0)
        mgr.on_error("API 연결 실패")
        assert mock_post.call_count == 1

    def test_on_trade_entry_disabled(self):
        mgr = AlertManager(AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c", alert_on_trade=False))
        mgr.on_trade_entry("BTCUSDT", "LONG", 0.01, 65000.0, "Test")
