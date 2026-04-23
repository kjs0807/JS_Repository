"""BBKCSqueezeHTFTrend tests.

Focus: the confirmed 4h EMA alignment filter gates entries correctly,
and the 4h aggregation has no lookahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.types import Bar, BarSeries
from src.strategies.bbkc_squeeze_htf_trend import (
    BBKCSqueezeHTFTrend,
    _build_confirmed_4h_ema,
)


H = 3_600_000  # 1h in ms


class MockBroker:
    def __init__(self):
        self.buys = []
        self.sells = []
        self.positions = {}

    def buy(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.buys.append((symbol, qty, stop_loss, take_profit, reason))
        return "mock_buy"

    def sell(self, symbol, qty, stop_loss, take_profit=None, reason=""):
        self.sells.append((symbol, qty, stop_loss, take_profit, reason))
        return "mock_sell"

    def close(self, symbol, reason=""):
        self.positions.pop(symbol, None)
        return "mock_close"

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def calc_qty(self, symbol, risk_pct, stop_distance):
        return 1.0

    def update_stop(self, symbol, new_stop):
        pos = self.positions.get(symbol)
        if pos:
            pos.stop_loss = new_stop


def _make_1h_series(closes, highs=None, lows=None):
    """Build a 1h BarSeries with timestamp column set to 0, H, 2H, ...
    The timestamps matter: _build_confirmed_4h_ema uses them to bucket
    into 4h groups."""
    n = len(closes)
    if highs is None:
        highs = [c + 0.5 for c in closes]
    if lows is None:
        lows = [c - 0.5 for c in closes]
    df = pd.DataFrame({
        "timestamp": [i * H for i in range(n)],
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": [1000.0] * n, "turnover": [1.0] * n,
    })
    return BarSeries(symbol="BTCUSDT", timeframe="1h", bars=df)


class TestHTFEMAAggregation:
    """Unit test the 1h -> 4h EMA alignment helper directly."""

    def test_empty_series_returns_empty_array(self):
        series = _make_1h_series([])
        out = _build_confirmed_4h_ema(series, period=5)
        assert len(out) == 0

    def test_early_bars_have_nan(self):
        """The first few 1h bars have no confirmed 4h bar yet because
        bucket 0 is still open. Bars in bucket 0 see confirmed bucket
        -1 which does not exist -> NaN."""
        closes = [100.0] * 8  # 2 full 4h buckets
        series = _make_1h_series(closes)
        out = _build_confirmed_4h_ema(series, period=1)
        # Bars 0..3 are in bucket 0, confirmed bucket = -1 -> NaN
        assert np.all(np.isnan(out[0:4]))
        # Bars 4..7 are in bucket 1, confirmed bucket = 0 -> has value
        assert not np.isnan(out[4])

    def test_confirmed_ema_matches_manual_calculation(self):
        """For a simple monotonic fixture, verify the aligned 4h EMA
        matches a manual 4h resample + EMA(1) = just the 4h close."""
        # 12 1h bars: closes = [100, 101, 102, 103, 200, 201, 202, 203, 300, 301, 302, 303]
        # 4h buckets:
        #   bucket 0 (bars 0..3): open=100, close=103
        #   bucket 1 (bars 4..7): open=200, close=203
        #   bucket 2 (bars 8..11): open=300, close=303
        # EMA(1) of 4h closes = [103, 203, 303]
        #
        # At 1h bar 0..3 (in bucket 0): confirmed = -1 -> NaN
        # At 1h bar 4..7 (in bucket 1): confirmed = bucket 0 -> 103
        # At 1h bar 8..11 (in bucket 2): confirmed = bucket 1 -> 203
        closes = [100.0, 101.0, 102.0, 103.0,
                  200.0, 201.0, 202.0, 203.0,
                  300.0, 301.0, 302.0, 303.0]
        series = _make_1h_series(closes)
        out = _build_confirmed_4h_ema(series, period=1)
        assert np.all(np.isnan(out[0:4]))
        assert np.allclose(out[4:8], 103.0)
        assert np.allclose(out[8:12], 203.0)

    def test_no_lookahead(self):
        """Computing the confirmed 4h EMA on the full series should
        agree, at every 1h index i, with computing it on the series
        truncated to [0..i+1]. Any disagreement means the aligned EMA
        at position i used data from bars > i."""
        closes = [100.0 + 0.5 * i for i in range(40)]
        full = _make_1h_series(closes)
        out_full = _build_confirmed_4h_ema(full, period=2)
        for i in range(40):
            partial = _make_1h_series(closes[: i + 1])
            out_partial = _build_confirmed_4h_ema(partial, period=2)
            # Both arrays must have length i+1 and match at index i
            assert len(out_partial) == i + 1
            a = out_full[i]
            b = out_partial[i]
            if np.isnan(a) and np.isnan(b):
                continue
            assert np.isclose(a, b), (
                f"lookahead at i={i}: full={a} partial={b}"
            )


class TestBBKCSqueezeHTFTrendBasics:
    def test_name_and_params(self):
        s = BBKCSqueezeHTFTrend()
        assert s.name == "BBKCSqueeze_HTF_Trend"
        assert s.htf_ema_period == 50
        params = s.get_params()
        assert "htf_ema_period" in params
        assert params["htf_ema_period"] == 50

    def test_warmup_accounts_for_htf(self):
        s = BBKCSqueezeHTFTrend(htf_ema_period=50)
        # Needs at least 50 4h bars = 200 1h bars
        assert s.warmup_bars >= 200


class TestBBKCSqueezeHTFTrendGate:
    """The gate only allows entries when 1h close aligns with 4h EMA
    direction. We verify this by constructing a minimal squeeze-release
    fixture and direct-patching the cache htf_ema value."""

    def _squeeze_release_fixture(self) -> "tuple[BarSeries, int]":
        """Return a series with a clear BB/KC squeeze release at a known
        1h index. BBKC's squeeze detection uses 20-bar rolling BB std
        and 20-bar KC ATR, so we need ~40+ bars to form a squeeze.

        Fixture: 30 flat bars (very tight close, wide intra-bar range)
        so BB contracts inside KC; then an expansion bar that breaks
        BB out of KC. Squeeze release at the expansion bar.
        """
        rng = np.random.default_rng(0)
        n = 60
        closes = []
        highs = []
        lows = []
        price = 100.0
        for i in range(n - 1):
            # Tight close with wide high-low range keeps BB small and
            # KC large -> squeeze ON
            price += rng.normal(0.0, 0.01)
            c = float(price)
            h = c + 0.6
            low = c - 0.6
            closes.append(c)
            highs.append(h)
            lows.append(low)
        # Release bar: directional jump so BB widens past KC next bar.
        # For the first release to be detected we need squeeze_on[i-1]==1
        # and squeeze_on[i]==0. Make the last few bars expansive.
        # Turn on expansion starting at bar n-5 with accelerating moves.
        for j in range(5):
            idx = n - 5 + j
            price += 2.0 * (j + 1)
            c = float(price)
            h = c + 0.5
            low = c - 0.5
            if idx < len(closes):
                closes[idx] = c
                highs[idx] = h
                lows[idx] = low
            else:
                closes.append(c)
                highs.append(h)
                lows.append(low)
        series = _make_1h_series(closes, highs, lows)
        return series, n - 1  # last bar index

    def test_gate_blocks_long_when_htf_below_close(self):
        """Build a squeeze release fixture, then directly patch the
        htf_ema_4h cache to a value ABOVE the release close so the
        long branch is blocked.

        'close > htf' is required for long; so if htf > close, long
        is blocked. Meanwhile close > bb_mid means the baseline would
        go long; HTF rejects it.
        """
        series, i = self._squeeze_release_fixture()
        s = BBKCSqueezeHTFTrend(
            bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
            atr_period=14, rsi_period=14, rsi_filter=70.0,
            htf_ema_period=50,
        )
        broker = MockBroker()
        cache = s.prepare(series)

        # Patch the htf_ema_4h value at index i to force gate block.
        # We need htf > close (so close < htf -> long blocked).
        close_i = series.bars["close"].iloc[i]
        cache.arrays["htf_ema_4h"][i] = close_i + 100.0  # way above

        bar = Bar(
            "BTCUSDT",
            int(series.bars["timestamp"].iloc[i]),
            "1h",
            float(series.bars["open"].iloc[i]),
            float(series.bars["high"].iloc[i]),
            float(series.bars["low"].iloc[i]),
            float(close_i),
            1000,
        )
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.buys) == 0
        assert len(broker.sells) == 0

    def test_gate_blocks_short_when_htf_above_close(self):
        """Mirror: htf < close, so close > htf, baseline would go
        short (close < bb_mid), HTF rejects (short needs close < htf)."""
        series, i = self._squeeze_release_fixture()
        # Force a SHORT scenario by inverting the release direction.
        # Mutate the last 5 bars to break DOWN instead of up.
        mutable = series.bars.copy()
        base_price = float(mutable["close"].iloc[i - 5])
        for j in range(5):
            idx = i - 4 + j
            if idx >= len(mutable):
                continue
            new_close = base_price - 2.0 * (j + 1)
            mutable.at[idx, "close"] = new_close
            mutable.at[idx, "high"] = new_close + 0.5
            mutable.at[idx, "low"] = new_close - 0.5
            mutable.at[idx, "open"] = new_close + 0.2
        series_down = BarSeries(
            symbol="BTCUSDT", timeframe="1h", bars=mutable,
        )
        s = BBKCSqueezeHTFTrend(
            bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
            atr_period=14, rsi_period=14, rsi_filter=70.0,
            htf_ema_period=50,
        )
        broker = MockBroker()
        cache = s.prepare(series_down)
        close_i = float(series_down.bars["close"].iloc[i])
        # Force htf way below close so short is blocked (short needs
        # close < htf, but close > htf here).
        cache.arrays["htf_ema_4h"][i] = close_i - 100.0
        bar = Bar(
            "BTCUSDT",
            int(series_down.bars["timestamp"].iloc[i]),
            "1h",
            float(series_down.bars["open"].iloc[i]),
            float(series_down.bars["high"].iloc[i]),
            float(series_down.bars["low"].iloc[i]),
            close_i,
            1000,
        )
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.sells) == 0
        assert len(broker.buys) == 0

    def test_gate_allows_entry_when_htf_aligned(self):
        """Squeeze release in the long direction AND htf below close
        (long direction aligned). The organic squeeze fixture is
        fragile, so we patch the cache arrays directly to guarantee
        every non-HTF precondition is satisfied and then verify the
        HTF-aligned path fires.
        """
        # Need a long-enough series so indices are in range
        closes = [100.0 + 0.01 * i for i in range(60)]
        series = _make_1h_series(closes)
        s = BBKCSqueezeHTFTrend(
            bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
            atr_period=14, rsi_period=14, rsi_filter=70.0,
            htf_ema_period=50,
        )
        broker = MockBroker()
        cache = s.prepare(series)

        # Pick an index well past warmup for the sub-indicators used
        # (bb_period=20, kc_period=20 -> 20+ fine)
        i = 55
        close_i = 100.0

        # Force all non-HTF conditions to pass:
        #   squeeze_prev = 1 (was squeezing)
        #   squeeze_now = 0 (just released)
        #   bb_mid < close (long direction)
        #   rsi < 70 (not overbought)
        #   htf_ema_4h < close (long aligned)
        cache.arrays["squeeze_on"][i - 1] = 1.0
        cache.arrays["squeeze_on"][i] = 0.0
        cache.arrays["bb_mid"][i] = close_i - 1.0   # close > bb_mid -> long
        cache.arrays["rsi"][i] = 50.0               # not overbought
        cache.arrays["htf_ema_4h"][i] = close_i - 5.0  # long aligned

        bar = Bar(
            "BTCUSDT",
            int(series.bars["timestamp"].iloc[i]),
            "1h",
            close_i,   # open
            close_i + 0.5,  # high
            close_i - 0.5,  # low
            close_i,   # close
            1000,
        )
        s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.buys) == 1, (
            "all preconditions satisfied (squeeze release + long "
            "direction + RSI pass + HTF aligned) should fire a long"
        )
        assert len(broker.sells) == 0


class TestBBKCSqueezeHTFTrendBaselineRegression:
    """Sanity: parent's logic still works when HTF is NaN (very early
    bars) -- no crash, no entry."""

    def test_nan_htf_blocks_entry(self):
        """If htf_ema_4h is NaN at index i (early warmup), the gate
        must block entry without crashing."""
        closes = [100.0 + 0.01 * i for i in range(20)]
        series = _make_1h_series(closes)
        s = BBKCSqueezeHTFTrend()
        broker = MockBroker()
        cache = s.prepare(series)
        # At early bars, everything is NaN; on_bar_fast should just
        # return without firing or crashing.
        for i in range(len(closes)):
            bar = Bar(
                "BTCUSDT",
                int(series.bars["timestamp"].iloc[i]),
                "1h",
                float(series.bars["open"].iloc[i]),
                float(series.bars["high"].iloc[i]),
                float(series.bars["low"].iloc[i]),
                float(series.bars["close"].iloc[i]),
                1000,
            )
            s.on_bar_fast(bar, i, cache, broker)
        assert len(broker.buys) == 0
        assert len(broker.sells) == 0
