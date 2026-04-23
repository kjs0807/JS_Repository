"""End-to-end wrapper tests for BBKCFilterPattern.

Scope: BBKC-specific checks that the pct label mode round-trips through
PatternMLFilterStrategy correctly and that on_bar_fast delivers pct
stop_loss / take_profit values to the broker.

We deliberately do NOT exercise scripts.train_ml_pattern.run_pipeline
here. The pipeline itself is pattern-agnostic and already covered by
test_e2e_rsi_divergence.py (atr mode) and test_e2e_engulfing_mtf.py
(holdout section assertion). The synthetic squeeze fixture used for
BBKC produces very directionally-clean releases, which makes
constructing a mixed-label IS slice for a full train run brittle.
Keep those checks as unit tests against the wrapper instead.

Parity checks covered by test_patterns/test_bbkc_filter.py (unit tests):
    - event bar indices 1:1 with raw BBKCSqueeze
    - feature schema locked to 11 P0 keys
    - primary_tf=4h HTF gate zero-fills h4_* features
    - filter never emits more events than raw BBKCSqueeze

Checks covered here:
    - wrapper pct mode constructor accepts BBKCFilterPattern
    - on_bar_fast orders broker with tp/sl = entry * (1 ± pct)
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from src.core.types import Bar, BarSeries
from src.ml.patterns.bbkc_filter import BBKCFilterPattern
from src.ml.types import MTFData
from src.strategies.pattern_ml_filter import PatternMLFilterStrategy


H = 3_600_000
D = 24 * H


def _build_squeeze_mtf(symbol: str = "BTCUSDT", seed: int = 7) -> MTFData:
    """Squeeze / expansion fixture that produces multiple BBKCSqueeze
    release events (see test_patterns/test_bbkc_filter.py for the
    parity-tested fixture design). Used here only to drive the wrapper
    through one live entry."""
    rng = np.random.default_rng(seed)
    n = 600
    closes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    opens: List[float] = []
    price = 100.0
    for i in range(n):
        cycle_pos = i % 45
        if cycle_pos < 30:
            price += rng.normal(0.0, 0.01)
            c = float(price)
            intraday = 0.5 + rng.uniform(0.0, 0.2)
            o = c + rng.normal(0.0, 0.05)
            h = c + intraday
            low = c - intraday
        else:
            direction = 1.0 if (i // 45) % 2 == 0 else -1.0
            step = rng.normal(direction * 1.5, 0.3)
            price += step
            c = float(price)
            o = c - step * 0.5
            h = max(c, o) + 0.2
            low = min(c, o) - 0.2
        closes.append(c)
        opens.append(float(o))
        highs.append(float(h))
        lows.append(float(low))

    df_1h = pd.DataFrame({
        "timestamp": [i * H for i in range(n)],
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1.0] * n, "turnover": [1.0] * n,
    })
    s_1h = BarSeries(symbol=symbol, timeframe="1h", bars=df_1h)

    def _resample(step):
        out = []
        for j in range(n // step):
            seg = slice(j * step, (j + 1) * step)
            c_seg = closes[seg]
            if not c_seg:
                continue
            out.append({
                "timestamp": j * step * H,
                "open": float(opens[j * step]),
                "high": float(max(highs[seg])),
                "low": float(min(lows[seg])),
                "close": float(c_seg[-1]),
                "volume": 1.0,
                "turnover": 1.0,
            })
        return out

    df_4h = pd.DataFrame(_resample(4))
    s_4h = BarSeries(symbol=symbol, timeframe="4h", bars=df_4h)
    df_1d = pd.DataFrame(_resample(24))
    s_1d = BarSeries(symbol=symbol, timeframe="1d", bars=df_1d)

    return MTFData(
        symbol=symbol, primary_tf="1h",
        series={"1h": s_1h, "4h": s_4h, "1d": s_1d},
    )


class _RecordingBroker:
    def __init__(self) -> None:
        self.orders: list = []

    def calc_qty(self, symbol, risk_pct, stop_distance):
        return 1.0

    def buy(self, symbol, qty, stop_loss, take_profit, reason, **kwargs):
        self.orders.append({
            "side": "buy", "symbol": symbol, "qty": qty,
            "sl": stop_loss, "tp": take_profit, "reason": reason,
        })

    def sell(self, symbol, qty, stop_loss, take_profit, reason, **kwargs):
        self.orders.append({
            "side": "sell", "symbol": symbol, "qty": qty,
            "sl": stop_loss, "tp": take_profit, "reason": reason,
        })

    def close_position(self, symbol):
        self.orders.append({"side": "close", "symbol": symbol})


class _AlwaysConfidentModel:
    """Stub that always emits P(label=1) = 0.95 so every detected event
    passes the wrapper's threshold filter."""

    def predict_proba(self, X):
        n = len(X)
        out = np.zeros((n, 2), dtype=float)
        out[:, 0] = 0.05
        out[:, 1] = 0.95
        return out


def test_wrapper_accepts_bbkc_filter_in_pct_mode():
    """Direct wrapper construction in pct mode using BBKCFilterPattern.

    Mirrors the BBKCSqueeze leverage-adjusted barriers
    (tp=0.06/3=0.02, sl=0.07/3=0.0233) which is what a production run
    of train_ml_pattern would pass via the --tp / --sl CLI flags.
    """
    mtf = _build_squeeze_mtf("BTCUSDT", seed=7)
    pattern = BBKCFilterPattern()

    # Detect one event so we can harvest the real feature column list
    sample_event = None
    for i in range(pattern.warmup_bars, len(mtf.get_primary())):
        ev = pattern.detect_at(mtf, i)
        if ev is not None:
            sample_event = ev
            break
    assert sample_event is not None, (
        "fixture must produce at least one BBKCSqueeze release event"
    )
    feature_columns = list(pattern.extract_features(sample_event, mtf).keys())

    strat = PatternMLFilterStrategy(
        pattern=pattern,
        model=_AlwaysConfidentModel(),
        feature_columns=feature_columns,
        threshold=0.5,
        max_holding_bars=48,
        label_mode="pct",
        tp_pct=0.02,
        sl_pct=0.0233,
        timeframe="1h",
        mtf_data=mtf,
    )
    assert strat.label_mode == "pct"
    assert strat.tp_pct == 0.02
    assert strat.sl_pct == 0.0233
    assert strat.tp_atr_mult is None and strat.sl_atr_mult is None
    assert strat.timeframe == "1h"

    cache = strat.prepare(mtf.get_primary())
    assert hasattr(cache, "mtf")
    # pct mode should NOT compute an atr_arr cache (unlike the ATR mode
    # branch), so this attribute must be absent
    assert not hasattr(cache, "atr_arr")


def test_on_bar_fast_orders_broker_with_pct_barriers():
    """The wrapper in pct mode must hand broker.buy/sell stop_loss and
    take_profit values equal to entry * (1 ± tp_pct). RSI tested this
    for ATR mode; BBKC is the pct-mode regression case. Production
    barrier values matching BBKCSqueeze leverage-adjusted TP/SL.
    """
    mtf = _build_squeeze_mtf("BTCUSDT", seed=7)
    primary = mtf.get_primary()
    pattern = BBKCFilterPattern()

    sample_event = None
    for i in range(pattern.warmup_bars, len(primary)):
        ev = pattern.detect_at(mtf, i)
        if ev is not None:
            sample_event = ev
            break
    assert sample_event is not None
    feature_columns = list(pattern.extract_features(sample_event, mtf).keys())

    tp_pct = 0.02
    sl_pct = 0.0233
    strat = PatternMLFilterStrategy(
        pattern=pattern,
        model=_AlwaysConfidentModel(),
        feature_columns=feature_columns,
        threshold=0.5,
        max_holding_bars=48,
        label_mode="pct",
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        timeframe="1h",
        mtf_data=mtf,
    )
    cache = strat.prepare(primary)
    broker = _RecordingBroker()

    fired_at = None
    fired_entry = None
    fired_side = None
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
            fired_side = broker.orders[-1]["side"]
            break

    assert fired_at is not None, "wrapper failed to submit any order"
    order = broker.orders[-1]
    if fired_side == "buy":
        expected_tp = fired_entry * (1.0 + tp_pct)
        expected_sl = fired_entry * (1.0 - sl_pct)
    else:
        expected_tp = fired_entry * (1.0 - tp_pct)
        expected_sl = fired_entry * (1.0 + sl_pct)
    assert abs(order["tp"] - expected_tp) < 1e-6, (
        f"take_profit mismatch: got {order['tp']}, expected {expected_tp}"
    )
    assert abs(order["sl"] - expected_sl) < 1e-6, (
        f"stop_loss mismatch: got {order['sl']}, expected {expected_sl}"
    )
