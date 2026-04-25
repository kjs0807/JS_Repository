"""BBKCSqueeze exit_mode extension tests."""
import pytest
from src.strategies.bbkc_squeeze import BBKCSqueeze


def test_default_params_preserve_fixed_mode():
    s = BBKCSqueeze()
    p = s.get_params()
    assert p["exit_mode"] == "fixed"
    assert p["trail_be_r"] == 1.0
    assert p["trail_start_r"] == 2.0
    assert p["trail_distance_r"] == 0.5
    assert p["time_stop_bars"] == 0


def test_set_params_updates_exit_mode():
    s = BBKCSqueeze()
    s.set_params({"exit_mode": "be_trail", "time_stop_bars": 48})
    assert s.exit_mode == "be_trail"
    assert s.time_stop_bars == 48


def test_invalid_exit_mode_rejected():
    with pytest.raises((ValueError, AssertionError)):
        BBKCSqueeze(exit_mode="bogus")
