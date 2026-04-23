"""KIS API Configuration for Overseas Futures.

해외선물은 실전 서버만 사용 (모의투자 미지원).
"""

import os
from dataclasses import dataclass
from typing import Literal
from dotenv import load_dotenv


class ConfigurationError(Exception):
    """설정 검증 실패."""
    pass


@dataclass
class KISConfig:
    """KIS API 설정.

    해외선물은 실전 서버 고정 (모의투자 미지원).

    Attributes:
        env: 환경 ('real' 고정)
        base_url: API 서버 URL
        app_key: 앱 키
        app_secret: 앱 시크릿
        account_no: 계좌번호 (CANO-ACNT_PRDT_CD)
        cano: 종합계좌번호
        acnt_prdt_cd: 계좌상품코드
        rate_limit_per_sec: 초당 API 호출 제한
        db_path: SQLite DB 경로
    """

    env: Literal['real'] = 'real'
    base_url: str = ''
    app_key: str = ''
    app_secret: str = ''
    account_no: str = ''
    cano: str = ''
    acnt_prdt_cd: str = ''
    rate_limit_per_sec: int = 20
    db_path: str = 'db/futures.db'

    def __post_init__(self) -> None:
        """환경변수에서 자격증명 로드."""
        from config import ENV_FILE
        load_dotenv(ENV_FILE)

        self.base_url = 'https://openapi.koreainvestment.com:9443'
        self.rate_limit_per_sec = 20

        self.app_key = os.getenv('KIS_APP_KEY_REAL', '')
        self.app_secret = os.getenv('KIS_APP_SECRET_REAL', '')
        self.account_no = os.getenv('KIS_ACCOUNT_REAL', '')

        if '-' in self.account_no:
            parts = self.account_no.split('-')
            self.cano = parts[0]
            self.acnt_prdt_cd = parts[1]

        self.validate()

    def validate(self) -> None:
        """필수 설정값 검증."""
        missing = []
        if not self.app_key:
            missing.append('KIS_APP_KEY_REAL')
        if not self.app_secret:
            missing.append('KIS_APP_SECRET_REAL')
        if not self.account_no:
            missing.append('KIS_ACCOUNT_REAL')
        if missing:
            raise ConfigurationError(
                f"Missing environment variables: {', '.join(missing)}"
            )

    def __repr__(self) -> str:
        return (
            f"KISConfig(env='real', base_url='{self.base_url}', "
            f"app_key='***', account='{self.cano}***', "
            f"rate_limit={self.rate_limit_per_sec}/sec)"
        )
