"""데이터 수집기. Bybit REST API 응답을 Bar로 정규화."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.core.types import Bar

logger = logging.getLogger(__name__)


class Collector:
    @staticmethod
    def normalize_kline(raw: Dict[str, Any], symbol: str, timeframe: str) -> Bar:
        return Bar(
            symbol=symbol, timestamp=int(raw["start"]), timeframe=timeframe,
            open=float(raw["open"]), high=float(raw["high"]),
            low=float(raw["low"]), close=float(raw["close"]),
            volume=float(raw["volume"]),
            turnover=float(raw.get("turnover", 0)) if raw.get("turnover") else None,
        )

    @staticmethod
    def normalize_kline_list(raw: List[str], symbol: str, timeframe: str) -> Bar:
        return Bar(
            symbol=symbol, timestamp=int(raw[0]), timeframe=timeframe,
            open=float(raw[1]), high=float(raw[2]),
            low=float(raw[3]), close=float(raw[4]),
            volume=float(raw[5]),
            turnover=float(raw[6]) if len(raw) > 6 and raw[6] else None,
        )

    @staticmethod
    def bars_to_db_rows(bars: List[Bar]) -> List[Dict[str, Any]]:
        return [{
            "symbol": b.symbol, "open_time": b.timestamp,
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume, "turnover": b.turnover,
        } for b in bars]


def collect_klines_mtf(
    rest_client,
    db_manager,
    symbol: str,
    timeframes: list,
    start_ms: int,
    end_ms: int,
) -> dict:
    """Collect OHLCV for a symbol across multiple timeframes.

    Returns: {tf: rows_inserted}.
    """
    results: dict = {}
    for tf in timeframes:
        bars = rest_client.get_klines(
            symbol=symbol,
            interval=tf,
            start=start_ms,
            end=end_ms,
            limit=1000,
        )
        for bar in bars:
            bar.setdefault("timeframe", tf)
        n = db_manager.upsert_ohlcv(symbol=symbol, timeframe=tf, rows=bars)
        results[tf] = n
    return results


__all__ = ["Collector", "collect_klines_mtf"]
