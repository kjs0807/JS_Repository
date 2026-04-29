"""exit_round_grid in registry_builder (round 4, fine sweep schema)."""
from src.strategies.registry_builder import STRATEGY_CONFIGS


BE_VALUES = (0.25, 0.30, 0.35)
START_VALUES = (0.50, 0.60, 0.70)
DIST_VALUES = (0.20, 0.30, 0.40)


def _fine_cell_id(be: float, start: float, dist: float) -> str:
    return f"be{int(round(be * 100)):02d}_st{int(round(start * 100)):02d}_di{int(round(dist * 100)):02d}"


def test_bbkc_has_exit_round_grid():
    cfg = STRATEGY_CONFIGS["BBKCSqueeze"]
    assert "exit_round_grid" in cfg


def test_exit_round_grid_has_28_cells():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    assert len(list(grid)) == 28


def test_exit_round_grid_includes_F0():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    f0 = next((c for c in grid if c["cell_id"] == "F0"), None)
    assert f0 is not None
    assert f0["exit_mode"] == "fixed"
    assert f0["drop_tp"] is False
    assert f0["time_stop_bars"] == 0


def test_exit_round_grid_27_fine_cells_full_grid():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    fine = [c for c in grid if c["cell_id"] != "F0"]
    assert len(fine) == 27
    expected_ids = {
        _fine_cell_id(be, st, di)
        for be in BE_VALUES for st in START_VALUES for di in DIST_VALUES
    }
    actual_ids = {c["cell_id"] for c in fine}
    assert actual_ids == expected_ids


def test_fine_cells_use_be_trail_and_drop_tp_false():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    for c in grid:
        if c["cell_id"] == "F0":
            continue
        assert c["exit_mode"] == "be_trail"
        assert c["drop_tp"] is False
        assert c["time_stop_bars"] == 0


def test_fine_cells_satisfy_invariant():
    """Every fine cell must have 0 < be < start < 1.0 and dist > 0."""
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    for c in grid:
        if c["cell_id"] == "F0":
            continue
        be = c["trail_be_at_tp_frac"]
        st = c["trail_start_at_tp_frac"]
        di = c["trail_distance_tp_frac"]
        assert 0 < be < st < 1.0, f"{c['cell_id']}: be={be}, st={st}"
        assert di > 0, f"{c['cell_id']}: di={di}"


def test_TF_early_reproduce_cell_present():
    """Round 3 TF_early params (0.30/0.60/0.30) must appear as be30_st60_di30."""
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cell = next((c for c in grid if c["cell_id"] == "be30_st60_di30"), None)
    assert cell is not None
    assert cell["trail_be_at_tp_frac"] == 0.30
    assert cell["trail_start_at_tp_frac"] == 0.60
    assert cell["trail_distance_tp_frac"] == 0.30


def test_no_round3_archetype_cell_ids():
    """Round 3 archetype names must not appear in the round 4 grid."""
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    ids = {c["cell_id"] for c in grid}
    forbidden = {"TF_default", "TF_wide", "TF_early", "TF_late",
                 "TF_immediate", "TR_default", "TR_immediate"}
    assert ids.isdisjoint(forbidden)
