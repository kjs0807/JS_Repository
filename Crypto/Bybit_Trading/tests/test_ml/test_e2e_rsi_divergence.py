"""End-to-end integration: RSI Divergence pipeline on synthetic MTF data.

The synthetic series is designed to produce multiple confirmed RSI divergences
inside the IS window with MIXED triple-barrier labels, so XGBoost training
does not collapse to a single class.

Construction strategy
---------------------
A base price series is built from four drop-zigzag cycles.  The zigzag
descent (alternating loss/gain bars) keeps RSI elevated relative to the
preceding steep drop, while still reaching a lower absolute price low.  This
naturally causes detect_divergence (confirmation_bars=3, lookback=30) to fire
at intermediate confirmed-low pairs inside the zigzag.

The base series produces 5 events per symbol at bars [120, 132, 142, 240, 252]
(long/short/long/long/short).  Without patches, EventDatasetBuilder assigns
label=1 to all events (direction-aware triple-barrier: rising post-event bars
hit long TP; falling bars hit short TP).

For BTCUSDT the series starts at timestamp 0 (no offset).  For ETHUSDT the
series starts at timestamp OFFSET_BARS * H so that ETHUSDT events interleave
with BTCUSDT events in time-sorted order.  After interleaving the first
TimeSeriesSplit fold (n_splits=2 effective for N=10) contains events from
BOTH symbols and BOTH labels, preventing the single-class XGBoost crash.

Post-event patches
------------------
Bars 142 and 252 in both symbols are overwritten with directed candles to
flip their labels from 1 to 0:

  bar 142 (long  regular_bull) + drop candles → long SL hit  → label=0
  bar 252 (short hidden_bear)  + rise candles → short SL hit → label=0

  BTCUSDT labels: [1, 1, 0, 1, 0]  (2 zeros, 3 ones)
  ETHUSDT labels: [1, 1, 0, 1, 0]  (2 zeros, 3 ones)
  Combined total: N=10, 4 zeros + 6 ones

With N=10, half=5, all 10 permutation trials (seed=42) produce mixed
first-half slices — verified by offline probe.

TimeSeriesSplit(n_splits=2) fold 1 trains on first ~3 IS events with labels
[1, 1, 0] → mixed ✓.

v2 detector compatibility
--------------------------
Every pivot has ≥ 3 strictly-higher bars on both sides (confirmation_bars=3).
detect_divergence fires at end_index = p2 + 3; never retroactively.
Patching bars 142 and 252 does not suppress any other events (verified by probe).
"""
import numpy as np
import pandas as pd

from src.core.types import BarSeries
from src.ml.persistence import load_run
from src.ml.patterns.rsi_divergence import RSIDivergence
from src.ml.types import MTFData
from src.strategies.pattern_ml_filter import PatternMLFilterStrategy
import scripts.train_ml_pattern as cli

H = 3_600_000
D = 24 * H

# ETHUSDT timestamp offset so its events interleave with BTCUSDT in time order
OFFSET_BARS = 60

# Post-event patch map: bar_index → candle kind for 8 bars after event
#   "rise": high = c+2.5, low = c-0.3  (hits long TP or short SL)
#   "drop": high = c+0.3, low = c-2.5  (hits long SL or short TP)
#
# Base series events (bars): 120 (long/reg_bull), 132 (short/hidden_bear),
#                             142 (long/reg_bull), 240 (long/reg_bull), 252 (short/hidden_bear)
# Builder labels (direction-aware triple-barrier, tp=sl=2%, max_bars=8):
#   Natural (no patches): all 5 events → label=1 for both symbols.
#
# Patches flip selected events to label=0:
#   bar 142 (long):  drop candles → long SL hit  → label=0
#   bar 252 (short): rise candles → short SL hit → label=0
#
# BTCUSDT: {142:'drop', 252:'rise'} → labels [1,1,0,1,0] (2 zeros, 3 ones)
# ETHUSDT: {142:'drop', 252:'rise'} → labels [1,1,0,1,0] (2 zeros, 3 ones)
# Combined: N=10, 4 zeros + 6 ones.
# With seed=42, all 10 permutation trials produce mixed first-half slices (verified).
# TimeSeriesSplit fold 1 trains on first ~3 IS events → [1,1,0] = mixed ✓.
# Patching bars 142 and 252 does not suppress any other events (verified by probe).
_PATCHES_BTC = {
    142: "drop",   # long regular_bull → long SL hit → label=0
    252: "rise",   # short hidden_bear → short SL hit → label=0
}
_PATCHES_ETH = {
    142: "drop",   # long regular_bull → long SL hit → label=0
    252: "rise",   # short hidden_bear → short SL hit → label=0
}
_PATCH_BARS = 8


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _add(records: list, c: float, kind: str = "neutral") -> None:
    if kind == "rise":
        h, l = c + 2.5, c - 0.3
    elif kind == "drop":
        h, l = c + 0.3, c - 2.5
    else:
        h, l = c + 0.5, c - 0.5
    records.append((c, h, l, c))


def _build_cycle(
    records: list,
    start_level: float,
    drop_rate: float,
    drop_bars: int,
    zigzag_cycles: int,
    rise_bars_after_low1: int,
) -> None:
    """Append one drop→zigzag→confirm cycle to *records*."""
    for i in range(drop_bars):
        _add(records, start_level - i * drop_rate, "drop")
    low1_close = records[-1][3]
    for off in [drop_rate * 1.5, drop_rate * 3.0, drop_rate * 4.5]:
        _add(records, low1_close + off, "rise")
    cur = low1_close + drop_rate * 4.5
    for i in range(rise_bars_after_low1):
        _add(records, cur + i * (drop_rate * 0.5), "rise")
    base = records[-1][3]
    for _ in range(zigzag_cycles):
        base -= drop_rate; _add(records, base, "drop")
        base -= drop_rate; _add(records, base, "drop")
        base -= drop_rate; _add(records, base, "drop")
        base += drop_rate * 0.5; _add(records, base, "rise")
    base -= drop_rate; _add(records, base, "drop")
    base -= drop_rate; _add(records, base, "drop")
    base -= drop_rate; _add(records, base, "drop")
    low2_close = base
    for off in [drop_rate * 1.0, drop_rate * 2.5, drop_rate * 4.5]:
        _add(records, low2_close + off, "rise")


def _build_base_records() -> list:
    """Build the unpatched 1-h record list (four cycles + tail)."""
    records: list = []
    for i in range(70):
        _add(records, 100.0 + 6.0 * np.sin(2 * np.pi * i / 14))

    _build_cycle(records, 100.0, 2.0, 10, 4, 3)
    e1 = records[-1][3]
    for i in range(10): _add(records, e1 - i * 1.5, "drop")
    r1 = records[-1][3]
    for i in range(12): _add(records, r1 + i * 1.2, "rise")

    _build_cycle(records, records[-1][3], 1.8, 10, 4, 3)
    e2 = records[-1][3]
    for i in range(10): _add(records, e2 + i * 1.5, "rise")
    r2 = records[-1][3]
    for i in range(12): _add(records, r2 + i * 1.0, "rise")

    _build_cycle(records, records[-1][3], 1.8, 10, 4, 3)
    e3 = records[-1][3]
    for i in range(10): _add(records, e3 - i * 1.5, "drop")
    r3 = records[-1][3]
    for i in range(12): _add(records, r3 + i * 1.0, "rise")

    _build_cycle(records, records[-1][3], 1.8, 10, 4, 3)
    e4 = records[-1][3]
    for i in range(10): _add(records, e4 + i * 1.5, "rise")

    tail_s = records[-1][3]
    for i in range(120): _add(records, tail_s + i * 0.4)
    return records


def _apply_patches(records: list, patches: dict) -> list:
    """Overwrite _PATCH_BARS bars after each event bar with the patch kind."""
    records = list(records)
    for event_bar, kind in patches.items():
        start = event_bar + 1
        end = min(start + _PATCH_BARS, len(records))
        entry_close = records[event_bar][3]
        for j, k in enumerate(range(start, end)):
            if kind == "rise":
                c = entry_close + (j + 1) * 0.8
                h, l = c + 2.5, c - 0.3
            else:
                c = entry_close - (j + 1) * 0.8
                h, l = c + 0.3, c - 2.5
            records[k] = (c, h, l, c)
    return records


def _build_primary_records(patches: dict) -> list:
    return _apply_patches(_build_base_records(), patches)


def _records_to_series(
    records: list, tf: str, step_ms: int, symbol: str, ts_offset: int = 0
) -> BarSeries:
    rows = [
        {
            "timestamp": ts_offset + i * step_ms,
            "open": float(o), "high": float(h),
            "low": float(l), "close": float(c),
            "volume": 1.0, "turnover": 1.0,
        }
        for i, (o, h, l, c) in enumerate(records)
    ]
    return BarSeries(symbol=symbol, timeframe=tf, bars=pd.DataFrame(rows))


def _make_mtf(symbol: str, ts_offset_bars: int = 0, patches: dict = None) -> MTFData:
    """Build a 3-TF MTFData bundle.

    ts_offset_bars shifts every bar timestamp by that many 1-h periods so
    that two symbols with different offsets interleave properly when sorted
    by timestamp in the event dataset.

    patches controls which post-event bars get directional candles; defaults
    to _PATCHES_BTC when None.
    """
    if patches is None:
        patches = _PATCHES_BTC
    records_1h = _build_primary_records(patches)
    n = len(records_1h)
    ts_off = ts_offset_bars * H

    s_1h = _records_to_series(records_1h, "1h", H, symbol, ts_off)

    records_4h = []
    for i in range(n // 4):
        seg = records_1h[i * 4: (i + 1) * 4]
        records_4h.append((
            seg[0][0], max(x[1] for x in seg),
            min(x[2] for x in seg), seg[-1][3],
        ))
    s_4h = _records_to_series(records_4h, "4h", 4 * H, symbol, ts_off)

    records_1d = []
    for i in range(max(1, n // 24)):
        seg = records_1h[i * 24: (i + 1) * 24]
        if not seg:
            continue
        records_1d.append((
            seg[0][0], max(x[1] for x in seg),
            min(x[2] for x in seg), seg[-1][3],
        ))
    s_1d = _records_to_series(records_1d, "1d", D, symbol, ts_off)

    return MTFData(
        symbol=symbol, primary_tf="1h",
        series={"1h": s_1h, "4h": s_4h, "1d": s_1d},
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_e2e_rsi_divergence(tmp_path, monkeypatch):
    # BTCUSDT starts at t=0; ETHUSDT starts OFFSET_BARS later so their events
    # interleave in time-sorted order.  Both symbols use the same patch map
    # (bars 142 and 252 forced to label=0) so the combined dataset has 4 zeros
    # and 6 ones across 10 events; the permutation overfit test is safe.
    mtf_per_symbol = {
        "BTCUSDT": _make_mtf("BTCUSDT", ts_offset_bars=0, patches=_PATCHES_BTC),
        "ETHUSDT": _make_mtf("ETHUSDT", ts_offset_bars=OFFSET_BARS, patches=_PATCHES_ETH),
    }
    monkeypatch.setattr(
        cli, "load_mtf_data",
        lambda symbols, timeframes, start_ms, end_ms, primary_tf="1h": {
            s: mtf_per_symbol[s] for s in symbols
        },
    )

    # Use BTCUSDT length for total end_ms (ETHUSDT ends OFFSET_BARS later but
    # that is fine — we only care that IS contains enough events).
    primary_len = len(mtf_per_symbol["BTCUSDT"].get_primary())
    # Add OFFSET_BARS to cover ETHUSDT's shifted events too
    end_ms = (primary_len + OFFSET_BARS) * H

    artifact_dir = cli.run_pipeline(
        pattern_name="rsi_divergence",
        symbols=["BTCUSDT", "ETHUSDT"],
        is_start_ms=0,
        is_end_ms=int(end_ms * 0.7),
        oos_start_ms=int(end_ms * 0.7),
        oos_end_ms=end_ms,
        tp_pct=0.02,
        sl_pct=0.02,
        max_holding_bars=8,
        n_trials=2,
        hpo_timeout=60,
        cache_dir=tmp_path / "cache",
        out_root=tmp_path / "logs" / "ml",
    )

    artifact = load_run(artifact_dir)
    assert artifact.meta["pattern_name"] == "rsi_divergence"
    assert "verdict" in artifact.report
    assert "metrics" in artifact.report
    assert "feature_columns" in artifact.meta

    # Verify locked metadata schema fields are in the feature columns
    fc = artifact.meta["feature_columns"]
    assert "rsi_primary" in fc
    assert "divergence_strength" in fc
    assert "dt_regular_bull" in fc

    # Wrap as a deployable Strategy
    strat = PatternMLFilterStrategy.from_artifact(
        run_dir=artifact_dir,
        pattern_factory=lambda: RSIDivergence(),
    )
    assert strat.pattern.name == "rsi_divergence"
    assert strat.threshold >= 0.5
    # pct artifact → wrapper runs in pct mode
    assert strat.label_mode == "pct"
    assert strat.tp_pct is not None and strat.sl_pct is not None
    assert strat.tp_atr_mult is None and strat.sl_atr_mult is None
    assert strat.timeframe == "1h"

    # Holdout section must exist in the report and carry a verdict that
    # is one of the supported values. This pins the new evaluate_holdout
    # wiring in run_pipeline and build_report.
    holdout = artifact.report.get("metrics", {}).get("holdout")
    assert holdout is not None, "report.metrics.holdout should be emitted"
    assert holdout["verdict"] in (
        "HOLDOUT_PASS", "HOLDOUT_FAIL", "HOLDOUT_NO_TRADES"
    )
    assert holdout["oos_period_ms"][0] < holdout["oos_period_ms"][1]
    assert holdout["n_events"] >= 0


def test_e2e_rsi_divergence_atr_wrapper_parity(tmp_path, monkeypatch):
    """ATR-labeled artifact must produce a wrapper that executes ATR
    barriers, not pct placeholders. This is the regression test for the
    'wrapper runs a different strategy than what was trained' bug."""
    mtf_per_symbol = {
        "BTCUSDT": _make_mtf("BTCUSDT", ts_offset_bars=0, patches=_PATCHES_BTC),
        "ETHUSDT": _make_mtf("ETHUSDT", ts_offset_bars=OFFSET_BARS, patches=_PATCHES_ETH),
    }
    monkeypatch.setattr(
        cli, "load_mtf_data",
        lambda symbols, timeframes, start_ms, end_ms, primary_tf="1h": {
            s: mtf_per_symbol[s] for s in symbols
        },
    )
    primary_len = len(mtf_per_symbol["BTCUSDT"].get_primary())
    end_ms = (primary_len + OFFSET_BARS) * H

    artifact_dir = cli.run_pipeline(
        pattern_name="rsi_divergence",
        symbols=["BTCUSDT", "ETHUSDT"],
        is_start_ms=0,
        is_end_ms=int(end_ms * 0.7),
        oos_start_ms=int(end_ms * 0.7),
        oos_end_ms=end_ms,
        tp_pct=0.02,  # stale placeholder — must not leak into artifact
        sl_pct=0.02,  # stale placeholder
        max_holding_bars=8,
        n_trials=2,
        hpo_timeout=60,
        cache_dir=tmp_path / "cache",
        out_root=tmp_path / "logs" / "ml",
        label_mode="atr",
        tp_atr_mult=2.0,
        sl_atr_mult=1.0,
        atr_period=14,
    )

    artifact = load_run(artifact_dir)
    policy = artifact.meta["policy"]
    assert policy["label"]["mode"] == "atr"
    assert policy["label"]["tp_atr_mult"] == 2.0
    assert policy["label"]["sl_atr_mult"] == 1.0
    # Stale pct placeholders must be stripped in atr mode
    assert policy["tp_pct"] is None
    assert policy["sl_pct"] is None
    # primary_tf bookkeeping should survive
    assert artifact.meta["data"]["primary_tf"] == "1h"

    # Wrapper reflects ATR execution rules
    strat = PatternMLFilterStrategy.from_artifact(
        run_dir=artifact_dir,
        pattern_factory=lambda: RSIDivergence(),
        mtf_data=mtf_per_symbol["BTCUSDT"],
    )
    assert strat.label_mode == "atr"
    assert strat.tp_atr_mult == 2.0
    assert strat.sl_atr_mult == 1.0
    assert strat.atr_period == 14
    assert strat.tp_pct is None and strat.sl_pct is None
    assert strat.timeframe == "1h"

    # prepare() must precompute a primary-TF ATR array on the cache
    primary_series = mtf_per_symbol["BTCUSDT"].get_primary()
    cache = strat.prepare(primary_series)
    assert hasattr(cache, "atr_arr")
    assert len(cache.atr_arr) == len(primary_series)

    # _compute_barriers must use ATR(t), not entry * pct
    i = strat.pattern.warmup_bars + 50  # well past warmup
    atr_i = float(cache.atr_arr[i])
    assert atr_i > 0
    entry = 100.0
    tp, sl = strat._compute_barriers(entry, "long", i, cache)
    assert abs((tp - entry) - 2.0 * atr_i) < 1e-9
    assert abs((entry - sl) - 1.0 * atr_i) < 1e-9
    # Short should mirror
    tp_s, sl_s = strat._compute_barriers(entry, "short", i, cache)
    assert abs((entry - tp_s) - 2.0 * atr_i) < 1e-9
    assert abs((sl_s - entry) - 1.0 * atr_i) < 1e-9


# ---------------------------------------------------------------------------
# Reviewer-requested reinforcements
#
# Two things the earlier ATR parity test did NOT cover:
#   1. 4h primary artifact path — the production candidate uses primary_tf="4h"
#      but existing tests only exercise the 1h path. This directly pins the
#      branch in from_artifact that reads meta.data.primary_tf and sets the
#      wrapper's timeframe instance attribute.
#   2. on_bar_fast orders the broker with ATR-derived stop_loss / take_profit.
#      _compute_barriers was unit-tested in isolation; this locks in that the
#      value actually reaches broker.buy() / broker.sell().
# ---------------------------------------------------------------------------


def _make_4h_mtf(symbol: str = "BTCUSDT", n_bars: int = 300) -> MTFData:
    """Build a minimal 4h-primary MTFData for wrapper smoke tests.

    The bars themselves don't need to produce divergences — the test only
    checks that from_artifact wires timeframe + ATR caching from the 4h
    series. Event firing is validated separately by the 1h tests above.
    """
    H_4 = 4 * H
    # Gentle oscillation so ATR is non-zero and finite.
    closes = [100.0 + 0.6 * (i % 7 - 3) for i in range(n_bars)]
    bars_4h = pd.DataFrame({
        "timestamp": [i * H_4 for i in range(n_bars)],
        "open":   closes,
        "high":   [c + 0.5 for c in closes],
        "low":    [c - 0.5 for c in closes],
        "close":  closes,
        "volume":   [1.0] * n_bars,
        "turnover": [1.0] * n_bars,
    })
    s_4h = BarSeries(symbol=symbol, timeframe="4h", bars=bars_4h)

    n_1d = max(1, n_bars // 6)  # 1d = 6 × 4h
    closes_1d = [float(np.mean(closes[i * 6 : (i + 1) * 6])) for i in range(n_1d)]
    bars_1d = pd.DataFrame({
        "timestamp": [i * D for i in range(n_1d)],
        "open":   closes_1d,
        "high":   [c + 0.5 for c in closes_1d],
        "low":    [c - 0.5 for c in closes_1d],
        "close":  closes_1d,
        "volume":   [1.0] * n_1d,
        "turnover": [1.0] * n_1d,
    })
    s_1d = BarSeries(symbol=symbol, timeframe="1d", bars=bars_1d)

    return MTFData(
        symbol=symbol, primary_tf="4h",
        series={"4h": s_4h, "1d": s_1d},
    )


def _write_fake_atr_artifact(
    run_dir, primary_tf: str, feature_columns: list, atr_period: int = 14
) -> None:
    """Persist a minimal ATR-labeled artifact for wrapper smoke tests.

    Uses a tiny XGBoost model so load_run's joblib round-trip works, and
    writes meta/report dicts matching what run_pipeline would emit under
    label_mode=atr.
    """
    from xgboost import XGBClassifier
    from src.ml.persistence import save_run
    rng = np.random.default_rng(0)
    X = rng.normal(size=(24, len(feature_columns)))
    y = np.array([0, 1] * 12)
    model = XGBClassifier(
        n_estimators=2, max_depth=2, use_label_encoder=False,
        eval_metric="logloss", verbosity=0,
    )
    model.fit(X, y)
    meta = {
        "pattern_name": "rsi_divergence",
        "pattern_version": "1.0.0",
        "run_id": "test_atr_smoke",
        "git_sha": "test",
        "trained_at": "2026-04-14T00:00:00",
        "policy": {
            "threshold": 0.5,
            "tp_pct": None,
            "sl_pct": None,
            "max_holding_bars": 8,
            "label": {
                "type": "triple_barrier_binary",
                "timeout_class": "negative",
                "mode": "atr",
                "tp_atr_mult": 2.0,
                "sl_atr_mult": 1.0,
                "atr_period": atr_period,
            },
            "weighting_policy": "inverse_symbol_count",
            "dataset_filter": {"hidden_only": False, "min_adx": 0.0},
        },
        "data": {
            "symbols": ["BTCUSDT"],
            "timeframes": ["1h", "4h", "1d"],
            "primary_tf": primary_tf,
            "is_period_ms": [0, 1],
            "oos_period_ms": [1, 2],
            "n_features": len(feature_columns),
            "n_samples_is": 12,
            "dataset_hash": "sha256:test",
        },
        "training": {"objective": "expectancy"},
        "feature_columns": feature_columns,
    }
    report = {"verdict": "TEST", "metrics": {}, "artifacts": {}}
    save_run(run_dir=run_dir, model=model, meta=meta, report=report)


def test_wrapper_respects_4h_primary_tf_on_artifact(tmp_path):
    """4h-labeled artifact → wrapper.timeframe == '4h' and prepare() caches
    ATR on the *4h* series (length matches the 4h BarSeries, not the 1h)."""
    mtf = _make_4h_mtf(symbol="BTCUSDT", n_bars=300)
    n_4h = len(mtf.get_primary())

    run_dir = tmp_path / "fake_atr_4h"
    pattern_tmp = RSIDivergence(percentile_lookback=30)
    feature_columns = sorted(pattern_tmp.extract_features.__doc__ is not None and [] or [])
    # Use a real feature column set that extract_features would emit so
    # from_artifact wiring exercises the real list; the model's expected
    # input dim matches this list.
    feature_columns = [
        "rsi_primary", "divergence_strength", "price_slope", "rsi_slope",
        "dt_regular_bull", "dt_regular_bear", "dt_hidden_bull", "dt_hidden_bear",
    ]
    _write_fake_atr_artifact(run_dir, primary_tf="4h", feature_columns=feature_columns)

    strat = PatternMLFilterStrategy.from_artifact(
        run_dir=run_dir,
        pattern_factory=lambda: RSIDivergence(percentile_lookback=30),
        mtf_data=mtf,
    )
    assert strat.timeframe == "4h", f"expected 4h, got {strat.timeframe}"
    assert strat.label_mode == "atr"
    assert strat.tp_atr_mult == 2.0 and strat.sl_atr_mult == 1.0
    assert strat.atr_period == 14

    cache = strat.prepare(mtf.get_primary())
    assert hasattr(cache, "atr_arr")
    assert len(cache.atr_arr) == n_4h
    # Sanity: the 4h series is shorter than the hypothetical 1h equivalent
    # (4× more bars), so if someone later regressed prepare() to use the
    # wrong series, this length assertion would catch it.
    assert n_4h == 300


class _RecordingBroker:
    """Minimal broker stub that records the kwargs of buy/sell calls.

    Used by the on_bar_fast ATR-order test to assert that stop_loss and
    take_profit reach the broker with ATR-derived values, not pct placeholders.
    """

    def __init__(self) -> None:
        self.orders: list = []

    def calc_qty(self, symbol, risk_pct, stop_distance):
        return 1.0

    def buy(self, symbol, qty, stop_loss, take_profit, reason):
        self.orders.append({
            "side": "buy", "symbol": symbol, "qty": qty,
            "sl": stop_loss, "tp": take_profit, "reason": reason,
        })

    def sell(self, symbol, qty, stop_loss, take_profit, reason):
        self.orders.append({
            "side": "sell", "symbol": symbol, "qty": qty,
            "sl": stop_loss, "tp": take_profit, "reason": reason,
        })

    def close_position(self, symbol):
        self.orders.append({"side": "close", "symbol": symbol})


class _AlwaysConfidentModel:
    """Stub ML model that always emits P(label=1) = 0.95, so every
    pattern event passes the wrapper's threshold filter."""

    def predict_proba(self, X):
        n = len(X)
        out = np.zeros((n, 2), dtype=float)
        out[:, 0] = 0.05
        out[:, 1] = 0.95
        return out


def test_on_bar_fast_orders_broker_with_atr_barriers():
    """End-to-end wrapper test: iterate the same synthetic series that
    produces RSI divergences in the pct test, configure the wrapper in ATR
    mode, and assert that the FIRST order sent to the broker carries
    stop_loss / take_profit equal to entry ± k×ATR(i) — not entry ± k%.
    """
    from src.core.types import Bar

    mtf = _make_mtf("BTCUSDT", ts_offset_bars=0, patches=_PATCHES_BTC)
    pattern = RSIDivergence(percentile_lookback=30)
    feature_columns = sorted(pattern.extract_features.__annotations__ and [] or [])
    # We don't know exact column order unless we detect one event first.
    # Detect at the earliest bar where the pattern fires, then extract keys.
    primary = mtf.get_primary()
    sample_event = None
    sample_i = None
    for i in range(pattern.warmup_bars, len(primary)):
        ev = pattern.detect_at(mtf, i)
        if ev is not None:
            sample_event = ev
            sample_i = i
            break
    assert sample_event is not None, "synthetic fixture must produce events"
    feature_columns = list(pattern.extract_features(sample_event, mtf).keys())

    strat = PatternMLFilterStrategy(
        pattern=pattern,
        model=_AlwaysConfidentModel(),
        feature_columns=feature_columns,
        threshold=0.5,
        max_holding_bars=8,
        label_mode="atr",
        tp_atr_mult=2.0,
        sl_atr_mult=1.0,
        atr_period=14,
        timeframe="1h",
        mtf_data=mtf,
    )
    cache = strat.prepare(primary)
    broker = _RecordingBroker()

    fired_at = None
    fired_entry = None
    fired_direction = None
    for i in range(strat.warmup_bars, len(primary)):
        row = primary.bars.iloc[i]
        bar = Bar(
            symbol=mtf.symbol,
            timestamp=int(row["timestamp"]),
            timeframe="1h",
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            turnover=float(row["turnover"]),
        )
        before = len(broker.orders)
        strat.on_bar_fast(bar=bar, i=i, cache=cache, broker=broker)
        if len(broker.orders) > before:
            fired_at = i
            fired_entry = float(row["close"])
            # Detect direction by the side of the last recorded order
            fired_direction = broker.orders[-1]["side"]
            break

    assert fired_at is not None, "wrapper failed to submit any order"
    order = broker.orders[-1]
    atr_i = float(cache.atr_arr[fired_at])
    assert atr_i > 0 and np.isfinite(atr_i)

    if fired_direction == "buy":
        expected_tp = fired_entry + 2.0 * atr_i
        expected_sl = fired_entry - 1.0 * atr_i
    else:  # sell
        expected_tp = fired_entry - 2.0 * atr_i
        expected_sl = fired_entry + 1.0 * atr_i
    assert abs(order["tp"] - expected_tp) < 1e-6, (
        f"take_profit mismatch: got {order['tp']}, expected {expected_tp}"
    )
    assert abs(order["sl"] - expected_sl) < 1e-6, (
        f"stop_loss mismatch: got {order['sl']}, expected {expected_sl}"
    )
    # Guardrail: make sure we didn't accidentally match the pct formula.
    # entry * 0.02 would be ~2 at entry=100, vs. 2*ATR which is typically
    # 1–2 on the zigzag fixture — different enough that accidental equality
    # would have to be engineered.
    pct_tp_buy = fired_entry * (1.0 + 0.02)
    pct_sl_buy = fired_entry * (1.0 - 0.02)
    if fired_direction == "buy":
        assert abs(order["tp"] - pct_tp_buy) > 1e-6 or abs(2.0 * atr_i - fired_entry * 0.02) > 1e-6
        assert abs(order["sl"] - pct_sl_buy) > 1e-6 or abs(1.0 * atr_i - fired_entry * 0.02) > 1e-6
