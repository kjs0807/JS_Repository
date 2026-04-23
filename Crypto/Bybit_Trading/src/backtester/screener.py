"""Screener — 1차 스크리닝."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import List, Tuple
from src.backtester.config import ScreeningCriteria
from src.backtester.engine import BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class ScreeningVerdict:
    passed: bool
    reason: str = ""
    strategy_name: str = ""


class Screener:
    def __init__(self, criteria: ScreeningCriteria) -> None:
        self.criteria = criteria

    def screen(self, result: BacktestResult) -> ScreeningVerdict:
        c = self.criteria
        if result.total_trades < c.min_trades:
            return ScreeningVerdict(False, f"거래 수 부족: {result.total_trades} < {c.min_trades}", result.strategy_name)
        if result.profit_factor < c.min_profit_factor:
            return ScreeningVerdict(False, f"Profit Factor 부족: {result.profit_factor:.2f} < {c.min_profit_factor}", result.strategy_name)
        if result.win_rate < c.min_win_rate:
            return ScreeningVerdict(False, f"승률 부족: {result.win_rate:.1%} < {c.min_win_rate:.1%}", result.strategy_name)
        if result.max_drawdown > c.max_drawdown:
            return ScreeningVerdict(False, f"MDD 초과: {result.max_drawdown:.2%} > {c.max_drawdown:.2%}", result.strategy_name)
        if result.sharpe_ratio < c.min_sharpe:
            return ScreeningVerdict(False, f"Sharpe 부족: {result.sharpe_ratio:.3f} < {c.min_sharpe}", result.strategy_name)
        if result.expectancy < c.min_expectancy:
            return ScreeningVerdict(False, f"기대값 부족: {result.expectancy:.2f} < {c.min_expectancy}", result.strategy_name)
        return ScreeningVerdict(True, "", result.strategy_name)

    def bulk_screen(self, results: List[BacktestResult]) -> Tuple[List[BacktestResult], List[BacktestResult]]:
        passed, failed = [], []
        for r in results:
            (passed if self.screen(r).passed else failed).append(r)
        return passed, failed


__all__ = ["Screener", "ScreeningVerdict"]
