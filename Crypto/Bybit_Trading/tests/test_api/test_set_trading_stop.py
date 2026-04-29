"""BybitRestClient.set_trading_stop 단위 테스트 (round 5)."""
from unittest.mock import MagicMock

from src.api.rest_client import BybitRestClient


def _make_client() -> BybitRestClient:
    """Bypass __init__ to avoid pybit HTTP construction with real keys."""
    rest = BybitRestClient.__new__(BybitRestClient)
    rest.api_key = "k"
    rest.api_secret = "s"
    rest.base_url = "https://api-demo.bybit.com"
    rest._session = MagicMock()
    rest._session.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    return rest


def test_set_trading_stop_passes_both_sl_and_tp():
    rest = _make_client()
    rest.set_trading_stop(symbol="BTCUSDT", stop_loss=99.5, take_profit=101.5,
                          position_idx=1)
    assert rest._session.set_trading_stop.call_count == 1
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert kwargs["category"] == "linear"
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["tpslMode"] == "Full"
    assert kwargs["positionIdx"] == 1
    assert kwargs["stopLoss"] == "99.5"
    assert kwargs["takeProfit"] == "101.5"


def test_set_trading_stop_omits_sl_when_none():
    rest = _make_client()
    rest.set_trading_stop(symbol="ETHUSDT", stop_loss=None, take_profit=2500.0,
                          position_idx=2)
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert "stopLoss" not in kwargs
    assert kwargs["takeProfit"] == "2500.0"
    assert kwargs["positionIdx"] == 2


def test_set_trading_stop_omits_tp_when_none():
    rest = _make_client()
    rest.set_trading_stop(symbol="ETHUSDT", stop_loss=2400.0, take_profit=None,
                          position_idx=1)
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert kwargs["stopLoss"] == "2400.0"
    assert "takeProfit" not in kwargs


def test_set_trading_stop_default_position_idx_zero():
    rest = _make_client()
    rest.set_trading_stop(symbol="BTCUSDT", stop_loss=99.0)
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert kwargs["positionIdx"] == 0


def test_set_trading_stop_returns_session_response():
    rest = _make_client()
    rest._session.set_trading_stop.return_value = {"retCode": 0, "result": {"x": 1}}
    out = rest.set_trading_stop(symbol="BTCUSDT", stop_loss=99.0, position_idx=1)
    assert out == {"retCode": 0, "result": {"x": 1}}
