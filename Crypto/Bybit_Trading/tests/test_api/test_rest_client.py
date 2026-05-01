"""api/rest_client.py 단위 테스트 (mock 기반)."""
import pytest
from unittest.mock import MagicMock, patch
from src.api.rest_client import BybitRestClient

class TestBybitRestClient:
    def setup_method(self):
        self.client = BybitRestClient(api_key="test_key", api_secret="test_secret",
                                      base_url="https://api-demo.bybit.com")

    def test_init(self):
        assert self.client.api_key == "test_key"
        assert self.client.base_url == "https://api-demo.bybit.com"

    @patch("src.api.rest_client.HTTP")
    def test_get_klines(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_kline.return_value = {"retCode": 0, "result": {"list": [
            ["1700000000000","40000","40150","39900","40050","1000","40025000"],
            ["1699996400000","39800","40100","39700","40000","900","35910000"]]}}
        client = BybitRestClient("k","s","https://api-demo.bybit.com")
        bars = client.get_klines("BTCUSDT", "60", limit=2)
        assert len(bars) == 2
        assert bars[0]["symbol"] == "BTCUSDT"
        assert bars[0]["open_time"] == 1700000000000
        assert bars[0]["close"] == 40050.0

    @patch("src.api.rest_client.HTTP")
    def test_get_klines_empty(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_kline.return_value = {"retCode": 0, "result": {"list": []}}
        assert BybitRestClient("k","s","https://api-demo.bybit.com").get_klines("BTCUSDT","60") == []

    @patch("src.api.rest_client.HTTP")
    def test_get_instruments(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_instruments_info.return_value = {"retCode": 0, "result": {"list": [
            {"symbol":"BTCUSDT","baseCoin":"BTC","quoteCoin":"USDT",
             "lotSizeFilter":{"minOrderQty":"0.001","qtyStep":"0.001"},
             "priceFilter":{"tickSize":"0.1","minPrice":"0.1"},
             "leverageFilter":{"maxLeverage":"100"},"contractType":"LinearPerpetual"}]}}
        products = BybitRestClient("k","s","https://api-demo.bybit.com").get_instruments()
        assert len(products) == 1
        assert products[0]["symbol"] == "BTCUSDT"
        assert products[0]["max_leverage"] == 100

    @patch("src.api.rest_client.HTTP")
    def test_place_order(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.place_order.return_value = {"retCode": 0, "result": {"orderId": "order123"}}
        result = BybitRestClient("k","s","https://api-demo.bybit.com").place_order(
            symbol="BTCUSDT", side="Buy", qty="0.01", order_type="Market")
        assert result["orderId"] == "order123"

    @patch("src.api.rest_client.HTTP")
    def test_get_positions(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_positions.return_value = {"retCode": 0, "result": {"list": [
            {"symbol":"BTCUSDT","side":"Buy","size":"0.01","avgPrice":"65000.0",
             "unrealisedPnl":"50.0","leverage":"3"}]}}
        positions = BybitRestClient("k","s","https://api-demo.bybit.com").get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTCUSDT"

    @patch("src.api.rest_client.HTTP")
    def test_get_wallet_balance(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_wallet_balance.return_value = {"retCode": 0, "result": {"list": [
            {"totalEquity": "177000.0", "totalAvailableBalance": "48000.0",
             "coin": [{"coin": "USDT", "walletBalance": "50500.0",
                       "availableToWithdraw": "48000.0"}]}]}}
        balance = BybitRestClient("k","s","https://api-demo.bybit.com").get_wallet_balance()
        assert balance["equity"] == 50500.0
        assert balance["available"] == 48000.0

    @patch("src.api.rest_client.HTTP")
    def test_get_wallet_balance_available_uses_usdt_coin_when_withdraw_empty(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_wallet_balance.return_value = {"retCode": 0, "result": {"list": [
            {"totalEquity": "177000.0", "totalAvailableBalance": "98482.0",
             "coin": [{"coin": "USDT", "walletBalance": "48517.0",
                       "equity": "48517.0", "availableToWithdraw": ""}]}]}}
        balance = BybitRestClient("k","s","https://api-demo.bybit.com").get_wallet_balance()
        assert balance["equity"] == 48517.0
        assert balance["available"] == 48517.0

    @patch("src.api.rest_client.HTTP")
    def test_get_wallet_balance_falls_back_to_total_equity(self, mock_http_cls):
        mock_session = MagicMock()
        mock_http_cls.return_value = mock_session
        mock_session.get_wallet_balance.return_value = {"retCode": 0, "result": {"list": [
            {"totalEquity": "50500.0", "totalAvailableBalance": "48000.0"}]}}
        balance = BybitRestClient("k","s","https://api-demo.bybit.com").get_wallet_balance()
        assert balance["equity"] == 50500.0
        assert balance["available"] == 48000.0
