"""WalkForwardAnalyzer — IS 최적화 → OOS 검증 롤링."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type
import numpy as np
from src.core.config import BacktestConfig
from src.data_manager.feed import HistoricalDataFeed
from src.backtester.config import WalkForwardConfig
from src.backtester.engine import BacktestEngine, BacktestResult
from src.backtester.optimizer import GridSearchOptimizer

logger = logging.getLogger(__name__)

BARS_PER_YEAR = {"1m":525600,"5m":105120,"15m":35040,"30m":17520,"1h":8760,"4h":2190,"1d":365}

@dataclass
class WalkForwardWindow:
    window_idx: int
    is_start: int
    is_end: int
    oos_start: int
    oos_end: int
    best_params: Optional[Dict[str, Any]] = None
    is_result: Optional[BacktestResult] = None
    oos_result: Optional[BacktestResult] = None
    oos_retention: float = 0.0

@dataclass
class WalkForwardResult:
    strategy_name: str
    symbol: str
    windows: List[WalkForwardWindow] = field(default_factory=list)
    avg_oos_retention: float = 0.0
    avg_oos_sharpe: float = 0.0
    oos_positive_pct: float = 0.0

class WalkForwardAnalyzer:
    def __init__(self, wf_config: Optional[WalkForwardConfig] = None) -> None:
        self.wf_config = wf_config or WalkForwardConfig()
        self.engine = BacktestEngine()

    def run(self, strategy_cls: Type, param_space: Dict[str, List],
            data_feed: HistoricalDataFeed, config: BacktestConfig,
            symbol: str = "UNKNOWN") -> WalkForwardResult:
        data_feed.reset()
        timeframe = data_feed.timeframe
        all_bars_df = data_feed._data.get(symbol)
        tmp = strategy_cls()
        if all_bars_df is None or all_bars_df.empty:
            return WalkForwardResult(strategy_name=tmp.name, symbol=symbol)

        n = len(all_bars_df)
        bars_per_month = self._calc_bars_per_month(
            timeframe, n, all_bars_df,
            self.wf_config.is_months, self.wf_config.oos_months,
        )
        is_bars = self.wf_config.is_months * bars_per_month
        oos_bars = self.wf_config.oos_months * bars_per_month
        window_size = is_bars + oos_bars

        if window_size < 1:
            return WalkForwardResult(strategy_name=tmp.name, symbol=symbol)

        result = WalkForwardResult(strategy_name=tmp.name, symbol=symbol)
        optimizer = GridSearchOptimizer(self.engine)
        windows, start, window_idx = [], 0, 0

        while start + window_size <= n:
            is_end = start + is_bars
            oos_end = start + window_size
            is_feed = self._make_sub_feed(data_feed, symbol, start, is_end)
            opt_result = optimizer.run(strategy_cls, param_space, is_feed, config, symbol)
            oos_feed = self._make_sub_feed(data_feed, symbol, is_end, oos_end)
            oos_strategy = strategy_cls()
            oos_strategy.set_params(opt_result.best_params)
            oos_result = self.engine.run(oos_strategy, oos_feed, config, symbol)
            is_strategy = strategy_cls()
            is_strategy.set_params(opt_result.best_params)
            is_feed2 = self._make_sub_feed(data_feed, symbol, start, is_end)
            is_result = self.engine.run(is_strategy, is_feed2, config, symbol)
            retention = self._calc_retention(is_result.sharpe_ratio, oos_result.sharpe_ratio)
            windows.append(WalkForwardWindow(window_idx=window_idx,
                is_start=start, is_end=is_end, oos_start=is_end, oos_end=oos_end,
                best_params=opt_result.best_params, is_result=is_result,
                oos_result=oos_result, oos_retention=retention))
            start += oos_bars
            window_idx += 1

        # Discard all windows if fewer than min_windows threshold
        if len(windows) < self.wf_config.min_windows:
            return result

        result.windows = windows
        if windows:
            result.avg_oos_retention = float(np.mean([w.oos_retention for w in windows]))
            oos_sharpes = [w.oos_result.sharpe_ratio for w in windows if w.oos_result]
            result.avg_oos_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
            result.oos_positive_pct = sum(1 for w in windows if w.oos_result and w.oos_result.total_pnl > 0) / len(windows)
        return result

    @staticmethod
    def _calc_bars_per_month(
        timeframe: str, n_bars: int, df, is_months: int, oos_months: int
    ) -> int:
        """실제 데이터 타임스탬프 기반으로 bars_per_month 산출.

        데이터가 요청 윈도우보다 짧으면 실제 밀도에 맞게 스케일 다운한다.
        """
        MS_PER_MONTH = 30.44 * 24 * 3600 * 1000
        total_window_months = is_months + oos_months

        if "open_time" in df.columns and n_bars >= 2:
            first_ts = float(df["open_time"].iloc[0])
            last_ts = float(df["open_time"].iloc[-1])
            total_ms = last_ts - first_ts
            if total_ms > 0:
                total_months = total_ms / MS_PER_MONTH
                if total_months >= total_window_months:
                    # Enough real data: use actual bar density
                    return max(1, int((n_bars - 1) / total_months))
                # Data shorter than required window: scale proportionally
                # Aim for enough bars to form at least 1 window with a small margin
                bpm = int(n_bars / (total_window_months + 0.5))
                return max(1, bpm)

        # Fallback: theoretical bars_per_month from timeframe constant
        theoretical = BARS_PER_YEAR.get(timeframe, 8760) // 12
        if theoretical * total_window_months <= n_bars:
            return theoretical
        bpm = int(n_bars / (total_window_months + 0.5))
        return max(1, bpm)

    def _make_sub_feed(self, parent_feed, symbol, start_idx, end_idx):
        from src.data_manager.feed import HistoricalDataFeed as HDF
        sub_df = parent_feed._data[symbol].iloc[start_idx:end_idx].copy()
        feed = object.__new__(HDF)
        feed.db = parent_feed.db
        feed.symbols = [symbol]
        feed.timeframe = parent_feed.timeframe
        feed._data = {symbol: sub_df}
        feed._indices = {symbol: 0}
        feed._bar_count = 0
        return feed

    @staticmethod
    def _calc_retention(is_sharpe, oos_sharpe):
        if is_sharpe <= 0: return 1.0 if oos_sharpe > 0 else 0.0
        return float(max(0.0, min(1.0, oos_sharpe / is_sharpe)))

__all__ = ["WalkForwardAnalyzer", "WalkForwardResult", "WalkForwardWindow"]
