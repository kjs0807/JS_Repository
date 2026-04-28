"""exit_round_grid in registry_builder (round 3, TP-fraction schema)."""
from src.strategies.registry_builder import STRATEGY_CONFIGS


EXPECTED_CELL_IDS = {
    "F0", "TF_default", "TF_wide", "TF_early", "TF_late",
    "TF_immediate", "TR_default", "TR_immediate",
}


def test_bbkc_has_exit_round_grid():
    cfg = STRATEGY_CONFIGS["BBKCSqueeze"]
    assert "exit_round_grid" in cfg


def test_exit_round_grid_has_8_cells():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    assert len(list(grid)) == 8
    expected_keys = {
        "cell_id", "exit_mode", "trail_be_at_tp_frac", "trail_start_at_tp_frac",
        "trail_distance_tp_frac", "drop_tp", "time_stop_bars",
    }
    for c in grid:
        assert expected_keys.issubset(c.keys())


def test_exit_round_grid_cell_ids():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    actual = {c["cell_id"] for c in grid}
    assert actual == EXPECTED_CELL_IDS


def test_baseline_cell_F0():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    f0 = next(c for c in grid if c["cell_id"] == "F0")
    assert f0["exit_mode"] == "fixed"
    assert f0["drop_tp"] is False
    assert f0["time_stop_bars"] == 0


def test_TF_default_has_TP_fraction_defaults():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cell = next(c for c in grid if c["cell_id"] == "TF_default")
    assert cell["exit_mode"] == "be_trail"
    assert cell["trail_be_at_tp_frac"] == 0.50
    assert cell["trail_start_at_tp_frac"] == 0.80
    assert cell["trail_distance_tp_frac"] == 0.30
    assert cell["drop_tp"] is False


def test_TR_cells_have_drop_tp_true():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    tr_cells = [c for c in grid if c["cell_id"].startswith("TR_")]
    assert len(tr_cells) == 2
    for c in tr_cells:
        assert c["drop_tp"] is True


def test_immediate_cells_use_0p49_0p50():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    for cell_id in ("TF_immediate", "TR_immediate"):
        c = next(g for g in grid if g["cell_id"] == cell_id)
        assert c["trail_be_at_tp_frac"] == 0.49
        assert c["trail_start_at_tp_frac"] == 0.50
