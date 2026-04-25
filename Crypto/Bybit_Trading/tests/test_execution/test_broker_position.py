"""Position dataclass max_favorable field tests."""
from src.execution.broker import Position


def test_position_has_max_favorable_field_with_default_zero():
    pos = Position(
        symbol="BTCUSDT", side="LONG", qty=0.1, entry_price=100.0,
        entry_time=1700000000000, stop_loss=95.0, take_profit=110.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    assert hasattr(pos, "max_favorable")
    assert pos.max_favorable == 0.0


def test_position_max_favorable_is_settable():
    pos = Position(
        symbol="BTCUSDT", side="LONG", qty=0.1, entry_price=100.0,
        entry_time=1700000000000, stop_loss=95.0, take_profit=110.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    pos.max_favorable = 5.5
    assert pos.max_favorable == 5.5
