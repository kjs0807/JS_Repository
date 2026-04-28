# BBKC Exit Strategy Round 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace BBKCSqueeze R-unit trailing params with TP-fraction units, add `drop_tp` for fat-tail capture, switch `judge()` to baseline-relative delta rules, and run an 8-cell × BIGTHREE × 9 WF window archetype sweep to determine if be_trail is viable on BBKC scale.

**Architecture:** Drop-in refactor of round 2 infrastructure. `_pos_meta` no longer stores `R` (computed on each `_manage_position` call from `entry_price × tp_pct/leverage`). `drop_tp=True` passes `take_profit=None` at entry, no need for `broker.update_tp` (YAGNI). `judge()` becomes per-symbol baseline-relative with five verdict tiers.

**Tech Stack:** Python 3.11+, pytest, existing `BacktestEngine` + `BacktestBroker` + `HoldoutSpec` pipeline (round 2 already wired).

**Spec:** `Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-28_bbkc_exit_round3_design.md` (commit `72beff8`)

**Working directory for all bash commands:** `C:/Users/ceoji/Desktop/python_ibks/Crypto/Bybit_Trading/`

---

## File Structure

### Modified Files

| Path | Change |
|---|---|
| `src/strategies/bbkc_squeeze.py` | Drop R-unit params, add TP-fraction params + `drop_tp`, invariant, rewrite `_manage_position`, branch `take_profit=None` at entry when drop_tp |
| `src/strategies/registry_builder.py` | Replace `exit_round_grid` 12 cells → 8 cells (TP-fraction schema) |
| `scripts/bbkc_exit_eval.py` | Update `make_strategy_factory`, rewrite `judge()`, update report header / verdict column ("Round 2" → "Round 3", add `UNKNOWN`/`STRONG_PROMOTE`/`NEUTRAL`) |
| `tests/test_strategies/test_bbkc_squeeze_exit_modes.py` | Replace R-unit tests with TP-fraction + drop_tp + invariant tests |
| `tests/test_strategies/test_registry_builder_exit_grid.py` | Update cell_id assertions for 8-cell schema |

### New Files

| Path | Purpose |
|---|---|
| `tests/test_scripts/__init__.py` | Empty marker for pytest discovery |
| `tests/test_scripts/test_bbkc_exit_eval_judge.py` | judge() rule branch tests (BASELINE/UNKNOWN/STRONG_PROMOTE/PROMOTE/NEUTRAL/KILL/WARNING) |

---

## Phase A — BBKCSqueeze TP-fraction Refactor

### Task 1: Replace strategy params (R-unit out, TP-fraction in, `drop_tp`, invariant)

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py:21-46` (`__init__`), `:129-146` (`get_params`)
- Modify: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py` (replace R-unit param tests)

- [ ] **Step 1: Rewrite the param tests**

Open `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`. Replace the three top tests (`test_default_params_preserve_fixed_mode`, `test_set_params_updates_exit_mode`, `test_invalid_exit_mode_rejected`) with the new schema:

```python
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
    # be_at >= start_at → invalid for be_trail mode
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
    # 0.49 < 0.50 must pass (immediate archetype)
    s = BBKCSqueeze(exit_mode="be_trail",
                    trail_be_at_tp_frac=0.49,
                    trail_start_at_tp_frac=0.50,
                    trail_distance_tp_frac=0.3)
    assert s.trail_be_at_tp_frac == 0.49
    assert s.trail_start_at_tp_frac == 0.50


def test_invariant_skipped_for_fixed_mode():
    # fixed mode shouldn't enforce trail invariants (caller may leave defaults)
    s = BBKCSqueeze(exit_mode="fixed", trail_be_at_tp_frac=0.9,
                    trail_start_at_tp_frac=0.5)  # would violate if be_trail
    assert s.exit_mode == "fixed"
```

Also delete the OLD R-unit tests from this file: any test referencing `trail_be_r`, `trail_start_r`, `trail_distance_r`, or `meta["R"]`. We rewrite them in Tasks 2-3.

For now leave the existing lazy-init / be_trail / time_stop tests intact — Tasks 2-3 rewrite them.

- [ ] **Step 2: Run param tests to verify failure**

```
python -m pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_default_params_are_tp_fraction_units tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_invariant_rejects_be_geq_start -v
```
Expected: FAIL — current `BBKCSqueeze` has no `trail_be_at_tp_frac` etc.

- [ ] **Step 3: Replace `__init__` and `get_params`**

In `src/strategies/bbkc_squeeze.py`, replace the existing `__init__` block (currently around line 21-58, including R-unit params) with:

```python
    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 1.5,
        kc_period: int = 20,
        kc_mult: float = 1.0,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_filter: float = 70.0,
        tp_pct: float = 0.06,
        sl_pct: float = 0.07,
        leverage: int = 3,
        timeframe: str = "1h",
        exit_mode: str = "fixed",
        trail_be_at_tp_frac: float = 0.5,
        trail_start_at_tp_frac: float = 0.8,
        trail_distance_tp_frac: float = 0.3,
        drop_tp: bool = False,
        time_stop_bars: int = 0,
    ) -> None:
        if exit_mode not in ("fixed", "be_trail"):
            raise ValueError(f"exit_mode must be 'fixed' or 'be_trail', got {exit_mode!r}")
        if exit_mode == "be_trail":
            if not (0 < trail_be_at_tp_frac < trail_start_at_tp_frac < 1.0):
                raise ValueError(
                    f"need 0 < trail_be_at_tp_frac < trail_start_at_tp_frac < 1.0, "
                    f"got be={trail_be_at_tp_frac}, start={trail_start_at_tp_frac}"
                )
            if trail_distance_tp_frac <= 0:
                raise ValueError(
                    f"trail_distance_tp_frac must be > 0, got {trail_distance_tp_frac}"
                )
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.rsi_filter = rsi_filter
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.leverage = leverage
        self.timeframe = timeframe
        self.exit_mode = exit_mode
        self.trail_be_at_tp_frac = trail_be_at_tp_frac
        self.trail_start_at_tp_frac = trail_start_at_tp_frac
        self.trail_distance_tp_frac = trail_distance_tp_frac
        self.drop_tp = drop_tp
        self.time_stop_bars = time_stop_bars
        self._pos_meta: dict = {}
```

Replace `get_params`:

```python
    def get_params(self) -> dict:
        return {
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "kc_period": self.kc_period,
            "kc_mult": self.kc_mult,
            "atr_period": self.atr_period,
            "rsi_period": self.rsi_period,
            "rsi_filter": self.rsi_filter,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "leverage": self.leverage,
            "exit_mode": self.exit_mode,
            "trail_be_at_tp_frac": self.trail_be_at_tp_frac,
            "trail_start_at_tp_frac": self.trail_start_at_tp_frac,
            "trail_distance_tp_frac": self.trail_distance_tp_frac,
            "drop_tp": self.drop_tp,
            "time_stop_bars": self.time_stop_bars,
        }
```

`set_params` needs no changes (iterates `setattr` over kwargs).

At this point `_manage_position` still references `meta["R"]` from round 2 — that breaks; it gets fixed in Task 2. For now expect existing be_trail/lazy-init/time-stop tests to fail — that's covered in Tasks 2-3.

- [ ] **Step 4: Run param tests to verify pass**

```
python -m pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_default_params_are_tp_fraction_units tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_invariant_rejects_be_geq_start tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_invariant_rejects_out_of_unit_interval tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_invariant_rejects_distance_zero_or_negative tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_invariant_accepts_immediate_cell tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_invariant_skipped_for_fixed_mode tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_set_params_updates_exit_mode tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_invalid_exit_mode_rejected -v
```
Expected: PASS for all eight.

- [ ] **Step 5: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "refactor(bbkc): replace R-unit trail params with TP-fraction + drop_tp + invariant"
```

---

### Task 2: Rewrite `_manage_position` with TP-fraction math + update `_pos_meta`

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py:_manage_position` and lazy-init in `on_bar_fast`
- Modify: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py` (replace R-unit be_trail tests)

- [ ] **Step 1: Rewrite the lazy-init + be_trail tests**

Open `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`. Replace the existing lazy-init and be_trail tests with the TP-fraction equivalents:

```python
class _MockBroker:
    def __init__(self):
        self.buys = []
        self.sells = []
        self.closes = []
        self.stop_updates = []
        self.positions = {}

    def buy(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.buys.append((symbol, qty, stop_loss, take_profit, reason))
        return "buy"

    def sell(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.sells.append((symbol, qty, stop_loss, take_profit, reason))
        return "sell"

    def close(self, symbol, reason=""):
        self.closes.append((symbol, reason))
        return "close"

    def update_stop(self, symbol, new_stop):
        self.stop_updates.append((symbol, new_stop))

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def calc_qty(self, symbol, risk_pct, stop_distance):
        return 1.0


def _bars(closes):
    n = len(closes)
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    })
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)


def _stub_cache(s):
    closes = [100.0] * 60
    series = _bars(closes)
    return s.prepare(series)


def _make_long_pos(entry=100.0, stop=97.67, tp=102.0):
    """Default LONG position. tp_distance = entry × 0.06/3 = 2.0."""
    return Position(
        "BTCUSDT", "LONG", 1.0, entry, 1700000000000,
        stop, tp, 0.0, "BBKCSqueeze", 0.0,
    )


def _make_short_pos(entry=100.0, stop=102.33, tp=98.0):
    return Position(
        "BTCUSDT", "SHORT", 1.0, entry, 1700000000000,
        stop, tp, 0.0, "BBKCSqueeze", 0.0,
    )


# ── lazy init / cleanup (no R in meta anymore) ───────────────────────────


def test_pos_meta_lazy_init_when_position_appears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    assert "BTCUSDT" not in s._pos_meta

    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)

    assert "BTCUSDT" in s._pos_meta
    meta = s._pos_meta["BTCUSDT"]
    # Round 3: only behavioral flags + bars_held in meta, no R
    assert meta["be_triggered"] is False
    assert meta["trail_active"] is False
    assert meta["bars_held"] == 1
    assert "R" not in meta


def test_pos_meta_cleanup_when_position_disappears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert "BTCUSDT" in s._pos_meta

    del broker.positions["BTCUSDT"]
    s.on_bar_fast(bar, 51, cache, broker)
    assert "BTCUSDT" not in s._pos_meta


# ── be_trail TP-fraction triggers ────────────────────────────────────────
# tp_distance = 100 × 0.06 / 3 = 2.0
# default trail_be_at_tp_frac=0.5 → BE at +1.0
# default trail_start_at_tp_frac=0.8 → trail at +1.6
# default trail_distance_tp_frac=0.3 → SL = close - 0.6


def test_be_trail_long_below_be_threshold_no_change():
    s = BBKCSqueeze(exit_mode="be_trail")  # defaults 0.5/0.8/0.3
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    # close=100.5 → move=+0.5 < 1.0 (=0.5 × tp_dist=2.0)
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100.5, 100.5, 100.5, 100.5, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is False


def test_be_trail_long_at_be_threshold_triggers_BE():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    # close=101.0 → move=+1.0 = 0.5 × tp_dist=2.0 → BE
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is True


def test_be_trail_long_BE_only_triggers_once():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101, 101, 101, 101, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    # Second bar still above BE but below trail_start (1.6)
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 101.2, 101.2, 101.2, 101.2, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert len(broker.stop_updates) == 1


def test_be_trail_long_at_start_threshold_activates_trailing():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)  # avoid TP exit
    # close=101.6 → move=+1.6 = 0.8 × tp_dist=2.0 → BE + trail activated
    # trail SL = close - 0.3 × tp_dist = 101.6 - 0.6 = 101.0
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.6, 101.6, 101.6, 101.6, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates       # BE
    assert ("BTCUSDT", pytest.approx(101.0)) in broker.stop_updates  # trail
    assert s._pos_meta["BTCUSDT"]["trail_active"] is True


def test_be_trail_long_trailing_ratchets_up_only():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)
    # First bar: close=102 → move=+2.0 ≥ 1.6 → trail SL = 102 - 0.6 = 101.4
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 102, 102, 102, 102, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    broker.positions["BTCUSDT"].stop_loss = 101.4   # simulate broker applied the trail

    # Lower close → would compute SL = 101.5 - 0.6 = 100.9 < 101.4, no ratchet
    broker.stop_updates.clear()
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 101.5, 101.5, 101.5, 101.5, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.stop_updates == []

    # Higher close → ratchet up: SL = 103 - 0.6 = 102.4
    bar3 = Bar("BTCUSDT", 1700000000002, "1h", 103, 103, 103, 103, 1000)
    s.on_bar_fast(bar3, 52, cache, broker)
    assert broker.stop_updates == [("BTCUSDT", pytest.approx(102.4))]


def test_be_trail_short_symmetry():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_tp_frac=0.3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    # SHORT entry=100, sl=102.33, tp=98.0; tp_distance = 100 × 0.06/3 = 2.0
    broker.positions["BTCUSDT"] = _make_short_pos(stop=102.33, tp=70.0)  # wide TP to avoid TP
    # close=98.4 → move = entry - close = 1.6 = 0.8 × tp_dist → BE + trail
    # SHORT trail SL = close + 0.3 × tp_dist = 98.4 + 0.6 = 99.0
    bar = Bar("BTCUSDT", 1700000000000, "1h", 98.4, 98.4, 98.4, 98.4, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates       # BE = entry
    assert ("BTCUSDT", pytest.approx(99.0)) in broker.stop_updates  # trail


def test_immediate_cell_be_and_trail_same_bar():
    """0.49/0.50 immediate archetype: both BE and trail fire at +1.0 (= 0.5 × tp_dist)."""
    s = BBKCSqueeze(
        exit_mode="be_trail",
        trail_be_at_tp_frac=0.49,
        trail_start_at_tp_frac=0.50,
        trail_distance_tp_frac=0.3,
    )
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)
    # close = 100 + 0.50 × 2.0 = 101.0; move = +1.0; both 0.49×2=0.98 and 0.50×2=1.00 met
    bar = Bar("BTCUSDT", 1700000000000, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates  # BE
    # trail SL = 101 - 0.3 × 2.0 = 100.4
    assert ("BTCUSDT", pytest.approx(100.4)) in broker.stop_updates


def test_be_trail_fixed_mode_does_not_BE():
    s = BBKCSqueeze(exit_mode="fixed")
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar = Bar("BTCUSDT", 1700000000000, "1h", 110, 110, 110, 110, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []


# ── time_stop preserved from round 2 ─────────────────────────────────────


def test_time_stop_zero_does_nothing():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=0)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    for k in range(100):
        bar = Bar("BTCUSDT", 1700000000000 + k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50 + k, cache, broker)
    assert broker.closes == []


def test_time_stop_triggers_at_N_bars_held():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=3)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    for k in range(3):
        bar = Bar("BTCUSDT", 1700000000000 + k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50 + k, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]


def test_time_stop_works_with_be_trail():
    s = BBKCSqueeze(exit_mode="be_trail", time_stop_bars=2)
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos()
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 100.2, 100.2, 100.2, 100.2, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 100.4, 100.4, 100.4, 100.4, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]
```

Delete any remaining tests in this file that reference R-unit semantics (`trail_be_r=`, `trail_distance_r=`, `meta["R"]`, `_make_long_pos` calls with old SL/TP that don't match the new defaults). The smoke `test_be_trail_full_lifecycle_smoke` should also be replaced — keep one final test:

```python
def test_be_trail_full_lifecycle_smoke():
    """Smoke: entry → BE → trail path with default TF_default params."""
    s = BBKCSqueeze(exit_mode="be_trail")  # defaults match TF_default
    broker = _MockBroker()
    cache = _stub_cache(s)
    broker.positions["BTCUSDT"] = _make_long_pos(tp=130.0)
    # +1.0 → BE
    bar_be = Bar("BTCUSDT", 1700000000001, "1h", 101.0, 101.0, 101.0, 101.0, 1000)
    s.on_bar_fast(bar_be, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is True
    # +2.0 → trail at 101.4
    bar_trail = Bar("BTCUSDT", 1700000000002, "1h", 102.0, 102.0, 102.0, 102.0, 1000)
    s.on_bar_fast(bar_trail, 51, cache, broker)
    assert s._pos_meta["BTCUSDT"]["trail_active"] is True
```

- [ ] **Step 2: Run tests to verify failure**

```
python -m pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: many tests FAIL (manage_position still uses R-unit math).

- [ ] **Step 3: Replace `_manage_position` and lazy-init in `bbkc_squeeze.py`**

Find `on_bar_fast`. Replace the lazy-init block (the one that reads `pos.entry_price - pos.stop_loss` to compute R) with:

```python
        # ── _pos_meta lazy init / cleanup (on_fill 비의존) ─────────────────
        if pos is None and sym in self._pos_meta:
            del self._pos_meta[sym]
        if pos is not None and sym not in self._pos_meta:
            self._pos_meta[sym] = {
                "be_triggered": False,
                "trail_active": False,
                "bars_held": 0,
            }
```

Find `_manage_position` and replace its body with:

```python
    def _manage_position(self, bar: Bar, pos, broker: Broker) -> None:
        """포지션 보유 중 관리: be_trail BE/trailing (TP-fraction units) + time_stop."""
        sym = bar.symbol
        meta = self._pos_meta[sym]

        # tp_distance = entry × tp_pct / leverage. Safety guards.
        if pos.entry_price <= 0 or self.tp_pct <= 0 or self.leverage <= 0:
            return
        tp_distance = pos.entry_price * self.tp_pct / self.leverage

        close = bar.close
        if pos.side == "LONG":
            move = close - pos.entry_price
        else:
            move = pos.entry_price - close

        if self.exit_mode == "be_trail":
            # BE step (한 번만)
            if not meta["be_triggered"] and move >= self.trail_be_at_tp_frac * tp_distance:
                broker.update_stop(sym, pos.entry_price)
                meta["be_triggered"] = True

            # Trailing step (활성 후 ratchet only)
            if move >= self.trail_start_at_tp_frac * tp_distance:
                offset = self.trail_distance_tp_frac * tp_distance
                new_sl = (close - offset) if pos.side == "LONG" else (close + offset)

                if not meta["trail_active"]:
                    broker.update_stop(sym, new_sl)
                    meta["trail_active"] = True
                else:
                    if pos.side == "LONG" and new_sl > pos.stop_loss:
                        broker.update_stop(sym, new_sl)
                    elif pos.side == "SHORT" and new_sl < pos.stop_loss:
                        broker.update_stop(sym, new_sl)

        # time_stop fallback (직교)
        if self.time_stop_bars > 0 and meta["bars_held"] >= self.time_stop_bars:
            broker.close(sym, reason="time_stop")
```

- [ ] **Step 4: Run tests to verify pass**

```
python -m pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: ALL pass.

- [ ] **Step 5: Run wider regression**

```
python -m pytest tests/test_strategies/ tests/test_execution/ -q --no-header
```
Expected: all pass (entry tests still work — `on_bar_fast` entry branch unchanged in this task).

- [ ] **Step 6: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "refactor(bbkc): _manage_position uses TP-fraction units, _pos_meta drops R"
```

---

### Task 3: drop_tp at entry (on_bar_fast entry branch)

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py:on_bar_fast` entry branch
- Modify: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`

- [ ] **Step 1: Add drop_tp tests**

Append to `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`:

```python
# ── drop_tp behavior at entry ────────────────────────────────────────────


def _entry_signal_series():
    """Build a series that produces a squeeze release LONG signal at index 49."""
    closes = [100.0]*40 + list(np.linspace(100, 108, 10))
    return _bars(closes)


def test_drop_tp_false_passes_take_profit_at_entry():
    s = BBKCSqueeze(exit_mode="be_trail", drop_tp=False)
    broker = _MockBroker()
    series = _entry_signal_series()
    cache = s.prepare(series)

    last = float(series.bars["close"].iloc[-1])
    bar = Bar("BTCUSDT", 1700000000000, "1h", last, last+0.5, last-0.5, last, 1000)
    s.on_bar_fast(bar, 49, cache, broker)
    if not broker.buys:
        pytest.skip("squeeze release didn't fire on this synthetic series — entry path skipped")
    sym, qty, sl, tp, _ = broker.buys[-1]
    assert tp is not None
    assert tp > last   # LONG: TP above entry


def test_drop_tp_true_passes_none_take_profit_at_entry():
    s = BBKCSqueeze(exit_mode="be_trail", drop_tp=True)
    broker = _MockBroker()
    series = _entry_signal_series()
    cache = s.prepare(series)

    last = float(series.bars["close"].iloc[-1])
    bar = Bar("BTCUSDT", 1700000000000, "1h", last, last+0.5, last-0.5, last, 1000)
    s.on_bar_fast(bar, 49, cache, broker)
    if not broker.buys:
        pytest.skip("squeeze release didn't fire on this synthetic series — entry path skipped")
    sym, qty, sl, tp, _ = broker.buys[-1]
    assert tp is None
    assert sl is not None and sl < last   # SL still set
```

- [ ] **Step 2: Run tests to verify failure**

```
python -m pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_drop_tp_true_passes_none_take_profit_at_entry -v
```
Expected: FAIL — current code always passes a numeric tp.

- [ ] **Step 3: Modify entry branch in `on_bar_fast`**

Find the LONG and SHORT entry blocks at the bottom of `on_bar_fast`. Replace them:

```python
        price_tp = self.tp_pct / self.leverage
        price_sl = self.sl_pct / self.leverage

        # LONG: 상단 이탈 + RSI 과열 아님
        if close > bb_mid and rsi_val < self.rsi_filter:
            sl = close * (1 - price_sl)
            tp = None if self.drop_tp else close * (1 + price_tp)
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                           reason=f"BBKCSqueeze LONG rsi={rsi_val:.1f}")

        # SHORT: 하단 이탈 + RSI 과매도 아님
        elif close < bb_mid and rsi_val > (100.0 - self.rsi_filter):
            sl = close * (1 + price_sl)
            tp = None if self.drop_tp else close * (1 - price_tp)
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                            reason=f"BBKCSqueeze SHORT rsi={rsi_val:.1f}")
```

- [ ] **Step 4: Run tests to verify pass**

```
python -m pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: ALL pass (drop_tp tests may `skip` if synthetic series doesn't trigger entry — that's acceptable).

- [ ] **Step 5: Regression**

```
python -m pytest tests/test_strategies/ tests/test_execution/ -q --no-header
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "feat(bbkc): drop_tp=True passes take_profit=None at entry"
```

---

## Phase B — Registry + Sweep Script + Judge

### Task 4: Replace `exit_round_grid` with 8 cells (TP-fraction schema)

**Files:**
- Modify: `src/strategies/registry_builder.py`
- Modify: `tests/test_strategies/test_registry_builder_exit_grid.py`

- [ ] **Step 1: Update registry tests**

Replace `tests/test_strategies/test_registry_builder_exit_grid.py` content:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

```
python -m pytest tests/test_strategies/test_registry_builder_exit_grid.py -v
```
Expected: FAIL — old grid has 12 cells with R-unit keys.

- [ ] **Step 3: Replace the grid in `registry_builder.py`**

Replace the existing `"exit_round_grid": [...]` block inside `STRATEGY_CONFIGS["BBKCSqueeze"]` with:

```python
        # 2026-04-28 round 3: TP-fraction trailing thresholds (round 2 R-unit dead path).
        # 8 hand-picked archetypes. time_stop=0 across all (round 4 deferred).
        "exit_round_grid": [
            {"cell_id": "F0",           "exit_mode": "fixed",    "trail_be_at_tp_frac": None, "trail_start_at_tp_frac": None, "trail_distance_tp_frac": None, "drop_tp": False, "time_stop_bars": 0},
            {"cell_id": "TF_default",   "exit_mode": "be_trail", "trail_be_at_tp_frac": 0.50, "trail_start_at_tp_frac": 0.80, "trail_distance_tp_frac": 0.30, "drop_tp": False, "time_stop_bars": 0},
            {"cell_id": "TF_wide",      "exit_mode": "be_trail", "trail_be_at_tp_frac": 0.50, "trail_start_at_tp_frac": 0.80, "trail_distance_tp_frac": 0.50, "drop_tp": False, "time_stop_bars": 0},
            {"cell_id": "TF_early",     "exit_mode": "be_trail", "trail_be_at_tp_frac": 0.30, "trail_start_at_tp_frac": 0.60, "trail_distance_tp_frac": 0.30, "drop_tp": False, "time_stop_bars": 0},
            {"cell_id": "TF_late",      "exit_mode": "be_trail", "trail_be_at_tp_frac": 0.70, "trail_start_at_tp_frac": 0.90, "trail_distance_tp_frac": 0.30, "drop_tp": False, "time_stop_bars": 0},
            {"cell_id": "TF_immediate", "exit_mode": "be_trail", "trail_be_at_tp_frac": 0.49, "trail_start_at_tp_frac": 0.50, "trail_distance_tp_frac": 0.30, "drop_tp": False, "time_stop_bars": 0},
            {"cell_id": "TR_default",   "exit_mode": "be_trail", "trail_be_at_tp_frac": 0.50, "trail_start_at_tp_frac": 0.80, "trail_distance_tp_frac": 0.30, "drop_tp": True,  "time_stop_bars": 0},
            {"cell_id": "TR_immediate", "exit_mode": "be_trail", "trail_be_at_tp_frac": 0.49, "trail_start_at_tp_frac": 0.50, "trail_distance_tp_frac": 0.30, "drop_tp": True,  "time_stop_bars": 0},
        ],
```

- [ ] **Step 4: Run tests to verify pass**

```
python -m pytest tests/test_strategies/test_registry_builder_exit_grid.py tests/test_strategies/test_registry_builder.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add src/strategies/registry_builder.py tests/test_strategies/test_registry_builder_exit_grid.py
git commit -m "feat(registry): replace exit_round_grid with 8-cell TP-fraction archetypes"
```

---

### Task 5: Update `make_strategy_factory` for new params

**Files:**
- Modify: `scripts/bbkc_exit_eval.py:make_strategy_factory`

- [ ] **Step 1: Inspect current factory + replace**

Open `scripts/bbkc_exit_eval.py`. Find `make_strategy_factory`. Replace with:

```python
def make_strategy_factory(cell: Dict[str, Any]):
    """Return a zero-arg factory that builds BBKCSqueeze with cell params (round 3 schema)."""
    kwargs: Dict[str, Any] = dict(
        bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
        atr_period=14, rsi_period=14, rsi_filter=70.0,
        tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h",
        exit_mode=cell["exit_mode"],
        drop_tp=cell.get("drop_tp", False),
        time_stop_bars=cell["time_stop_bars"],
    )
    # be_trail cells set the three TP-fraction params; fixed cells leave defaults
    if cell["exit_mode"] == "be_trail":
        kwargs["trail_be_at_tp_frac"] = cell["trail_be_at_tp_frac"]
        kwargs["trail_start_at_tp_frac"] = cell["trail_start_at_tp_frac"]
        kwargs["trail_distance_tp_frac"] = cell["trail_distance_tp_frac"]
    return lambda: BBKCSqueeze(**kwargs)
```

- [ ] **Step 2: Smoke check the factory**

```
python -c "from scripts.bbkc_exit_eval import make_strategy_factory; from src.strategies.registry_builder import STRATEGY_CONFIGS; grid = STRATEGY_CONFIGS['BBKCSqueeze']['exit_round_grid']; [make_strategy_factory(c)() for c in grid]; print('all 8 factories built OK')"
```
Expected: prints `all 8 factories built OK`.

- [ ] **Step 3: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): bbkc_exit_eval factory builds new TP-fraction params + drop_tp"
```

---

### Task 6: Rewrite `judge()` with baseline-relative delta rules

**Files:**
- Modify: `scripts/bbkc_exit_eval.py:judge`
- Create: `tests/test_scripts/__init__.py`
- Create: `tests/test_scripts/test_bbkc_exit_eval_judge.py`

- [ ] **Step 1: Create the test harness**

Create empty `tests/test_scripts/__init__.py`:

```python
```

Create `tests/test_scripts/test_bbkc_exit_eval_judge.py`:

```python
"""bbkc_exit_eval.judge() baseline-relative delta rule tests."""
from scripts.bbkc_exit_eval import judge


def _base(wf=4, r=0.05, dd=0.10, n=100):
    return {"wf_oos_positive": wf, "wf_total": 9, "mean_r_per_trade": r,
            "max_dd": dd, "trade_count": n, "mean_oos_pnl": 200.0}


def _summary(base, **cells):
    """Build the summary dict shape that judge() consumes.
    cells: cell_id -> {symbol -> metric dict}.
    """
    out = {"F0": {"BTCUSDT": base}}
    for cid, syms in cells.items():
        out[cid] = syms
    return out


def test_F0_returns_BASELINE():
    base = _base()
    s = _summary(base)
    judged = judge(s)
    assert judged["F0"]["BTCUSDT"]["verdict"] == "BASELINE"
    assert judged["F0"]["BTCUSDT"]["warning"] is False


def test_no_F0_baseline_returns_UNKNOWN():
    """If --cell skipped F0, non-F0 cells lose their reference -> UNKNOWN."""
    s = {"TF_default": {"BTCUSDT": _base(wf=5, r=0.10, dd=0.08)}}
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "UNKNOWN"


def test_strong_promote_pos_plus_2_r_geq_dd_leq():
    base = _base(wf=4, r=0.05, dd=0.10, n=100)
    cell = _base(wf=6, r=0.10, dd=0.08, n=100)
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "STRONG_PROMOTE"


def test_promote_pos_plus_1_r_geq():
    base = _base(wf=4, r=0.05, dd=0.10)
    cell = _base(wf=5, r=0.06, dd=0.12)   # DD worse — only PROMOTE not STRONG
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "PROMOTE"


def test_neutral_within_thresholds():
    base = _base(wf=4, r=0.05)
    cell = _base(wf=4, r=0.06)   # |Δwf|=0, |Δr|=0.01
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "NEUTRAL"


def test_kill_pos_minus_2():
    base = _base(wf=4, r=0.05)
    cell = _base(wf=2, r=0.05)
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "KILL"


def test_kill_r_drop_more_than_threshold():
    base = _base(wf=4, r=0.05)
    cell = _base(wf=4, r=-0.10)   # Δr = -0.15 < -0.05
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "KILL"


def test_warning_flag_when_trade_count_below_half_baseline():
    base = _base(wf=4, r=0.05, n=100)
    cell = _base(wf=5, r=0.06, n=40)   # 40 < 100 × 0.5
    s = _summary(base, TF_default={"BTCUSDT": cell})
    judged = judge(s)
    assert judged["TF_default"]["BTCUSDT"]["warning"] is True
    assert judged["TF_default"]["BTCUSDT"]["verdict"] == "PROMOTE"
```

- [ ] **Step 2: Run tests to verify failure**

```
python -m pytest tests/test_scripts/test_bbkc_exit_eval_judge.py -v
```
Expected: FAIL — old judge returns `BASELINE` for both F0 and missing-base cases (round 2 bug per spec §9 review).

- [ ] **Step 3: Replace `judge()` body in `scripts/bbkc_exit_eval.py`**

Replace the existing `judge()` function with:

```python
def judge(summary: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Apply baseline-relative delta rules per (cell, symbol). Round 3 §9.

    F0 is BASELINE per symbol. Cells without an F0 baseline (e.g. when --cell
    skipped F0) get verdict='UNKNOWN'.
    """
    f0 = summary.get("F0", {})
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for cell_id, by_sym in summary.items():
        for sym, m in by_sym.items():
            entry = dict(m)
            if cell_id == "F0":
                entry["verdict"] = "BASELINE"
                entry["warning"] = False
                out.setdefault(cell_id, {})[sym] = entry
                continue
            base = f0.get(sym)
            if base is None:
                entry["verdict"] = "UNKNOWN"
                entry["warning"] = False
                out.setdefault(cell_id, {})[sym] = entry
                continue

            warning = m["trade_count"] < base["trade_count"] * 0.5
            pos_delta = m["wf_oos_positive"] - base["wf_oos_positive"]
            r_delta = m["mean_r_per_trade"] - base["mean_r_per_trade"]

            if pos_delta >= 2 and r_delta >= 0 and m["max_dd"] <= base["max_dd"]:
                verdict = "STRONG_PROMOTE"
            elif pos_delta >= 1 and r_delta >= 0:
                verdict = "PROMOTE"
            elif abs(pos_delta) <= 1 and abs(r_delta) <= 0.05:
                verdict = "NEUTRAL"
            elif pos_delta < -1 or r_delta < -0.05:
                verdict = "KILL"
            else:
                verdict = "NEUTRAL"   # safety fallback

            entry["verdict"] = verdict
            entry["warning"] = warning
            out.setdefault(cell_id, {})[sym] = entry
    return out
```

- [ ] **Step 4: Run tests to verify pass**

```
python -m pytest tests/test_scripts/test_bbkc_exit_eval_judge.py -v
```
Expected: PASS for all 8.

- [ ] **Step 5: Commit**

```
git add scripts/bbkc_exit_eval.py tests/test_scripts/__init__.py tests/test_scripts/test_bbkc_exit_eval_judge.py
git commit -m "feat(scripts): rewrite judge() with baseline-relative delta rules + UNKNOWN for missing baseline"
```

---

### Task 7: Update report titles + verdict column ("Round 2" → "Round 3", surface UNKNOWN)

**Files:**
- Modify: `scripts/bbkc_exit_eval.py` (module docstring + `build_report` title strings)

- [ ] **Step 1: Update module docstring**

In `scripts/bbkc_exit_eval.py`, locate the top-of-file docstring. Replace:

```python
"""BBKC Exit Round 2 evaluation runner.
```

with:

```python
"""BBKC Exit Round 3 evaluation runner.
```

The rest of the docstring stays the same — output paths and CLI flags didn't change.

- [ ] **Step 2: Update report title in `build_report`**

Find `build_report`. Replace the first lines line:

```python
    lines: List[str] = [
        "# BBKC Exit Round 2 — Sweep Report",
```

with:

```python
    lines: List[str] = [
        "# BBKC Exit Round 3 — Sweep Report",
```

- [ ] **Step 3: Smoke that report still emits and includes new verdicts**

```
python -m scripts.bbkc_exit_eval --smoke
```
Expected: completes; produces files under `logs/research/bbkc_squeeze/exit_round/<timestamp>_smoke/` and `latest/`. Verify report.md starts with `# BBKC Exit Round 3 — Sweep Report`:

```
head -1 logs/research/bbkc_squeeze/exit_round/latest/report.md
```

- [ ] **Step 4: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "chore(scripts): update bbkc_exit_eval header strings to round 3"
```

---

## Phase C — Sweep + Round-up

### Task 8: Smoke run (1 cell, sanity check the full pipeline)

**Files:**
- (no code changes)

- [ ] **Step 1: Smoke the F0 baseline cell explicitly**

```
python -m scripts.bbkc_exit_eval --smoke
```
Expected: completes in <30s, produces:
- `logs/research/bbkc_squeeze/exit_round/<timestamp>_smoke/wf_results.jsonl` (1 line, F0 × BTCUSDT × window 0)
- `logs/research/bbkc_squeeze/exit_round/<timestamp>_smoke/auxiliary.json`
- `logs/research/bbkc_squeeze/exit_round/<timestamp>_smoke/summary.json` (F0 verdict = "BASELINE")
- `logs/research/bbkc_squeeze/exit_round/<timestamp>_smoke/report.md` (title says "Round 3")
- `logs/research/bbkc_squeeze/exit_round/latest/` mirroring above

- [ ] **Step 2: Quick sanity — also run a be_trail cell explicitly**

```
python -m scripts.bbkc_exit_eval --cell TR_default --symbol BTCUSDT
```
Expected: 9 windows, results emitted. Inspect:

```
grep TR_default logs/research/bbkc_squeeze/exit_round/latest/wf_results.jsonl | head -3
```

Then check that drop_tp=True yielded no `TP` exits (sanity check from §10):

```
python -c "import json; aux = json.load(open('logs/research/bbkc_squeeze/exit_round/latest/auxiliary.json')); print(aux.get('TR_default', {}).get('BTCUSDT', {}).get('exit_reason_dist'))"
```
Expected: a dict with `"TP"` either absent or 0.0. Other reasons (`STOP`, `BACKTEST_END`, etc.) may appear.

If TP is non-zero, drop_tp logic is broken — stop and investigate (do not proceed to full sweep).

- [ ] **Step 3: No commit needed (no code changes)**

---

### Task 9: Full sweep (8 cells × BIGTHREE × 9 windows = 216 runs)

**Files:**
- (no code changes — execution only)

- [ ] **Step 1: Full sweep**

```
python -m scripts.bbkc_exit_eval --full
```
Expected: completes in ~80-120s. Console logs every (cell, symbol, window) progress.

- [ ] **Step 2: Inspect summary**

```
cat logs/research/bbkc_squeeze/exit_round/latest/report.md
```

Expect 3 per-symbol tables × 8 cells each = 24 verdicts. F0 row should be `BASELINE` for each symbol. Other cells should be `STRONG_PROMOTE`/`PROMOTE`/`NEUTRAL`/`KILL` per the delta rule.

```
python -c "import json; s = json.load(open('logs/research/bbkc_squeeze/exit_round/latest/summary.json')); from collections import Counter; print(Counter((m['verdict']) for sym in s.values() for m in sym.values()))"
```

This prints the verdict distribution across all 24 cell-symbol pairs. Sanity targets:
- 3 `BASELINE` (one per symbol on F0)
- 0 `UNKNOWN` (because we ran full sweep with F0)
- 21 split among STRONG_PROMOTE / PROMOTE / NEUTRAL / KILL

If verdict counts don't add up to 24, or `UNKNOWN` > 0, investigate.

- [ ] **Step 3: Commit results**

`logs/` is in `.gitignore` (round 2 confirmed: `Crypto/**/logs/`), so no commit. Results stay local under the timestamp dir.

---

### Task 10: Round 3 §15 round-up + main merge

**Files:**
- Modify: `Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-28_bbkc_exit_round3_design.md` (§15)

- [ ] **Step 1: Open the design doc and fill §15**

Locate the §15 placeholder block (`## 15. Round 3 Results (sweep 후 채울 placeholder)`). Replace with concrete findings derived from `logs/research/bbkc_squeeze/exit_round/latest/report.md` and `summary.json`.

Mandatory subsections to fill (model the round 2 §12 style):

```markdown
## 15. Round 3 Results (<run_dir name>)

**Run**: `logs/research/bbkc_squeeze/exit_round/<timestamp>/` + `latest/`
**Coverage**: 8 cells × 3 symbols × 9 WF windows = 216 backtests, ~XXs

### 15.1 판정 결과

(Fill verdict table per symbol — paste from report.md or summarize)

### 15.2 핵심 발견 1: be_trail 컨셉이 BBKC에 살아있는가?

(Yes/No/Conditional. Cite which TF_* archetype showed signal vs F0 if any.)

### 15.3 핵심 발견 2: drop_tp의 fat-tail 캡처 효과

(Compare TR_default vs TF_default and TR_immediate vs TF_immediate.
Use mean_r_win / mfe_retention from auxiliary.json.)

### 15.4 archetype 비교 학습

(TF_wide vs TF_default — wider trail value? TF_early/TF_late — trigger location?
TF_immediate — BE plateau가 의미 있는가?)

### 15.5 부수 검증

- exit_reason TR_* 셀의 `TP` 비율 = 0% 확인 (drop_tp 동작 sanity)
- TF_late가 fixed와 거의 동일했는가 (라운드 2 TF_immediate 패턴 재현?)

### 15.6 라운드 4 후보

- (학습 기반 후속)
- ETH 한정 time_stop 정밀 sweep (라운드 2 §12.6에서 이월된 항목)

### 15.7 한 줄 요약

(라운드 3은 [핵심 결론]. 라운드 4는 [다음 액션].)
```

Replace the bracketed placeholders with the actual findings. Numbers come from `summary.json`/`report.md`/`auxiliary.json`.

- [ ] **Step 2: Commit the round-up**

```
git add Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-28_bbkc_exit_round3_design.md
git commit -m "docs(bbkc_exit): round 3 results + post-sweep findings"
```

- [ ] **Step 3: Final regression check**

```
python -m pytest tests/test_strategies/ tests/test_execution/ tests/test_scripts/ tests/_legacy/ -q --no-header
```
Expected: all pass.

- [ ] **Step 4: Merge feature branch (if used) to main**

If working on a feature branch (recommended per round 2 pattern):

```
git checkout main
git merge --no-ff feature/bbkc-exit-round3 -m "Merge feature/bbkc-exit-round3: BBKC exit round 3 (TP-fraction + drop_tp)"
git push origin main
git branch -d feature/bbkc-exit-round3
```

If working directly on `main`, skip to:

```
git push origin main
```

---

## Self-Review Checklist (run after writing all tasks)

- [ ] **Spec coverage**: Every IN bullet in spec §3 has a task
  - bbkc_squeeze.py R-unit→TP-fraction → Tasks 1-2
  - bbkc_squeeze.py drop_tp at entry → Task 3
  - registry_builder.py 8-cell grid → Task 4
  - bbkc_exit_eval.py make_strategy_factory → Task 5
  - bbkc_exit_eval.py judge() rewrite → Task 6
  - bbkc_exit_eval.py docstring + report title → Task 7
  - tests for new params, drop_tp, invariant, judge → Tasks 1-3, 6
- [ ] **No placeholders**: §15 round-up template stays as bracketed instructions only inside Task 10 (intentional — fills after sweep). All other steps have concrete code.
- [ ] **Type consistency**: `trail_be_at_tp_frac`, `trail_start_at_tp_frac`, `trail_distance_tp_frac`, `drop_tp` used identically in Tasks 1, 2, 4, 5. Cell IDs (`F0`/`TF_*`/`TR_*`) consistent across Tasks 4, 6, 8, 9.
- [ ] **Path consistency**: `logs/research/bbkc_squeeze/exit_round/<timestamp>/` reused from round 2 (no path change).

## Deferred Items (out-of-scope for this plan)

1. ETH-focused time_stop precision sweep (round 4 candidate per spec §3 OUT)
2. Per-symbol exit-mode operational policy (round 4 candidate)
3. legacy `_legacy/` further changes (round 2 changes still in effect, untouched here)
4. broker.update_tp Protocol addition (YAGNI per spec §3 OUT — drop_tp via entry-time None suffices)
