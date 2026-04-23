"""PerformanceAnalyzer — 성과 지표 + 비교 + 리포트 + 포트폴리오 분석."""
from __future__ import annotations
import logging
from typing import Dict, List
import numpy as np
import pandas as pd
from src.backtester.engine import BacktestResult

logger = logging.getLogger(__name__)


class PerformanceAnalyzer:
    def compare(self, results: List[BacktestResult]) -> List[Dict]:
        table = [{"strategy_name": r.strategy_name, "symbol": r.symbol,
                  "total_trades": r.total_trades, "win_rate": round(r.win_rate, 3),
                  "total_pnl": round(r.total_pnl, 2), "sharpe_ratio": round(r.sharpe_ratio, 3),
                  "max_drawdown": round(r.max_drawdown, 4), "profit_factor": round(r.profit_factor, 3),
                  "calmar_ratio": round(r.calmar_ratio, 3), "expectancy": round(r.expectancy, 2)}
                 for r in results]
        table.sort(key=lambda x: x["total_pnl"], reverse=True)
        return table

    def generate_report(self, result: BacktestResult) -> str:
        return "\n".join([
            f"=== {result.strategy_name} | {result.symbol} ===",
            f"거래 수: {result.total_trades}", f"승률: {result.win_rate:.1%}",
            f"총 PnL: {result.total_pnl:,.2f} USDT", f"Sharpe: {result.sharpe_ratio:.3f}",
            f"MDD: {result.max_drawdown:.2%}", f"Profit Factor: {result.profit_factor:.3f}",
            f"Calmar: {result.calmar_ratio:.3f}", f"기대값: {result.expectancy:,.2f} USDT/trade"])

    def calc_correlation(self, results: List[BacktestResult]) -> pd.DataFrame:
        seen: Dict[str, int] = {}
        pnl_dict: Dict[str, List[float]] = {}
        for r in results:
            base = f"{r.strategy_name}_{r.symbol}"
            count = seen.get(base, 0)
            key = base if count == 0 else f"{base}_{count}"
            seen[base] = count + 1
            pnl_dict[key] = [t.pnl for t in r.trades]
        max_len = max(len(v) for v in pnl_dict.values()) if pnl_dict else 0
        for key in pnl_dict:
            while len(pnl_dict[key]) < max_len:
                pnl_dict[key].append(0.0)
        return pd.DataFrame(pnl_dict).corr()

    def suggest_allocation(self, results: List[BacktestResult]) -> Dict[str, float]:
        if not results:
            return {}
        seen: Dict[str, int] = {}
        inv_vols: Dict[str, float] = {}
        for r in results:
            base = r.strategy_name
            count = seen.get(base, 0)
            key = base if count == 0 else f"{base}_{count}"
            seen[base] = count + 1
            pnl_list = [t.pnl for t in r.trades]
            vol = np.std(pnl_list, ddof=1) if len(pnl_list) >= 2 else 1e-10
            inv_vols[key] = 1.0 / (vol + 1e-10)
        total = sum(inv_vols.values())
        return {k: v / total for k, v in inv_vols.items()}


__all__ = ["PerformanceAnalyzer"]
