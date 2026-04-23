"""Strategy Protocol 정의. 전략은 환경(백테스트/실거래)을 모른다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Protocol, runtime_checkable

import numpy as np

from src.core.types import Bar, BarSeries
from src.execution.broker import Broker, Fill


@dataclass
class IndicatorCache:
    """전략이 반환하는 사전 계산된 지표 캐시.

    Strategy.prepare()가 전체 시계열에 대해 한 번 계산하여 반환하고,
    Strategy.on_bar_fast()가 인덱스로 조회한다.
    """
    arrays: Dict[str, np.ndarray] = field(default_factory=dict)

    def get(self, key: str) -> np.ndarray:
        """지표 배열 조회. 없으면 KeyError."""
        return self.arrays[key]


@runtime_checkable
class Strategy(Protocol):
    name: str
    timeframe: str

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None: ...
    def on_fill(self, fill: Fill) -> None: ...
    def get_params(self) -> dict: ...
    def set_params(self, params: dict) -> None: ...

    @property
    def warmup_bars(self) -> int: ...


__all__ = ["Strategy", "IndicatorCache"]
