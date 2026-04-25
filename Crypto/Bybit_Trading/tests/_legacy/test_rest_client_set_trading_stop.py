"""Legacy BybitRestClient.set_trading_stop tests."""
from __future__ import annotations
from unittest.mock import MagicMock

from api.rest_client import BybitRestClient


def _make_client() -> BybitRestClient:
    """Build a client without triggering __init__ (which reads settings/auth)."""
    rest = BybitRestClient.__new__(BybitRestClient)
    rest.base_url = "https://example.test"
    rest.auth = MagicMock()
    rest.rate_limit_per_sec = 10
    rest.timeout = 10
    from collections import deque
    rest._request_timestamps = deque()
    rest._post = MagicMock(return_value={"retCode": 0, "result": {}})
    return rest


def test_set_trading_stop_passes_both_sl_and_tp():
    rest = _make_client()
    rest.set_trading_stop(symbol="BTCUSDT", stop_loss=99.5, take_profit=101.5)
    assert rest._post.call_count == 1
    args, kwargs = rest._post.call_args
    assert args[0] == "/v5/position/trading-stop"
    body = kwargs.get("body") or args[1]
    assert body["symbol"] == "BTCUSDT"
    assert body["category"] == "linear"
    assert body["tpslMode"] == "Full"
    assert body["positionIdx"] == 0
    assert body["stopLoss"] == "99.5"
    assert body["takeProfit"] == "101.5"


def test_set_trading_stop_omits_sl_when_none():
    rest = _make_client()
    rest.set_trading_stop(symbol="ETHUSDT", stop_loss=None, take_profit=2500.0)
    args, kwargs = rest._post.call_args
    body = kwargs.get("body") or args[1]
    assert "stopLoss" not in body
    assert body["takeProfit"] == "2500.0"


def test_set_trading_stop_omits_tp_when_none():
    rest = _make_client()
    rest.set_trading_stop(symbol="ETHUSDT", stop_loss=2400.0, take_profit=None)
    args, kwargs = rest._post.call_args
    body = kwargs.get("body") or args[1]
    assert body["stopLoss"] == "2400.0"
    assert "takeProfit" not in body


def test_set_trading_stop_position_idx_for_hedge_mode():
    rest = _make_client()
    rest.set_trading_stop(
        symbol="BTCUSDT", stop_loss=99.0, take_profit=101.0, position_idx=2,
    )
    args, kwargs = rest._post.call_args
    body = kwargs.get("body") or args[1]
    assert body["positionIdx"] == 2
