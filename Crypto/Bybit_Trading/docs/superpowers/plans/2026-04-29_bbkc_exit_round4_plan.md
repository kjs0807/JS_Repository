# BBKC Exit Strategy Round 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `exit_round_grid` with a 28-cell fine sweep around ETH × TF_early, add per-cell integrated labeling on top of round 3's per-symbol verdicts, surface reproducibility sanity + per-symbol verdict heatmaps in the report, and run the 756-backtest sweep to determine whether TF_early is a robust region or cherry-pick.

**Architecture:** Drop-in extension of round 3 infrastructure. `BBKCSqueeze` and broker layer untouched (round 3 + set_params fix already in main). Registry replaces 8 archetypes with 28 systematic cells (`F0` + 27 `be{XX}_st{YY}_di{ZZ}`). `judge()`'s per-symbol logic unchanged; new `integrate_label()` consumes the per-symbol output to emit one of 6 cell-level labels (BASELINE / ROBUST_PROMOTE / ETH_ONLY_PROMOTE / ETH_PROMOTE_MIXED / DAMAGING / NO_SIGNAL). `build_report()` gains 4 new sections: Reproducibility Sanity, Per-Cell Integrated Labels, Label Distribution, and 9 Per-Symbol × Distance Heatmaps.

**Tech Stack:** Python 3.11+, pytest, existing `BacktestEngine` + `BacktestBroker` + `HoldoutSpec` pipeline.

**Spec:** `Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round4_design.md` (commit `022d8b8`)

**Working directory for all bash commands:** `C:/Users/ceoji/Desktop/python_ibks/Crypto/Bybit_Trading/`

---

## File Structure

### Modified Files

| Path | Change |
|---|---|
| `src/strategies/registry_builder.py` | Replace `exit_round_grid` 8 cells → 28 cells (F0 + 3×3×3 fine grid) |
| `scripts/bbkc_exit_eval.py` | Add `integrate_label()`; add `EXPECTED_REPRODUCE` constants; augment `build_report` with 4 new sections (Reproducibility Sanity, Per-Cell Integrated Labels, Label Distribution, 9 Heatmaps); update docstring + report title to "Round 4" |
| `tests/test_strategies/test_registry_builder_exit_grid.py` | Update assertions for 28-cell schema and `be{XX}_st{YY}_di{ZZ}` naming |
| `tests/test_scripts/test_bbkc_exit_eval_judge.py` | Add 8 `integrate_label()` test cases (ETH warning case included) |

### New Files

None (round 3 infrastructure covers everything).

---

## Phase A — Registry Replacement

### Task 1: Replace `exit_round_grid` with 28 cells

**Files:**
- Modify: `src/strategies/registry_builder.py` (the `exit_round_grid` block inside `STRATEGY_CONFIGS["BBKCSqueeze"]`)
- Modify: `tests/test_strategies/test_registry_builder_exit_grid.py` (replace round 3 assertions)

- [ ] **Step 1: Rewrite the registry test file**

Open `tests/test_strategies/test_registry_builder_exit_grid.py` and replace its content with:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_strategies/test_registry_builder_exit_grid.py -v
```
Expected: most FAIL — current grid is 8 round-3 archetypes.

- [ ] **Step 3: Replace the grid in `registry_builder.py`**

Find `STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]` and replace its existing 8-cell list with:

```python
        # 2026-04-29 round 4: 3×3×3 fine sweep around ETH × TF_early (round 3 STRONG_PROMOTE).
        # Replaces round 3's 8 archetypes — round 3 archetypes preserved only in
        # design doc §15 + result files (logs/research/.../2026-04-28_T2104/).
        # Policy: this key always points to the LATEST round's fine sweep matrix.
        "exit_round_grid": [
            # Baseline
            {"cell_id": "F0",
             "exit_mode": "fixed",
             "trail_be_at_tp_frac": None, "trail_start_at_tp_frac": None,
             "trail_distance_tp_frac": None,
             "drop_tp": False, "time_stop_bars": 0},
        ] + [
            {
                "cell_id": f"be{int(round(be * 100)):02d}_st{int(round(st * 100)):02d}_di{int(round(di * 100)):02d}",
                "exit_mode": "be_trail",
                "trail_be_at_tp_frac": be,
                "trail_start_at_tp_frac": st,
                "trail_distance_tp_frac": di,
                "drop_tp": False,
                "time_stop_bars": 0,
            }
            for be in (0.25, 0.30, 0.35)
            for st in (0.50, 0.60, 0.70)
            for di in (0.20, 0.30, 0.40)
        ],
```

- [ ] **Step 4: Run tests to verify pass**

```
python -m pytest tests/test_strategies/test_registry_builder_exit_grid.py -v
```
Expected: ALL pass (8 tests).

- [ ] **Step 5: Wider regression**

```
python -m pytest tests/test_strategies/ tests/test_scripts/ -q --no-header
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add src/strategies/registry_builder.py tests/test_strategies/test_registry_builder_exit_grid.py
git commit -m "feat(registry): replace exit_round_grid with 28-cell round 4 fine sweep"
```

---

## Phase B — Script Updates

### Task 2: Add `integrate_label()` + 8 test cases

**Files:**
- Modify: `scripts/bbkc_exit_eval.py` (add `integrate_label()` standalone function and call it from `judge()`)
- Modify: `tests/test_scripts/test_bbkc_exit_eval_judge.py` (append 8 new test cases)

- [ ] **Step 1: Append integrate_label tests to test file**

Append to `tests/test_scripts/test_bbkc_exit_eval_judge.py`:

```python
# ── integrate_label: per-cell roll-up tests (round 4 §6.2) ────────────────


from scripts.bbkc_exit_eval import integrate_label


def _per_sym(verdict: str, warning: bool = False) -> dict:
    return {"verdict": verdict, "warning": warning}


def test_integrate_label_F0_returns_BASELINE():
    by_sym = {
        "ETHUSDT": _per_sym("BASELINE"),
        "BTCUSDT": _per_sym("BASELINE"),
        "AVAXUSDT": _per_sym("BASELINE"),
    }
    assert integrate_label("F0", by_sym) == "BASELINE"


def test_integrate_label_eth_warning_routes_to_MIXED():
    """ETH PROMOTE with warning=True must NOT reach ROBUST_PROMOTE."""
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE", warning=True),
        "BTCUSDT": _per_sym("NEUTRAL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_PROMOTE_MIXED"


def test_integrate_label_eth_only_promote_when_one_other_KILL():
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE"),
        "BTCUSDT": _per_sym("KILL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_ONLY_PROMOTE"


def test_integrate_label_eth_only_promote_when_both_others_KILL():
    """Both BTC and AVAX KILL → still ETH_ONLY_PROMOTE (not DAMAGING since ETH gains)."""
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE"),
        "BTCUSDT": _per_sym("KILL"),
        "AVAXUSDT": _per_sym("KILL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_ONLY_PROMOTE"


def test_integrate_label_eth_promote_mixed_when_other_warning_no_kill():
    by_sym = {
        "ETHUSDT": _per_sym("PROMOTE"),
        "BTCUSDT": _per_sym("NEUTRAL", warning=True),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ETH_PROMOTE_MIXED"


def test_integrate_label_robust_promote_when_all_safe():
    by_sym = {
        "ETHUSDT": _per_sym("STRONG_PROMOTE"),
        "BTCUSDT": _per_sym("NEUTRAL"),
        "AVAXUSDT": _per_sym("BASELINE"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "ROBUST_PROMOTE"


def test_integrate_label_damaging_when_eth_no_promote_other_KILL():
    by_sym = {
        "ETHUSDT": _per_sym("NEUTRAL"),
        "BTCUSDT": _per_sym("KILL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "DAMAGING"


def test_integrate_label_no_signal_when_eth_no_promote_no_other_KILL():
    by_sym = {
        "ETHUSDT": _per_sym("NEUTRAL"),
        "BTCUSDT": _per_sym("NEUTRAL"),
        "AVAXUSDT": _per_sym("NEUTRAL"),
    }
    assert integrate_label("be30_st60_di30", by_sym) == "NO_SIGNAL"
```

- [ ] **Step 2: Run new tests to verify failure**

```
python -m pytest tests/test_scripts/test_bbkc_exit_eval_judge.py -v -k integrate_label
```
Expected: 8 FAIL — `integrate_label` does not exist yet.

- [ ] **Step 3: Add `integrate_label()` to `scripts/bbkc_exit_eval.py`**

Insert this function immediately above the existing `judge()` function:

```python
def integrate_label(cell_id: str, by_sym: Dict[str, Dict[str, Any]]) -> str:
    """Per-cell integrated label from per-symbol verdicts (round 4 §6.2).

    Priority order (first match wins):
      1. F0                                              → BASELINE
      2. ETH promote AND ETH warning=True                → ETH_PROMOTE_MIXED
      3. ETH promote AND any (BTC/AVAX) KILL             → ETH_ONLY_PROMOTE
      4. ETH promote AND any (BTC/AVAX) UNKNOWN/warning  → ETH_PROMOTE_MIXED
      5. ETH promote AND no KILL/UNKNOWN/warning anywhere → ROBUST_PROMOTE
      6. ETH not promote AND any (BTC/AVAX) KILL          → DAMAGING
      7. otherwise                                        → NO_SIGNAL

    "promote" means verdict in {"STRONG_PROMOTE", "PROMOTE"}.
    """
    if cell_id == "F0":
        return "BASELINE"

    eth = by_sym.get("ETHUSDT", {})
    others = [by_sym.get("BTCUSDT", {}), by_sym.get("AVAXUSDT", {})]

    eth_promote = eth.get("verdict") in ("STRONG_PROMOTE", "PROMOTE")
    eth_warning = eth.get("warning") is True
    has_kill = any(o.get("verdict") == "KILL" for o in others)
    has_unknown_or_warning = any(
        o.get("verdict") == "UNKNOWN" or o.get("warning") is True
        for o in others
    )

    if eth_promote:
        if eth_warning:
            return "ETH_PROMOTE_MIXED"
        if has_kill:
            return "ETH_ONLY_PROMOTE"
        if has_unknown_or_warning:
            return "ETH_PROMOTE_MIXED"
        return "ROBUST_PROMOTE"
    else:
        if has_kill:
            return "DAMAGING"
        return "NO_SIGNAL"
```

- [ ] **Step 4: Wire `integrate_label` into `judge()` so each per-cell entry carries it**

Find the existing `judge()` function. After the per-symbol verdict loop builds `out`, add a final pass that attaches the cell-level label as a sibling key. Replace `judge()`'s final `return out` with:

```python
    # Round 4: attach per-cell integrated label.
    # The integrated label is cell-level (not per-symbol) so we store it under a
    # synthetic "_cell" key inside each cell's dict in `out`. Existing per-symbol
    # entries (BTCUSDT/ETHUSDT/AVAXUSDT) remain untouched for backward compat.
    for cell_id, by_sym in out.items():
        # by_sym already contains the symbol entries we just populated above.
        cell_label = integrate_label(cell_id, by_sym)
        by_sym["_cell"] = {"integrated_label": cell_label}
    return out
```

If the existing `judge()` body uses a different variable name than `out` for the result dict, adjust accordingly.

- [ ] **Step 5: Run tests to verify pass**

```
python -m pytest tests/test_scripts/test_bbkc_exit_eval_judge.py -v
```
Expected: ALL pass (8 existing per-symbol delta tests + 8 new integrate_label tests = 16 total).

- [ ] **Step 6: Commit**

```
git add scripts/bbkc_exit_eval.py tests/test_scripts/test_bbkc_exit_eval_judge.py
git commit -m "feat(scripts): integrate_label() per-cell roll-up (round 4 §6.2) + 8 tests"
```

---

### Task 3: Reproducibility sanity constants + report block

**Files:**
- Modify: `scripts/bbkc_exit_eval.py` (add `EXPECTED_REPRODUCE` constants, helper `format_reproducibility_block`, integrate into `build_report`)

- [ ] **Step 1: Add reproducibility constants near the top of `scripts/bbkc_exit_eval.py`**

Locate the section just below the existing `SYMBOLS = [...]`, `DATA_START`, `DATA_END`, `OUTPUT_BASE` constants. Append:

```python
# ── Round 4 §7: reproducibility sanity ─────────────────────────────────────
# `be30_st60_di30 × ETHUSDT` re-runs round 3's TF_early × ETH (same params,
# same code path). Expected exact floats from
# logs/research/bbkc_squeeze/exit_round/2026-04-28_T2104/summary.json.
REPRODUCE_CELL_ID = "be30_st60_di30"
REPRODUCE_SYMBOL = "ETHUSDT"
EXPECTED_REPRODUCE = {
    "wf_oos_positive": 6,
    "mean_r_per_trade": 0.0635821965450038,
    "trade_count": 154,
    "max_dd": 0.11123736375303807,
    "mean_oos_pnl": 325.6180389395652,
}
REPRODUCE_TOLERANCE = {
    "wf_oos_positive_exact": 6,
    "mean_r_per_trade_abs": 0.005,
    "trade_count_abs": 2,
}
```

- [ ] **Step 2: Add helper `format_reproducibility_block`**

Insert before `build_report`:

```python
def format_reproducibility_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[str]:
    """Return Markdown lines for the Reproducibility Sanity section.

    Compares summary_judged[REPRODUCE_CELL_ID][REPRODUCE_SYMBOL] against
    EXPECTED_REPRODUCE within REPRODUCE_TOLERANCE. Always emits a block
    (even if cell missing — uses fallback message).
    """
    lines: List[str] = [
        f"## Reproducibility Sanity ({REPRODUCE_CELL_ID} × {REPRODUCE_SYMBOL} vs Round 3 TF_early)",
        "",
    ]
    cell = summary_judged.get(REPRODUCE_CELL_ID, {})
    actual = cell.get(REPRODUCE_SYMBOL)
    if not actual:
        lines.append(
            f"❗ MATCH SKIPPED — `{REPRODUCE_CELL_ID} × {REPRODUCE_SYMBOL}` "
            f"not in summary (partial run?)."
        )
        lines.append("")
        return lines

    diffs: List[str] = []
    if actual["wf_oos_positive"] != REPRODUCE_TOLERANCE["wf_oos_positive_exact"]:
        diffs.append(
            f"wf {actual['wf_oos_positive']} != {REPRODUCE_TOLERANCE['wf_oos_positive_exact']}"
        )
    if abs(actual["mean_r_per_trade"] - EXPECTED_REPRODUCE["mean_r_per_trade"]) > REPRODUCE_TOLERANCE["mean_r_per_trade_abs"]:
        diffs.append(
            f"R {actual['mean_r_per_trade']:+.6f} vs "
            f"{EXPECTED_REPRODUCE['mean_r_per_trade']:+.6f} "
            f"(tol ±{REPRODUCE_TOLERANCE['mean_r_per_trade_abs']})"
        )
    if abs(actual["trade_count"] - EXPECTED_REPRODUCE["trade_count"]) > REPRODUCE_TOLERANCE["trade_count_abs"]:
        diffs.append(
            f"n {actual['trade_count']} vs {EXPECTED_REPRODUCE['trade_count']} "
            f"(tol ±{REPRODUCE_TOLERANCE['trade_count_abs']})"
        )

    expected_str = (
        f"  Round 3 TF_early ETH: wf {EXPECTED_REPRODUCE['wf_oos_positive']}/9, "
        f"R/trade {EXPECTED_REPRODUCE['mean_r_per_trade']:+.4f}, "
        f"n={EXPECTED_REPRODUCE['trade_count']}"
    )
    actual_str = (
        f"  Round 4 reproduce:    wf {actual['wf_oos_positive']}/9, "
        f"R/trade {actual['mean_r_per_trade']:+.4f}, "
        f"n={actual['trade_count']}"
    )
    lines.append(expected_str)
    lines.append(actual_str)
    if not diffs:
        lines.append("  Match: ✓")
    else:
        lines.append("  Match: ✗")
        lines.append("  Diffs: " + "; ".join(diffs))
        lines.append("  ⚠️  REPRODUCIBILITY MISMATCH — investigate before trusting other cells.")
    lines.append("")
    return lines
```

- [ ] **Step 3: Wire into `build_report`**

Find `build_report()`. After the existing report header (`# BBKC Exit Round 3 — Sweep Report` line + `Generated:` line + blank line), insert reproducibility block right before the existing per-symbol verdicts section. Update the function so its `lines` accumulation looks like:

```python
    lines: List[str] = [
        "# BBKC Exit Round 3 — Sweep Report",   # (Task 5 will rename to Round 4)
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    lines.extend(format_reproducibility_block(summary_judged))
    # ... existing per-symbol verdict tables follow ...
```

(Exact existing structure of `build_report` may differ slightly — preserve it; just inject `format_reproducibility_block` after the header.)

- [ ] **Step 4: Smoke that the block renders**

```
python -m scripts.bbkc_exit_eval --smoke
head -15 logs/research/bbkc_squeeze/exit_round/latest/report.md
```
Expected: report contains the Reproducibility Sanity section. Since `--smoke` runs only F0 × BTC, the section emits the "MATCH SKIPPED" fallback (cell `be30_st60_di30` not in summary).

- [ ] **Step 5: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): reproducibility sanity block (round 4 §7) with explicit float tolerance"
```

---

### Task 4: Per-cell integrated labels table + label distribution + 9 heatmaps

**Files:**
- Modify: `scripts/bbkc_exit_eval.py` (extend `build_report` with three more sections)

- [ ] **Step 1: Add `format_integrated_labels_block` helper**

Insert above `build_report`:

```python
# Round 4 §8.2: per-cell integrated labels table.
def format_integrated_labels_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
    grid: List[Dict[str, Any]],
) -> List[str]:
    """Markdown table of (cell, label, ETH/BTC/AVAX verdict). Cell order = grid order."""
    lines: List[str] = [
        "## Per-Cell Integrated Labels",
        "",
        "| cell | label | ETH | BTC | AVAX |",
        "|---|---|---|---|---|",
    ]
    for c in grid:
        cell_id = c["cell_id"]
        cell_entry = summary_judged.get(cell_id, {})
        label = cell_entry.get("_cell", {}).get("integrated_label", "?")
        eth_v = cell_entry.get("ETHUSDT", {}).get("verdict", "-")
        btc_v = cell_entry.get("BTCUSDT", {}).get("verdict", "-")
        avx_v = cell_entry.get("AVAXUSDT", {}).get("verdict", "-")
        eth_w = " *" if cell_entry.get("ETHUSDT", {}).get("warning") else ""
        btc_w = " *" if cell_entry.get("BTCUSDT", {}).get("warning") else ""
        avx_w = " *" if cell_entry.get("AVAXUSDT", {}).get("warning") else ""
        lines.append(
            f"| {cell_id} | {label} | {eth_v}{eth_w} | {btc_v}{btc_w} | {avx_v}{avx_w} |"
        )
    lines.append("")
    return lines
```

- [ ] **Step 2: Add `format_label_distribution_block` helper**

```python
# Round 4 §8.2: label distribution counter.
def format_label_distribution_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[str]:
    counts: Dict[str, int] = {}
    for cell_id, cell_entry in summary_judged.items():
        label = cell_entry.get("_cell", {}).get("integrated_label", "?")
        counts[label] = counts.get(label, 0) + 1
    label_order = [
        "ROBUST_PROMOTE", "ETH_ONLY_PROMOTE", "ETH_PROMOTE_MIXED",
        "DAMAGING", "NO_SIGNAL", "BASELINE",
    ]
    lines: List[str] = ["## Label Distribution", ""]
    for label in label_order:
        n = counts.get(label, 0)
        lines.append(f"- {label}: {n}")
    # Catch any unexpected labels
    for label, n in counts.items():
        if label not in label_order:
            lines.append(f"- {label}: {n}  (unexpected)")
    lines.append("")
    return lines
```

- [ ] **Step 3: Add `format_heatmaps_block` helper**

```python
# Round 4 §8.2: 9 per-symbol × distance heatmaps.
_VERDICT_ABBREV = {
    "STRONG_PROMOTE": "SP",
    "PROMOTE": "P",
    "NEUTRAL": "N",
    "KILL": "K",
    "BASELINE": "B",
    "UNKNOWN": "U",
}


def _abbrev(per_sym_entry: Dict[str, Any]) -> str:
    """Abbreviate per-symbol verdict; suffix '*' if warning=True."""
    v = per_sym_entry.get("verdict", "?")
    abbrev = _VERDICT_ABBREV.get(v, v[:2] if v else "?")
    if per_sym_entry.get("warning"):
        abbrev += "*"
    return abbrev


def format_heatmaps_block(
    summary_judged: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[str]:
    """Render 9 heatmaps: 3 symbols × 3 dist values, each a 3×3 (be × start) grid."""
    lines: List[str] = [
        "## Per-Symbol × Distance Heatmaps (3×3 grid of be × start, per-symbol verdict per cell)",
        "",
    ]
    be_values = (0.25, 0.30, 0.35)
    st_values = (0.50, 0.60, 0.70)
    di_values = (0.20, 0.30, 0.40)
    symbols = ["ETHUSDT", "BTCUSDT", "AVAXUSDT"]   # ETH first (primary)

    for sym in symbols:
        for di in di_values:
            lines.append(f"### {sym}, dist={di:.2f}")
            lines.append("")
            header = "|        | " + " | ".join(f"st={st:.2f}" for st in st_values) + " |"
            sep = "|" + "|".join(["--------"] * (len(st_values) + 1)) + "|"
            lines.append(header)
            lines.append(sep)
            for be in be_values:
                row_cells = []
                for st in st_values:
                    cell_id = (
                        f"be{int(round(be * 100)):02d}"
                        f"_st{int(round(st * 100)):02d}"
                        f"_di{int(round(di * 100)):02d}"
                    )
                    entry = summary_judged.get(cell_id, {}).get(sym, {})
                    row_cells.append(_abbrev(entry) if entry else "-")
                lines.append(f"| be={be:.2f} | " + " | ".join(row_cells) + " |")
            lines.append("")
    return lines
```

- [ ] **Step 4: Wire all three blocks into `build_report`**

Inside `build_report`, after the reproducibility block (Task 3) but before the existing per-symbol verdict tables, insert:

```python
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    lines.extend(format_integrated_labels_block(summary_judged, grid))
    lines.extend(format_label_distribution_block(summary_judged))
    lines.extend(format_heatmaps_block(summary_judged))
```

If `STRATEGY_CONFIGS` is not already imported at the top of the file, ensure it is — the existing module already imports it (see `make_strategy_factory`). No additional import needed.

- [ ] **Step 5: Smoke run**

```
python -m scripts.bbkc_exit_eval --smoke
cat logs/research/bbkc_squeeze/exit_round/latest/report.md
```
Expected: report renders all four new sections (reproducibility skipped fallback + integrated labels table with 1 row + label distribution + heatmaps with mostly `-` since smoke covers only F0 × BTC × window 0).

- [ ] **Step 6: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): build_report adds integrated labels + label distribution + 9 heatmaps"
```

---

### Task 5: Update header strings ("Round 3" → "Round 4")

**Files:**
- Modify: `scripts/bbkc_exit_eval.py` (module docstring + `build_report` title)

- [ ] **Step 1: Locate and update strings**

```
grep -n "Round 3" scripts/bbkc_exit_eval.py
```
Expected: 2 hits — module docstring line 1 (`"""BBKC Exit Round 3 evaluation runner.`) and `build_report` title (`"# BBKC Exit Round 3 — Sweep Report"`).

Replace both occurrences:

In module docstring at line 1:
```
"""BBKC Exit Round 3 evaluation runner.
```
→
```
"""BBKC Exit Round 4 evaluation runner.
```

In `build_report`:
```
"# BBKC Exit Round 3 — Sweep Report",
```
→
```
"# BBKC Exit Round 4 — Sweep Report",
```

- [ ] **Step 2: Verify with grep**

```
grep -c "Round 3" scripts/bbkc_exit_eval.py
grep -c "Round 4" scripts/bbkc_exit_eval.py
```
Expected: `Round 3` → 0 (or only inside Reproducibility comments referencing round 3 expected values — those are intentional). `Round 4` → ≥ 2.

If `grep -c "Round 3"` shows non-zero, manually confirm the matches are intentional comments (e.g., `# Round 3 TF_early × ETH`).

- [ ] **Step 3: Smoke**

```
python -m scripts.bbkc_exit_eval --smoke
head -1 logs/research/bbkc_squeeze/exit_round/latest/report.md
```
Expected: `# BBKC Exit Round 4 — Sweep Report`.

- [ ] **Step 4: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "chore(scripts): update bbkc_exit_eval header strings to round 4"
```

---

## Phase C — Run + Round-up

### Task 6: Smoke run + Reproduction quick check (combined sanity)

**Files:** (no code changes)

- [ ] **Step 1: Smoke run (F0 × BTC × window 0)**

```
python -m scripts.bbkc_exit_eval --smoke
```
Expected: completes < 30s. Outputs in `logs/research/bbkc_squeeze/exit_round/<timestamp>_smoke/`:
- `wf_results.jsonl` (1 line, F0 × BTCUSDT × window 0)
- `auxiliary.json` / `summary.json` / `report.md`
- `latest/` mirrored

Verify report.md title says `# BBKC Exit Round 4`. Verify Reproducibility Sanity emits the "MATCH SKIPPED" fallback (because the smoke didn't run be30_st60_di30 × ETH).

- [ ] **Step 2: Reproduction quick check (`be30_st60_di30 × ETHUSDT`)**

```
python -m scripts.bbkc_exit_eval --cell be30_st60_di30 --symbol ETHUSDT
```
Expected: completes < 30s. 9 windows × 1 cell × 1 symbol.

Inspect:
```
cat logs/research/bbkc_squeeze/exit_round/latest/summary.json
```
The `be30_st60_di30 × ETHUSDT` entry should have:
- `wf_oos_positive`: 6
- `mean_r_per_trade`: ~0.0635821965 (within ±0.005)
- `trade_count`: ~154 (within ±2)

Also note `verdict: "UNKNOWN"` is expected for this entry because F0 baseline isn't in the partial run.

- [ ] **Step 3: Verify reproducibility status by hand**

```
python -c "import json; s=json.load(open('logs/research/bbkc_squeeze/exit_round/latest/summary.json')); m=s['be30_st60_di30']['ETHUSDT']; print('wf:', m['wf_oos_positive']); print('R:', m['mean_r_per_trade']); print('n:', m['trade_count'])"
```
Compare against `EXPECTED_REPRODUCE`:
- wf=6 → exact match required
- |R - 0.0635821965| ≤ 0.005
- |n - 154| ≤ 2

If any out of tolerance, **STOP** and investigate before running full sweep. Compare against round 3's `2026-04-28_T2104/wf_results.jsonl` window-by-window to find where divergence began.

- [ ] **Step 4: No commit (no code changes)**

---

### Task 7: Full sweep (28 cells × BIGTHREE × 9 windows = 756 runs)

**Files:** (no code changes — execution only)

- [ ] **Step 1: Full sweep**

```
python -m scripts.bbkc_exit_eval --full
```
Expected: completes in ~3 minutes (756 runs at ~150ms each from round 3 timing). Console logs every (cell, sym, win) progress.

- [ ] **Step 2: Inspect verdict distribution**

```
python -c "
import json
from collections import Counter
s = json.load(open('logs/research/bbkc_squeeze/exit_round/latest/summary.json'))

print('=== Per-cell integrated label distribution ===')
labels = Counter(c.get('_cell', {}).get('integrated_label', '?') for c in s.values())
for label, n in sorted(labels.items(), key=lambda x: -x[1]):
    print(f'  {label}: {n}')

print()
print('=== Per-symbol verdict distribution ===')
for sym in ['ETHUSDT', 'BTCUSDT', 'AVAXUSDT']:
    v = Counter(c.get(sym, {}).get('verdict', '?') for c in s.values())
    print(f'  {sym}: {dict(v)}')
"
```

Sanity targets:
- Total integrated labels = 28 cells (one per cell)
- BASELINE = 1 (F0)
- Sum of others = 27
- ETHUSDT verdict counts must include BASELINE = 1 (F0 row)

- [ ] **Step 3: Verify reproducibility match in the report**

```
sed -n '/Reproducibility Sanity/,/^##/p' logs/research/bbkc_squeeze/exit_round/latest/report.md | head -10
```
Expected: shows `Round 4 reproduce: wf 6/9, R/trade +0.0636, n=154` and `Match: ✓`.

If `Match: ✗`, **STOP** and investigate — full sweep may be using a different code path than round 3's TF_early run.

- [ ] **Step 4: Inspect heatmaps in report**

```
sed -n '/Per-Symbol × Distance Heatmaps/,$p' logs/research/bbkc_squeeze/exit_round/latest/report.md | head -60
```
Expected: 9 heatmap subsections (ETHUSDT × 3 dist + BTCUSDT × 3 dist + AVAXUSDT × 3 dist), each a 3×3 (be × start) Markdown table.

- [ ] **Step 5: No commit (results in `logs/` are gitignored)**

---

### Task 8: Round 4 §14 round-up + main push

**Files:**
- Modify: `Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round4_design.md` (§14)

- [ ] **Step 1: Open the design doc and fill §14**

Locate the §14 placeholder (`## 14. Round 4 Results (sweep 후 채울 placeholder)`). Replace with concrete findings derived from `logs/research/bbkc_squeeze/exit_round/latest/`:

Mandatory subsections (mirror round 3 §15 style):

```markdown
## 14. Round 4 Results (<run_dir name>)

**Run**: `logs/research/bbkc_squeeze/exit_round/<timestamp>/` + `latest/`
**Coverage**: 28 cells × 3 symbols × 9 WF windows = 756 backtests, ~Xs

### 14.1 Reproducibility 검증

(Match ✓ or ✗ + 차이 list. From the report's Reproducibility Sanity section.)

### 14.2 판정 결과 — per-cell integrated label 분포

(BASELINE / ROBUST_PROMOTE / ETH_ONLY_PROMOTE / ETH_PROMOTE_MIXED /
DAMAGING / NO_SIGNAL counts, from the Label Distribution section.)

### 14.3 핵심 발견 1: TF_early가 robust 영역인가?

(Yes/Partial/No based on count of ROBUST_PROMOTE + adjacency to TF_early.
Cite which specific cells if any are ROBUST_PROMOTE or strong ETH_ONLY_PROMOTE.)

### 14.4 핵심 발견 2: trail 파라미터 표면 형태

(From per-symbol heatmaps. Where do PROMOTE-ish verdicts cluster on the
ETH heatmap? Does BTC stay all NEUTRAL? Does AVAX stay all KILL?)

### 14.5 부수 검증

- ✅/❌ Reproducibility match (be30_st60_di30 × ETH vs round 3)
- ETH F0 baseline 재현 정합성 (wf 4/9, R +0.024 expected)
- 라운드 3 결과(`047dfd9`) 기반 trade_count drift

### 14.6 라운드 5 후보

(Based on findings — pick from spec §12: time_stop ETH precision sweep,
live deployment policy, 13-coin generalization, or new axes if TF_early
turns out cherry-pick.)

### 14.7 한 줄 요약

(라운드 4 결론 + 라운드 5 다음 액션.)
```

Replace bracketed text with the actual findings. Numbers come from `summary.json`/`report.md`/`auxiliary.json`.

- [ ] **Step 2: Final regression test**

```
python -m pytest tests/test_strategies/ tests/test_execution/ tests/test_scripts/ tests/_legacy/ -q --no-header
```
Expected: all pass.

- [ ] **Step 3: Commit + push round 4 directly to main**

(Round 3 used a feature branch; round 4 has lower risk surface — only registry grid + script — and the round 3 pattern of "feature branch → merge" is optional for plans this small. We commit on `main` directly. If working on a feature branch, switch first.)

```
git status
git add Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round4_design.md
git commit -m "docs(bbkc_exit): round 4 results + post-sweep findings"
git push origin main
```

(If on a feature branch, run `git checkout main && git merge --no-ff <branch>` first.)

- [ ] **Step 4: Tag round 4 completion (optional)**

```
git tag -a bbkc-exit-round4 -m "BBKC exit round 4: TF_early fine sweep complete"
git push origin bbkc-exit-round4
```

---

## Self-Review Checklist (run after writing all tasks)

- [ ] **Spec coverage**: Every IN bullet in spec §3 has a task
  - registry_builder.exit_round_grid 8→28 → Task 1
  - judge integrate_label addition → Task 2
  - build_report reproducibility sanity → Task 3
  - build_report integrated labels + label distribution + heatmaps → Task 4
  - "Round 3" → "Round 4" string updates → Task 5
  - smoke + reproduction quick check → Task 6
  - full sweep (756 runs) → Task 7
  - §14 round-up → Task 8
- [ ] **No placeholders**: §14 round-up template stays as bracketed instructions only inside Task 8 (intentional — fills after sweep). All other steps have concrete code.
- [ ] **Type consistency**: `integrate_label(cell_id, by_sym)` signature consistent across Task 2 (definition + call from `judge`), Task 4 (consumed via `_cell.integrated_label`). Cell IDs (`F0`, `be{XX}_st{YY}_di{ZZ}`) consistent in Tasks 1, 4, 6, 7. Verdict abbreviations (`SP`/`P`/`N`/`K`/`B`/`U` + optional `*`) consistent in Task 4 heatmap helper.
- [ ] **Path consistency**: `logs/research/bbkc_squeeze/exit_round/<timestamp>/` reused from rounds 2/3.

## Deferred Items (out-of-scope for this plan)

1. ETH-focused time_stop precision sweep (round 5 candidate per spec §12)
2. Live deployment policy decision (round 5 — depends on round 4 outcome)
3. 13-coin generalization (round 5 if ROBUST_PROMOTE found)
4. Investigation if TF_early turns out cherry-pick (round 5 alternative axes)
