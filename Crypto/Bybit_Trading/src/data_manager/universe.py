"""심볼 유니버스 관리. 거래량 상위 코인 선정, 밈코인 필터링."""
from __future__ import annotations

import logging
from typing import List

from src.core.config import DataConfig

logger = logging.getLogger(__name__)


class UniverseManager:
    def __init__(self, config: DataConfig) -> None:
        self.config = config

    def filter_meme_coins(self, symbols: List[str]) -> List[str]:
        blacklist = set(self.config.meme_blacklist)
        return [s for s in symbols if s not in blacklist]

    def limit(self, symbols: List[str]) -> List[str]:
        return symbols[: self.config.universe_size]

    def build(self, raw_symbols: List[str]) -> List[str]:
        filtered = self.filter_meme_coins(raw_symbols)
        result = self.limit(filtered)
        logger.info("유니버스 구성: 원본=%d → 밈제거=%d → 최종=%d",
                     len(raw_symbols), len(filtered), len(result))
        return result


__all__ = ["UniverseManager"]
