"""과거 OHLCV 데이터 수집기.

Bybit REST API를 통해 2년치 OHLCV 데이터를 수집하여 SQLite에 저장한다.
1000봉씩 페이징하며 0.1초 간격으로 요청한다.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from api.rest_client import BybitRestClient
from db.db_manager import DBManager

logger = logging.getLogger(__name__)

# 타임프레임 → Bybit interval 문자열 매핑
INTERVAL_MAP: Dict[str, str] = {
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "daily": "D",
}

# 타임프레임별 봉 간격 (밀리초)
INTERVAL_MS: Dict[str, int] = {
    "15": 15 * 60 * 1000,
    "60": 60 * 60 * 1000,
    "240": 4 * 60 * 60 * 1000,
    "D": 24 * 60 * 60 * 1000,
}


class HistoricalCollector:
    """Bybit 과거 OHLCV 수집기.

    REST API 페이징으로 지정 기간의 OHLCV를 수집하여 DB에 저장한다.
    products_master도 함께 수집한다.

    Attributes:
        client: BybitRestClient 인스턴스
        db: DBManager 인스턴스
        sleep_sec: API 요청 간 sleep 시간 (초)
        batch_size: 한 번에 요청할 봉 수 (최대 1000)
    """

    def __init__(
        self,
        client: Optional[BybitRestClient] = None,
        db: Optional[DBManager] = None,
        sleep_sec: float = 0.1,
        batch_size: int = 1000,
    ) -> None:
        """HistoricalCollector 초기화.

        Args:
            client: BybitRestClient. None이면 공개 API 기본 클라이언트 생성.
            db: DBManager. None이면 기본 DB_PATH로 생성.
            sleep_sec: 요청 간 sleep 초 (기본 0.1)
            batch_size: 요청당 봉 수 (기본 1000)
        """
        if client is None:
            client = BybitRestClient()
        self.client = client

        if db is None:
            db = DBManager()
        self.db = db

        self.sleep_sec = sleep_sec
        self.batch_size = min(batch_size, 1000)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────

    def _dt_to_ms(self, dt: datetime) -> int:
        """datetime → Unix 밀리초 변환.

        Args:
            dt: datetime 객체 (timezone-aware 또는 naive UTC)

        Returns:
            Unix 밀리초 정수
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _ms_to_dt(self, ms: int) -> datetime:
        """Unix 밀리초 → datetime UTC 변환."""
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    def _collect_one_symbol_interval(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> int:
        """단일 심볼 + 타임프레임의 OHLCV를 페이징으로 수집.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            interval: Bybit interval 문자열 ("15", "60", "240", "D")
            start_ms: 수집 시작 Unix 밀리초
            end_ms: 수집 종료 Unix 밀리초

        Returns:
            저장된 총 봉 수
        """
        # 타임프레임 → DB 테이블명 매핑
        from db.db_manager import TIMEFRAME_TABLE
        table = TIMEFRAME_TABLE.get(interval)
        if table is None:
            logger.warning("지원하지 않는 interval: %s", interval)
            return 0

        total_saved = 0
        current_start = start_ms
        interval_ms = INTERVAL_MS.get(interval, 15 * 60 * 1000)
        request_count = 0

        while current_start < end_ms:
            try:
                # end를 API에 전달하지 않음: Bybit는 start+end 동시 지정 시
                # 가장 최근 N봉을 반환하여 start가 무시됨.
                # start만 지정하면 start 이후 순서대로 봉을 반환한다.
                bars = self.client.get_kline(
                    symbol=symbol,
                    interval=interval,
                    limit=self.batch_size,
                    start=current_start,
                )
            except Exception as e:
                logger.error(
                    "Kline 조회 실패 (symbol=%s, interval=%s, start=%d): %s",
                    symbol, interval, current_start, e,
                )
                break

            if not bars:
                break

            # end_ms 이후 봉은 클라이언트에서 필터링
            bars = [b for b in bars if b.get("open_time", 0) <= end_ms]
            if not bars:
                break

            # NaN/유효성 필터링
            valid_bars = []
            for bar in bars:
                if (
                    bar.get("open_time") is None
                    or bar.get("close") is None
                    or bar.get("close", 0) <= 0
                ):
                    continue
                bar["symbol"] = symbol
                if bar.get("turnover") is None:
                    bar["turnover"] = None
                valid_bars.append(bar)

            if valid_bars:
                saved = self.db.upsert_ohlcv(table, valid_bars)
                total_saved += saved

            request_count += 1

            # 다음 페이지 시작 시각: 마지막 봉 open_time + 1 interval
            last_bar = bars[-1]
            last_open_time = last_bar.get("open_time", 0)
            if last_open_time == 0:
                break

            next_start = last_open_time + interval_ms
            if next_start <= current_start:
                # 무한 루프 방지
                break
            current_start = next_start

            if current_start >= end_ms:
                break

            time.sleep(self.sleep_sec)

        logger.debug(
            "[%s %s] %d건 저장 (%d회 요청)",
            symbol, interval, total_saved, request_count,
        )
        return total_saved

    # ── products_master 수집 ───────────────────────────────────────────

    def collect_products(self, symbols: Optional[List[str]] = None) -> int:
        """상품 마스터를 수집하여 DB에 저장.

        Args:
            symbols: 수집할 심볼 목록. None이면 AppSettings.symbols 사용.

        Returns:
            저장된 상품 수
        """
        if symbols is None:
            from config.settings import settings
            symbols = settings.symbols

        logger.info("상품 마스터 수집 시작: %d개 심볼", len(symbols))

        try:
            instruments = self.client.get_instruments_info(category="linear")
        except Exception as e:
            logger.error("instruments-info 조회 실패: %s", e)
            return 0

        # 대상 심볼 필터링
        symbol_set = set(symbols)
        rows = []
        now_ms = int(time.time() * 1000)

        for item in instruments:
            sym = item.get("symbol", "")
            if sym not in symbol_set:
                continue

            price_filter = item.get("priceFilter", {})
            lot_filter = item.get("lotSizeFilter", {})

            rows.append({
                "symbol": sym,
                "base_coin": item.get("baseCoin", ""),
                "quote_coin": item.get("quoteCoin", "USDT"),
                "min_qty": _safe_float(lot_filter.get("minOrderQty")),
                "qty_step": _safe_float(lot_filter.get("qtyStep")),
                "tick_size": _safe_float(price_filter.get("tickSize")),
                "min_notional": _safe_float(lot_filter.get("minOrderAmt")),
                "max_leverage": None,  # 별도 조회 필요
                "contract_type": item.get("contractType", "LinearPerpetual"),
                "updated_at": now_ms,
            })

        if rows:
            saved = self.db.upsert_products(rows)
            logger.info("상품 마스터 %d개 저장 완료", saved)
            return saved

        logger.warning("저장할 상품 마스터가 없습니다.")
        return 0

    # ── 전체 수집 ─────────────────────────────────────────────────────

    def collect_all(
        self,
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        years: int = 2,
        collect_products: bool = True,
    ) -> Dict[str, Dict[str, int]]:
        """전체 심볼 × 타임프레임 OHLCV 수집.

        Args:
            symbols: 수집할 심볼 목록. None이면 AppSettings.symbols.
            intervals: 수집할 Bybit interval 목록 (예: ["15", "60", "240", "D"]).
                None이면 ["15", "60", "240"] 기본값.
            start_date: 수집 시작일 "YYYY-MM-DD". None이면 years 전 날짜.
            years: start_date 미지정 시 몇 년치 수집할지 (기본 2)
            collect_products: True이면 상품 마스터도 수집 (기본 True)

        Returns:
            {symbol: {interval: saved_count}} 형태의 결과 딕셔너리
        """
        if symbols is None:
            from config.settings import settings
            symbols = settings.symbols

        if intervals is None:
            intervals = ["15", "60", "240"]

        # 시작 날짜 계산
        if start_date is not None:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(days=365 * years)

        end_dt = datetime.now(tz=timezone.utc)
        start_ms = self._dt_to_ms(start_dt)
        end_ms = self._dt_to_ms(end_dt)

        logger.info(
            "수집 시작 — 심볼: %d개, 타임프레임: %s, 기간: %s ~ %s",
            len(symbols),
            intervals,
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
        )

        # DB 초기화 (스키마 적용)
        self.db.initialize()

        # 상품 마스터 수집
        if collect_products:
            self.collect_products(symbols)

        results: Dict[str, Dict[str, int]] = {}
        total_symbols = len(symbols)
        total_intervals = len(intervals)

        for sym_idx, symbol in enumerate(symbols):
            results[symbol] = {}
            for ivl_idx, interval in enumerate(intervals):
                # 진행률 표시
                progress_pct = (
                    (sym_idx * total_intervals + ivl_idx)
                    / (total_symbols * total_intervals)
                    * 100
                )
                print(
                    f"[{progress_pct:5.1f}%] {symbol} {interval} 수집 중...",
                    flush=True,
                )

                saved = self._collect_one_symbol_interval(
                    symbol=symbol,
                    interval=interval,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
                results[symbol][interval] = saved

        # 최종 요약
        total_saved = sum(
            cnt for sym_res in results.values() for cnt in sym_res.values()
        )
        print(f"\n수집 완료: 총 {total_saved:,}봉 저장", flush=True)
        logger.info("전체 수집 완료: %d봉", total_saved)

        return results

    def collect_symbol(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
    ) -> int:
        """단일 심볼의 최근 N봉 수집 (테스트/업데이트용).

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            interval: Bybit interval 문자열 ("15", "60", "240", "D")
            limit: 수집할 봉 수 (기본 100)

        Returns:
            저장된 봉 수
        """
        from db.db_manager import TIMEFRAME_TABLE
        table = TIMEFRAME_TABLE.get(interval, "ohlcv_15m")

        self.db.initialize()

        try:
            bars = self.client.get_kline(
                symbol=symbol,
                interval=interval,
                limit=limit,
            )
        except Exception as e:
            logger.error("collect_symbol 실패 (symbol=%s): %s", symbol, e)
            return 0

        valid_bars = []
        for bar in bars:
            if bar.get("close") is None or bar.get("close", 0) <= 0:
                continue
            bar["symbol"] = symbol
            if bar.get("turnover") is None:
                bar["turnover"] = None
            valid_bars.append(bar)

        if not valid_bars:
            logger.warning("유효한 봉 없음: %s %s", symbol, interval)
            return 0

        saved = self.db.upsert_ohlcv(table, valid_bars)
        logger.info(
            "collect_symbol 완료: %s %s — %d봉 저장",
            symbol, interval, saved,
        )
        return saved


def _safe_float(val: object) -> Optional[float]:
    """문자열/숫자 → float 변환. 실패 시 None."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


__all__ = ["HistoricalCollector"]
