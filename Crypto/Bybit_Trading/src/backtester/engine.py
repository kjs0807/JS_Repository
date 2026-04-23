"""BacktestEngine — 봉 루프를 돌리고 Broker에게 체결을 위임한다."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np
from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.feed import HistoricalDataFeed
from src.execution.backtest_broker import BacktestBroker, TradeRecord

logger = logging.getLogger(__name__)

@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    expectancy: float = 0.0
    avg_trade_pnl: float = 0.0
    equity_curve: List[float] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)

class BacktestEngine:
    def run(
        self,
        strategy,
        data_feed: HistoricalDataFeed,
        config: Optional[BacktestConfig] = None,
        symbol: str = "UNKNOWN",
        risk_config: Optional[RiskConfig] = None,
        reference_symbols: Optional[List[str]] = None,
    ) -> BacktestResult:
        if config is None:
            config = BacktestConfig()
        data_feed.reset()

        # Fast path detection
        has_fast_path = hasattr(strategy, "prepare") and hasattr(strategy, "on_bar_fast")
        has_multi_path = hasattr(strategy, "prepare_multi")

        cache = None
        if has_multi_path and reference_symbols:
            full_series = data_feed.get_full_series(symbol)
            reference_series = {
                ref: data_feed.get_full_series(ref) for ref in reference_symbols
            }
            cache = strategy.prepare_multi(full_series, reference_series)
        elif has_fast_path:
            full_series = data_feed.get_full_series(symbol)
            cache = strategy.prepare(full_series)

        broker = BacktestBroker(config, risk_config)
        bar_count = 0
        while data_feed.has_next():
            bar = data_feed.next_bar(symbol)
            if bar is None:
                break
            broker.process_bar(bar)
            bar_count += 1
            if bar_count <= strategy.warmup_bars:
                continue

            if (has_multi_path and reference_symbols) or has_fast_path:
                i = bar_count - 1
                strategy.on_bar_fast(bar, i, cache, broker)
            else:
                # EWM 지표(ema, atr)는 과거 데이터가 많을수록 수렴한다.
                # warmup_bars만 전달하면 EWM 초기값이 fast path(전체 시계열)와 달라져
                # 동등성이 깨진다. bar_count(현재까지 전체 봉 수)를 전달하여 동일한
                # EWM 히스토리를 보장한다.
                series = data_feed.get_history(symbol, bar_count)
                strategy.on_bar(bar, series, broker)

        broker.close_all(reason="BACKTEST_END")
        trades = broker.get_trades()
        equity_curve = broker.get_equity_curve()
        result = BacktestResult(strategy_name=strategy.name, symbol=symbol,
                                trades=trades, equity_curve=equity_curve)
        self._calc_metrics(result, config.initial_capital)
        return result

    def _calc_metrics(self, result: BacktestResult, initial_capital: float) -> None:
        trades = result.trades
        equity = result.equity_curve
        if not trades:
            result.equity_curve = result.equity_curve or [initial_capital]
            return
        pnl_list = [t.pnl for t in trades]
        wins = [p for p in pnl_list if p > 0]
        losses = [p for p in pnl_list if p <= 0]
        result.total_trades = len(trades)
        result.win_rate = len(wins) / len(trades) if trades else 0.0
        result.total_pnl = sum(pnl_list)
        result.avg_trade_pnl = result.total_pnl / len(trades) if trades else 0.0
        result.expectancy = result.avg_trade_pnl
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        if gross_loss > 0:
            result.profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            result.profit_factor = float("inf")
        if len(pnl_list) > 1:
            pnl_arr = np.array(pnl_list)
            std_pnl = np.std(pnl_arr, ddof=1)
            if std_pnl > 0:
                result.sharpe_ratio = (np.mean(pnl_arr) / std_pnl) * np.sqrt(252)
        if len(equity) > 1:
            eq_arr = np.array(equity)
            peak = np.maximum.accumulate(eq_arr)
            dd = (peak - eq_arr) / (peak + 1e-9)
            result.max_drawdown = float(dd.max())
        if result.max_drawdown > 0 and equity:
            annual_return = (equity[-1] - initial_capital) / initial_capital
            result.calmar_ratio = annual_return / result.max_drawdown

__all__ = ["BacktestEngine", "BacktestResult"]
