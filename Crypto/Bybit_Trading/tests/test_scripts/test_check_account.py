"""Stage C-2c: scripts/check_account.py mode migration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import scripts.check_account as ca
from src.core import mode as M


@pytest.fixture
def _no_keys(monkeypatch):
    """Strip every Bybit credential env so resolve_api_credentials sees
    a clean slate. Each test that wants creds sets its own."""
    for k in (
        "BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET",
        "BYBIT_LIVE_API_KEY", "BYBIT_LIVE_API_SECRET",
        "BYBIT_API_KEY", "BYBIT_API_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)


class TestModeResolution:
    @patch("scripts.check_account.BybitRestClient")
    def test_demo_default_uses_demo_endpoint(
        self, mock_rest_cls, monkeypatch, _no_keys, capsys,
    ):
        monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo_k_abcd1234abcd")
        monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo_s_efgh5678efgh")
        rest = MagicMock()
        rest.get_positions.return_value = []
        rest.get_wallet_balance.return_value = {"equity": 0.0, "available": 0.0}
        mock_rest_cls.return_value = rest
        rc = ca.main([])
        out = capsys.readouterr().out
        assert rc == 0
        # Endpoint must be the demo one.
        passed_base_url = mock_rest_cls.call_args.args[2]
        assert "api-demo.bybit.com" in passed_base_url
        # Banner shows mode + fingerprint (NOT the secret).
        assert "mode      : demo" in out
        # Fingerprint shows head...tail format only.
        assert "demo" in out
        assert "demo_s_efgh5678efgh" not in out
        assert "demo_k_abcd1234abcd" not in out

    @patch("scripts.check_account.BybitRestClient")
    def test_live_requires_ack(self, mock_rest_cls, monkeypatch, _no_keys, capsys):
        monkeypatch.setenv("BYBIT_LIVE_API_KEY", "live_k_aaaa1111aaaa")
        monkeypatch.setenv("BYBIT_LIVE_API_SECRET", "live_s_bbbb2222bbbb")
        rc = ca.main(["--mode", "live"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "ERROR:" in out
        # REST client must not have been constructed.
        mock_rest_cls.assert_not_called()

    @patch("scripts.check_account.BybitRestClient")
    def test_live_with_ack_uses_live_endpoint(
        self, mock_rest_cls, monkeypatch, _no_keys, capsys,
    ):
        monkeypatch.setenv("BYBIT_LIVE_API_KEY", "live_k_aaaa1111aaaa")
        monkeypatch.setenv("BYBIT_LIVE_API_SECRET", "live_s_bbbb2222bbbb")
        rest = MagicMock()
        rest.get_positions.return_value = []
        rest.get_wallet_balance.return_value = {"equity": 0.0, "available": 0.0}
        mock_rest_cls.return_value = rest
        rc = ca.main(["--mode", "live", "--i-understand-real-money"])
        out = capsys.readouterr().out
        assert rc == 0
        passed_base_url = mock_rest_cls.call_args.args[2]
        # Live endpoint, no -demo subdomain.
        assert "api-demo.bybit.com" not in passed_base_url
        assert "bybit.com" in passed_base_url
        # The live banner must shout REAL MONEY.
        assert "LIVE" in out
        # Secrets stay out of stdout.
        assert "live_s_bbbb2222bbbb" not in out
        assert "live_k_aaaa1111aaaa" not in out

    @patch("scripts.check_account.BybitRestClient")
    def test_legacy_keys_not_accepted(
        self, mock_rest_cls, monkeypatch, _no_keys, capsys,
    ):
        """C-2c: the legacy un-prefixed pair is no longer honoured. With
        ONLY ``BYBIT_API_KEY`` set, demo resolution refuses to start."""
        monkeypatch.setenv("BYBIT_API_KEY", "legacy_k_xxxx1111xxxx")
        monkeypatch.setenv("BYBIT_API_SECRET", "legacy_s_yyyy2222yyyy")
        rc = ca.main([])
        out = capsys.readouterr().out
        assert rc == 1
        assert "ERROR:" in out
        mock_rest_cls.assert_not_called()
