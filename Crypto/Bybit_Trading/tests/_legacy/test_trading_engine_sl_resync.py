"""Legacy F2 SL/TP resync tests for TradingEngine helpers.

The actual production call site is _process_signal — this test focuses on
the two extracted helpers (_compute_desired_sl_tp + _resync_sl_tp) so we
can verify the math + the failure-handling contract without standing up
the entire engine harness.
"""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest


def _make_engine_with_strategy_params(
    bbkc_exit_mode: str = "fixed",
    bbkc_tp_pct: float = 0.06,
    bbkc_sl_pct: float = 0.07,
    rsimacd_tp_pct: float = 0.06,
    rsimacd_sl_pct: float = 0.05,
    leverage: int = 3,
):
    """Build a minimal TradingEngine instance (via __new__) with stubbed deps."""
    from paper_engine.trading_engine import TradingEngine
    eng = TradingEngine.__new__(TradingEngine)
    eng.leverage = leverage
    eng.rest_client = MagicMock()

    # strategy_params is a module-level singleton. Patch the bbkc/rsimacd attrs
    # on a stub that mimics it.
    sp = MagicMock()
    sp.bbkc_exit_mode = bbkc_exit_mode
    sp.bbkc_tp_pct = bbkc_tp_pct
    sp.bbkc_sl_pct = bbkc_sl_pct
    sp.rsimacd_tp_pct = rsimacd_tp_pct
    sp.rsimacd_sl_pct = rsimacd_sl_pct
    eng._strategy_params = sp
    return eng


# ── _compute_desired_sl_tp ─────────────────────────────────────────────────


def test_compute_desired_sl_tp_bbkc_long_uses_avg_price():
    eng = _make_engine_with_strategy_params()
    # avg_price = 99.5, sl_pct = 0.07, leverage = 3 → sl_dist = 0.0233
    sl, tp = eng._compute_desired_sl_tp("BBKCSqueeze", "LONG", 99.5)
    assert sl == pytest.approx(99.5 * (1 - 0.07 / 3), rel=1e-9)
    assert tp == pytest.approx(99.5 * (1 + 0.06 / 3), rel=1e-9)


def test_compute_desired_sl_tp_bbkc_short_uses_avg_price():
    eng = _make_engine_with_strategy_params()
    sl, tp = eng._compute_desired_sl_tp("BBKCSqueeze", "SHORT", 100.5)
    assert sl == pytest.approx(100.5 * (1 + 0.07 / 3), rel=1e-9)
    assert tp == pytest.approx(100.5 * (1 - 0.06 / 3), rel=1e-9)


def test_compute_desired_sl_tp_rsimacd_long():
    eng = _make_engine_with_strategy_params()
    sl, tp = eng._compute_desired_sl_tp("RSIMACDStrategy", "LONG", 100.0)
    assert sl == pytest.approx(100.0 * (1 - 0.05 / 3), rel=1e-9)
    assert tp == pytest.approx(100.0 * (1 + 0.06 / 3), rel=1e-9)


def test_compute_desired_sl_tp_unknown_strategy_returns_none():
    eng = _make_engine_with_strategy_params()
    sl, tp = eng._compute_desired_sl_tp("PairsTrading", "LONG", 100.0)
    assert sl is None
    assert tp is None


def test_compute_desired_sl_tp_bbkc_atr_mode_returns_none():
    """ATR mode uses ATR-based SL/TP set by strategy; F2 only handles 'fixed'."""
    eng = _make_engine_with_strategy_params(bbkc_exit_mode="atr")
    sl, tp = eng._compute_desired_sl_tp("BBKCSqueeze", "LONG", 99.5)
    assert sl is None
    assert tp is None


# ── _resync_sl_tp ──────────────────────────────────────────────────────────


def test_resync_sl_tp_calls_rest_client_with_correct_args():
    eng = _make_engine_with_strategy_params()
    eng.rest_client.set_trading_stop.return_value = {}
    failed = eng._resync_sl_tp("BTCUSDT", 99.0, 101.0, position_idx=1)
    assert failed is False
    eng.rest_client.set_trading_stop.assert_called_once()
    kwargs = eng.rest_client.set_trading_stop.call_args.kwargs
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["stop_loss"] == 99.0
    assert kwargs["take_profit"] == 101.0
    assert kwargs["position_idx"] == 1


def test_resync_sl_tp_returns_failed_on_api_error():
    eng = _make_engine_with_strategy_params()
    eng.rest_client.set_trading_stop.side_effect = RuntimeError("boom")
    failed = eng._resync_sl_tp("BTCUSDT", 99.0, 101.0, position_idx=1)
    assert failed is True
