# BBKC Exit Strategy Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `be_trail`(BE+1R trailing) and `time_stop` exit primitives to `src/strategies/bbkc_squeeze.py`, fix legacy post-fill SL/TP resync bug (F2), gate legacy global ATR trailing for BBKC, and run a 12-cell × 3-symbol × 9-window grid sweep producing PROMOTE/KILL judgments.

**Architecture:** Extend the existing `BBKCSqueeze` class with `exit_mode` parameter (no new class). State for trailing/time-stop tracked in strategy-internal `_pos_meta` dict, lazily initialized from broker position state in `on_bar_fast` (no `on_fill` dependency — runtime never calls it). MFE retention enabled by adding `max_favorable` to `Position`/`TradeRecord` and tracking in `BacktestBroker.process_bar`. Legacy bug fix: add `set_trading_stop` API call after `place_order` to re-sync SL/TP based on actual fill price; on failure, keep local state matching server-side. Legacy global ATR trailing gated to skip BBKC positions so live `fixed` matches src `fixed`.

**Tech Stack:** Python 3.11+, pytest, pandas, numpy, pybit (Bybit SDK), existing `src/backtester/engine.py` + `src/execution/backtest_broker.py` infrastructure.

**Spec:** `Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-25_bbkc_exit_strategy_design.md` (commit `c4b474c`)

**Working directory for all bash commands:** `C:/Users/ceoji/Desktop/python_ibks/Crypto/Bybit_Trading/`

---

## File Structure

### New Files

| Path | Purpose |
|---|---|
| `scripts/bbkc_exit_eval.py` | Sweep runner: 12 cells × 3 symbols × 9 WF windows |
| `tests/test_strategies/test_bbkc_squeeze_exit_modes.py` | New unit tests for exit_mode extension |
| `tests/_legacy/test_trading_engine_sl_resync.py` | F2 bug-fix tests |
| `tests/_legacy/test_trading_engine_bbkc_trailing_gate.py` | BBKC-only trailing gate tests |
| `logs/research/bbkc_squeeze/exit_round/` | Result outputs (jsonl/json/md) |

### Modified Files

| Path | Change |
|---|---|
| `src/execution/broker.py` | Add `max_favorable` to `Position` |
| `src/execution/backtest_broker.py` | Add `max_favorable` to `TradeRecord`, track in `process_bar`, pass-through in `_execute_*` |
| `src/strategies/bbkc_squeeze.py` | Add 5 exit params, `_pos_meta` dict, `_manage_position`, lazy init in `on_bar_fast` |
| `src/strategies/registry_builder.py` | Add `exit_round_grid` config |
| `_legacy/api/rest_client.py` | Add `set_trading_stop` method |
| `_legacy/paper_engine/trading_engine.py` | Post-fill SL/TP resync (F2), BBKC trailing gate (3a), `_PositionInfo` fields |

---

## Phase A — MFE Tracking Foundation

### Task 1: Add `max_favorable` field to `Position`

**Files:**
- Modify: `src/execution/broker.py:6-16`
- Test: `tests/test_execution/test_broker_position.py` (new file or extend existing)

- [ ] **Step 1: Write failing test**

Create `tests/test_execution/test_broker_position.py`:

```python
"""Position dataclass max_favorable field tests."""
from src.execution.broker import Position


def test_position_has_max_favorable_field_with_default_zero():
    pos = Position(
        symbol="BTCUSDT", side="LONG", qty=0.1, entry_price=100.0,
        entry_time=1700000000000, stop_loss=95.0, take_profit=110.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    assert hasattr(pos, "max_favorable")
    assert pos.max_favorable == 0.0


def test_position_max_favorable_is_settable():
    pos = Position(
        symbol="BTCUSDT", side="LONG", qty=0.1, entry_price=100.0,
        entry_time=1700000000000, stop_loss=95.0, take_profit=110.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    pos.max_favorable = 5.5
    assert pos.max_favorable == 5.5
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_execution/test_broker_position.py -v
```
Expected: FAIL — `Position` does not accept `max_favorable` (or attribute does not exist).

- [ ] **Step 3: Modify `src/execution/broker.py:6-16`**

Add `max_favorable: float = 0.0` to `Position`. Final dataclass:

```python
@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_time: int
    stop_loss: float
    take_profit: Optional[float]
    unrealized_pnl: float
    strategy_name: str
    max_favorable: float = 0.0
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_execution/test_broker_position.py -v
```
Expected: PASS.

- [ ] **Step 5: Run full src test suite to confirm no regression**

```
pytest tests/ -x --ignore=tests/_legacy --ignore=tests/test_cli -q
```
Expected: existing tests still pass. `Position(...)` callsites use positional args followed by `max_favorable=` kwarg or rely on default (0.0).

If a test fails because `Position` is constructed positionally with 9 args (no `max_favorable`), the default makes it pass — no fix needed. If any constructor passes 10 positional args using something else, fix at that callsite.

- [ ] **Step 6: Commit**

```
git add src/execution/broker.py tests/test_execution/test_broker_position.py
git commit -m "feat(broker): add max_favorable field to Position dataclass"
```

---

### Task 2: Track `max_favorable` in `BacktestBroker.process_bar` and pass through to `TradeRecord`

**Files:**
- Modify: `src/execution/backtest_broker.py:18-31` (TradeRecord), `:117-172` (process_bar), `:200-205, :215-220` (_execute_close, _execute_exit)
- Test: extend `tests/test_execution/test_broker_position.py` or new `tests/test_execution/test_backtest_broker_mfe.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_execution/test_backtest_broker_mfe.py`:

```python
"""BacktestBroker max_favorable tracking tests."""
import pytest
from src.core.types import Bar
from src.core.config import BacktestConfig, RiskConfig
from src.execution.backtest_broker import BacktestBroker, TradeRecord


@pytest.fixture
def broker():
    cfg = BacktestConfig(initial_capital=10000.0, taker_fee_pct=0.0,
                         maker_fee_pct=0.0, slippage_pct=0.0)
    return BacktestBroker(cfg, RiskConfig())


def _bar(symbol: str, ts: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(symbol, ts, "1h", o, h, l, c, 1000.0)


def test_traderecord_has_max_favorable_field():
    tr = TradeRecord(
        symbol="BTCUSDT", strategy_name="X", side="LONG",
        entry_time=0, exit_time=1, entry_price=100.0, exit_price=110.0,
        qty=1.0, pnl=10.0, fee=0.0, exit_reason="TP", source="STRATEGY",
    )
    assert hasattr(tr, "max_favorable")
    assert tr.max_favorable == 0.0


def test_long_max_favorable_uses_bar_high(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.buy("BTCUSDT", qty=0.1, stop_loss=90.0, take_profit=120.0)
    bar1 = _bar("BTCUSDT", 2, 100, 105, 99, 102)   # fill at open=100
    broker.process_bar(bar1)
    pos = broker.get_position("BTCUSDT")
    assert pos is not None
    # high=105, entry=100 → max_favorable = 5.0
    assert pos.max_favorable == pytest.approx(5.0, rel=1e-6)

    bar2 = _bar("BTCUSDT", 3, 102, 108, 100, 107)
    broker.process_bar(bar2)
    pos = broker.get_position("BTCUSDT")
    assert pos.max_favorable == pytest.approx(8.0, rel=1e-6)  # 108 - 100 = 8


def test_short_max_favorable_uses_bar_low(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.sell("BTCUSDT", qty=0.1, stop_loss=110.0, take_profit=90.0)
    bar1 = _bar("BTCUSDT", 2, 100, 101, 95, 96)   # fill at open=100
    broker.process_bar(bar1)
    pos = broker.get_position("BTCUSDT")
    # entry=100, low=95 → max_favorable = 5.0
    assert pos.max_favorable == pytest.approx(5.0, rel=1e-6)


def test_max_favorable_passed_to_trade_record_on_exit(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.buy("BTCUSDT", qty=0.1, stop_loss=90.0, take_profit=110.0)
    bar1 = _bar("BTCUSDT", 2, 100, 108, 99, 105)
    broker.process_bar(bar1)
    bar2 = _bar("BTCUSDT", 3, 105, 112, 104, 111)  # TP=110 hits → exit
    broker.process_bar(bar2)
    trades = broker.get_trades()
    assert len(trades) == 1
    # max_favorable observed: bar1 high=108 (entry 100 → +8), bar2 high=112 (+12)
    assert trades[0].max_favorable == pytest.approx(12.0, rel=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_execution/test_backtest_broker_mfe.py -v
```
Expected: FAIL on `hasattr(tr, "max_favorable")` and on `pos.max_favorable` reads (defaults stay 0.0).

- [ ] **Step 3: Modify `src/execution/backtest_broker.py`**

3a. Update `TradeRecord` (line 18-31):

```python
@dataclass
class TradeRecord:
    symbol: str
    strategy_name: str
    side: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    fee: float
    exit_reason: str
    source: str
    max_favorable: float = 0.0
```

3b. In `process_bar` (line 117-172), after the position update block (around line 168), add MFE tracking. Replace the block from `pos = self._positions.get(bar.symbol)` (line 154) onward with:

```python
        # 3. Check intra-bar stop/TP for existing position
        pos = self._positions.get(bar.symbol)
        if pos is None:
            self._equity_curve.append(
                self._equity + sum(p.unrealized_pnl for p in self._positions.get_all())
            )
            return

        # Track max_favorable using bar high/low BEFORE checking exits
        if pos.side == "LONG":
            mfe_this_bar = bar.high - pos.entry_price
        else:
            mfe_this_bar = pos.entry_price - bar.low
        if mfe_this_bar > pos.max_favorable:
            pos.max_favorable = mfe_this_bar

        exit_price, exit_reason = self._check_exit(
            pos.side, pos.stop_loss, pos.take_profit,
            bar.open, bar.high, bar.low,
        )
        if exit_reason:
            self._execute_exit(pos, exit_price, bar.timestamp, exit_reason)
        elif self._positions.has_position(bar.symbol):
            self._positions.update_unrealized(bar.symbol, bar.close)

        self._equity_curve.append(
            self._equity + sum(p.unrealized_pnl for p in self._positions.get_all())
        )
```

3c. Update `_execute_close` (line 190-206) — append `max_favorable=pos.max_favorable` to TradeRecord:

```python
    def _execute_close(self, symbol: str, price: float, timestamp: int,
                       reason: str, source: str) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            return
        fee = pos.qty * price * self._config.taker_fee_pct
        pnl = self._calc_pnl(pos.side, pos.entry_price, price, pos.qty) - fee
        self._equity += pnl
        self._realized_pnl += pnl
        self._risk.record_trade(pnl, pnl > 0)
        self._trades.append(TradeRecord(
            symbol=symbol, strategy_name=pos.strategy_name, side=pos.side,
            entry_time=pos.entry_time, exit_time=timestamp,
            entry_price=pos.entry_price, exit_price=price,
            qty=pos.qty, pnl=pnl, fee=fee, exit_reason=reason, source=source,
            max_favorable=pos.max_favorable,
        ))
        self._positions.close(symbol)
```

3d. Same change in `_execute_exit` (line 208-221):

```python
    def _execute_exit(self, pos: Position, exit_price: float,
                      timestamp: int, exit_reason: str) -> None:
        fee = pos.qty * exit_price * self._config.taker_fee_pct
        pnl = self._calc_pnl(pos.side, pos.entry_price, exit_price, pos.qty) - fee
        self._equity += pnl
        self._realized_pnl += pnl
        self._risk.record_trade(pnl, pnl > 0)
        self._trades.append(TradeRecord(
            symbol=pos.symbol, strategy_name=pos.strategy_name, side=pos.side,
            entry_time=pos.entry_time, exit_time=timestamp,
            entry_price=pos.entry_price, exit_price=exit_price,
            qty=pos.qty, pnl=pnl, fee=fee, exit_reason=exit_reason, source="STRATEGY",
            max_favorable=pos.max_favorable,
        ))
        self._positions.close(pos.symbol)
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_execution/test_backtest_broker_mfe.py -v
```
Expected: PASS.

- [ ] **Step 5: Run full src test suite for regression**

```
pytest tests/ -x --ignore=tests/_legacy --ignore=tests/test_cli -q
```
Expected: existing tests pass.

- [ ] **Step 6: Commit**

```
git add src/execution/backtest_broker.py tests/test_execution/test_backtest_broker_mfe.py
git commit -m "feat(broker): track max_favorable in BacktestBroker and pass through to TradeRecord"
```

---

## Phase B — BBKCSqueeze Exit Mode Extension

### Task 3: Add 5 new params with backward-compatible defaults

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py:21-46` (`__init__`, `get_params`, `set_params`)
- Test: `tests/test_strategies/test_bbkc_squeeze.py` extension or new `test_bbkc_squeeze_exit_modes.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: FAIL — params not present.

- [ ] **Step 3: Modify `src/strategies/bbkc_squeeze.py:21-46`**

Replace `__init__` signature and body with:

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
        trail_be_r: float = 1.0,
        trail_start_r: float = 2.0,
        trail_distance_r: float = 0.5,
        time_stop_bars: int = 0,
    ) -> None:
        if exit_mode not in ("fixed", "be_trail"):
            raise ValueError(f"exit_mode must be 'fixed' or 'be_trail', got {exit_mode!r}")
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
        self.trail_be_r = trail_be_r
        self.trail_start_r = trail_start_r
        self.trail_distance_r = trail_distance_r
        self.time_stop_bars = time_stop_bars
        self._pos_meta: dict = {}
```

Update `get_params` (line 129-141):

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
            "trail_be_r": self.trail_be_r,
            "trail_start_r": self.trail_start_r,
            "trail_distance_r": self.trail_distance_r,
            "time_stop_bars": self.time_stop_bars,
        }
```

`set_params` (line 143-146) needs no change — it already iterates `params.items()` and `setattr`s. The new attributes are reachable through it.

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: PASS.

- [ ] **Step 5: Run existing BBKC tests to confirm no regression**

```
pytest tests/test_strategies/test_bbkc_squeeze.py -v
```
Expected: PASS (defaults preserve all existing behavior).

- [ ] **Step 6: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "feat(bbkc): add exit_mode and trail/time-stop params with backward-compatible defaults"
```

---

### Task 4: Lazy `_pos_meta` init/cleanup in `on_bar_fast`

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py:69-114` (`on_bar_fast`)
- Test: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`:

```python
import numpy as np
import pandas as pd
from src.core.types import Bar, BarSeries
from src.execution.broker import Position
from src.strategies.bbkc_squeeze import BBKCSqueeze


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
        "open": closes, "high": [c+1 for c in closes], "low": [c-1 for c in closes],
        "close": closes, "volume": [1000.0]*n,
    })
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)


def _stub_cache_with_position(s):
    """Build a cache long enough that on_bar_fast can run; values don't matter when pos exists."""
    closes = [100.0]*60
    series = _bars(closes)
    return s.prepare(series)


def test_pos_meta_lazy_init_when_position_appears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    # Initially no position, no meta
    assert "BTCUSDT" not in s._pos_meta

    # Simulate broker has a fresh LONG position (entry 100, SL 95 → R = 5)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)

    assert "BTCUSDT" in s._pos_meta
    meta = s._pos_meta["BTCUSDT"]
    assert meta["R"] == pytest.approx(5.0)
    assert meta["initial_sl"] == pytest.approx(95.0)
    assert meta["be_triggered"] is False
    assert meta["trail_active"] is False
    assert meta["bars_held"] == 1   # incremented on first bar after position observed


def test_pos_meta_cleanup_when_position_disappears():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)

    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert "BTCUSDT" in s._pos_meta

    # Position closed externally
    del broker.positions["BTCUSDT"]
    s.on_bar_fast(bar, 51, cache, broker)
    assert "BTCUSDT" not in s._pos_meta


def test_short_pos_meta_R_calculation():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)

    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "SHORT", 1.0, 100.0, 1700000000000,
        105.0, 90.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 100, 101, 99, 100, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    meta = s._pos_meta["BTCUSDT"]
    # SHORT: R = SL - entry = 105 - 100 = 5
    assert meta["R"] == pytest.approx(5.0)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_pos_meta_lazy_init_when_position_appears -v
```
Expected: FAIL — `_pos_meta` empty (no init logic yet).

- [ ] **Step 3: Modify `src/strategies/bbkc_squeeze.py:69-114`**

Replace `on_bar_fast` so it does lazy init/cleanup BEFORE the existing entry/return logic:

```python
    def on_bar_fast(self, bar: Bar, i: int, cache: IndicatorCache, broker: Broker) -> None:
        """사전 계산된 cache에서 인덱스로 조회."""
        if i < 1:
            return

        sym = bar.symbol
        pos = broker.get_position(sym)

        # ── _pos_meta lazy init / cleanup (on_fill 비의존) ──────────────────
        if pos is None and sym in self._pos_meta:
            del self._pos_meta[sym]
        if pos is not None and sym not in self._pos_meta:
            if pos.side == "LONG":
                R = pos.entry_price - pos.stop_loss
            else:
                R = pos.stop_loss - pos.entry_price
            self._pos_meta[sym] = {
                "R": R,
                "initial_sl": pos.stop_loss,
                "be_triggered": False,
                "trail_active": False,
                "bars_held": 0,
            }

        # 포지션 보유 시: bars_held 증가 + 관리 (next task adds _manage_position)
        if pos is not None:
            self._pos_meta[sym]["bars_held"] += 1
            return

        # ── 진입 로직 (기존 코드) ────────────────────────────────────────────
        bb_mid = cache.arrays["bb_mid"][i]
        rsi_val = cache.arrays["rsi"][i]
        squeeze_now = cache.arrays["squeeze_on"][i]
        squeeze_prev = cache.arrays["squeeze_on"][i - 1]

        if np.isnan(bb_mid) or np.isnan(rsi_val) or np.isnan(squeeze_now) or np.isnan(squeeze_prev):
            return

        close = bar.close

        if not (squeeze_prev >= 1.0 and squeeze_now < 1.0):
            return

        price_tp = self.tp_pct / self.leverage
        price_sl = self.sl_pct / self.leverage

        if close > bb_mid and rsi_val < self.rsi_filter:
            tp = close * (1 + price_tp)
            sl = close * (1 - price_sl)
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                           reason=f"BBKCSqueeze LONG rsi={rsi_val:.1f}")

        elif close < bb_mid and rsi_val > (100.0 - self.rsi_filter):
            tp = close * (1 - price_tp)
            sl = close * (1 + price_sl)
            qty = broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                            reason=f"BBKCSqueeze SHORT rsi={rsi_val:.1f}")
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: lazy init / cleanup / SHORT R tests PASS.

- [ ] **Step 5: Run existing BBKC tests for regression**

```
pytest tests/test_strategies/test_bbkc_squeeze.py -v
```
Expected: PASS. The `test_no_signal_when_position_exists` test in particular should still pass — when `pos is not None`, we now `return` early after bars_held increment.

- [ ] **Step 6: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "feat(bbkc): lazy _pos_meta init/cleanup in on_bar_fast (no on_fill dep)"
```

---

### Task 5: `_manage_position` skeleton + `be_trail` BE step (+1R → SL=entry)

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py` (add `_manage_position`, route from `on_bar_fast`)
- Test: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`

- [ ] **Step 1: Write failing test**

Append:

```python
def test_be_trail_long_below_1R_no_change():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # close=104 → +4 < 1R(=5) → no BE
    bar = Bar("BTCUSDT", 1700000000000, "1h", 104, 104, 104, 104, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is False


def test_be_trail_long_at_1R_triggers_BE():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # close=105 → +5 >= 1R → BE: stop = entry = 100
    bar = Bar("BTCUSDT", 1700000000000, "1h", 105, 105, 105, 105, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == [("BTCUSDT", 100.0)]
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is True


def test_be_trail_long_BE_only_triggers_once():
    s = BBKCSqueeze(exit_mode="be_trail")
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 105, 105, 105, 105, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    # Second bar still above 1R but below 2R — should NOT re-trigger BE
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 106, 106, 106, 106, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert len(broker.stop_updates) == 1   # still just the one BE call


def test_be_trail_fixed_mode_does_not_BE():
    s = BBKCSqueeze(exit_mode="fixed")  # NOT be_trail
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar = Bar("BTCUSDT", 1700000000000, "1h", 110, 110, 110, 110, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert broker.stop_updates == []   # fixed never updates stop
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_be_trail_long_at_1R_triggers_BE -v
```
Expected: FAIL — no `_manage_position` yet.

- [ ] **Step 3: Add `_manage_position` and route from `on_bar_fast`**

Replace the position-held block in `on_bar_fast` (the `if pos is not None: ... return` block):

```python
        if pos is not None:
            self._pos_meta[sym]["bars_held"] += 1
            self._manage_position(bar, pos, broker)
            return
```

Add new method `_manage_position` to `BBKCSqueeze` (insert before `on_fill`):

```python
    def _manage_position(self, bar: Bar, pos, broker: Broker) -> None:
        """포지션 보유 중 관리: be_trail BE/trailing + time_stop."""
        sym = bar.symbol
        meta = self._pos_meta[sym]
        R = meta["R"]
        if R <= 0:
            return  # invariants violated; bail out safely

        close = bar.close
        if pos.side == "LONG":
            move = close - pos.entry_price
        else:
            move = pos.entry_price - close

        # be_trail: BE step (+trail_be_r * R → SL = entry)
        if self.exit_mode == "be_trail":
            if not meta["be_triggered"] and move >= self.trail_be_r * R:
                broker.update_stop(sym, pos.entry_price)
                meta["be_triggered"] = True
            # trailing step: filled in next task
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: BE-related tests PASS (trailing tests not yet in file).

- [ ] **Step 5: Regression check**

```
pytest tests/test_strategies/test_bbkc_squeeze.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "feat(bbkc): be_trail BE step (+1R → SL=entry)"
```

---

### Task 6: `be_trail` trailing step (+2R → trail SL)

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py` (`_manage_position`)
- Test: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`

- [ ] **Step 1: Write failing test**

Append:

```python
def test_be_trail_long_at_2R_activates_trailing_with_first_sl():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_r=0.5)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 130.0, 0.0, "BBKCSqueeze", 0.0,   # tp 130 to avoid TP exit
    )
    # close=110 → +10 >= 2R(=10) → BE + trailing activated
    # trailing SL = close - 0.5*R = 110 - 2.5 = 107.5
    bar = Bar("BTCUSDT", 1700000000000, "1h", 110, 110, 110, 110, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    # MockBroker captures all update_stop calls; we expect BE first, then trailing
    assert ("BTCUSDT", 100.0) in broker.stop_updates
    assert ("BTCUSDT", 107.5) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["trail_active"] is True


def test_be_trail_long_trailing_only_ratchets_up():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_r=0.5)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 130.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # First bar: close=112 → +12 >= 2R → trail activated, SL = 112 - 2.5 = 109.5
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 112, 112, 112, 112, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    # Simulate broker now has the trailed stop
    broker.positions["BTCUSDT"].stop_loss = 109.5

    # Second bar: close=111 (lower than first) → would compute SL = 108.5, but
    # ratchet rule says SL only goes up. So no update_stop call.
    broker.stop_updates.clear()
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 111, 111, 111, 111, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.stop_updates == []

    # Third bar: close=115 → SL would be 112.5, higher than 109.5 → ratchet up
    bar3 = Bar("BTCUSDT", 1700000000002, "1h", 115, 115, 115, 115, 1000)
    s.on_bar_fast(bar3, 52, cache, broker)
    assert broker.stop_updates == [("BTCUSDT", 112.5)]


def test_be_trail_short_symmetry_at_2R():
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_r=0.5)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "SHORT", 1.0, 100.0, 1700000000000,
        105.0, 70.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # SHORT: move = entry - close. close=90 → +10 >= 2R(=5)? Wait R=5 so 2R=10.
    # close=90 → move=10 → BE + trail
    # trailing SL = close + 0.5*R = 90 + 2.5 = 92.5
    bar = Bar("BTCUSDT", 1700000000000, "1h", 90, 90, 90, 90, 1000)
    s.on_bar_fast(bar, 50, cache, broker)
    assert ("BTCUSDT", 100.0) in broker.stop_updates       # BE
    assert ("BTCUSDT", 92.5) in broker.stop_updates        # trail
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_be_trail_long_at_2R_activates_trailing_with_first_sl -v
```
Expected: FAIL — trailing step not implemented.

- [ ] **Step 3: Modify `_manage_position` to add trailing step**

Replace `_manage_position` body:

```python
    def _manage_position(self, bar: Bar, pos, broker: Broker) -> None:
        """포지션 보유 중 관리: be_trail BE/trailing + time_stop."""
        sym = bar.symbol
        meta = self._pos_meta[sym]
        R = meta["R"]
        if R <= 0:
            return

        close = bar.close
        if pos.side == "LONG":
            move = close - pos.entry_price
        else:
            move = pos.entry_price - close

        if self.exit_mode == "be_trail":
            # BE step
            if not meta["be_triggered"] and move >= self.trail_be_r * R:
                broker.update_stop(sym, pos.entry_price)
                meta["be_triggered"] = True

            # Trailing step
            if move >= self.trail_start_r * R:
                if pos.side == "LONG":
                    new_sl = close - self.trail_distance_r * R
                else:
                    new_sl = close + self.trail_distance_r * R

                if not meta["trail_active"]:
                    broker.update_stop(sym, new_sl)
                    meta["trail_active"] = True
                else:
                    # Ratchet: LONG only up, SHORT only down
                    if pos.side == "LONG" and new_sl > pos.stop_loss:
                        broker.update_stop(sym, new_sl)
                    elif pos.side == "SHORT" and new_sl < pos.stop_loss:
                        broker.update_stop(sym, new_sl)
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: PASS for all be_trail tests so far.

- [ ] **Step 5: Regression check**

```
pytest tests/test_strategies/test_bbkc_squeeze.py -v
pytest tests/test_execution/ -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "feat(bbkc): be_trail trailing step (+2R activate, ratchet up SL)"
```

---

### Task 7: `time_stop` fallback

**Files:**
- Modify: `src/strategies/bbkc_squeeze.py` (`_manage_position`)
- Test: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`

- [ ] **Step 1: Write failing test**

Append:

```python
def test_time_stop_zero_does_nothing():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=0)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    for k in range(100):
        bar = Bar("BTCUSDT", 1700000000000+k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50+k, cache, broker)
    assert broker.closes == []


def test_time_stop_triggers_at_N_bars_held():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=3)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # bars_held increments to 1, 2, 3 → at 3 should fire close
    for k in range(3):
        bar = Bar("BTCUSDT", 1700000000000+k, "1h", 100, 100, 100, 100, 1000)
        s.on_bar_fast(bar, 50+k, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]


def test_time_stop_works_with_be_trail():
    s = BBKCSqueeze(exit_mode="be_trail", time_stop_bars=2)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    # Below 1R both bars → no BE, just bars_held increments
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 102, 102, 102, 102, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 103, 103, 103, 103, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.closes == [("BTCUSDT", "time_stop")]


def test_time_stop_skipped_if_position_already_gone():
    s = BBKCSqueeze(exit_mode="fixed", time_stop_bars=2)
    broker = _MockBroker()
    cache = _stub_cache_with_position(s)
    broker.positions["BTCUSDT"] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000,
        95.0, 110.0, 0.0, "BBKCSqueeze", 0.0,
    )
    bar1 = Bar("BTCUSDT", 1700000000000, "1h", 100, 100, 100, 100, 1000)
    s.on_bar_fast(bar1, 50, cache, broker)
    # SL hit externally → broker removes position
    del broker.positions["BTCUSDT"]
    bar2 = Bar("BTCUSDT", 1700000000001, "1h", 100, 100, 100, 100, 1000)
    s.on_bar_fast(bar2, 51, cache, broker)
    assert broker.closes == []
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_time_stop_triggers_at_N_bars_held -v
```
Expected: FAIL.

- [ ] **Step 3: Modify `_manage_position` to add time_stop**

Append the time_stop block at the end of `_manage_position`:

```python
        # time_stop fallback (직교 with exit_mode)
        if self.time_stop_bars > 0 and meta["bars_held"] >= self.time_stop_bars:
            broker.close(sym, reason="time_stop")
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py -v
```
Expected: PASS.

- [ ] **Step 5: Regression check**

```
pytest tests/test_strategies/ tests/test_execution/ -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```
git add src/strategies/bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "feat(bbkc): time_stop fallback after N 1h bars held"
```

---

### Task 8: Strategy-level integration smoke (entry → SL/TP set, then on_bar_fast manages)

**Files:**
- Test: `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`

- [ ] **Step 1: Add integration smoke test**

Append:

```python
def test_be_trail_full_lifecycle_smoke():
    """Smoke: build a series that triggers entry, then walks price up to BE+trail."""
    s = BBKCSqueeze(exit_mode="be_trail", trail_distance_r=0.5)
    broker = _MockBroker()
    # Build a series that ends with a squeeze release LONG signal (close > bb_mid).
    # Steady prices → squeeze ON; then big up move → release.
    closes = [100.0]*40 + list(np.linspace(100, 108, 10))   # length 50
    series = _bars(closes)
    cache = s.prepare(series)

    # i=49: signal expected (close above bb_mid, RSI not overbought, squeeze just released)
    last = closes[-1]
    bar_signal = Bar("BTCUSDT", 1700000000000, "1h", last, last+0.5, last-0.5, last, 1000)
    s.on_bar_fast(bar_signal, 49, cache, broker)
    # Either a buy was emitted (squeeze release detected) OR not — the smoke focuses on
    # the management path. Simulate that broker now has a position regardless:
    if broker.buys:
        sym, qty, sl, tp, _ = broker.buys[-1]
        broker.positions["BTCUSDT"] = Position(
            "BTCUSDT", "LONG", qty, last, 1700000000000, sl, tp, 0.0, "BBKCSqueeze", 0.0,
        )

    # If no entry happened (tight squeeze threshold), fabricate one for management test:
    if "BTCUSDT" not in broker.positions:
        broker.positions["BTCUSDT"] = Position(
            "BTCUSDT", "LONG", 1.0, 100.0, 1700000000000, 95.0, 130.0, 0.0, "BBKCSqueeze", 0.0,
        )

    # Walk to +1R then +2R
    bar_be = Bar("BTCUSDT", 1700000000001, "1h", 105, 105, 105, 105, 1000)
    s.on_bar_fast(bar_be, 50, cache, broker)
    assert ("BTCUSDT", broker.positions["BTCUSDT"].entry_price) in broker.stop_updates
    assert s._pos_meta["BTCUSDT"]["be_triggered"] is True

    bar_trail = Bar("BTCUSDT", 1700000000002, "1h", 115, 115, 115, 115, 1000)
    s.on_bar_fast(bar_trail, 51, cache, broker)
    assert s._pos_meta["BTCUSDT"]["trail_active"] is True
```

- [ ] **Step 2: Run test**

```
pytest tests/test_strategies/test_bbkc_squeeze_exit_modes.py::test_be_trail_full_lifecycle_smoke -v
```
Expected: PASS (no implementation change needed; this just exercises the integrated path).

- [ ] **Step 3: Commit**

```
git add tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "test(bbkc): be_trail full lifecycle smoke test"
```

---

## Phase C — Registry + Sweep Infrastructure

### Task 9: Add `exit_round_grid` to `registry_builder.py`

**Files:**
- Modify: `src/strategies/registry_builder.py`
- Test: `tests/test_strategies/test_registry_builder.py` (extend or add new file)

- [ ] **Step 1: Inspect current grid structure**

```
pytest tests/test_strategies/test_registry_builder.py -v
```

Read `src/strategies/registry_builder.py:56-68` (or wherever `STRATEGY_CONFIGS["BBKCSqueeze"]` is defined) to see the dict shape. Verify `coarse_grid` exists and that `exit_round_grid` will sit alongside.

- [ ] **Step 2: Write test**

Create `tests/test_strategies/test_registry_builder_exit_grid.py`:

```python
"""exit_round_grid in registry_builder."""
from src.strategies.registry_builder import STRATEGY_CONFIGS


def test_bbkc_has_exit_round_grid():
    cfg = STRATEGY_CONFIGS["BBKCSqueeze"]
    assert "exit_round_grid" in cfg


def test_exit_round_grid_has_12_cells():
    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cells = list(grid)
    assert len(cells) == 12
    # Verify each cell is a dict with expected keys
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
    # 0.5/1.0 × {0,24,48,72} = 8
    distances = sorted({c["trail_distance_r"] for c in cells})
    times = sorted({c["time_stop_bars"] for c in cells})
    assert distances == [0.5, 1.0]
    assert times == [0, 24, 48, 72]
```

- [ ] **Step 3: Run to verify fail**

```
pytest tests/test_strategies/test_registry_builder_exit_grid.py -v
```
Expected: FAIL.

- [ ] **Step 4: Add `exit_round_grid` to `STRATEGY_CONFIGS["BBKCSqueeze"]`**

In `src/strategies/registry_builder.py`, find `STRATEGY_CONFIGS["BBKCSqueeze"]` and add a new key `exit_round_grid`:

```python
"exit_round_grid": [
    {"cell_id": "F0",     "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 0},
    {"cell_id": "F24",    "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 24},
    {"cell_id": "F48",    "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 48},
    {"cell_id": "F72",    "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 72},
    {"cell_id": "T05_0",  "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 0},
    {"cell_id": "T05_24", "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 24},
    {"cell_id": "T05_48", "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 48},
    {"cell_id": "T05_72", "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 72},
    {"cell_id": "T10_0",  "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 0},
    {"cell_id": "T10_24", "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 24},
    {"cell_id": "T10_48", "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 48},
    {"cell_id": "T10_72", "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 72},
],
```

- [ ] **Step 5: Run test to verify pass**

```
pytest tests/test_strategies/test_registry_builder_exit_grid.py -v
```
Expected: PASS.

- [ ] **Step 6: Regression check**

```
pytest tests/test_strategies/test_registry_builder.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```
git add src/strategies/registry_builder.py tests/test_strategies/test_registry_builder_exit_grid.py
git commit -m "feat(registry): add exit_round_grid (12 cells) for BBKCSqueeze"
```

---

### Task 10: Sweep runner skeleton — `scripts/bbkc_exit_eval.py` (load data, build strategy, single-window run)

**Files:**
- Create: `scripts/bbkc_exit_eval.py`

- [ ] **Step 1: Create the file**

`scripts/bbkc_exit_eval.py`:

```python
"""BBKC Exit Round 2 evaluation runner.

Sweeps 12 exit cells × BIGTHREE × 9 walk-forward windows.
Output: logs/research/bbkc_squeeze/exit_round/{wf_results.jsonl, summary.json,
        auxiliary.json, report.md}

Usage:
    python -m scripts.bbkc_exit_eval --smoke         # 1 cell × 1 symbol × 1 window
    python -m scripts.bbkc_exit_eval --full          # all 324 runs
"""
from __future__ import annotations
import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.config import BacktestConfig, RiskConfig
from src.core.types import BarSeries
from src.execution.backtest_broker import BacktestBroker, TradeRecord
from src.strategies.bbkc_squeeze import BBKCSqueeze
from src.strategies.registry_builder import STRATEGY_CONFIGS
from src.backtester.engine import run_backtest   # adjust import if name differs


SYMBOLS = ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
DATA_START = "2024-03-01"
DATA_END = "2026-04-30"
OUTPUT_DIR = Path("logs/research/bbkc_squeeze/exit_round")


logger = logging.getLogger("bbkc_exit_eval")


@dataclass
class WindowResult:
    cell_id: str
    symbol: str
    window_idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    oos_pnl: float
    oos_trades: int
    oos_winrate: float
    oos_max_dd: float
    oos_r_per_trade: float


def load_series(symbol: str, start: str, end: str) -> BarSeries:
    """Load 1h OHLCV from db/bybit_data.db."""
    # TODO: actual DB load — placeholder until wired to existing data loader
    raise NotImplementedError("Wire to existing data loader (see scripts/bbkc_universe_eval.py)")


def make_strategy(cell: dict[str, Any]) -> BBKCSqueeze:
    kwargs = dict(
        bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
        atr_period=14, rsi_period=14, rsi_filter=70.0,
        tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h",
        exit_mode=cell["exit_mode"],
        trail_be_r=1.0,
        trail_start_r=2.0,
        time_stop_bars=cell["time_stop_bars"],
    )
    if cell["trail_distance_r"] is not None:
        kwargs["trail_distance_r"] = cell["trail_distance_r"]
    return BBKCSqueeze(**kwargs)


def run_one_window(cell: dict, symbol: str, series: BarSeries, oos_start_ts: int,
                   oos_end_ts: int) -> tuple[list[TradeRecord], list[float]]:
    """Run a single OOS window and return (trades, equity_curve)."""
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        taker_fee_pct=0.00055,
        maker_fee_pct=0.0002,
        slippage_pct=0.0003,
    )
    broker = BacktestBroker(cfg, RiskConfig())
    strat = make_strategy(cell)
    # Subset series to OOS window (let prepare see full history; engine only acts in-range)
    run_backtest(strat, series, broker, oos_start_ts, oos_end_ts)
    return broker.get_trades(), broker.get_equity_curve()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true", help="1 cell × 1 symbol × 1 window")
    p.add_argument("--full", action="store_true", help="all 324 runs")
    p.add_argument("--cell", default=None, help="run only this cell_id (e.g. F0)")
    p.add_argument("--symbol", default=None, help="run only this symbol")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cells = [c for c in grid if args.cell is None or c["cell_id"] == args.cell]
    symbols = SYMBOLS if args.symbol is None else [args.symbol]
    if args.smoke:
        cells = cells[:1]
        symbols = symbols[:1]

    logger.info("running %d cells × %d symbols", len(cells), len(symbols))
    logger.info("cells: %s", [c["cell_id"] for c in cells])
    logger.info("symbols: %s", symbols)
    # Window/result logic added in next tasks


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke verify CLI parses**

```
python -m scripts.bbkc_exit_eval --smoke
```
Expected: prints "running 1 cells × 1 symbols" and exits cleanly. (No data load yet — that's Task 11.)

- [ ] **Step 3: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): bbkc_exit_eval runner skeleton (cell/symbol selection, no data load yet)"
```

---

### Task 11: Wire `load_series` to existing data loader

**Files:**
- Modify: `scripts/bbkc_exit_eval.py`

- [ ] **Step 1: Inspect the existing pattern**

Read how `scripts/bbkc_universe_eval.py` loads OHLCV (typically `from db.db_manager import DBManager` or similar). Identify the function/class that returns a `BarSeries` for `(symbol, timeframe="1h", start, end)`.

```
grep -n "load\|BarSeries\|read_ohlcv\|db.fetch" scripts/bbkc_universe_eval.py
```

Use the discovered call signature in `load_series`.

- [ ] **Step 2: Implement `load_series`**

Replace the `NotImplementedError` body with a call matching the discovered loader. Example (adjust to actual API):

```python
def load_series(symbol: str, start: str, end: str) -> BarSeries:
    from src.data_manager.feed import load_bar_series  # adjust to actual location
    return load_bar_series(symbol=symbol, timeframe="1h", start=start, end=end)
```

If the loader returns a DataFrame, wrap it: `BarSeries(symbol=symbol, timeframe="1h", bars=df)`.

- [ ] **Step 3: Smoke test the loader**

```
python -c "from scripts.bbkc_exit_eval import load_series; s = load_series('BTCUSDT', '2024-03-01', '2024-04-01'); print(len(s.bars))"
```
Expected: prints number of 1h bars (~720 for 30 days).

- [ ] **Step 4: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): wire bbkc_exit_eval load_series to existing data loader"
```

---

### Task 12: Walk-forward windows + per-window run

**Files:**
- Modify: `scripts/bbkc_exit_eval.py`

- [ ] **Step 1: Add window builder**

Add to `scripts/bbkc_exit_eval.py` near top:

```python
from datetime import timedelta


def build_wf_windows(
    data_start: str, data_end: str,
    is_months: int = 6, oos_months: int = 2, step_months: int = 2,
    n_windows: int = 9,
) -> list[tuple[str, str, str, str]]:
    """Return list of (is_start, is_end, oos_start, oos_end) ISO date strings.

    First IS window starts at data_start. Each subsequent window steps forward
    by step_months. OOS immediately follows IS. Last OOS must end ≤ data_end.
    """
    fmt = "%Y-%m-%d"
    start = datetime.strptime(data_start, fmt).replace(tzinfo=timezone.utc)
    out: list[tuple[str, str, str, str]] = []
    for k in range(n_windows):
        is_s = start + _months(step_months * k)
        is_e = is_s + _months(is_months)
        oos_s = is_e
        oos_e = oos_s + _months(oos_months)
        out.append((is_s.strftime(fmt), is_e.strftime(fmt),
                    oos_s.strftime(fmt), oos_e.strftime(fmt)))
    return out


def _months(n: int) -> timedelta:
    # Approximate 30 days/month — fine for window definitions
    return timedelta(days=n * 30)
```

- [ ] **Step 2: Replace `main()` with full sweep loop**

```python
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    grid = STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]
    cells = [c for c in grid if args.cell is None or c["cell_id"] == args.cell]
    symbols = SYMBOLS if args.symbol is None else [args.symbol]
    windows = build_wf_windows(DATA_START, DATA_END)
    if args.smoke:
        cells = cells[:1]
        symbols = symbols[:1]
        windows = windows[:1]

    out_jsonl = OUTPUT_DIR / "wf_results.jsonl"
    series_cache: dict[str, BarSeries] = {}

    with out_jsonl.open("w", encoding="utf-8") as fout:
        for sym in symbols:
            if sym not in series_cache:
                series_cache[sym] = load_series(sym, DATA_START, DATA_END)
            series = series_cache[sym]
            for cell in cells:
                for w_idx, (is_s, is_e, oos_s, oos_e) in enumerate(windows):
                    logger.info("cell=%s sym=%s window=%d oos=%s..%s",
                                cell["cell_id"], sym, w_idx, oos_s, oos_e)
                    oos_start_ts = int(datetime.strptime(oos_s, "%Y-%m-%d")
                                       .replace(tzinfo=timezone.utc).timestamp() * 1000)
                    oos_end_ts = int(datetime.strptime(oos_e, "%Y-%m-%d")
                                     .replace(tzinfo=timezone.utc).timestamp() * 1000)
                    trades, equity = run_one_window(cell, sym, series,
                                                    oos_start_ts, oos_end_ts)
                    metrics = compute_window_metrics(trades, equity, cell, sym,
                                                     w_idx, is_s, is_e, oos_s, oos_e)
                    fout.write(json.dumps(asdict(metrics)) + "\n")
                    fout.flush()

    logger.info("wrote %s", out_jsonl)
```

- [ ] **Step 3: Add `compute_window_metrics`**

```python
def compute_window_metrics(
    trades: list[TradeRecord], equity: list[float], cell: dict, symbol: str,
    w_idx: int, is_s: str, is_e: str, oos_s: str, oos_e: str,
) -> WindowResult:
    if not trades:
        return WindowResult(
            cell_id=cell["cell_id"], symbol=symbol, window_idx=w_idx,
            is_start=is_s, is_end=is_e, oos_start=oos_s, oos_end=oos_e,
            oos_pnl=0.0, oos_trades=0, oos_winrate=0.0,
            oos_max_dd=0.0, oos_r_per_trade=0.0,
        )

    pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    winrate = wins / len(trades)

    # Max drawdown from equity curve
    peak = -float("inf")
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    # R per trade: pnl / (qty * (entry - sl))
    rs = []
    for t in trades:
        # We need initial SL distance — approximate from entry × sl_pct/leverage = entry × 0.07/3
        risk = t.entry_price * 0.07 / 3 * t.qty
        if risk > 0:
            rs.append(t.pnl / risk)
    r_per_trade = sum(rs) / len(rs) if rs else 0.0

    return WindowResult(
        cell_id=cell["cell_id"], symbol=symbol, window_idx=w_idx,
        is_start=is_s, is_end=is_e, oos_start=oos_s, oos_end=oos_e,
        oos_pnl=pnl, oos_trades=len(trades), oos_winrate=winrate,
        oos_max_dd=max_dd, oos_r_per_trade=r_per_trade,
    )
```

- [ ] **Step 4: Smoke run**

```
python -m scripts.bbkc_exit_eval --smoke
```
Expected: produces `logs/research/bbkc_squeeze/exit_round/wf_results.jsonl` with 1 line.

```
cat logs/research/bbkc_squeeze/exit_round/wf_results.jsonl
```
Verify it parses as JSON, has expected keys.

- [ ] **Step 5: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): bbkc_exit_eval WF window logic + per-window metrics"
```

---

### Task 13: Auxiliary metrics + summary aggregation

**Files:**
- Modify: `scripts/bbkc_exit_eval.py`

- [ ] **Step 1: Add auxiliary metric calculator**

Add to `scripts/bbkc_exit_eval.py`:

```python
def compute_auxiliary(trades: list[TradeRecord]) -> dict[str, Any]:
    """Per-window auxiliary metrics (used for interpretation, not PROMOTE/KILL)."""
    if not trades:
        return {
            "exit_reason_dist": {},
            "mean_r_win": 0.0,
            "mean_r_loss": 0.0,
            "mfe_retention": 0.0,
            "mean_holding_bars": 0.0,
        }

    # Exit reason distribution
    counts: dict[str, int] = {}
    for t in trades:
        counts[t.exit_reason] = counts.get(t.exit_reason, 0) + 1
    total = len(trades)
    dist = {k: v / total for k, v in counts.items()}

    # R per win / per loss
    sl_pct, leverage = 0.07, 3
    win_rs, loss_rs = [], []
    retentions = []
    holdings = []
    for t in trades:
        risk = t.entry_price * sl_pct / leverage * t.qty
        if risk <= 0:
            continue
        r = t.pnl / risk
        if t.pnl > 0:
            win_rs.append(r)
        else:
            loss_rs.append(r)
        # MFE retention
        max_fav_pnl = t.max_favorable * t.qty   # max_favorable already absolute distance
        if max_fav_pnl > 0:
            retentions.append(t.pnl / max_fav_pnl)
        # Holding bars (1h timeframe → ms → bar count)
        holdings.append((t.exit_time - t.entry_time) / (60 * 60 * 1000))

    return {
        "exit_reason_dist": dist,
        "mean_r_win": sum(win_rs) / len(win_rs) if win_rs else 0.0,
        "mean_r_loss": sum(loss_rs) / len(loss_rs) if loss_rs else 0.0,
        "mfe_retention": sum(retentions) / len(retentions) if retentions else 0.0,
        "mean_holding_bars": sum(holdings) / len(holdings) if holdings else 0.0,
    }
```

- [ ] **Step 2: Wire auxiliary into main loop**

Inside `main()`, alongside `wf_results.jsonl`, also accumulate auxiliary stats per (cell, symbol):

```python
    aux_buckets: dict[tuple[str, str], list[dict]] = {}

    with out_jsonl.open("w", encoding="utf-8") as fout:
        for sym in symbols:
            if sym not in series_cache:
                series_cache[sym] = load_series(sym, DATA_START, DATA_END)
            series = series_cache[sym]
            for cell in cells:
                for w_idx, (is_s, is_e, oos_s, oos_e) in enumerate(windows):
                    logger.info("cell=%s sym=%s window=%d oos=%s..%s",
                                cell["cell_id"], sym, w_idx, oos_s, oos_e)
                    oos_start_ts = int(datetime.strptime(oos_s, "%Y-%m-%d")
                                       .replace(tzinfo=timezone.utc).timestamp() * 1000)
                    oos_end_ts = int(datetime.strptime(oos_e, "%Y-%m-%d")
                                     .replace(tzinfo=timezone.utc).timestamp() * 1000)
                    trades, equity = run_one_window(cell, sym, series,
                                                    oos_start_ts, oos_end_ts)
                    metrics = compute_window_metrics(trades, equity, cell, sym,
                                                     w_idx, is_s, is_e, oos_s, oos_e)
                    fout.write(json.dumps(asdict(metrics)) + "\n")
                    fout.flush()
                    aux = compute_auxiliary(trades)
                    aux_buckets.setdefault((cell["cell_id"], sym), []).append(aux)

    # Aggregate auxiliary across windows
    auxiliary = {}
    for (cell_id, sym), lst in aux_buckets.items():
        avg = {
            "exit_reason_dist": _avg_dist([d["exit_reason_dist"] for d in lst]),
            "mean_r_win": sum(d["mean_r_win"] for d in lst) / len(lst),
            "mean_r_loss": sum(d["mean_r_loss"] for d in lst) / len(lst),
            "mfe_retention": sum(d["mfe_retention"] for d in lst) / len(lst),
            "mean_holding_bars": sum(d["mean_holding_bars"] for d in lst) / len(lst),
        }
        auxiliary.setdefault(cell_id, {})[sym] = avg

    (OUTPUT_DIR / "auxiliary.json").write_text(json.dumps(auxiliary, indent=2))


def _avg_dist(dists: list[dict[str, float]]) -> dict[str, float]:
    keys = set()
    for d in dists:
        keys.update(d.keys())
    out = {}
    for k in keys:
        out[k] = sum(d.get(k, 0.0) for d in dists) / len(dists)
    return out
```

- [ ] **Step 3: Smoke run**

```
python -m scripts.bbkc_exit_eval --smoke
```
Expected: `auxiliary.json` exists with 1 cell × 1 symbol entry.

```
cat logs/research/bbkc_squeeze/exit_round/auxiliary.json
```

- [ ] **Step 4: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): bbkc_exit_eval auxiliary metrics (exit dist, R/win, R/loss, MFE, hold)"
```

---

### Task 14: Summary aggregation + PROMOTE/KILL judgment

**Files:**
- Modify: `scripts/bbkc_exit_eval.py`

- [ ] **Step 1: Add summary builder**

```python
def build_summary(jsonl_path: Path) -> dict:
    """Aggregate per-window results into per-(cell, symbol) summary."""
    rows: list[dict] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))

    by_pair: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_pair.setdefault((r["cell_id"], r["symbol"]), []).append(r)

    summary = {}
    for (cell_id, sym), windows in by_pair.items():
        oos_pos = sum(1 for w in windows if w["oos_pnl"] > 0)
        mean_r = sum(w["oos_r_per_trade"] for w in windows) / len(windows)
        max_dd = max(w["oos_max_dd"] for w in windows)
        n_trades = sum(w["oos_trades"] for w in windows)
        mean_pnl = sum(w["oos_pnl"] for w in windows) / len(windows)
        summary.setdefault(cell_id, {})[sym] = {
            "wf_oos_positive": oos_pos,
            "wf_total": len(windows),
            "mean_r_per_trade": mean_r,
            "max_dd": max_dd,
            "trade_count": n_trades,
            "mean_oos_pnl": mean_pnl,
        }
    return summary


def judge(summary: dict) -> dict:
    """Apply PROMOTE/STRONG_PROMOTE/KILL/WARNING per (cell, symbol)."""
    out = {}
    # Find F0 baseline per symbol
    f0 = summary.get("F0", {})
    for cell_id, by_sym in summary.items():
        for sym, m in by_sym.items():
            base = f0.get(sym)
            verdict = "KILL"
            warning = False
            if base:
                if m["trade_count"] < base["trade_count"] * 0.5:
                    warning = True
                if m["wf_oos_positive"] >= 7 and m["mean_r_per_trade"] >= base["mean_r_per_trade"]:
                    verdict = "PROMOTE"
                    if m["max_dd"] <= base["max_dd"]:
                        verdict = "STRONG_PROMOTE"
            out.setdefault(cell_id, {})[sym] = {**m, "verdict": verdict, "warning": warning}
    return out
```

- [ ] **Step 2: Wire at end of `main()`**

Add after `(OUTPUT_DIR / "auxiliary.json").write_text(...)`:

```python
    summary_raw = build_summary(out_jsonl)
    summary_judged = judge(summary_raw)
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary_judged, indent=2))
    logger.info("wrote %s", OUTPUT_DIR / "summary.json")
```

- [ ] **Step 3: Smoke run**

```
python -m scripts.bbkc_exit_eval --smoke
cat logs/research/bbkc_squeeze/exit_round/summary.json
```

- [ ] **Step 4: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): bbkc_exit_eval summary + PROMOTE/KILL judgment"
```

---

### Task 15: Markdown report generator

**Files:**
- Modify: `scripts/bbkc_exit_eval.py`

- [ ] **Step 1: Add report builder**

```python
def build_report(summary_judged: dict, auxiliary: dict, out_path: Path) -> None:
    """Generate human-readable Markdown report."""
    lines = ["# BBKC Exit Round 2 — Sweep Report",
             f"\nGenerated: {datetime.now(timezone.utc).isoformat()}\n"]
    lines.append("## Per-Symbol Results\n")

    for sym in SYMBOLS:
        lines.append(f"### {sym}\n")
        lines.append("| Cell | WF OOS+/9 | R/trade | Max DD | Trades | Verdict |")
        lines.append("|---|---|---|---|---|---|")
        for cell_id, by_sym in sorted(summary_judged.items()):
            m = by_sym.get(sym)
            if not m:
                continue
            verdict = m["verdict"]
            if m.get("warning"):
                verdict += " ⚠"
            lines.append(
                f"| {cell_id} | {m['wf_oos_positive']}/{m['wf_total']} | "
                f"{m['mean_r_per_trade']:.3f} | {m['max_dd']*100:.2f}% | "
                f"{m['trade_count']} | {verdict} |"
            )
        lines.append("")

    lines.append("## Auxiliary Metrics (per cell × symbol)\n")
    for cell_id, by_sym in sorted(auxiliary.items()):
        lines.append(f"### {cell_id}\n")
        for sym, aux in by_sym.items():
            lines.append(f"**{sym}**")
            lines.append(f"- Exit reasons: {aux['exit_reason_dist']}")
            lines.append(f"- Mean R/win: {aux['mean_r_win']:.3f}, R/loss: {aux['mean_r_loss']:.3f}")
            lines.append(f"- MFE retention: {aux['mfe_retention']:.3f}")
            lines.append(f"- Mean holding bars: {aux['mean_holding_bars']:.1f}")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 2: Wire at end of `main()`**

```python
    auxiliary = json.loads((OUTPUT_DIR / "auxiliary.json").read_text())
    build_report(summary_judged, auxiliary, OUTPUT_DIR / "report.md")
    logger.info("wrote %s", OUTPUT_DIR / "report.md")
```

- [ ] **Step 3: Smoke run**

```
python -m scripts.bbkc_exit_eval --smoke
cat logs/research/bbkc_squeeze/exit_round/report.md
```

- [ ] **Step 4: Commit**

```
git add scripts/bbkc_exit_eval.py
git commit -m "feat(scripts): bbkc_exit_eval Markdown report generator"
```

---

## Phase D — Legacy F2 Bug Fix + BBKC Trailing Gate

### Task 16: Add `set_trading_stop` to legacy `rest_client.py`

**Files:**
- Modify: `_legacy/api/rest_client.py`
- Test: `tests/_legacy/test_trading_engine_sl_resync.py` (mock-based)

- [ ] **Step 1: Inspect existing patterns**

Read `_legacy/api/rest_client.py` to find the patterns used for `place_order`, signing, error handling. Identify whether requests use `pybit` or raw `requests`.

```
grep -n "def place_order\|self._session\|self._http\|requests\." _legacy/api/rest_client.py | head -40
```

- [ ] **Step 2: Add the method**

Append to `_legacy/api/rest_client.py` (within the class):

```python
    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        position_idx: int = 0,
    ) -> dict:
        """Update Bybit position's SL/TP via /v5/position/trading-stop.

        Args:
            symbol: USDT perpetual symbol
            stop_loss: new SL price (None to leave unchanged)
            take_profit: new TP price (None to leave unchanged)
            position_idx: 0=one-way, 1=hedge buy, 2=hedge sell

        Returns:
            API response dict.

        Raises:
            Exception subclass (network or bybit error) — caller logs and proceeds.
        """
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "tpslMode": "Full",
            "positionIdx": position_idx,
        }
        if stop_loss is not None:
            params["stopLoss"] = f"{stop_loss:.2f}"
        if take_profit is not None:
            params["takeProfit"] = f"{take_profit:.2f}"
        # If pybit available:
        try:
            return self._session.set_trading_stop(**params)
        except AttributeError:
            # Fallback to raw signed POST
            return self._signed_post("/v5/position/trading-stop", params)
```

If the existing client uses `requests` directly (no `_session`), implement only the `_signed_post` path.

- [ ] **Step 3: Write mock-based unit test**

Create `tests/_legacy/test_trading_engine_sl_resync.py` (placeholder for now — full test in next task):

```python
"""Legacy F2 SL/TP resync tests (using mocks)."""
import pytest
from unittest.mock import MagicMock


def test_set_trading_stop_called_with_actual_entry(monkeypatch):
    pytest.importorskip("_legacy.api.rest_client")
    from _legacy.api.rest_client import BybitRestClient
    rest = BybitRestClient.__new__(BybitRestClient)  # bypass __init__
    rest._session = MagicMock()
    rest.set_trading_stop("BTCUSDT", stop_loss=99.0, take_profit=101.0)
    rest._session.set_trading_stop.assert_called_once()
    call = rest._session.set_trading_stop.call_args
    assert call.kwargs["symbol"] == "BTCUSDT"
    assert call.kwargs["stopLoss"] == "99.00"
    assert call.kwargs["takeProfit"] == "101.00"
```

- [ ] **Step 4: Run test**

```
pytest tests/_legacy/test_trading_engine_sl_resync.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add _legacy/api/rest_client.py tests/_legacy/test_trading_engine_sl_resync.py
git commit -m "feat(_legacy/api): add set_trading_stop method on rest_client"
```

---

### Task 17: F2 — post-fill SL/TP resync in `trading_engine.py`

**Files:**
- Modify: `_legacy/paper_engine/trading_engine.py:1064-1071` (and around) + `_PositionInfo` dataclass
- Test: `tests/_legacy/test_trading_engine_sl_resync.py`

- [ ] **Step 1: Find `_PositionInfo` definition**

```
grep -n "class _PositionInfo\|@dataclass" _legacy/paper_engine/trading_engine.py | head
```

- [ ] **Step 2: Write failing test for the fix**

Append to `tests/_legacy/test_trading_engine_sl_resync.py`:

```python
def test_post_fill_resync_recalculates_sl_tp_from_avg_price(monkeypatch):
    pytest.importorskip("_legacy.paper_engine.trading_engine")
    from _legacy.paper_engine.trading_engine import TradingEngine

    # Build a TradingEngine with stubbed dependencies
    eng = TradingEngine.__new__(TradingEngine)
    eng.rest_client = MagicMock()
    eng.rest_client.place_order.return_value = {
        "result": {"avgPrice": "100.00", "qty": "1.0", "orderId": "x"},
        "retCode": 0,
    }
    eng.rest_client.set_trading_stop = MagicMock(return_value={"retCode": 0})
    # ... (real implementation requires more stubs; see existing test patterns)
    # Assert: after _process_signal completes, set_trading_stop was called with
    #         SL = 100 * (1 - 0.07/3) = 97.6667
    #         TP = 100 * (1 + 0.06/3) = 102.00
    pass   # Detailed assertion built in step 4; keep skeleton here
```

This test will be expanded in step 4 once we know the exact stub surface needed.

- [ ] **Step 3: Modify `_PositionInfo`**

Add fields:

```python
desired_stop_loss: float = 0.0
desired_take_profit: Optional[float] = None
sl_tp_resync_failed: bool = False
```

- [ ] **Step 4: Modify `_process_signal` post-fill block (around line 1064-1071)**

After `entry_price_actual = avgPrice` is set, insert:

```python
            # F2: post-fill SL/TP re-sync based on actual fill price.
            # Recalculate using the strategy's exit configuration.
            if signal.strategy_name == "BBKCSqueeze":
                # BBKC fixed: pct/leverage based on avgPrice
                sl_pct = settings.bbkc_sl_pct  # adjust attr name to actual
                tp_pct = settings.bbkc_tp_pct
                lev = settings.leverage
                if signal.direction == "LONG":
                    sl_actual = entry_price_actual * (1 - sl_pct / lev)
                    tp_actual = entry_price_actual * (1 + tp_pct / lev)
                else:
                    sl_actual = entry_price_actual * (1 + sl_pct / lev)
                    tp_actual = entry_price_actual * (1 - tp_pct / lev)
            else:
                # Other strategies: scale signal SL/TP by ratio of actual/signal entry
                ratio = entry_price_actual / signal.entry_price
                sl_actual = signal.stop_loss * ratio
                tp_actual = signal.take_profit * ratio if signal.take_profit else None

            # Always call set_trading_stop (idempotent)
            try:
                self.rest_client.set_trading_stop(
                    symbol=symbol,
                    stop_loss=sl_actual,
                    take_profit=tp_actual,
                    position_idx=close_pos_idx if signal.direction == "LONG" else 2,
                )
                # Success: update local to match server
                pos_info_kwargs["stop_loss"] = sl_actual
                pos_info_kwargs["take_profit"] = tp_actual
                pos_info_kwargs["sl_tp_resync_failed"] = False
                pos_info_kwargs["desired_stop_loss"] = sl_actual
                pos_info_kwargs["desired_take_profit"] = tp_actual
            except Exception as exc:
                logger.warning("set_trading_stop failed for %s: %s", symbol, exc)
                # Failure: keep local stop_loss/take_profit at signal-based (= server-side)
                # but record desired_* for potential retry
                pos_info_kwargs["sl_tp_resync_failed"] = True
                pos_info_kwargs["desired_stop_loss"] = sl_actual
                pos_info_kwargs["desired_take_profit"] = tp_actual
```

The exact attribute names (`pos_info_kwargs`, `settings.bbkc_sl_pct`, etc.) depend on the actual code surface — adjust to match real names found via `grep`.

- [ ] **Step 5: Flesh out the test**

Replace the `pass` in the test with a minimal stub of the call surface needed for `_process_signal`. Verify:
- After call: `rest_client.set_trading_stop` called once with `symbol="BTCUSDT"`, `stop_loss≈97.67`, `take_profit≈102.0`
- `_PositionInfo` saved with `stop_loss=97.67`, `take_profit=102.0`, `sl_tp_resync_failed=False`

If the existing engine code has high coupling making isolated testing hard, add an integration-style test instead that uses a `_LiveDemoFakeRestClient` recording calls.

- [ ] **Step 6: Run test**

```
pytest tests/_legacy/test_trading_engine_sl_resync.py -v
```
Expected: PASS.

- [ ] **Step 7: Add the failure case test**

```python
def test_resync_failure_does_not_overwrite_local_stop():
    """If set_trading_stop raises, local stop_loss stays at signal-based value."""
    # ... using same harness as previous test, but rest_client.set_trading_stop = MagicMock(side_effect=RuntimeError("boom"))
    # Assert _PositionInfo.stop_loss == signal.stop_loss (NOT sl_actual)
    # Assert _PositionInfo.sl_tp_resync_failed == True
    # Assert _PositionInfo.desired_stop_loss == sl_actual (the would-be value)
```

- [ ] **Step 8: Run all legacy tests**

```
pytest tests/_legacy/ -v
```
Expected: PASS.

- [ ] **Step 9: Commit**

```
git add _legacy/paper_engine/trading_engine.py tests/_legacy/test_trading_engine_sl_resync.py
git commit -m "fix(_legacy): F2 post-fill SL/TP resync via set_trading_stop"
```

---

### Task 18: BBKC-only gate on legacy global ATR trailing

**Files:**
- Modify: `_legacy/paper_engine/trading_engine.py:1297-1310`
- Test: `tests/_legacy/test_trading_engine_bbkc_trailing_gate.py`

- [ ] **Step 1: Write failing test**

Create `tests/_legacy/test_trading_engine_bbkc_trailing_gate.py`:

```python
"""BBKC trailing gate test — global ATR trailing must skip BBKCSqueeze positions."""
import pytest
from unittest.mock import MagicMock


def test_bbkc_position_skips_global_atr_trailing(monkeypatch):
    pytest.importorskip("_legacy.paper_engine.trading_engine")
    from _legacy.paper_engine.trading_engine import TradingEngine, _PositionInfo

    eng = TradingEngine.__new__(TradingEngine)
    eng.risk_manager = MagicMock()
    eng.risk_manager.params.trailing_activation_atr = 2.5
    eng.risk_manager.params.trailing_distance_atr = 1.5
    # update_trailing_stop should NEVER be called for BBKC
    eng.risk_manager.update_trailing_stop = MagicMock()

    pos = _PositionInfo(
        strategy="BBKCSqueeze", symbol="BTCUSDT", direction="LONG",
        quantity=1.0, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, atr=2.0,
        bar_count=0, max_favorable=0.0, max_adverse=0.0,
        # ... whatever fields exist
    )
    eng._positions = {("BBKCSqueeze", "BTCUSDT"): pos}
    # Simulate price at +5 ATR favorable (would normally activate trailing)
    eng._check_open_positions_for_symbol("BTCUSDT", current_price=110.0)
    eng.risk_manager.update_trailing_stop.assert_not_called()
    assert pos.stop_loss == 95.0   # unchanged


def test_other_strategy_still_uses_global_atr_trailing():
    """Non-BBKC strategies retain the existing trailing behavior (regression guard)."""
    # Same harness but with strategy="RSIMACDStrategy"
    # Assert update_trailing_stop IS called
    pass
```

- [ ] **Step 2: Run test to verify fail**

```
pytest tests/_legacy/test_trading_engine_bbkc_trailing_gate.py::test_bbkc_position_skips_global_atr_trailing -v
```
Expected: FAIL — `update_trailing_stop` was called.

- [ ] **Step 3: Modify `_legacy/paper_engine/trading_engine.py:1297-1310`**

Replace:

```python
            # 트레일링 스톱 갱신 (활성화 조건: 수익이 trailing_activation_atr * ATR 이상)
            if pos.atr > 0:
                activation_dist = self.risk_manager.params.trailing_activation_atr * pos.atr
                ...
```

With:

```python
            # 트레일링 스톱 갱신 (활성화 조건: 수익이 trailing_activation_atr * ATR 이상)
            # BBKCSqueeze는 자체 청산 정책(fixed/be_trail)을 따르므로 전역 ATR trailing 제외.
            # 평가 fixed와 라이브 fixed의 의미를 일치시키기 위함 (round 2 design §4.7).
            if pos.strategy != "BBKCSqueeze" and pos.atr > 0:
                activation_dist = self.risk_manager.params.trailing_activation_atr * pos.atr
                if pos.direction == "LONG":
                    profit_dist = current_price - pos.entry_price
                else:
                    profit_dist = pos.entry_price - current_price
                if profit_dist >= activation_dist:
                    pos.stop_loss = self.risk_manager.update_trailing_stop(
                        current_price=current_price,
                        current_stop=pos.stop_loss,
                        direction=pos.direction,
                        atr=pos.atr,
                    )
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/_legacy/test_trading_engine_bbkc_trailing_gate.py -v
```
Expected: PASS for both BBKC-skip and other-strategy-keeps tests.

- [ ] **Step 5: Run full legacy suite**

```
pytest tests/_legacy/ -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```
git add _legacy/paper_engine/trading_engine.py tests/_legacy/test_trading_engine_bbkc_trailing_gate.py
git commit -m "fix(_legacy): gate global ATR trailing to skip BBKCSqueeze positions (3a)"
```

---

## Phase E — Run Sweep + Report

### Task 19: Smoke run — verify pipeline end-to-end with real data

**Files:**
- (no code changes — execution only)

- [ ] **Step 1: Smoke run**

```
python -m scripts.bbkc_exit_eval --smoke
```
Expected: completes without error in < 30 seconds. Outputs in `logs/research/bbkc_squeeze/exit_round/`:
- `wf_results.jsonl` (1 line)
- `auxiliary.json` (1 cell × 1 symbol)
- `summary.json` (1 cell × 1 symbol)
- `report.md`

- [ ] **Step 2: Inspect output**

```
cat logs/research/bbkc_squeeze/exit_round/report.md
```

Verify:
- Report renders cleanly
- Trade count > 0 (else loaders/window range may be misconfigured)
- No NaN / Inf values in metrics

If issues found, fix the specific bug, commit a follow-up patch, and re-smoke.

- [ ] **Step 3: Commit if any fix needed**

```
git add <fixed file> logs/research/bbkc_squeeze/exit_round/.gitkeep
git commit -m "fix(bbkc_exit_eval): <specific issue>"
```

---

### Task 20: Full sweep — 12 cells × 3 symbols × 9 windows = 324 runs

**Files:**
- (no code changes — execution only)

- [ ] **Step 1: Full sweep**

```
python -m scripts.bbkc_exit_eval --full
```
Expected: completes; estimated runtime depends on data size (likely 10-60 minutes). Progress logged per cell × symbol × window.

If a single window run errors, check the log, fix code if a bug, then resume. Re-running overwrites `wf_results.jsonl` — for partial recovery, use `--cell` / `--symbol` flags to fill missing cells and merge JSONL manually.

- [ ] **Step 2: Inspect summary**

```
cat logs/research/bbkc_squeeze/exit_round/summary.json | head -100
cat logs/research/bbkc_squeeze/exit_round/report.md
```

Verify:
- 12 cells × 3 symbols = 36 (cell, symbol) entries
- F0 baseline metrics match the known winner (~ Calmar 8.16, WR 64% from 2026-03-30 grid — note: WF window aggregation differs)
- Verdicts assigned

- [ ] **Step 3: Commit results**

```
git add logs/research/bbkc_squeeze/exit_round/wf_results.jsonl \
        logs/research/bbkc_squeeze/exit_round/summary.json \
        logs/research/bbkc_squeeze/exit_round/auxiliary.json \
        logs/research/bbkc_squeeze/exit_round/report.md
git commit -m "data(bbkc_exit): full sweep results — 12 cells × BIGTHREE × 9 WF windows"
```

---

### Task 21: Round-up note in design doc

**Files:**
- Modify: `docs/superpowers/specs/experiments/2026-04-25_bbkc_exit_strategy_design.md`

- [ ] **Step 1: Add §12 results summary**

Append a new section at the end of the design doc:

```markdown
---

## 12. Round 2 Results (TO BE FILLED AFTER SWEEP)

**Status**: TBD (waiting on Task 20 completion)

### PROMOTE cells (per symbol)
- BTCUSDT: ...
- ETHUSDT: ...
- AVAXUSDT: ...

### KILL cells
- ...

### Notable findings
- (e.g., be_trail captures more upside on AVAX but breaks even on BTC)

### Next round candidates
- (Promoted cells → live carryover discussion)
- (KILL clusters → B7 / B4 next-round candidates)
```

The actual content gets filled by inspecting `report.md` and writing 5-10 lines of findings.

- [ ] **Step 2: Commit**

```
git add docs/superpowers/specs/experiments/2026-04-25_bbkc_exit_strategy_design.md
git commit -m "docs(bbkc_exit): round 2 results summary"
```

---

## Self-Review Checklist (run after writing all tasks)

- [ ] **Spec coverage**: Every IN bullet in spec §3 has a task
  - Bbkc_squeeze.py expansion → Tasks 3-7
  - registry_builder grid → Task 9
  - sweep script → Tasks 10-15
  - rest_client.set_trading_stop → Task 16
  - F2 post-fill resync → Task 17
  - Legacy BBKC trailing gate → Task 18
  - max_favorable tracking → Tasks 1-2
  - WF report → Tasks 14-15, 21
- [ ] **No placeholders**: search this file for "TBD", "TODO", "..." in code blocks. The placeholder in Task 21 §12 is intentional (results filled after run).
- [ ] **Type consistency**: `_pos_meta` dict keys (`R`, `initial_sl`, `be_triggered`, `trail_active`, `bars_held`) used identically in Tasks 4, 5, 6, 7. `cell_id` field used identically in Tasks 9, 12, 14, 15.
- [ ] **Path consistency**: `logs/research/bbkc_squeeze/exit_round/` used in all output references.

## Deployment policy (BBKC trailing gate — pinned per spec §4.7)

**Decision**: Apply Task 18 patch only after current live BBKC position (id=123, BTC LONG entered 2026-04-24 19:23) closes naturally via SL/TP/manual. Do NOT force-close to apply the gate.

Reasoning: Removing the global ATR trailing while a position is open changes its effective stop without warning. Safer to wait for natural turnover (typical hold ~10-30 hours for BBKC).

If sweep results require urgent live deployment, force-close the position manually first, document in the round-up note (§12).
