"""End-to-end smoke tests for the three rule-based improvement
variants (D2, D1, B1). Each variant is wired through the real
``BacktestEngine`` on a synthetic in-memory feed so we verify:
  - prepare()/on_bar_fast() round-trip without crashing
  - warmup skipping works
  - no exceptions during the bar loop
  - the broker receives at least one bar (result object populated)

We do not assert specific trade counts or P&L — that is what the real
holdout experiments measure. These tests only catch integration-level
breakage in the variant -> engine glue.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from src.backtester.engine import BacktestEngine, BacktestResult
from src.core.config import BacktestConfig, RiskConfig
from src.core.types import Bar, BarSeries
from src.data_manager.feed import DataFeed
from src.strategies.bbkc_squeeze_htf_trend import BBKCSqueezeHTFTrend
from src.strategies.donchian_fixed_rr_trend_filter import (
    DonchianFixedRRTrendFilter,
)
from src.strategies.donchian_trend_filter_adx import (
    DonchianTrendFilterADX20,
    DonchianTrendFilterADX25,
)


H = 3_600_000


class InMemoryFeed(DataFeed):
    """Minimal DataFeed for a single symbol backed by an in-memory
    DataFrame. Implements only what BacktestEngine.run() actually
    calls: next_bar / get_full_series / get_history / has_next / reset
    + the timeframe attribute.
    """

    def __init__(self, symbol: str, df: pd.DataFrame, timeframe: str = "1h"):
        self.symbol = symbol
        self.timeframe = timeframe
        self._df = df.reset_index(drop=True)
        self._idx = 0
        self._bar_count_val = 0

    def next_bar(self, symbol: str) -> Optional[Bar]:
        if symbol != self.symbol:
            return None
        if self._idx >= len(self._df):
            return None
        row = self._df.iloc[self._idx]
        self._idx += 1
        self._bar_count_val += 1
        return Bar(
            symbol=symbol,
            timestamp=int(row["timestamp"]),
            timeframe=self.timeframe,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            turnover=float(row.get("turnover", 1.0)),
        )

    def has_next(self) -> bool:
        return self._idx < len(self._df)

    def get_full_series(self, symbol: str) -> BarSeries:
        return BarSeries(
            symbol=symbol, timeframe=self.timeframe,
            bars=self._df.copy(),
        )

    def get_history(self, symbol: str, lookback: int) -> BarSeries:
        start = max(0, self._idx - lookback)
        return BarSeries(
            symbol=symbol, timeframe=self.timeframe,
            bars=self._df.iloc[start : self._idx].copy(),
        )

    @property
    def bar_count(self) -> int:
        return self._bar_count_val

    def reset(self) -> None:
        self._idx = 0
        self._bar_count_val = 0


def _make_synthetic_1h_df(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """Mixed trend + chop series so that at least some of the variants
    have reason to either fire or deliberately skip. The exact counts
    don't matter — we only assert the engine loop completes."""
    rng = np.random.default_rng(seed)
    closes = []
    price = 100.0
    for i in range(n):
        # 200-bar uptrend, then 200-bar downtrend: rough regime mix
        drift = 0.3 if i < n // 2 else -0.3
        price += rng.normal(drift, 0.5)
        closes.append(float(price))
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    opens = [c - 0.1 for c in closes]
    df = pd.DataFrame({
        "timestamp": [i * H for i in range(n)],
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": [1000.0] * n, "turnover": [1.0] * n,
    })
    return df


@pytest.fixture
def synthetic_feed():
    df = _make_synthetic_1h_df(n=400, seed=7)
    return InMemoryFeed(symbol="BTCUSDT", df=df, timeframe="1h")


class TestVariantEngineSmoke:
    """Every variant must run through the engine without exceptions
    and return a populated BacktestResult."""

    def test_donchian_fixed_rr_trend_filter_runs(self, synthetic_feed):
        strat = DonchianFixedRRTrendFilter(
            entry_period=10, atr_period=10, ema_filter=50,
            stop_atr=2.0, tp_r_ratio=2.0,
        )
        engine = BacktestEngine()
        result = engine.run(
            strategy=strat, data_feed=synthetic_feed,
            config=BacktestConfig(initial_capital=10_000.0),
            symbol="BTCUSDT",
            risk_config=RiskConfig(),
        )
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "Donchian_FixedRR_TrendFilter"
        # equity curve must exist (at minimum the initial capital)
        assert len(result.equity_curve) >= 1

    def test_donchian_trend_filter_adx20_runs(self, synthetic_feed):
        strat = DonchianTrendFilterADX20(
            entry_period=10, exit_period=5, ema_filter=50,
            atr_period=10, adx_period=14,
        )
        engine = BacktestEngine()
        result = engine.run(
            strategy=strat, data_feed=synthetic_feed,
            config=BacktestConfig(initial_capital=10_000.0),
            symbol="BTCUSDT",
            risk_config=RiskConfig(),
        )
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "Donchian_TrendFilter_ADX20"
        assert len(result.equity_curve) >= 1

    def test_donchian_trend_filter_adx25_runs(self, synthetic_feed):
        strat = DonchianTrendFilterADX25(
            entry_period=10, exit_period=5, ema_filter=50,
            atr_period=10, adx_period=14,
        )
        engine = BacktestEngine()
        result = engine.run(
            strategy=strat, data_feed=synthetic_feed,
            config=BacktestConfig(initial_capital=10_000.0),
            symbol="BTCUSDT",
            risk_config=RiskConfig(),
        )
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "Donchian_TrendFilter_ADX25"
        assert len(result.equity_curve) >= 1

    def test_bbkc_squeeze_htf_trend_runs(self, synthetic_feed):
        strat = BBKCSqueezeHTFTrend(
            bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
            atr_period=14, rsi_period=14, htf_ema_period=10,
        )
        engine = BacktestEngine()
        result = engine.run(
            strategy=strat, data_feed=synthetic_feed,
            config=BacktestConfig(initial_capital=10_000.0),
            symbol="BTCUSDT",
            risk_config=RiskConfig(),
        )
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "BBKCSqueeze_HTF_Trend"
        assert len(result.equity_curve) >= 1
