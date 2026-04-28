"""BBKCSqueeze exit_mode extension tests (round 3 — TP-fraction units)."""
import numpy as np
import pandas as pd
import pytest

from src.core.types import Bar, BarSeries
from src.execution.broker import Position
from src.strategies.bbkc_squeeze import BBKCSqueeze


def test_default_params_are_tp_fraction_units():
    s = BBKCSqueeze()
    p = s.get_params()
    assert p["exit_mode"] == "fixed"
    assert p["trail_be_at_tp_frac"] == 0.5
    assert p["trail_start_at_tp_frac"] == 0.8
    assert p["trail_distance_tp_frac"] == 0.3
    assert p["drop_tp"] is False
    assert p["time_stop_bars"] == 0
    assert "trail_be_r" not in p
    assert "trail_start_r" not in p
    assert "trail_distance_r" not in p


def test_set_params_updates_exit_mode():
    s = BBKCSqueeze()
    s.set_params({"exit_mode": "be_trail", "drop_tp": True, "time_stop_bars": 48})
    assert s.exit_mode == "be_trail"
    assert s.drop_tp is True
    assert s.time_stop_bars == 48


def test_invalid_exit_mode_rejected():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="bogus")


def test_invariant_rejects_be_geq_start():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.5,
                    trail_start_at_tp_frac=0.5)
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.7,
                    trail_start_at_tp_frac=0.5)


def test_invariant_rejects_out_of_unit_interval():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.0,
                    trail_start_at_tp_frac=0.8)
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_be_at_tp_frac=0.5,
                    trail_start_at_tp_frac=1.0)


def test_invariant_rejects_distance_zero_or_negative():
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.0)
    with pytest.raises(ValueError):
        BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=-0.1)


def test_invariant_accepts_immediate_cell():
    s = BBKCSqueeze(exit_mode="be_trail",
                    trail_be_at_tp_frac=0.49,
                    trail_start_at_tp_frac=0.50,
                    trail_distance_tp_frac=0.3)
    assert s.trail_be_at_tp_frac == 0.49
    assert s.trail_start_at_tp_frac == 0.50


def test_invariant_skipped_for_fixed_mode():
    s = BBKCSqueeze(exit_mode="fixed", trail_be_at_tp_frac=0.9,
                    trail_start_at_tp_frac=0.5)
    assert s.exit_mode == "fixed"
