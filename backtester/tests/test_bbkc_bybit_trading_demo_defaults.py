"""Bybit_Trading demo BBKC parity defaults.

These tests pin the current ``Crypto/Bybit_Trading/config.yaml`` demo settings
that the compatibility strategy should use when created from the registry.
"""

from __future__ import annotations

from decimal import Decimal

from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.registry import build_strategy


def test_bbkc_legacy_compat_defaults_match_bybit_trading_demo_exit() -> None:
    strat = BBKCLegacyCompatStrategy()

    assert strat.leverage == Decimal("3")
    assert strat.margin_pct == Decimal("0.05")
    assert strat.tp_pct == Decimal("0.06")
    assert strat.sl_pct == Decimal("0.07")
    assert strat.rsi_filter == 70.0
    assert strat.exit_mode == "be_trail"
    assert strat.trail_be_at_tp_frac == Decimal("0.25")
    assert strat.trail_start_at_tp_frac == Decimal("0.60")
    assert strat.trail_distance_tp_frac == Decimal("0.3")
    assert strat.drop_tp is False
    assert strat.time_stop_bars is None
    assert strat.allow_short is True


def test_bbkc_legacy_compat_treats_time_stop_zero_as_disabled() -> None:
    strat = BBKCLegacyCompatStrategy(time_stop_bars=0)
    bracket = strat._build_bracket(Decimal("100"), "buy")

    assert strat.time_stop_bars is None
    assert bracket is not None
    assert bracket.time_stop_bars is None


def test_bbkc_legacy_compat_registry_builds_demo_defaults() -> None:
    strat = build_strategy("bbkc_legacy_compat", {})

    assert isinstance(strat, BBKCLegacyCompatStrategy)
    assert strat.exit_mode == "be_trail"
    assert strat.allow_short is True
