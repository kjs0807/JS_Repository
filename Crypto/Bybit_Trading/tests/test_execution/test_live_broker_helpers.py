"""LiveBroker helper 메서드 단위 테스트 (round 5)."""
from unittest.mock import MagicMock

from src.execution.live_broker import LiveBroker


def _make_broker() -> LiveBroker:
    """Bypass __init__ to avoid wallet sync."""
    broker = LiveBroker.__new__(LiveBroker)
    broker._rest = MagicMock()
    broker._alert = None
    broker._risk = MagicMock()
    broker._leverage = 3
    broker._initial_capital = 50000.0
    broker._positions = {}
    broker._equity = 50000.0
    return broker


def test_position_idx_for_long_returns_1():
    broker = _make_broker()
    assert broker._position_idx_for_side("LONG") == 1


def test_position_idx_for_short_returns_2():
    broker = _make_broker()
    assert broker._position_idx_for_side("SHORT") == 2
