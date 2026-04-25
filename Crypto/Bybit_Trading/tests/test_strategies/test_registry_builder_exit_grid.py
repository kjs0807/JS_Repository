"""exit_round_grid in registry_builder."""
from src.strategies.registry_builder import STRATEGY_CONFIGS


def test_bbkc_has_exit_round_grid():
    cfg = STRATEGY_CONFIGS["BBKCSqueeze"]
    assert "exit_round_grid" in cfg


def test_exit_round_grid_has_12_cells():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cells = list(grid)
    assert len(cells) == 12
    expected_keys = {"exit_mode", "trail_distance_r", "time_stop_bars", "cell_id"}
    for c in cells:
        assert expected_keys.issubset(c.keys())


def test_exit_round_grid_baseline_cell_F0():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    f0 = next(c for c in grid if c["cell_id"] == "F0")
    assert f0["exit_mode"] == "fixed"
    assert f0["time_stop_bars"] == 0


def test_exit_round_grid_be_trail_cells():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cells = [c for c in grid if c["exit_mode"] == "be_trail"]
    assert len(cells) == 8
    distances = sorted({c["trail_distance_r"] for c in cells})
    times = sorted({c["time_stop_bars"] for c in cells})
    assert distances == [0.5, 1.0]
    assert times == [0, 24, 48, 72]
