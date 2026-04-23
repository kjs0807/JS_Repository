"""execution/risk.py 단위 테스트."""
import pytest
from src.execution.risk import RiskManager, RiskDecision
from src.execution.broker import Order, Portfolio, Position
from src.core.config import RiskConfig

def _make_order(source="STRATEGY", qty=0.01, symbol="BTCUSDT"):
    return Order(order_id="test", symbol=symbol, side="BUY", qty=qty,
                order_type="MARKET", stop_loss=60000.0, take_profit=70000.0,
                strategy_name="Test", source=source, reason="test", created_at=1700000000000)

def _make_portfolio(equity=50000.0, daily_pnl=0.0, positions=0):
    pos_list = [Position(f"SYM{i}USDT", "LONG", 0.01, 100.0, 1700000000000,
                         90.0, 110.0, 0.0, "Test") for i in range(positions)]
    return Portfolio(initial_capital=50000.0, equity=equity, available_margin=equity*0.8,
                    used_margin=equity*0.2, realized_pnl=equity-50000.0,
                    daily_pnl=daily_pnl, positions=pos_list)

class TestRiskManager:
    def setup_method(self):
        self.config = RiskConfig(max_position_pct=0.05, max_concurrent=3,
                                daily_loss_limit_pct=0.05, max_drawdown_pct=0.15)
        self.risk = RiskManager(self.config, initial_capital=50000.0)

    def test_allow_normal_order(self):
        decision = self.risk.check_order(_make_order(), _make_portfolio())
        assert decision.action == "ALLOW"

    def test_reject_max_concurrent(self):
        decision = self.risk.check_order(_make_order(), _make_portfolio(positions=3))
        assert decision.action == "REJECT"
        assert "동시 포지션" in decision.reason

    def test_manual_order_warns_max_concurrent(self):
        decision = self.risk.check_order(_make_order(source="MANUAL"), _make_portfolio(positions=3))
        assert decision.action == "ALLOW"

    def test_reject_daily_loss_limit(self):
        decision = self.risk.check_order(_make_order(), _make_portfolio(daily_pnl=-2600.0))
        assert decision.action == "REJECT"
        assert "일일 손실" in decision.reason

    def test_manual_also_rejected_on_daily_loss(self):
        decision = self.risk.check_order(_make_order(source="MANUAL"), _make_portfolio(daily_pnl=-2600.0))
        assert decision.action == "REJECT"

    def test_reject_mdd_limit(self):
        self.risk.update_equity(50000.0)
        self.risk.update_equity(42000.0)
        decision = self.risk.check_order(_make_order(), _make_portfolio(equity=42000.0))
        assert decision.action == "REJECT"
        assert "MDD" in decision.reason

    def test_manual_also_rejected_on_mdd(self):
        self.risk.update_equity(50000.0)
        self.risk.update_equity(42000.0)
        decision = self.risk.check_order(_make_order(source="MANUAL"), _make_portfolio(equity=42000.0))
        assert decision.action == "REJECT"

    def test_record_trade_updates_state(self):
        self.risk.record_trade(pnl=-500.0, is_win=False)
        assert self.risk.daily_pnl == -500.0
        self.risk.record_trade(pnl=200.0, is_win=True)
        assert self.risk.daily_pnl == -300.0

    def test_daily_reset(self):
        self.risk.record_trade(pnl=-100.0, is_win=False)
        self.risk.reset_daily()
        assert self.risk.daily_pnl == 0.0

    def test_drawdown_tracking(self):
        self.risk.update_equity(50000.0)
        self.risk.update_equity(48000.0)
        assert abs(self.risk.drawdown_pct - 0.04) < 0.001
        self.risk.update_equity(51000.0)
        assert self.risk.drawdown_pct == 0.0
        self.risk.update_equity(46000.0)
        expected_dd = (51000.0 - 46000.0) / 51000.0
        assert abs(self.risk.drawdown_pct - expected_dd) < 0.001

class TestRiskDecision:
    def test_allow(self):
        d = RiskDecision("ALLOW")
        assert d.action == "ALLOW"
        assert d.reason == ""

    def test_reject_with_reason(self):
        d = RiskDecision("REJECT", "MDD 한도 초과")
        assert d.reason == "MDD 한도 초과"
