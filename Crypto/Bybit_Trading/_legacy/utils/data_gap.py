"""DB 데이터 gap 수집 유틸리티.

DB의 마지막 봉 ~ 현재 사이의 gap을 Bybit public API로 수집한다.
main.py와 dashboard/app.py 양쪽에서 공통으로 사용한다.
"""

from __future__ import annotations

import logging
import time
from typing import List

import requests

logger = logging.getLogger(__name__)

# Bybit 공개 API (인증 불필요)
_PUBLIC_BASE_URL = "https://api.bybit.com"

# (타임프레임 문자열, 봉 간격 밀리초)
_TIMEFRAMES: List[tuple] = [("15", 900_000), ("60", 3_600_000), ("240", 14_400_000)]


def fill_data_gap(db: object, symbols: List[str]) -> None:
    """DB의 마지막 봉 ~ 현재 사이의 gap을 Bybit public API로 수집한다.

    upsert_ohlcv_by_timeframe(symbol, tf, rows)를 사용하여 테이블명을
    올바르게 매핑한다.

    Args:
        db: DBManager 인스턴스 (get_ohlcv_range, upsert_ohlcv_by_timeframe 필요)
        symbols: 수집할 심볼 리스트
    """
    for symbol in symbols:
        for tf, interval_ms in _TIMEFRAMES:
            try:
                _, last_ts = db.get_ohlcv_range(symbol, tf)  # type: ignore[union-attr]
                now_ms = int(time.time() * 1000)

                if last_ts is None:
                    start_ms = now_ms - 7 * 24 * 3600 * 1000  # 최근 7일
                else:
                    gap_bars = (now_ms - last_ts) // interval_ms
                    if gap_bars <= 1:
                        continue  # gap 없음
                    start_ms = last_ts + interval_ms

                total = 0
                current = start_ms
                while current < now_ms:
                    resp = requests.get(
                        f"{_PUBLIC_BASE_URL}/v5/market/kline",
                        params={
                            "category": "linear",
                            "symbol": symbol,
                            "interval": tf,
                            "start": current,
                            "limit": 1000,
                        },
                        timeout=10,
                    )
                    data = resp.json()
                    if data.get("retCode") != 0 or not data["result"]["list"]:
                        break
                    candles = data["result"]["list"]
                    candles.reverse()
                    rows = [
                        {
                            "symbol": symbol,
                            "open_time": int(c[0]),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5]),
                            "turnover": float(c[6]) if len(c) > 6 else 0.0,
                        }
                        for c in candles
                    ]
                    db.upsert_ohlcv_by_timeframe(symbol, tf, rows)  # type: ignore[union-attr]
                    total += len(rows)
                    current = int(candles[-1][0]) + interval_ms
                    time.sleep(0.12)

                if total > 0:
                    logger.info("  %s/%s: +%d봉 수집", symbol, tf, total)
            except Exception as exc:
                logger.warning(
                    "  %s/%s: gap 수집 실패 - %s: %s",
                    symbol, tf, type(exc).__name__, str(exc)[:80],
                )
