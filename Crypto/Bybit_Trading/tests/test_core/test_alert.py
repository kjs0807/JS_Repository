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

    @patch("src.core.alert.requests.post")
    def test_trade_entries_bypass_throttle(self, mock_post):
        """Back-to-back trade entries must all fire — every fill matters."""
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(
            AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c", alert_on_trade=True),
            throttle_seconds=60,
        )
        mgr.on_trade_entry("BTCUSDT", "LONG", 0.01, 65000.0, "Test")
        mgr.on_trade_entry("ETHUSDT", "SHORT", 0.5, 2400.0, "Test")
        mgr.on_trade_entry("BTCUSDT", "LONG", 0.02, 65100.0, "Test")
        assert mock_post.call_count == 3

    @patch("src.core.alert.requests.post")
    def test_trade_exits_bypass_throttle(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(
            AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c", alert_on_trade=True),
            throttle_seconds=60,
        )
        mgr.on_trade_exit("BTCUSDT", "LONG", 150.0, "TP", "Test")
        mgr.on_trade_exit("ETHUSDT", "SHORT", -80.0, "STOP", "Test")
        assert mock_post.call_count == 2

    @patch("src.core.alert.requests.post")
    def test_non_trade_levels_still_throttled(self, mock_post):
        """Regression guard: ERROR / DAILY / SYSTEM must remain throttled."""
        mock_post.return_value = MagicMock(status_code=200)
        mgr = AlertManager(
            AlertConfig(telegram_enabled=True, telegram_token="t", telegram_chat_id="c",
                        alert_on_error=True, alert_on_daily_summary=True),
            throttle_seconds=60,
        )
        mgr.on_error("first error")
        mgr.on_error("second error")
        mgr.on_system_event("first system")
        mgr.on_system_event("second system")
        assert mock_post.call_count == 2  # one per level, second drops by throttle


# ---------------------------------------------------------------------------
# Stage C-1: operational lifecycle alerts. These must bypass the
# throttle — collisions with a TRADE alert one second earlier would
# otherwise hide a breaker trip from the operator.
# ---------------------------------------------------------------------------
class TestLifecycleAlerts:
    def _mgr(self, throttle=60):
        return AlertManager(
            AlertConfig(
                telegram_enabled=True, telegram_token="t", telegram_chat_id="c",
                alert_on_error=True,
            ),
            throttle_seconds=throttle,
        )

    @patch("src.core.alert.requests.post")
    def test_on_start_sent_with_fingerprint(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = self._mgr()
        # Fingerprint uses ASCII ``head...tail`` per src.core.mode.fingerprint.
        mgr.on_start(
            mode="demo", strategy="BBKCSqueeze",
            universe=["BTCUSDT", "ETHUSDT"], leverage=3,
            equity=50_000.0,
            api_key_fingerprint="abcd...wxyz",
            timeframe="1h", run_id="today",
        )
        assert mock_post.call_count == 1
        body = str(mock_post.call_args)
        assert "abcd...wxyz" in body
        # Secret-shaped strings (raw token) must never appear.
        assert "telegram_token" not in body

    @patch("src.core.alert.requests.post")
    def test_lifecycle_alerts_are_ascii_or_clean_utf8(self, mock_post):
        """Regression guard for the C-1 mojibake hotfix: no em-dash or
        glyph that breaks on cp949 (Windows Korean) consoles. Telegram
        itself is fine with UTF-8, but the audit/console mirror must
        render cleanly too."""
        mock_post.return_value = MagicMock(status_code=200)
        mgr = self._mgr()
        for ch in ("—", "–", "…", "⚠"):  # — – … ⚠
            mgr.on_breaker_tripped(
                rate=0.4, failures=2, total=5,
                top_category="min_qty", window_minutes=60,
            )
            mgr.on_kill_switch_engaged(reason="circuit_breaker: ...")
            body = str(mock_post.call_args)
            assert ch not in body

    @patch("src.core.alert.requests.post")
    def test_on_start_bypasses_throttle(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = self._mgr(throttle=60)
        # Saturate the START level by firing back-to-back; both fire.
        mgr.on_start(
            mode="demo", strategy="X", universe=["A"], leverage=3,
            equity=1.0, api_key_fingerprint="f",
        )
        mgr.on_start(
            mode="demo", strategy="Y", universe=["B"], leverage=3,
            equity=1.0, api_key_fingerprint="f",
        )
        assert mock_post.call_count == 2

    @patch("src.core.alert.requests.post")
    def test_on_shutdown_sent(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = self._mgr()
        mgr.on_shutdown(
            reason="signal 2", equity=49_800.0,
            daily_pnl=-200.0, positions=0, bars_seen=1234,
        )
        assert mock_post.call_count == 1

    @patch("src.core.alert.requests.post")
    def test_on_breaker_tripped_sent(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = self._mgr()
        mgr.on_breaker_tripped(
            rate=0.4, failures=2, total=5,
            top_category="min_qty", window_minutes=60,
        )
        assert mock_post.call_count == 1
        body = str(mock_post.call_args)
        assert "min_qty" in body

    @patch("src.core.alert.requests.post")
    def test_on_kill_switch_engaged_sent(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mgr = self._mgr()
        mgr.on_kill_switch_engaged(reason="file disable_new_entry.flag")
        assert mock_post.call_count == 1
        body = str(mock_post.call_args)
        assert "disable_new_entry.flag" in body

    @patch("src.core.alert.requests.post")
    def test_breaker_and_kill_switch_bypass_throttle_against_each_other(
        self, mock_post,
    ):
        """A breaker trip immediately followed by a kill-switch engage
        must produce TWO Telegram messages, not one."""
        mock_post.return_value = MagicMock(status_code=200)
        mgr = self._mgr(throttle=60)
        mgr.on_breaker_tripped(
            rate=0.4, failures=2, total=5,
            top_category="min_qty", window_minutes=60,
        )
        mgr.on_kill_switch_engaged(reason="circuit_breaker: ...")
        assert mock_post.call_count == 2
