"""backtester/screener.py 단위 테스트."""
import pytest
from src.backtester.engine import BacktestResult
from src.backtester.screener import Screener
from src.backtester.config import ScreeningCriteria

def _make_result(**kwargs):
    defaults = {"strategy_name": "Test", "symbol": "BTCUSDT", "total_trades": 50,
        "win_rate": 0.55, "total_pnl": 500.0, "sharpe_ratio": 1.0,
        "max_drawdown": 0.10, "profit_factor": 1.5, "expectancy": 10.0}
    defaults.update(kwargs)
    return BacktestResult(**defaults)

class TestScreener:
    def setup_method(self):
        self.screener = Screener(ScreeningCriteria())

    def test_pass_good_result(self):
        assert self.screener.screen(_make_result()).passed is True

    def test_fail_low_trades(self):
        v = self.screener.screen(_make_result(total_trades=10))
        assert v.passed is False and "거래 수" in v.reason

    def test_fail_low_profit_factor(self):
        v = self.screener.screen(_make_result(profit_factor=1.0))
        assert v.passed is False and "Profit Factor" in v.reason

    def test_fail_low_win_rate(self):
        v = self.screener.screen(_make_result(win_rate=0.20))
        assert v.passed is False and "승률" in v.reason

    def test_fail_high_drawdown(self):
        v = self.screener.screen(_make_result(max_drawdown=0.30))
        assert v.passed is False and "MDD" in v.reason

    def test_fail_low_sharpe(self):
        v = self.screener.screen(_make_result(sharpe_ratio=0.2))
        assert v.passed is False and "Sharpe" in v.reason

    def test_fail_negative_expectancy(self):
        v = self.screener.screen(_make_result(expectancy=-5.0))
        assert v.passed is False and "기대값" in v.reason

    def test_bulk_screen(self):
        passed, failed = self.screener.bulk_screen([
            _make_result(strategy_name="Good"), _make_result(strategy_name="Bad", total_trades=5)])
        assert len(passed) == 1 and len(failed) == 1 and passed[0].strategy_name == "Good"

    def test_custom_criteria(self):
        assert Screener(ScreeningCriteria(min_sharpe=2.0)).screen(_make_result(sharpe_ratio=1.5)).passed is False
