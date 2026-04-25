"""BBKC trailing gate test (round 2 §4.7).

Global ATR trailing in _check_open_positions_for_symbol must skip
BBKCSqueeze positions so that live `fixed` mode matches src evaluation
`fixed` mode. Other strategies must retain the existing trailing.
"""
from __future__ import annotations
from unittest.mock import MagicMock


def _make_engine_with_pos(strategy: str):
    """Build a minimal TradingEngine + one LONG position for the given strategy."""
    from paper_engine.trading_engine import TradingEngine, _PositionInfo

    eng = TradingEngine.__new__(TradingEngine)
    eng.risk_manager = MagicMock()
    eng.risk_manager.params.trailing_activation_atr = 2.5
    eng.risk_manager.params.trailing_distance_atr = 1.5
    # If trailing fires, return a new stop above the original — so the test can detect
    eng.risk_manager.update_trailing_stop = MagicMock(return_value=99.0)

    pos = _PositionInfo(
        trade_id=1, strategy=strategy, symbol="BTCUSDT", direction="LONG",
        entry_price=100.0, stop_loss=95.0, take_profit=120.0,
        quantity=1.0, leverage=3, margin_used=33.0,
        atr=2.0, entry_time="t0",
    )
    eng._positions = {(strategy, "BTCUSDT"): pos}
    return eng, pos


def test_bbkc_position_skips_global_atr_trailing():
    eng, pos = _make_engine_with_pos("BBKCSqueeze")
    # current_price = 110: profit = +10, activation = 2.5 × 2 = 5 → would normally activate
    eng._check_open_positions_for_symbol("BTCUSDT", current_price=110.0)
    eng.risk_manager.update_trailing_stop.assert_not_called()
    assert pos.stop_loss == 95.0   # unchanged


def test_other_strategy_keeps_global_atr_trailing():
    eng, pos = _make_engine_with_pos("RSIMACDStrategy")
    eng._check_open_positions_for_symbol("BTCUSDT", current_price=110.0)
    eng.risk_manager.update_trailing_stop.assert_called_once()
    assert pos.stop_loss == 99.0   # updated by mocked update_trailing_stop


def test_bbkc_short_position_also_skips_trailing():
    """SHORT BBKC also bypasses; symmetric check."""
    from paper_engine.trading_engine import TradingEngine, _PositionInfo

    eng = TradingEngine.__new__(TradingEngine)
    eng.risk_manager = MagicMock()
    eng.risk_manager.params.trailing_activation_atr = 2.5
    eng.risk_manager.update_trailing_stop = MagicMock(return_value=101.0)

    pos = _PositionInfo(
        trade_id=1, strategy="BBKCSqueeze", symbol="BTCUSDT", direction="SHORT",
        entry_price=100.0, stop_loss=105.0, take_profit=80.0,
        quantity=1.0, leverage=3, margin_used=33.0,
        atr=2.0, entry_time="t0",
    )
    eng._positions = {("BBKCSqueeze", "BTCUSDT"): pos}

    # current_price = 90: profit (SHORT) = 100-90 = +10, activation = 5 → would activate
    eng._check_open_positions_for_symbol("BTCUSDT", current_price=90.0)
    eng.risk_manager.update_trailing_stop.assert_not_called()
    assert pos.stop_loss == 105.0
