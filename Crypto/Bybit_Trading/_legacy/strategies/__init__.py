"""strategies 패키지 — 전략 팩토리 및 공개 인터페이스.

6개 전략 클래스와 RegimeDetector를 export한다.
get_all_strategies()로 전체 전략 인스턴스 목록을 가져올 수 있다.
"""

from typing import List

from strategies.base import BaseStrategy, Signal, TradeRecord
from strategies.bb_kc_squeeze import BBKCSqueeze
from strategies.pairs_trading import PairsTrading
from strategies.keltner_mr import KeltnerMR
from strategies.ichimoku_cloud import IchimokuCloud
from strategies.kama_mr import KAMAMR
from strategies.rsi_macd import RSIMACDStrategy
from strategies.regime_detector import RegimeDetector


def get_all_strategies() -> List[BaseStrategy]:
    """기본 파라미터로 초기화된 전체 전략 인스턴스 목록을 반환한다.

    Returns:
        6개 전략 인스턴스 리스트
    """
    return [
        BBKCSqueeze(),
        PairsTrading(),
        KeltnerMR(),
        IchimokuCloud(),
        KAMAMR(),
        RSIMACDStrategy(),
    ]


def get_strategy_by_name(name: str) -> BaseStrategy:
    """전략명으로 해당 전략 인스턴스를 반환한다.

    Args:
        name: 전략명 (예: "BBKCSqueeze", "KeltnerMR")

    Returns:
        해당 전략 인스턴스

    Raises:
        ValueError: 존재하지 않는 전략명인 경우
    """
    _registry = {
        "BBKCSqueeze": BBKCSqueeze,
        "PairsTrading": PairsTrading,
        "KeltnerMR": KeltnerMR,
        "IchimokuCloud": IchimokuCloud,
        "KAMAMR": KAMAMR,
        "RSIMACDStrategy": RSIMACDStrategy,
    }

    if name not in _registry:
        raise ValueError(
            f"존재하지 않는 전략: '{name}'. "
            f"가능한 전략: {list(_registry.keys())}"
        )

    return _registry[name]()


__all__ = [
    "BaseStrategy",
    "Signal",
    "TradeRecord",
    "BBKCSqueeze",
    "PairsTrading",
    "KeltnerMR",
    "IchimokuCloud",
    "KAMAMR",
    "RSIMACDStrategy",
    "RegimeDetector",
    "get_all_strategies",
    "get_strategy_by_name",
]
