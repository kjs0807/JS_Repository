"""BacktestBroker max_favorable tracking tests."""
import pytest
from src.core.types import Bar
from src.core.config import BacktestConfig, RiskConfig
from src.execution.backtest_broker import BacktestBroker, TradeRecord


@pytest.fixture
def broker():
    cfg = BacktestConfig(initial_capital=10000.0, taker_fee_pct=0.0,
                         maker_fee_pct=0.0, slippage_pct=0.0)
    return BacktestBroker(cfg, RiskConfig())


def _bar(symbol: str, ts: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(symbol, ts, "1h", o, h, l, c, 1000.0)


def test_traderecord_has_max_favorable_field():
    tr = TradeRecord(
        symbol="BTCUSDT", strategy_name="X", side="LONG",
        entry_time=0, exit_time=1, entry_price=100.0, exit_price=110.0,
        qty=1.0, pnl=10.0, fee=0.0, exit_reason="TP", source="STRATEGY",
    )
    assert hasattr(tr, "max_favorable")
    assert tr.max_favorable == 0.0


def test_long_max_favorable_uses_bar_high(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.buy("BTCUSDT", qty=0.1, stop_loss=90.0, take_profit=120.0)
    bar1 = _bar("BTCUSDT", 2, 100, 105, 99, 102)   # fill at open=100
    broker.process_bar(bar1)
    pos = broker.get_position("BTCUSDT")
    assert pos is not None
    # high=105, entry=100 → max_favorable = 5.0
    assert pos.max_favorable == pytest.approx(5.0, rel=1e-6)

    bar2 = _bar("BTCUSDT", 3, 102, 108, 100, 107)
    broker.process_bar(bar2)
    pos = broker.get_position("BTCUSDT")
    assert pos.max_favorable == pytest.approx(8.0, rel=1e-6)  # 108 - 100 = 8


def test_short_max_favorable_uses_bar_low(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.sell("BTCUSDT", qty=0.1, stop_loss=110.0, take_profit=90.0)
    bar1 = _bar("BTCUSDT", 2, 100, 101, 95, 96)   # fill at open=100
    broker.process_bar(bar1)
    pos = broker.get_position("BTCUSDT")
    # entry=100, low=95 → max_favorable = 5.0
    assert pos.max_favorable == pytest.approx(5.0, rel=1e-6)


def test_max_favorable_passed_to_trade_record_on_exit(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.buy("BTCUSDT", qty=0.1, stop_loss=90.0, take_profit=110.0)
    bar1 = _bar("BTCUSDT", 2, 100, 108, 99, 105)
    broker.process_bar(bar1)
    bar2 = _bar("BTCUSDT", 3, 105, 112, 104, 111)  # TP=110 hits → exit
    broker.process_bar(bar2)
    trades = broker.get_trades()
    assert len(trades) == 1
    # max_favorable observed: bar1 high=108 (entry 100 → +8), bar2 high=112 (+12)
    assert trades[0].max_favorable == pytest.approx(12.0, rel=1e-6)
