"""동적 심볼 관리 모듈.

Bybit API에서 거래대금 상위 심볼을 조회하고,
전략별 심볼 유니버스를 관리한다.

구조:
  - all_symbols (100개): 데이터 수집, WS 구독, 대시보드 드롭다운
  - strategy_symbols (13개): BBKC/RSIMACD 시그널 스캔 (하드코딩)
  - pairs_universe (30개): PairsTrading 페어 선별 (24h마다 갱신)
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Bybit 공개 API
_PUBLIC_BASE_URL = "https://api.bybit.com"

# BBKC/RSIMACD용 고정 심볼 (그리드 최적화 완료)
STRATEGY_SYMBOLS: List[str] = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "TRXUSDT", "DOTUSDT",
    "ETCUSDT", "TIAUSDT", "LINKUSDT",
]


class SymbolManager:
    """동적 심볼 유니버스 관리자.

    Attributes:
        all_symbols: 데이터 수집 대상 전체 심볼 (거래대금 상위 N개)
        pairs_universe: PairsTrading용 상위 30개
        strategy_symbols: BBKC/RSIMACD용 고정 13개
    """

    def __init__(
        self,
        top_n: int = 100,
        pairs_n: int = 30,
        refresh_interval: float = 24 * 3600,
    ) -> None:
        """초기화.

        Args:
            top_n: 전체 수집 대상 심볼 수 (기본 100)
            pairs_n: 페어 트레이딩 유니버스 심볼 수 (기본 30)
            refresh_interval: 유니버스 갱신 주기 초 (기본 24시간)
        """
        self.top_n = top_n
        self.pairs_n = pairs_n
        self.refresh_interval = refresh_interval
        self.strategy_symbols = list(STRATEGY_SYMBOLS)

        self._all_symbols: List[str] = []
        self._pairs_universe: List[str] = []
        self._volume_ranking: List[Tuple[str, float]] = []
        self._last_refresh: float = 0.0

        # 초기 로딩
        self.refresh()

    @property
    def all_symbols(self) -> List[str]:
        """데이터 수집/WS 구독 대상 전체 심볼."""
        return self._all_symbols

    @property
    def pairs_universe(self) -> List[str]:
        """PairsTrading용 상위 심볼."""
        return self._pairs_universe

    def refresh(self) -> None:
        """Bybit API에서 거래대금 상위 심볼을 갱신한다.

        실패 시 이전 캐시를 유지하고 strategy_symbols를 fallback으로 사용.
        """
        try:
            ranking = self._fetch_volume_ranking()
            if not ranking:
                logger.warning("거래대금 랭킹 조회 결과 없음, 기존 유지")
                if not self._all_symbols:
                    self._all_symbols = list(STRATEGY_SYMBOLS)
                    self._pairs_universe = list(STRATEGY_SYMBOLS)
                return

            self._volume_ranking = ranking

            # 상위 top_n개 = 전체 수집 대상
            top_symbols = [sym for sym, _ in ranking[:self.top_n]]

            # strategy_symbols가 빠지지 않도록 합집합
            combined = list(top_symbols)
            for sym in self.strategy_symbols:
                if sym not in combined:
                    combined.append(sym)
            self._all_symbols = combined

            # 상위 pairs_n개 = 페어 트레이딩 유니버스
            self._pairs_universe = [sym for sym, _ in ranking[:self.pairs_n]]

            self._last_refresh = time.time()
            logger.info(
                "심볼 유니버스 갱신: 전체=%d개, 페어=%d개, 전략고정=%d개",
                len(self._all_symbols), len(self._pairs_universe),
                len(self.strategy_symbols),
            )
        except Exception as exc:
            logger.error("심볼 유니버스 갱신 실패: %s", exc)
            if not self._all_symbols:
                self._all_symbols = list(STRATEGY_SYMBOLS)
                self._pairs_universe = list(STRATEGY_SYMBOLS)

    def maybe_refresh(self) -> bool:
        """갱신 주기가 지났으면 refresh()를 호출한다.

        Returns:
            갱신 수행 여부
        """
        if time.time() - self._last_refresh >= self.refresh_interval:
            self.refresh()
            return True
        return False

    def _fetch_volume_ranking(self) -> List[Tuple[str, float]]:
        """Bybit API에서 USDT 무기한 선물 거래대금 랭킹을 조회한다.

        Returns:
            [(symbol, turnover_24h), ...] 거래대금 내림차순 리스트
        """
        resp = requests.get(
            f"{_PUBLIC_BASE_URL}/v5/market/tickers",
            params={"category": "linear"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            logger.warning("Bybit tickers API 오류: %s", data.get("retMsg"))
            return []

        tickers = data["result"]["list"]
        usdt_perps = []
        for t in tickers:
            sym = t["symbol"]
            if not sym.endswith("USDT"):
                continue
            turnover = float(t.get("turnover24h", 0))
            if turnover <= 0:
                continue
            usdt_perps.append((sym, turnover))

        usdt_perps.sort(key=lambda x: x[1], reverse=True)
        return usdt_perps

    def get_volume_rank(self, symbol: str) -> Optional[int]:
        """심볼의 거래대금 순위를 반환한다 (1-based).

        Args:
            symbol: 심볼

        Returns:
            순위 (1부터). 없으면 None.
        """
        for i, (sym, _) in enumerate(self._volume_ranking):
            if sym == symbol:
                return i + 1
        return None


# 싱글턴 인스턴스 (lazy init)
_instance: Optional[SymbolManager] = None


def get_symbol_manager() -> SymbolManager:
    """SymbolManager 싱글턴 인스턴스를 반환한다."""
    global _instance
    if _instance is None:
        _instance = SymbolManager()
    return _instance


def init_symbol_manager(
    top_n: int = 100, pairs_n: int = 30
) -> SymbolManager:
    """SymbolManager를 초기화하고 싱글턴으로 등록한다.

    Args:
        top_n: 전체 수집 대상 심볼 수
        pairs_n: 페어 트레이딩 유니버스 심볼 수

    Returns:
        초기화된 SymbolManager 인스턴스
    """
    global _instance
    _instance = SymbolManager(top_n=top_n, pairs_n=pairs_n)
    return _instance
