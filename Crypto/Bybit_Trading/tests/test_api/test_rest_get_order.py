"""Stage C-2b: BybitRestClient.get_order wrapper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api.rest_client import BybitRestClient


class TestGetOrder:
    @patch("src.api.rest_client.HTTP")
    def test_returns_single_order_record(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_order_history.return_value = {
            "retCode": 0, "retMsg": "OK",
            "result": {"list": [
                {
                    "orderId": "OID-1",
                    "symbol": "BTCUSDT",
                    "avgPrice": "70010.5",
                    "cumExecQty": "0.01",
                    "orderStatus": "Filled",
                },
            ]},
        }
        client = BybitRestClient("k", "s", "https://api-demo.bybit.com")
        data = client.get_order(order_id="OID-1", symbol="BTCUSDT")
        assert data["orderId"] == "OID-1"
        assert data["avgPrice"] == "70010.5"
        assert data["cumExecQty"] == "0.01"
        mock_session.get_order_history.assert_called_once_with(
            category="linear", symbol="BTCUSDT", orderId="OID-1",
        )

    @patch("src.api.rest_client.HTTP")
    def test_empty_list_returns_empty_dict(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_order_history.return_value = {
            "retCode": 0, "result": {"list": []},
        }
        client = BybitRestClient("k", "s", "https://api-demo.bybit.com")
        assert client.get_order(order_id="OID-1", symbol="BTCUSDT") == {}

    @patch("src.api.rest_client.HTTP")
    def test_non_zero_retcode_returns_empty_dict_and_warns(
        self, mock_http_cls, caplog,
    ):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_order_history.return_value = {
            "retCode": 10001, "retMsg": "invalid orderId",
        }
        client = BybitRestClient("k", "s", "https://api-demo.bybit.com")
        import logging
        with caplog.at_level(logging.WARNING, logger="src.api.rest_client"):
            assert client.get_order(order_id="BAD", symbol="BTCUSDT") == {}
        assert any("non-zero retCode" in r.message for r in caplog.records)

    @patch("src.api.rest_client.HTTP")
    def test_session_exception_returns_empty_dict(self, mock_http_cls, caplog):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_order_history.side_effect = RuntimeError("network down")
        client = BybitRestClient("k", "s", "https://api-demo.bybit.com")
        import logging
        with caplog.at_level(logging.WARNING, logger="src.api.rest_client"):
            assert client.get_order(order_id="OID-1", symbol="BTCUSDT") == {}
        assert any("raised" in r.message for r in caplog.records)
