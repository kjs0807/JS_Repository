"""Bybit REST API v5 클라이언트.

공개 엔드포인트(Kline, InstrumentsInfo)와 인증 필요 엔드포인트(주문, 잔고)를 제공한다.
슬라이딩 윈도우 Rate Limit(10req/s)을 내장한다.
"""

import json
import time
import logging
from collections import deque
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class BybitAPIError(Exception):
    """Bybit API가 retCode != 0 응답을 반환했을 때 발생.

    Attributes:
        ret_code: Bybit 에러 코드
        ret_msg: Bybit 에러 메시지
    """

    def __init__(self, ret_code: int, ret_msg: str) -> None:
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit API Error [{ret_code}]: {ret_msg}")


class BybitRestClient:
    """Bybit REST API v5 클라이언트.

    공개/인증 엔드포인트를 모두 지원한다.
    Rate Limit(10req/s)과 응답 검증을 내장한다.

    Attributes:
        base_url: Bybit API 서버 URL
        auth: BybitAuthManager 인스턴스 (None이면 공개 API만 사용)
        rate_limit_per_sec: 초당 최대 요청 수
        timeout: 요청 타임아웃 (초)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        auth: Optional[Any] = None,  # BybitAuthManager
        rate_limit_per_sec: int = 10,
        timeout: int = 10,
    ) -> None:
        """BybitRestClient 초기화.

        Args:
            base_url: Bybit API 서버 URL. None이면 AppSettings에서 로드.
            auth: BybitAuthManager 인스턴스. None이면 공개 API만 가능.
            rate_limit_per_sec: 초당 최대 요청 수 (기본 10)
            timeout: HTTP 요청 타임아웃 초 (기본 10)
        """
        if base_url is None:
            from config.settings import settings
            base_url = settings.base_url
        self.base_url = base_url.rstrip("/")
        if auth is None:
            try:
                from api.auth import BybitAuthManager
                auth = BybitAuthManager.from_settings()
            except (ValueError, ImportError):
                pass  # 공개 API만 사용
        self.auth = auth
        self.rate_limit_per_sec = rate_limit_per_sec
        self.timeout = timeout
        self._request_timestamps: deque = deque()

    # ── Rate Limit ──────────────────────────────────────────────────────

    def _rate_limit(self) -> None:
        """슬라이딩 윈도우 Rate Limit 적용.

        1초 내 요청 수가 rate_limit_per_sec를 초과하면 sleep으로 대기.
        """
        now = time.time()
        # 1초 이전 타임스탬프 제거
        while self._request_timestamps and now - self._request_timestamps[0] > 1.0:
            self._request_timestamps.popleft()

        if len(self._request_timestamps) >= self.rate_limit_per_sec:
            oldest = self._request_timestamps[0]
            sleep_time = 1.0 - (now - oldest)
            if sleep_time > 0:
                time.sleep(sleep_time)
            now = time.time()
            while (
                self._request_timestamps
                and now - self._request_timestamps[0] > 1.0
            ):
                self._request_timestamps.popleft()

        self._request_timestamps.append(time.time())

    # ── 응답 검증 ────────────────────────────────────────────────────────

    def _validate(self, data: Dict[str, Any]) -> None:
        """Bybit API 응답 retCode 검증.

        Args:
            data: Bybit API JSON 응답

        Raises:
            BybitAPIError: retCode가 0이 아닌 경우
        """
        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            ret_msg = data.get("retMsg", "Unknown error")
            raise BybitAPIError(ret_code, ret_msg)

    # ── HTTP 공통 메서드 ─────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        auth_required: bool = False,
    ) -> Dict[str, Any]:
        """GET 요청 실행.

        Args:
            path: API 경로 (예: "/v5/market/kline")
            params: 쿼리 파라미터
            auth_required: True이면 인증 헤더 첨부

        Returns:
            JSON 응답 딕셔너리

        Raises:
            BybitAPIError: retCode != 0
            requests.RequestException: 네트워크/HTTP 오류
        """
        self._rate_limit()
        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = {}
        str_params: Dict[str, str] = {k: str(v) for k, v in (params or {}).items()}

        if auth_required:
            if self.auth is None:
                raise ValueError(
                    f"인증이 필요한 요청이지만 auth가 설정되지 않았습니다: {path}"
                )
            auth_headers = self.auth.sign_get(str_params)
            headers.update(auth_headers)

        response = requests.get(
            url, headers=headers, params=str_params, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        self._validate(data)
        return data

    def _post(
        self,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        auth_required: bool = True,
    ) -> Dict[str, Any]:
        """POST 요청 실행.

        Args:
            path: API 경로
            body: 요청 바디 딕셔너리
            auth_required: True이면 인증 헤더 첨부

        Returns:
            JSON 응답 딕셔너리

        Raises:
            BybitAPIError: retCode != 0
        """
        self._rate_limit()
        url = f"{self.base_url}{path}"
        body_str = json.dumps(body or {})
        headers: Dict[str, str] = {"Content-Type": "application/json"}

        if auth_required:
            if self.auth is None:
                raise ValueError(
                    f"인증이 필요한 요청이지만 auth가 설정되지 않았습니다: {path}"
                )
            auth_headers = self.auth.sign_post(body_str)
            headers.update(auth_headers)

        response = requests.post(
            url, headers=headers, data=body_str, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        self._validate(data)
        return data

    # ── 공개 시세 API ───────────────────────────────────────────────────

    def get_kline(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        start: Optional[int] = None,
        end: Optional[int] = None,
        category: str = "linear",
    ) -> List[Dict[str, Any]]:
        """Kline(OHLCV) 데이터 조회.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            interval: 타임프레임 ("1","3","5","15","30","60","120","240","360","720","D","W")
            limit: 반환 봉 수 (최대 1000)
            start: 시작 시각 Unix 밀리초 (포함). None이면 최신부터.
            end: 종료 시각 Unix 밀리초 (포함). None이면 현재까지.
            category: "linear" (USDT Perpetual), "inverse", "spot"

        Returns:
            봉 딕셔너리 리스트. 각 딕셔너리:
            {open_time, open, high, low, close, volume, turnover}
            open_time은 Unix 밀리초 int.

        Raises:
            BybitAPIError: API 에러
        """
        params: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        data = self._get("/v5/market/kline", params=params, auth_required=False)
        raw_list = data.get("result", {}).get("list", [])

        # Bybit kline 응답: [[open_time, open, high, low, close, volume, turnover], ...]
        # 최신 봉이 앞에 있으므로 역순 정렬
        bars: List[Dict[str, Any]] = []
        for item in reversed(raw_list):
            # NaN 방어: 빈값이나 변환 실패 시 None 처리
            try:
                bars.append({
                    "open_time": int(item[0]),
                    "open": float(item[1]) if item[1] else None,
                    "high": float(item[2]) if item[2] else None,
                    "low": float(item[3]) if item[3] else None,
                    "close": float(item[4]) if item[4] else None,
                    "volume": float(item[5]) if item[5] else None,
                    "turnover": float(item[6]) if item[6] else None,
                })
            except (IndexError, ValueError, TypeError) as e:
                logger.warning("Kline 파싱 오류 (symbol=%s): %s - %s", symbol, item, e)
                continue

        return bars

    def get_instruments_info(
        self,
        symbol: Optional[str] = None,
        category: str = "linear",
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """상품 스펙 조회 (tick_size, min_qty, qty_step 등).

        Args:
            symbol: 특정 심볼. None이면 전체 조회.
            category: "linear" (USDT Perpetual)
            limit: 반환 상품 수 (최대 1000)

        Returns:
            상품 정보 딕셔너리 리스트. 각 딕셔너리:
            symbol, baseCoin, quoteCoin, priceFilter, lotSizeFilter 포함.
        """
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol

        data = self._get(
            "/v5/market/instruments-info", params=params, auth_required=False
        )
        return data.get("result", {}).get("list", [])

    def get_funding_rate_history(
        self,
        symbol: str,
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: int = 200,
        category: str = "linear",
    ) -> List[Dict[str, Any]]:
        """펀딩비 히스토리 조회.

        Args:
            symbol: 심볼
            start: 시작 시각 Unix 밀리초
            end: 종료 시각 Unix 밀리초
            limit: 반환 수 (최대 200)
            category: "linear"

        Returns:
            펀딩비 딕셔너리 리스트. 각 딕셔너리:
            {symbol, fundingRate, fundingRateTimestamp}
        """
        params: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "limit": min(limit, 200),
        }
        if start is not None:
            params["startTime"] = start
        if end is not None:
            params["endTime"] = end

        data = self._get(
            "/v5/market/funding/history", params=params, auth_required=False
        )
        return data.get("result", {}).get("list", [])

    # ── 인증 필요 API ────────────────────────────────────────────────────

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> Dict[str, Any]:
        """지갑 잔고 조회.

        Args:
            account_type: "UNIFIED" 또는 "CONTRACT"

        Returns:
            잔고 정보 딕셔너리 (result.list[0] 원소)
        """
        params = {"accountType": account_type}
        data = self._get("/v5/account/wallet-balance", params=params, auth_required=True)
        result_list = data.get("result", {}).get("list", [])
        return result_list[0] if result_list else {}

    def get_position(
        self, symbol: Optional[str] = None, category: str = "linear"
    ) -> List[Dict[str, Any]]:
        """포지션 조회.

        Args:
            symbol: 특정 심볼. None이면 전체.
            category: "linear"

        Returns:
            포지션 딕셔너리 리스트
        """
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol

        data = self._get("/v5/position/list", params=params, auth_required=True)
        return data.get("result", {}).get("list", [])

    def get_positions(self, category: str = "linear") -> List[Dict[str, Any]]:
        """전체 열린 포지션 조회.

        Bybit v5 GET /v5/position/list 엔드포인트를 호출하여
        size > 0인 포지션만 필터링하여 반환한다.

        Args:
            category: "linear" (USDT Perpetual)

        Returns:
            열린 포지션 딕셔너리 리스트. 각 딕셔너리:
            {symbol, side, size, avgPrice, ...}
        """
        params: Dict[str, Any] = {
            "category": category,
            "settleCoin": "USDT",
        }
        data = self._get("/v5/position/list", params=params, auth_required=True)
        positions = data.get("result", {}).get("list", [])
        return [p for p in positions if float(p.get("size", 0)) > 0]

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        category: str = "linear",
        reduce_only: bool = False,
        time_in_force: str = "IOC",
        position_idx: int = 0,
    ) -> Dict[str, Any]:
        """주문 생성.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            side: "Buy" 또는 "Sell"
            qty: 주문 수량
            order_type: "Market" 또는 "Limit"
            price: 지정가 (Limit 주문 시 필수)
            stop_loss: 스톱로스 가격
            take_profit: 테이크프로핏 가격
            category: "linear"
            reduce_only: True이면 청산 전용 주문
            time_in_force: "GTC", "IOC", "FOK"
            position_idx: 헤지모드 포지션 방향 (0=OneWay, 1=Long, 2=Short)

        Returns:
            주문 결과 딕셔너리 (orderId, orderLinkId 포함)

        Raises:
            BybitAPIError: 주문 실패 시
        """
        body: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(qty),
            "timeInForce": time_in_force,
            "positionIdx": position_idx,
        }
        # 헤지모드에서는 reduceOnly 대신 positionIdx로 방향 지정
        if reduce_only:
            body["reduceOnly"] = True
        if price is not None:
            body["price"] = str(price)
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)

        data = self._post("/v5/order/create", body=body, auth_required=True)
        return data.get("result", {})

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        position_idx: int = 0,
        category: str = "linear",
    ) -> Dict[str, Any]:
        """포지션의 SL/TP를 조정 (Bybit /v5/position/trading-stop).

        체결 후 실제 entry 기준으로 SL/TP를 재설정할 때 사용 (round 2 F2 fix).

        Args:
            symbol: USDT perpetual 심볼 (예: "BTCUSDT")
            stop_loss: 새 stop loss price. None이면 변경하지 않음.
            take_profit: 새 take profit price. None이면 변경하지 않음.
            position_idx: 0=OneWay, 1=Hedge Buy, 2=Hedge Sell
            category: "linear"

        Returns:
            API 응답 result 딕셔너리.

        Raises:
            BybitAPIError: retCode != 0 (caller가 잡아 WARN 로그로 처리할 것을 권장)
        """
        body: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "tpslMode": "Full",
            "positionIdx": position_idx,
        }
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)

        data = self._post(
            "/v5/position/trading-stop", body=body, auth_required=True
        )
        return data.get("result", {})

    def switch_position_mode(
        self, mode: int = 3, category: str = "linear"
    ) -> Dict[str, Any]:
        """포지션 모드 전환.

        Args:
            mode: 0=MergedSingle(OneWay), 3=BothSide(Hedge)
            category: "linear"

        Returns:
            API 응답 딕셔너리
        """
        body: Dict[str, Any] = {
            "category": category,
            "coin": "USDT",
            "mode": mode,
        }
        data = self._post("/v5/position/switch-mode", body=body, auth_required=True)
        return data.get("result", {})

    def cancel_order(
        self, symbol: str, order_id: str, category: str = "linear"
    ) -> Dict[str, Any]:
        """주문 취소.

        Args:
            symbol: 심볼
            order_id: 취소할 주문 ID
            category: "linear"

        Returns:
            취소 결과 딕셔너리
        """
        body: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "orderId": order_id,
        }
        data = self._post("/v5/order/cancel", body=body, auth_required=True)
        return data.get("result", {})

    def get_open_orders(
        self, symbol: str, category: str = "linear"
    ) -> Dict[str, Any]:
        """미체결 주문 조회.

        Bybit v5 GET /v5/order/realtime 엔드포인트를 호출한다.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            category: "linear" (USDT Perpetual)

        Returns:
            미체결 주문 응답 딕셔너리 (result.list 포함)
        """
        params: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
        }
        data = self._get("/v5/order/realtime", params=params, auth_required=True)
        return data.get("result", {})

    def set_leverage(
        self,
        symbol: str,
        buy_leverage: int,
        sell_leverage: int,
        category: str = "linear",
    ) -> Dict[str, Any]:
        """레버리지 설정.

        Args:
            symbol: 심볼
            buy_leverage: 매수 레버리지
            sell_leverage: 매도 레버리지
            category: "linear"

        Returns:
            설정 결과 딕셔너리
        """
        body: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "buyLeverage": str(buy_leverage),
            "sellLeverage": str(sell_leverage),
        }
        data = self._post("/v5/position/set-leverage", body=body, auth_required=True)
        return data.get("result", {})

    def __repr__(self) -> str:
        auth_status = "인증 설정됨" if self.auth else "공개 API만"
        return f"BybitRestClient(base_url='{self.base_url}', {auth_status})"


__all__ = ["BybitRestClient", "BybitAPIError"]
