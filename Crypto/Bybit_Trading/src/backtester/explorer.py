"""StrategyExplorer — 전략 탐색 파이프라인 오케스트레이터."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.core.config import BacktestConfig
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.registry import StrategyRegistry
from src.backtester.config import ScreeningCriteria
from src.backtester.engine import BacktestEngine
from src.backtester.screener import Screener

logger = logging.getLogger(__name__)

@dataclass
class ExplorationReport:
    screened: List[Dict] = field(default_factory=list)
    passed: List[Dict] = field(default_factory=list)
    failed: List[Dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["=== 전략 탐색 결과 ===", f"총 후보: {len(self.screened)}",
                 f"통과: {len(self.passed)}", f"탈락: {len(self.failed)}"]
        if self.passed:
            lines.append("\n--- 통과 전략 ---")
            for p in self.passed:
                lines.append(f"  {p['strategy_name']} ({p['symbol']}): PnL={p['total_pnl']:.0f}, Sharpe={p['sharpe']:.3f}")
        if self.failed:
            lines.append("\n--- 탈락 전략 ---")
            for f in self.failed:
                lines.append(f"  {f['strategy_name']} ({f['symbol']}): {f['reason']}")
        return "\n".join(lines)


class StrategyExplorer:
    def __init__(self, criteria: Optional[ScreeningCriteria] = None) -> None:
        self.engine = BacktestEngine()
        self.screener = Screener(criteria or ScreeningCriteria())

    def explore(self, registry: StrategyRegistry, data_feed: HistoricalDataFeed,
                config: BacktestConfig, symbols: List[str]) -> ExplorationReport:
        report = ExplorationReport()
        for strategy, params in registry.get_candidates():
            for symbol in symbols:
                result = self.engine.run(strategy, data_feed, config, symbol)
                entry = {"strategy_name": strategy.name, "symbol": symbol, "params": params,
                         "total_trades": result.total_trades, "total_pnl": result.total_pnl,
                         "sharpe": result.sharpe_ratio, "max_dd": result.max_drawdown,
                         "profit_factor": result.profit_factor, "win_rate": result.win_rate}
                report.screened.append(entry)
                verdict = self.screener.screen(result)
                if verdict.passed:
                    report.passed.append(entry)
                else:
                    entry["reason"] = verdict.reason
                    report.failed.append(entry)
        return report


__all__ = ["StrategyExplorer", "ExplorationReport"]
