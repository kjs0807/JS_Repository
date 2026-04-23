"""
KIS API Authentication Module

Handles OAuth2 token issuance, caching, and automatic renewal for KIS API.
Token lifecycle: 24h validity with 23h cache (1h safety margin).
Thread-safe token management with automatic retry on network failures.
Supports file-based token cache for reuse across process restarts.
"""

import json
import os
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

from config.settings import KISConfig

logger = logging.getLogger(__name__)


class KISAuthError(Exception):
    """Raised when authentication with KIS API fails."""
    pass


class TokenManager:
    """
    Manages KIS API OAuth2 access tokens with automatic renewal.

    Handles token caching (memory + file), expiration checking,
    and thread-safe renewal. File cache enables token reuse across restarts.

    Attributes:
        config: KIS API configuration object
        _token: Cached access token
        _token_issued_at: Timestamp of token issuance
        _lock: Thread lock for concurrent access protection
        _cache_file: Path to token cache file
    """

    TOKEN_VALIDITY_HOURS = 23

    def __init__(self, config: KISConfig, cache_file: Optional[str] = None) -> None:
        self.config = config
        self._token: Optional[str] = None
        self._token_issued_at: Optional[datetime] = None
        self._lock = threading.Lock()

        if cache_file is None:
            from config import LOGS_DIR
            os.makedirs(LOGS_DIR, exist_ok=True)
            self._cache_file = os.path.join(LOGS_DIR, "token_cache.json")
        else:
            self._cache_file = cache_file

        # 파일 캐시에서 토큰 복원 시도
        self._load_from_cache()

    def get_token(self) -> str:
        """유효한 토큰 반환. 만료 시 새로 발급."""
        with self._lock:
            if self._token is None or self._is_expired():
                self._token = self._issue_token()
                self._token_issued_at = datetime.now()
                self._save_to_cache()
            return self._token

    def _is_expired(self) -> bool:
        if self._token_issued_at is None:
            return True
        elapsed = datetime.now() - self._token_issued_at
        return elapsed >= timedelta(hours=self.TOKEN_VALIDITY_HOURS)

    def _issue_token(self) -> str:
        """KIS API에서 새 토큰 발급. 실패 시 재시도."""
        url = f"{self.config.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
        }

        max_retries = 3
        backoff_delays = [2, 5, 10]

        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=body, timeout=10)
                response.raise_for_status()

                data = response.json()
                token = data.get("access_token")

                if not token:
                    raise KISAuthError(
                        f"Token missing in response: {data}"
                    )

                logger.info("토큰 발급 성공")
                return token

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    delay = backoff_delays[attempt]
                    logger.warning("토큰 발급 실패 (시도 %d/%d), %ds 후 재시도: %s",
                                   attempt + 1, max_retries, delay, e)
                    time.sleep(delay)
                    continue
                else:
                    raise KISAuthError(
                        f"Failed to issue token after {max_retries} attempts: {e}"
                    ) from e

        raise KISAuthError("Unexpected error in token issuance")

    def _save_to_cache(self) -> None:
        """토큰을 파일에 캐시."""
        if self._token is None or self._token_issued_at is None:
            return
        try:
            data = {
                "token": self._token,
                "ts": self._token_issued_at.timestamp(),
            }
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.debug("토큰 캐시 저장: %s", self._cache_file)
        except Exception as e:
            logger.warning("토큰 캐시 저장 실패: %s", e)

    def _load_from_cache(self) -> None:
        """파일 캐시에서 토큰 복원."""
        if not os.path.exists(self._cache_file):
            return
        try:
            with open(self._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            token = data.get("token")
            ts = data.get("ts")
            if not token or not ts:
                return

            issued_at = datetime.fromtimestamp(ts)
            elapsed = datetime.now() - issued_at

            if elapsed < timedelta(hours=self.TOKEN_VALIDITY_HOURS):
                self._token = token
                self._token_issued_at = issued_at
                remaining = self.TOKEN_VALIDITY_HOURS - elapsed.total_seconds() / 3600
                logger.info("토큰 캐시 복원 성공 (잔여 %.1f시간)", remaining)
            else:
                logger.info("캐시 토큰 만료됨 — 새로 발급 필요")
        except Exception as e:
            logger.warning("토큰 캐시 로드 실패: %s", e)

    def invalidate(self) -> None:
        """캐시 토큰 무효화."""
        with self._lock:
            self._token = None
            self._token_issued_at = None
            if os.path.exists(self._cache_file):
                try:
                    os.remove(self._cache_file)
                except OSError:
                    pass
