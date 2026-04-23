"""Optimizer — 파라미터 탐색."""
from __future__ import annotations
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Type
from src.core.config import BacktestConfig
from src.data_manager.feed import HistoricalDataFeed
from src.backtester.engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class OptResult:
    best_params: Dict[str, Any]
    best_score: float
    all_results: List[Tuple[Dict[str, Any], float]] = field(default_factory=list)


class GridSearchOptimizer:
    def __init__(self, engine: BacktestEngine, objective: str = "sharpe") -> None:
        self.engine = engine
        self.objective = objective

    def _score(self, result: BacktestResult) -> float:
        if self.objective == "calmar":
            return result.calmar_ratio
        elif self.objective == "profit_factor":
            return result.profit_factor
        return result.sharpe_ratio

    def run(self, strategy_cls: Type, param_space: Dict[str, List],
            data_feed: HistoricalDataFeed, config: BacktestConfig,
            symbol: str = "UNKNOWN") -> OptResult:
        keys = list(param_space.keys())
        values = list(param_space.values())
        best_params, best_score = {}, float("-inf")
        all_results = []
        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            strategy = strategy_cls()
            strategy.set_params(params)
            result = self.engine.run(strategy, data_feed, config, symbol)
            score = self._score(result)
            all_results.append((params, score))
            if score > best_score:
                best_score, best_params = score, params
        return OptResult(best_params=best_params, best_score=best_score, all_results=all_results)


__all__ = ["GridSearchOptimizer", "OptResult"]
