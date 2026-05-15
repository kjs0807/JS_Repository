"""Bybit REST API 클라이언트. pybit SDK 래퍼."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pybit.unified_trading import HTTP

logger = logging.getLogger(__name__)


class BybitRestClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        is_demo = "demo" in base_url.lower()
        self._session = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            demo=is_demo,
        )

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        resp = self._session.get_kline(**params)
        if resp.get("retCode") != 0:
            logger.error("get_kline 실패: %s", resp)
            return []
        return [
            {
                "symbol": symbol,
                "open_time": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "turnover": float(item[6]) if len(item) > 6 else None,
            }
            for item in resp.get("result", {}).get("list", [])
        ]

    def get_instruments(self) -> List[Dict[str, Any]]:
        resp = self._session.get_instruments_info(category="linear")
        if resp.get("retCode") != 0:
            return []
        products = []
        for item in resp.get("result", {}).get("list", []):
            lot = item.get("lotSizeFilter", {})
            price = item.get("priceFilter", {})
            lev = item.get("leverageFilter", {})
            products.append(
                {
                    "symbol": item["symbol"],
                    "base_coin": item.get("baseCoin", ""),
                    "quote_coin": item.get("quoteCoin", "USDT"),
                    "min_qty": float(lot.get("minOrderQty", 0)),
                    "qty_step": float(lot.get("qtyStep", 0)),
                    "tick_size": float(price.get("tickSize", 0)),
                    "min_notional": float(price.get("minPrice", 0)),
                    "max_leverage": int(float(lev.get("maxLeverage", 0))),
                    "contract_type": item.get("contractType", ""),
                    "updated_at": None,
                }
            )
        return products

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: str,
        order_type: str = "Market",
        stop_loss: Optional[str] = None,
        take_profit: Optional[str] = None,
        position_idx: Optional[int] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": qty,
        }
        if stop_loss:
            params["stopLoss"] = stop_loss
        if take_profit:
            params["takeProfit"] = take_profit
        # 헤지모드: positionIdx 필수 (1=Long, 2=Short)
        # 원웨이: positionIdx=0
        if position_idx is not None:
            params["positionIdx"] = position_idx
        else:
            # 기본: 헤지모드 가정 (Buy→Long=1, Sell→Short=2)
            params["positionIdx"] = 1 if side == "Buy" else 2
        if reduce_only:
            params["reduceOnly"] = True
        resp = self._session.place_order(**params)
        if resp.get("retCode") != 0:
            logger.error("place_order 실패: %s", resp)
            return {"error": resp.get("retMsg", "unknown")}
        return resp.get("result", {})

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        position_idx: int = 0,
    ) -> Dict[str, Any]:
        """Bybit /v5/position/trading-stop via pybit.

        체결 후 또는 BE/trail 트리거 시 SL/TP를 갱신할 때 사용.
        positionIdx: 0=OneWay, 1=Hedge Buy, 2=Hedge Sell.
        Round 5 §5.1 참조. category="linear", tpslMode="Full" 고정.

        Raises: pybit/HTTP 예외 - caller가 잡아 WARN 로그로 처리할 것.
        """
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "tpslMode": "Full",
            "positionIdx": position_idx,
        }
        if stop_loss is not None:
            params["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            params["takeProfit"] = str(take_profit)
        return self._session.set_trading_stop(**params)

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        resp = self._session.cancel_order(
            category="linear", symbol=symbol, orderId=order_id
        )
        return resp.get("result", {})

    def get_positions(
        self, symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return position rows for the linear category.

        Without ``symbol`` Bybit returns only rows with ``size > 0``
        (one of the gotchas around the V5 API). Pass ``symbol`` to get
        every hedge-mode slot for that instrument, including empty ones
        (needed by :meth:`BbkcBroker.ensure_leverage_set` for read-back).
        """
        if symbol is None:
            resp = self._session.get_positions(category="linear", settleCoin="USDT")
        else:
            resp = self._session.get_positions(category="linear", symbol=symbol)
        if resp.get("retCode") != 0:
            return []
        return resp.get("result", {}).get("list", [])

    def get_wallet_balance(self) -> Dict[str, float]:
        resp = self._session.get_wallet_balance(accountType="UNIFIED")
        if resp.get("retCode") != 0:
            return {"equity": 0.0, "available": 0.0}
        accounts = resp.get("result", {}).get("list", [])
        if not accounts:
            return {"equity": 0.0, "available": 0.0}
        acct = accounts[0]
        usdt = next(
            (coin for coin in acct.get("coin", []) if coin.get("coin") == "USDT"),
            {},
        )
        if usdt:
            wallet_balance = float(usdt.get("walletBalance", 0) or 0)
            available_raw = (
                usdt.get("availableToWithdraw")
                or usdt.get("equity")
                or usdt.get("walletBalance")
                or 0
            )
            available = float(available_raw or 0)
            return {"equity": wallet_balance, "available": available}
        return {
            "equity": float(acct.get("totalEquity", 0)),
            "available": float(acct.get("totalAvailableBalance", 0)),
        }

    def get_funding_history(
        self,
        symbol: str,
        limit: int = 200,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """펀딩비 이력 조회.

        Returns:
            [{symbol, funding_rate, funding_time}, ...] for DB upsert
        """
        params: Dict[str, Any] = {
            "category": "linear", "symbol": symbol, "limit": limit,
        }
        if start:
            params["startTime"] = start
        if end:
            params["endTime"] = end
        resp = self._session.get_funding_rate_history(**params)
        if resp.get("retCode") != 0:
            logger.error("get_funding_rate_history 실패: %s", resp)
            return []
        items = resp.get("result", {}).get("list", [])
        return [
            {
                "symbol": item.get("symbol", symbol),
                "funding_rate": float(item.get("fundingRate", 0)),
                "funding_time": int(item.get("fundingRateTimestamp", 0)),
            }
            for item in items
        ]

    def get_open_interest_history(
        self,
        symbol: str,
        interval_time: str = "1h",
        limit: int = 200,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Open Interest 이력 조회.

        Args:
            symbol: 심볼
            interval_time: "5min", "15min", "30min", "1h", "4h", "1d"

        Returns:
            [{symbol, open_interest, open_interest_value, timestamp}, ...]
        """
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": interval_time,
            "limit": limit,
        }
        if start:
            params["startTime"] = start
        if end:
            params["endTime"] = end
        resp = self._session.get_open_interest(**params)
        if resp.get("retCode") != 0:
            logger.error("get_open_interest 실패: %s", resp)
            return []
        items = resp.get("result", {}).get("list", [])
        return [
            {
                "symbol": symbol,
                "open_interest": float(item.get("openInterest", 0)),
                "open_interest_value": float(item.get("openInterestValue", 0)) if item.get("openInterestValue") else None,
                "timestamp": int(item.get("timestamp", 0)),
            }
            for item in items
        ]

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        try:
            self._session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            return True
        except Exception:
            return False


__all__ = ["BybitRestClient"]
