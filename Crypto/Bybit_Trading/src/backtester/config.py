"""백테스터 설정 모듈."""
from __future__ import annotations
from dataclasses import dataclass
from src.core.config import BacktestConfig  # re-export

@dataclass
class ScreeningCriteria:
    min_trades: int = 30
    min_profit_factor: float = 1.3
    min_win_rate: float = 0.35
    max_drawdown: float = 0.25
    min_sharpe: float = 0.5
    min_expectancy: float = 0.0

@dataclass
class WalkForwardConfig:
    is_months: int = 6
    oos_months: int = 2
    min_windows: int = 3
    min_oos_retention: float = 0.5
    min_oos_positive_pct: float = 0.6

__all__ = ["BacktestConfig", "ScreeningCriteria", "WalkForwardConfig"]
