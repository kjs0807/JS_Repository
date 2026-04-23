"""전략 레지스트리. 전략 등록/조회/인스턴스화."""
from __future__ import annotations

import itertools
import logging
from typing import Any, Dict, List, Tuple, Type

logger = logging.getLogger(__name__)


class StrategyRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, Dict[str, Any]] = {}

    def register(self, strategy_cls: Type, param_space: Dict[str, List]) -> None:
        tmp = strategy_cls()
        name = tmp.name
        self._registry[name] = {
            "cls": strategy_cls,
            "param_space": param_space,
            "name": name,
            "timeframe": tmp.timeframe,
        }

    def list_all(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": e["name"],
                "timeframe": e["timeframe"],
                "param_space": e["param_space"],
            }
            for e in self._registry.values()
        ]

    def get(self, name: str, params: Dict[str, Any] | None = None) -> Any:
        if name not in self._registry:
            raise KeyError(f"등록되지 않은 전략: {name}")
        strategy = self._registry[name]["cls"]()
        if params:
            strategy.set_params(params)
        return strategy

    def get_candidates(self) -> List[Tuple[Any, Dict[str, Any]]]:
        candidates = []
        for entry in self._registry.values():
            keys = list(entry["param_space"].keys())
            values = list(entry["param_space"].values())
            for combo in itertools.product(*values):
                params = dict(zip(keys, combo))
                strategy = entry["cls"]()
                strategy.set_params(params)
                candidates.append((strategy, params))
        return candidates


__all__ = ["StrategyRegistry"]
