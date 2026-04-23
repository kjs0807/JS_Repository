"""
시장 데이터 수집기
=================
yfinance 일간 가격, ECOS 경제지표, FRED 경제지표 수집.
캐시 레이어(NewsDB) 활용. 재시도 로직 포함.
"""

import logging
import os
import time
import warnings
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests
import urllib3

from report.db_manager import NewsDB

logger = logging.getLogger(__name__)

# ── API 키: 환경변수에서 로드 (하드코딩 금지) ──
_DEFAULT_ECOS_KEY: str = os.environ.get("ECOS_API_KEY", "")
_DEFAULT_FRED_KEY: str = os.environ.get("FRED_API_KEY", "")
MAX_RETRIES: int = 3
RETRY_DELAY: int = 2
REQUEST_TIMEOUT: int = 60

DEFAULT_TICKERS: dict[str, list[str]] = {
    "fx": ["USDKRW=X", "EURUSD=X", "USDJPY=X", "DX-Y.NYB"],
    "indices": ["^KS11", "^GSPC", "^VIX"],
    "commodities": ["GC=F", "CL=F", "BZ=F"],
    "rates_proxy": ["^TNX", "^FVX", "^IRX", "^TYX"],
}

# 티커 → 사람이 읽을 수 있는 표시명
TICKER_DISPLAY_NAMES: dict[str, str] = {
    "USDKRW=X": "원/달러 환율",
    "EURUSD=X": "유로/달러",
    "USDJPY=X": "달러/엔",
    "DX-Y.NYB": "달러 인덱스(DXY)",
    "^KS11": "KOSPI",
    "^GSPC": "S&P 500",
    "^VIX": "VIX 변동성",
    "GC=F": "금(Gold)",
    "CL=F": "WTI 원유",
    "BZ=F": "브렌트 원유",
    "^TNX": "美 10년 국채금리",
    "^FVX": "美 5년 국채금리",
    "^IRX": "美 13주 국채금리",
    "^TYX": "美 30년 국채금리",
}

ECOS_SERIES: dict[str, dict[str, str]] = {
    "BASE_RATE": {
        "stat_code": "722Y001",
        "item_code": "0101000",
        "name": "한국은행 기준금리",
        "cycle": "M",
    },
    "M2": {
        "stat_code": "101Y003",
        "item_code": "BBHS00",
        "name": "M2(광의통화)",
        "cycle": "M",
    },
    "PPI": {
        "stat_code": "404Y014",
        "item_code": "*AA",
        "name": "생산자물가지수",
        "cycle": "M",
    },
}

FRED_SERIES: dict[str, list[str]] = {
    "rates": ["DGS2", "DGS5", "DGS10", "DGS30", "FEDFUNDS", "T10Y2Y"],
    "markets": ["VIXCLS", "SP500", "DTWEXBGS", "DCOILWTICO"],
    "inflation": ["CPIAUCSL", "CPILFESL"],
}


class MarketDataFetcher:
    """시장 가격 + 경제지표 통합 수집기."""

    def __init__(
        self,
        db: NewsDB,
        ecos_api_key: str = "",
        fred_api_key: str = "",
    ) -> None:
        """NewsDB 인스턴스를 받아 캐시 레이어로 활용.

        Args:
            db: NewsDB 인스턴스.
            ecos_api_key: ECOS API 키 (빈 문자열이면 기본값 사용).
            fred_api_key: FRED API 키 (빈 문자열이면 기본값 사용).
        """
        self.db = db
        self.ecos_api_key = ecos_api_key or _DEFAULT_ECOS_KEY
        self.fred_api_key = fred_api_key or _DEFAULT_FRED_KEY
        if not self.ecos_api_key:
            logger.warning("ECOS_API_KEY 환경변수 미설정 → ECOS 수집 건너뜀")
        if not self.fred_api_key:
            logger.warning("FRED_API_KEY 환경변수 미설정 → FRED 수집 건너뜀")

        _ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        # 일반 Session (SSL 검증 활성)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _ua})

        # ECOS 전용 Session (사내 프록시 환경에서 SSL 검증 비활성)
        self._ecos_session = requests.Session()
        self._ecos_session.headers.update({"User-Agent": _ua})
        self._ecos_session.verify = False

        # yfinance용 curl_cffi 세션 (사내 프록시 SSL 우회)
        try:
            from curl_cffi import requests as curl_requests
            self._yf_session = curl_requests.Session(impersonate="chrome", verify=False)
        except ImportError:
            self._yf_session = None

    def fetch_daily_prices(
        self, tickers: list[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        """yfinance로 일간 OHLCV 수집 (캐시 활용).

        Args:
            tickers: 티커 리스트.
            start: 시작 날짜.
            end: 종료 날짜.

        Returns:
            {ticker: DataFrame(columns=[open,high,low,close,volume])}
            실패 티커는 빈 DataFrame.
        """
        import yfinance as yf

        result: dict[str, pd.DataFrame] = {}

        for ticker in tickers:
            try:
                # 1단계: DB 캐시 확인
                cached = self.db.get_cached_market_data(ticker, start, end)
                if cached is not None and not cached.empty:
                    # 캐시에 충분한 데이터가 있는지 확인
                    result[ticker] = cached
                    logger.debug(f"캐시 히트: {ticker} ({len(cached)}행)")
                    continue

                # 2단계: yfinance 다운로드
                logger.info(f"yfinance 다운로드: {ticker}")
                t = yf.Ticker(ticker, session=self._yf_session)
                df = t.history(
                    start=str(start),
                    end=str(end + timedelta(days=1)),
                    auto_adjust=True,
                )

                if df is None or df.empty:
                    logger.warning(f"yfinance 데이터 없음: {ticker}")
                    result[ticker] = pd.DataFrame()
                    continue

                # MultiIndex 컬럼 처리 (yfinance 단일 티커 다운로드 시)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                # 컬럼명 소문자 통일
                df.columns = [str(c).lower() for c in df.columns]

                # 필요 컬럼만 선택
                needed_cols = ["open", "high", "low", "close", "volume"]
                available_cols = [c for c in needed_cols if c in df.columns]
                df = df[available_cols].copy()

                # 3단계: DB 캐시 저장
                self.db.cache_market_data(ticker, df)
                result[ticker] = df
                logger.info(f"yfinance 완료: {ticker} ({len(df)}행)")

            except Exception as e:
                logger.error(f"yfinance 수집 실패 [{ticker}]: {e}")
                result[ticker] = pd.DataFrame()

        return result

    def fetch_ecos(
        self, stat_code: str, item_code: str, start: str, end: str
    ) -> pd.Series:
        """ECOS API 직접 호출 (경량 버전).

        Args:
            stat_code: 통계표 코드.
            item_code: 항목 코드.
            start: 시작 기간 (예: "202301").
            end: 종료 기간 (예: "202603").

        Returns:
            pd.Series(index=date, values=float). 실패 시 빈 Series.
        """
        if not self.ecos_api_key:
            return pd.Series(dtype=float)

        base_url = "https://ecos.bok.or.kr/api/StatisticSearch"
        url = (
            f"{base_url}/{self.ecos_api_key}/json/kr/1/100/"
            f"{stat_code}/M/{start}/{end}/{item_code}/"
        )

        for attempt in range(MAX_RETRIES):
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        category=urllib3.exceptions.InsecureRequestWarning,
                    )
                    resp = self._ecos_session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    logger.warning(f"ECOS HTTP 오류: {resp.status_code}")
                    break

                data = resp.json()
                if "StatisticSearch" in data:
                    rows = data["StatisticSearch"].get("row", [])
                    if rows:
                        dates = []
                        values = []
                        for row in rows:
                            time_str = row.get("TIME", "")
                            val_str = row.get("DATA_VALUE", "")
                            try:
                                # 월간 데이터: "202301" → date
                                if len(time_str) == 6:
                                    dt = pd.to_datetime(time_str, format="%Y%m")
                                elif len(time_str) == 8:
                                    dt = pd.to_datetime(time_str, format="%Y%m%d")
                                else:
                                    dt = pd.to_datetime(time_str)
                                val = float(val_str)
                                dates.append(dt)
                                values.append(val)

                                # 캐시 저장
                                self.db.cache_indicator(
                                    "ECOS", stat_code, dt.date(), val
                                )
                            except (ValueError, TypeError):
                                continue
                        if dates:
                            return pd.Series(
                                data=values, index=dates, name=stat_code
                            )

                if "RESULT" in data:
                    msg = data["RESULT"].get("MESSAGE", "")
                    if "해당하는 데이터가 없습니다" not in msg:
                        logger.warning(f"ECOS: {msg}")
                break

            except Exception as e:
                if attempt < MAX_RETRIES - 1 and self._is_connection_error(str(e)):
                    logger.warning(f"ECOS 연결 오류, 재시도 {attempt + 2}...")
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"ECOS 수집 실패 [{stat_code}]: {e}")
                    break

        return pd.Series(dtype=float)

    def fetch_fred(self, series_id: str, start: str) -> pd.Series:
        """FRED API 호출 (fredapi 사용).

        Args:
            series_id: FRED 시리즈 ID.
            start: 시작 날짜 문자열 (예: "2025-12-01").

        Returns:
            pd.Series(index=date, values=float). 실패 시 빈 Series.
        """
        if not self.fred_api_key:
            return pd.Series(dtype=float)

        for attempt in range(MAX_RETRIES):
            try:
                from fredapi import Fred

                fred = Fred(api_key=self.fred_api_key)
                series = fred.get_series(
                    series_id, observation_start=start
                )

                if series is not None and len(series) > 0:
                    # NaN 제거
                    series = series.dropna()

                    # 캐시 저장
                    for dt, val in series.items():
                        try:
                            d = dt.date() if hasattr(dt, "date") else dt
                            self.db.cache_indicator(
                                "FRED", series_id, d, float(val)
                            )
                        except (ValueError, TypeError):
                            continue

                    return series

                logger.warning(f"FRED 데이터 없음: {series_id}")
                return pd.Series(dtype=float)

            except Exception as e:
                if attempt < MAX_RETRIES - 1 and self._is_connection_error(str(e)):
                    logger.warning(f"FRED 연결 오류, 재시도 {attempt + 2}...")
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"FRED 수집 실패 [{series_id}]: {e}")
                    break

        return pd.Series(dtype=float)

    def fetch_all_for_report(
        self, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """보고서 생성에 필요한 모든 시장 데이터를 한번에 수집.

        Args:
            start_date: 보고서 시작 날짜.
            end_date: 보고서 종료 날짜.

        Returns:
            {"prices": {...}, "ecos": {...}, "fred": {...}}
        """
        result: dict[str, Any] = {"prices": {}, "ecos": {}, "fred": {}}

        # 1) 일간 가격 (start - 60일 lookback ~ end)
        price_start = start_date - timedelta(days=90)  # 60 거래일 ≈ 90 일
        all_tickers = []
        for group_tickers in DEFAULT_TICKERS.values():
            all_tickers.extend(group_tickers)

        try:
            result["prices"] = self.fetch_daily_prices(
                all_tickers, price_start, end_date
            )
        except Exception as e:
            logger.error(f"일간 가격 수집 실패: {e}")

        # 2) ECOS 경제지표 (최근 90일)
        ecos_start = (end_date - timedelta(days=90)).strftime("%Y%m")
        ecos_end = end_date.strftime("%Y%m")

        for code, config in ECOS_SERIES.items():
            try:
                # 캐시 확인
                cached = self.db.get_cached_indicator("ECOS", config["stat_code"])
                if cached is not None and not cached.empty:
                    result["ecos"][code] = cached
                    logger.debug(f"ECOS 캐시 히트: {code}")
                    continue

                series = self.fetch_ecos(
                    config["stat_code"],
                    config["item_code"],
                    ecos_start,
                    ecos_end,
                )
                if not series.empty:
                    result["ecos"][code] = series
                    logger.info(f"ECOS 수집 완료: {code} ({len(series)}건)")
                else:
                    result["ecos"][code] = None
            except Exception as e:
                logger.error(f"ECOS 수집 실패 [{code}]: {e}")
                result["ecos"][code] = None

        # 3) FRED 경제지표 (최근 90일)
        fred_start = (end_date - timedelta(days=90)).strftime("%Y-%m-%d")

        for _group, series_ids in FRED_SERIES.items():
            for series_id in series_ids:
                try:
                    # 캐시 확인
                    cached = self.db.get_cached_indicator("FRED", series_id)
                    if cached is not None and not cached.empty:
                        result["fred"][series_id] = cached
                        logger.debug(f"FRED 캐시 히트: {series_id}")
                        continue

                    series = self.fetch_fred(series_id, fred_start)
                    if not series.empty:
                        result["fred"][series_id] = series
                        logger.info(
                            f"FRED 수집 완료: {series_id} ({len(series)}건)"
                        )
                    else:
                        result["fred"][series_id] = None
                except Exception as e:
                    logger.error(f"FRED 수집 실패 [{series_id}]: {e}")
                    result["fred"][series_id] = None

                time.sleep(0.3)  # API rate limit 방지

        return result

    def _is_connection_error(self, error_msg: str) -> bool:
        """연결 오류인지 확인.

        Args:
            error_msg: 예외 메시지 문자열.

        Returns:
            연결 관련 오류이면 True.
        """
        keywords = [
            "Remote",
            "Connection",
            "aborted",
            "timeout",
            "Timeout",
            "timed out",
        ]
        return any(keyword in error_msg for keyword in keywords)
