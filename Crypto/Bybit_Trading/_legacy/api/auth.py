"""Bybit API v5 HMAC-SHA256 인증 모듈.

Bybit REST API v5는 timestamp + api_key + recv_window + query_string 을
HMAC-SHA256으로 서명한다. 서명은 헤더에 X-BAPI-SIGN으로 전달된다.

참고: https://bybit-exchange.github.io/docs/v5/guide
"""

import hashlib
import hmac
import time
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def generate_signature(api_secret: str, param_str: str) -> str:
    """HMAC-SHA256 서명 생성.

    Args:
        api_secret: Bybit API 시크릿 키
        param_str: 서명 대상 문자열
            (timestamp + api_key + recv_window + query_string 조합)

    Returns:
        16진수 소문자 HMAC-SHA256 서명 문자열
    """
    return hmac.new(
        api_secret.encode("utf-8"),
        param_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class BybitAuthManager:
    """Bybit REST API v5 인증 헤더 생성기.

    timestamp + api_key + recv_window + (query_string 또는 JSON body) 를
    HMAC-SHA256으로 서명하여 요청 헤더 딕셔너리를 반환한다.

    Attributes:
        api_key: Bybit API 키
        api_secret: Bybit API 시크릿
        recv_window: 요청 허용 시간 오프셋 (밀리초, 기본값 5000)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        recv_window: int = 5000,
    ) -> None:
        """BybitAuthManager 초기화.

        Args:
            api_key: Bybit API 키 (.env의 BYBIT_API_KEY)
            api_secret: Bybit API 시크릿 (.env의 BYBIT_API_SECRET)
            recv_window: 요청 허용 시간 오프셋 밀리초 (기본 5000)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window

    @staticmethod
    def get_timestamp() -> int:
        """현재 Unix 타임스탬프(밀리초) 반환.

        Returns:
            현재 시각의 Unix 밀리초 정수
        """
        return int(time.time() * 1000)

    def sign_get(self, params: Dict[str, str]) -> Dict[str, str]:
        """GET 요청 서명 헤더 생성.

        GET 요청의 경우 쿼리 파라미터를 정렬하여 서명 문자열을 만든다.

        Args:
            params: GET 쿼리 파라미터 딕셔너리

        Returns:
            Bybit 인증 헤더 딕셔너리:
            {
                "X-BAPI-API-KEY": ...,
                "X-BAPI-TIMESTAMP": ...,
                "X-BAPI-RECV-WINDOW": ...,
                "X-BAPI-SIGN": ...,
            }
        """
        ts = str(self.get_timestamp())
        recv_window_str = str(self.recv_window)

        # 쿼리 문자열 조합 (정렬 후 &= 형식)
        query_string = "&".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )

        # 서명 대상: timestamp + api_key + recv_window + query_string
        param_str = f"{ts}{self.api_key}{recv_window_str}{query_string}"
        signature = generate_signature(self.api_secret, param_str)

        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window_str,
            "X-BAPI-SIGN": signature,
        }

    def sign_post(self, body_str: str) -> Dict[str, str]:
        """POST 요청 서명 헤더 생성.

        POST 요청의 경우 JSON body 문자열 그대로 서명한다.

        Args:
            body_str: JSON 직렬화된 요청 바디 문자열

        Returns:
            Bybit 인증 헤더 딕셔너리
        """
        ts = str(self.get_timestamp())
        recv_window_str = str(self.recv_window)

        # 서명 대상: timestamp + api_key + recv_window + body_str
        param_str = f"{ts}{self.api_key}{recv_window_str}{body_str}"
        signature = generate_signature(self.api_secret, param_str)

        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window_str,
            "X-BAPI-SIGN": signature,
            "Content-Type": "application/json",
        }

    @classmethod
    def from_settings(cls) -> "BybitAuthManager":
        """AppSettings에서 API 키를 자동 로드하여 인스턴스 생성.

        Returns:
            BybitAuthManager 인스턴스

        Raises:
            ValueError: API 키 또는 시크릿이 비어있을 때
        """
        from config.settings import settings
        api_key = settings.api_key
        api_secret = settings.api_secret
        if not api_key or not api_secret:
            raise ValueError(
                "BYBIT_API_KEY / BYBIT_API_SECRET 환경변수가 설정되지 않았습니다. "
                ".env 파일을 확인하세요."
            )
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            recv_window=settings.recv_window,
        )

    def __repr__(self) -> str:
        return (
            f"BybitAuthManager(api_key='***{self.api_key[-4:]}', "
            f"recv_window={self.recv_window})"
        )


__all__ = ["BybitAuthManager", "generate_signature"]
