"""backtester/analyzer.py 단위 테스트."""
import pytest
from src.backtester.engine import BacktestResult
from src.backtester.analyzer import PerformanceAnalyzer
from src.execution.backtest_broker import TradeRecord

def _make_result(pnls, initial=50000.0):
    trades, equity, eq = [], [initial], initial
    for i, pnl in enumerate(pnls):
        eq += pnl
        equity.append(eq)
        trades.append(TradeRecord(symbol="BTCUSDT", strategy_name="Test", side="LONG",
            entry_time=1700000000000+i*3600000, exit_time=1700000000000+(i+1)*3600000,
            entry_price=65000.0, exit_price=65000.0+pnl/0.01, qty=0.01, pnl=pnl,
            fee=0.0, exit_reason="TP", source="STRATEGY"))
    return BacktestResult(strategy_name="Test", symbol="BTCUSDT",
        total_trades=len(trades), trades=trades, equity_curve=equity, total_pnl=sum(pnls))

class TestPerformanceAnalyzer:
    def setup_method(self):
        self.analyzer = PerformanceAnalyzer()

    def test_compare_returns_table(self):
        table = self.analyzer.compare([_make_result([100,-50,200,-30]), _make_result([-100,-200,50])])
        assert len(table) == 2
        assert "strategy_name" in table[0] and "total_pnl" in table[0]

    def test_compare_sorted_by_pnl(self):
        table = self.analyzer.compare([_make_result([100,200]), _make_result([500,500])])
        assert table[0]["total_pnl"] >= table[1]["total_pnl"]

    def test_generate_report_string(self):
        report = self.analyzer.generate_report(_make_result([100,-50,200]))
        assert isinstance(report, str) and "Test" in report and "BTCUSDT" in report

    def test_calc_correlation_single(self):
        assert self.analyzer.calc_correlation([_make_result([100,-50,200,-30,150])]).shape == (1,1)

    def test_calc_correlation_two(self):
        corr = self.analyzer.calc_correlation([
            _make_result([100,-50,200,-30,150]), _make_result([-100,50,-200,30,-150])])
        assert corr.shape == (2,2) and corr.iloc[0,1] < 0

    def test_suggest_allocation_equal_weight(self):
        alloc = self.analyzer.suggest_allocation([_make_result([100,100,100]), _make_result([200,200,200])])
        assert len(alloc) == 2 and abs(sum(alloc.values()) - 1.0) < 0.01
