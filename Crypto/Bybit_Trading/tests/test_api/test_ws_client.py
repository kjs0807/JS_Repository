"""api/ws_client.py 단위 테스트 (mock 기반)."""
import json
import pytest
from src.api.ws_client import BybitWebSocketClient

class TestBybitWebSocketClient:
    def test_init(self):
        ws = BybitWebSocketClient(ws_url="wss://stream.bybit.com/v5/public/linear")
        assert ws.ws_url == "wss://stream.bybit.com/v5/public/linear"

    def test_build_subscribe_args(self):
        ws = BybitWebSocketClient()
        args = ws._build_subscribe_args(["BTCUSDT","ETHUSDT"], ["60"])
        assert "kline.60.BTCUSDT" in args and "kline.60.ETHUSDT" in args

    def test_parse_kline_message(self):
        ws = BybitWebSocketClient()
        msg = {"topic":"kline.60.BTCUSDT","type":"snapshot","data":[
            {"start":1700000000000,"end":1700003600000,"interval":"60",
             "open":"65000","close":"65500","high":"66000","low":"64500",
             "volume":"1000","turnover":"65250000","confirm":True}]}
        result = ws._parse_kline(msg)
        assert result is not None
        symbol, interval, kline = result
        assert symbol == "BTCUSDT" and interval == "60"
        assert kline["close"] == 65500.0 and kline["confirm"] is True

    def test_parse_kline_unconfirmed(self):
        ws = BybitWebSocketClient()
        msg = {"topic":"kline.60.BTCUSDT","data":[
            {"start":1700000000000,"interval":"60","open":"65000","close":"65100",
             "high":"65200","low":"64900","volume":"500","turnover":"32550000","confirm":False}]}
        _, _, kline = ws._parse_kline(msg)
        assert kline["confirm"] is False

    def test_on_kline_closed_callback(self):
        ws = BybitWebSocketClient()
        calls = []
        ws.on_kline_closed = lambda sym, interval, kline: calls.append((sym, interval))
        msg = {"topic":"kline.60.BTCUSDT","data":[
            {"start":1700000000000,"interval":"60","open":"65000","close":"65500",
             "high":"66000","low":"64500","volume":"1000","turnover":"65250000","confirm":True}]}
        ws._handle_message(json.dumps(msg))
        assert len(calls) == 1 and calls[0][0] == "BTCUSDT"

    def test_unconfirmed_kline_not_callback(self):
        ws = BybitWebSocketClient()
        calls = []
        ws.on_kline_closed = lambda sym, interval, kline: calls.append(1)
        msg = {"topic":"kline.60.BTCUSDT","data":[
            {"start":1700000000000,"interval":"60","open":"65000","close":"65100",
             "high":"65200","low":"64900","volume":"500","turnover":"32550000","confirm":False}]}
        ws._handle_message(json.dumps(msg))
        assert len(calls) == 0
