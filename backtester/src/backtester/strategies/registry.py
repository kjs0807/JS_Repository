"""Strategy registry — name → class 매핑 (Phase 1.5 PR 9).

CLI 가 ``BacktestConfig.strategy_name`` 을 받아 strategy 인스턴스를 생성할 때 사용한다.
새 strategy 를 추가할 때 이 dict 에 등록한다.
"""

from __future__ import annotations

from typing import Any

from backtester.core.errors import ConfigError
from backtester.strategies.base import BaseStrategy
from backtester.strategies.beda_bollinger_pullback import (
    BedaBollingerPullbackStrategy,
)
from backtester.strategies.beda_bollinger_modes import BedaBollingerModesStrategy
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.bbkc_multi_legacy_compat import BBKCMultiLegacyCompatStrategy
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from backtester.strategies.frama_channel import FRAMAChannelStrategy
from backtester.strategies.frama_ema200_channel import (
    FRAMAEMA200ChannelStrategy,
    FRAMAMultiEMA200ChannelStrategy,
)
from backtester.strategies.frama_multi_channel import FRAMAMultiChannelStrategy
from backtester.strategies.frama_pullback_channel import (
    FRAMAChannelPullbackStrategy,
    FRAMAMultiChannelPullbackStrategy,
)
from backtester.strategies.sats import SATSStrategy

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "beda_bollinger_modes": BedaBollingerModesStrategy,
    "beda_bollinger_pullback": BedaBollingerPullbackStrategy,
    "bbkc_legacy_compat": BBKCLegacyCompatStrategy,
    "bbkc_multi_legacy_compat": BBKCMultiLegacyCompatStrategy,
    "bbkc_squeeze": BBKCSqueezeStrategy,
    "frama_channel": FRAMAChannelStrategy,
    "frama_ema200_channel": FRAMAEMA200ChannelStrategy,
    "frama_multi_ema200_channel": FRAMAMultiEMA200ChannelStrategy,
    "frama_multi_channel": FRAMAMultiChannelStrategy,
    "frama_pullback_channel": FRAMAChannelPullbackStrategy,
    "frama_multi_pullback_channel": FRAMAMultiChannelPullbackStrategy,
    "sats": SATSStrategy,
}


def build_strategy(name: str, params: dict[str, Any]) -> BaseStrategy:
    """``name`` 을 등록된 strategy 클래스로 lookup → ``cls(**params)``.

    실패 케이스 → 모두 ``ConfigError`` (CLI 가 사용자 오류로 일관 처리):
    - 빈 ``name``
    - 미등록 ``name``
    - 잘못된 ``params`` (TypeError: unexpected keyword 등) — strategy 생성자 시그니처 불일치
    """
    if not name:
        raise ConfigError(
            "strategy_name is empty; CLI requires non-empty strategy_name "
            "(set 'strategy_name' in config.yaml)"
        )
    if name not in STRATEGY_REGISTRY:
        raise ConfigError(
            f"unknown strategy_name {name!r}; available: "
            f"{sorted(STRATEGY_REGISTRY)}"
        )
    cls = STRATEGY_REGISTRY[name]
    try:
        return cls(**params)
    except TypeError as e:
        raise ConfigError(
            f"strategy_params for {name!r} do not match {cls.__name__} signature: {e}"
        ) from e
