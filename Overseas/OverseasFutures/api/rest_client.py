"""KIS API REST 클라이언트 (해외선물 전용).

DESIGN.md 3.4절의 일봉 파라미터 버그를 수정한 버전.
- get_futures_daily_ohlcv: EXCH_CD 필수, QRY_TP="Q"/"P", QRY_GAP="", INDEX_KEY 페이지네이션
- get_futures_current_price: exchange 파라미터 추가, EXCH_CD 전달
- get_futures_detail: 신규 (TR_ID HHDFC55010100)
"""

import time
from collections import deque
from typing import Any, Dict, List, Optional

import requests

from config.settings import KISConfig
from api.auth import TokenManager


class KISAPIError(Exception):
    """KIS API가 에러 응답을 반환했을 때 발생.

    Attributes:
        msg_cd: KIS 에러 코드
        msg1: KIS 에러 메시지
    """

    def __init__(self, msg_cd: str, msg1: str) -> None:
        self.msg_cd = msg_cd
        self.msg1 = msg1
        super().__init__(f"KIS API Error [{msg_cd}]: {msg1}")


class KISRestClient:
    """KIS 해외선물 REST 클라이언트.

    자동 인증, Rate Limit, 응답 검증을 포함한 HTTP 요청 처리.
    일봉/현재가/종목상세 시세 조회 메서드 제공.

    Attributes:
        config: KIS API 설정 객체
        token_manager: OAuth2 토큰 관리자
        _request_timestamps: Rate limit용 요청 타임스탬프 큐
    """

    def __init__(self, config: KISConfig, token_manager: TokenManager) -> None:
        """KIS REST 클라이언트 초기화.

        Args:
            config: KIS API 설정
            token_manager: 인증 토큰 관리자
        """
        self.config = config
        self.token_manager = token_manager
        self._request_timestamps: deque = deque()

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────

    def _build_headers(self, tr_id: str) -> Dict[str, str]:
        """KIS API 요청 HTTP 헤더 구성.

        Args:
            tr_id: 트랜잭션 ID (TR_ID)

        Returns:
            HTTP 헤더 딕셔너리
        """
        token = self.token_manager.get_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": tr_id,
        }

    def _rate_limit(self) -> None:
        """슬라이딩 윈도우 방식 Rate Limit 적용.

        1초 내 요청 수가 rate_limit_per_sec를 초과하면 sleep.
        단일 스레드 인스턴스 기준 (thread-safe 미보장).
        """
        now = time.time()
        limit = self.config.rate_limit_per_sec

        # 1초 이전 타임스탬프 제거
        while self._request_timestamps and now - self._request_timestamps[0] > 1.0:
            self._request_timestamps.popleft()

        if len(self._request_timestamps) >= limit:
            oldest = self._request_timestamps[0]
            sleep_time = 1.0 - (now - oldest)
            if sleep_time > 0:
                time.sleep(sleep_time)
            now = time.time()
            while self._request_timestamps and now - self._request_timestamps[0] > 1.0:
                self._request_timestamps.popleft()

        self._request_timestamps.append(time.time())

    def _validate_response(self, data: Dict[str, Any]) -> None:
        """KIS API 응답 에러 검증.

        Args:
            data: KIS API JSON 응답

        Raises:
            KISAPIError: rt_cd가 "0"이 아닌 경우
        """
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg_cd = data.get("msg_cd", "UNKNOWN")
            msg1 = data.get("msg1", "Unknown error")
            raise KISAPIError(msg_cd, msg1)

    # ── 공통 HTTP 메서드 ───────────────────────────────────────────────────

    def get(self, path: str, params: Dict[str, Any], tr_id: str) -> Dict[str, Any]:
        """KIS API GET 요청 실행.

        Args:
            path: API 엔드포인트 경로 (base_url 제외)
            params: 쿼리 파라미터
            tr_id: 트랜잭션 ID

        Returns:
            JSON 응답 딕셔너리

        Raises:
            KISAPIError: API 에러 응답 시
            requests.RequestException: 네트워크/HTTP 오류 시
        """
        self._rate_limit()
        url = f"{self.config.base_url}{path}"
        headers = self._build_headers(tr_id)
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        self._validate_response(data)
        return data

    def post(self, path: str, body: Dict[str, Any], tr_id: str) -> Dict[str, Any]:
        """KIS API POST 요청 실행.

        Args:
            path: API 엔드포인트 경로 (base_url 제외)
            body: 요청 바디 딕셔너리
            tr_id: 트랜잭션 ID

        Returns:
            JSON 응답 딕셔너리

        Raises:
            KISAPIError: API 에러 응답 시
            requests.RequestException: 네트워크/HTTP 오류 시
        """
        self._rate_limit()
        url = f"{self.config.base_url}{path}"
        headers = self._build_headers(tr_id)
        response = requests.post(url, headers=headers, json=body, timeout=10)
        response.raise_for_status()
        data = response.json()
        self._validate_response(data)
        return data

    # ── 시세 조회 메서드 ───────────────────────────────────────────────────

    def get_futures_daily_ohlcv(
        self,
        symbol: str,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """해외선물 일봉 OHLCV 조회 (페이지네이션 자동 처리).

        DESIGN.md 3.4절 버그 수정 버전:
        - EXCH_CD 필수 지정
        - QRY_TP="Q"(첫페이지) / "P"(다음페이지)
        - QRY_GAP="" (빈값 필수)
        - INDEX_KEY로 페이지네이션 (CTX_AREA 대체)
        - 응답은 output2 배열에서 파싱

        Args:
            symbol: KIS 종목코드 (예: "VGM26")
            exchange: KIS 거래소 코드 (예: "EUREX", "OSE", "HKEx", "ASX", "FTX")
            start_date: 조회 시작일 YYYYMMDD
            end_date: 조회 종료일 YYYYMMDD

        Returns:
            output2 배열 원소 리스트. 각 원소는 다음 필드를 포함:
            - data_date: 날짜 (YYYYMMDD)
            - open_price: 시가
            - high_price: 고가
            - low_price: 저가
            - last_price: 종가
            - vol: 거래량

        Example:
            >>> bars = client.get_futures_daily_ohlcv("VGM26", "EUREX", "20260101", "20260318")
            >>> bars[0]["data_date"]
            '20260318'
        """
        path = "/uapi/overseas-futureoption/v1/quotations/daily-ccnl"
        tr_id = "HHDFC55020100"

        all_data: List[Dict[str, Any]] = []
        index_key = ""
        qry_tp = "Q"  # 첫 페이지

        while True:
            params: Dict[str, str] = {
                "SRS_CD":          symbol,
                "EXCH_CD":         exchange,   # 필수 — 거래소 미지정 시 일부 상품 동작 안 함
                "START_DATE_TIME": start_date,
                "CLOSE_DATE_TIME": end_date,
                "QRY_TP":          qry_tp,
                "QRY_CNT":         "40",
                "QRY_GAP":         "",         # 필수 빈값
                "INDEX_KEY":       index_key,  # 페이지네이션 키 (첫 요청은 빈값)
            }

            response = self.get(path, params, tr_id)

            # 일봉 데이터는 output2 배열에 위치
            output2 = response.get("output2") or []
            if isinstance(output2, list):
                all_data.extend(output2)
            elif isinstance(output2, dict):
                all_data.append(output2)

            # 다음 페이지 여부 확인
            tr_cont = response.get("tr_cont", "")
            if tr_cont not in ("F", "M"):
                break

            # INDEX_KEY 갱신 (output1에서 추출)
            output1 = response.get("output1") or {}
            next_key = ""
            if isinstance(output1, dict):
                next_key = output1.get("index_key", "")

            if not next_key:
                break

            index_key = next_key
            qry_tp = "P"  # 두 번째 페이지부터

        return all_data

    def get_futures_current_price(
        self,
        symbol: str,
        exchange: str = "",
    ) -> Dict[str, Any]:
        """해외선물 현재가 스냅샷 조회.

        Args:
            symbol: KIS 종목코드 (예: "VGM26")
            exchange: KIS 거래소 코드 (예: "EUREX"). 빈값이면 미전달.

        Returns:
            현재가 데이터 딕셔너리 (output 또는 output1 원소)

        Example:
            >>> data = client.get_futures_current_price("VGM26", "EUREX")
            >>> data["last_price"]
            '5916'
        """
        path = "/uapi/overseas-futureoption/v1/quotations/inquire-price"
        tr_id = "HHDFC55010000"

        params: Dict[str, str] = {"SRS_CD": symbol}
        if exchange:
            params["EXCH_CD"] = exchange

        response = self.get(path, params, tr_id)

        output = (
            response.get("output")
            or response.get("output1")
            or response.get("output2")
            or {}
        )
        return output if isinstance(output, dict) else {}

    def get_futures_detail(
        self,
        symbol: str,
        exchange: str,
    ) -> Dict[str, Any]:
        """해외선물 종목 상세 정보 조회.

        tick_size, margin, expiry 등 정적 상품 스펙을 반환한다.
        TR_ID: HHDFC55010100

        Args:
            symbol: KIS 종목코드 (예: "VGM26")
            exchange: KIS 거래소 코드 (예: "EUREX")

        Returns:
            종목 상세 딕셔너리 (output 또는 output1 원소)

        Example:
            >>> detail = client.get_futures_detail("VGM26", "EUREX")
            >>> detail["tick_size"]
            '1'
        """
        path = "/uapi/overseas-futureoption/v1/quotations/stock-detail"
        tr_id = "HHDFC55010100"

        params: Dict[str, str] = {
            "SRS_CD":  symbol,
            "EXCH_CD": exchange,
        }

        response = self.get(path, params, tr_id)

        output = (
            response.get("output")
            or response.get("output1")
            or response.get("output2")
            or {}
        )
        return output if isinstance(output, dict) else {}

    def get_futures_intraday(
        self,
        symbol: str,
        exchange: str = "",
        timeframe: str = "5m",
    ) -> List[Dict[str, Any]]:
        """해외선물 분봉 OHLCV 조회 (장중에만 데이터 반환).

        Args:
            symbol: KIS 종목코드
            exchange: KIS 거래소 코드
            timeframe: 시간 단위 ("1m", "5m", "15m", "60m")

        Returns:
            분봉 데이터 리스트
        """
        path = "/uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice"
        tr_id = "HHDFC55020400"

        timeframe_map = {"1m": "1", "5m": "5", "15m": "15", "60m": "60"}
        period_code = timeframe_map.get(timeframe, "5")

        params: Dict[str, str] = {
            "SRS_CD":  symbol,
            "EXCH_CD": exchange,
            "QRY_TP":  "0",
            "QRY_CNT": "100",
            "QRY_GAP": period_code,
        }

        response = self.get(path, params, tr_id)

        output = (
            response.get("output")
            or response.get("output1")
            or response.get("output2")
            or []
        )
        if isinstance(output, list):
            return output
        if isinstance(output, dict):
            return [output]
        return []
