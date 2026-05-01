"""PR 18 viz/metrics 테스트 (Phase 2, spec §10.5/§10.6)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from backtester.viz.metrics import compute_core_metrics, daily_resample

UTC = timezone.utc


def _equity_df(values: list[float]) -> pl.DataFrame:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    timestamps = [base + timedelta(days=i) for i in range(len(values))]
    df = pl.DataFrame(
        {
            "timestamp": timestamps,
            "equity": values,
            "cash": values,
            "realized_pnl": [0.0] * len(values),
            "unrealized_pnl": [0.0] * len(values),
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("equity").cum_max().alias("_running_max"),
    )
    df = df.with_columns(
        (pl.col("equity") - pl.col("_running_max")).alias("drawdown"),
        pl.when(pl.col("_running_max") != 0)
        .then(
            (pl.col("equity") - pl.col("_running_max")) / pl.col("_running_max")
        )
        .otherwise(None)
        .alias("drawdown_pct"),
    ).drop("_running_max")
    return df


# ---------- compute_core_metrics 기본 동작 -----------------------------------


def test_compute_core_metrics_empty_dataframe() -> None:
    empty = pl.DataFrame(
        schema={
            "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "equity": pl.Float64,
            "drawdown_pct": pl.Float64,
        }
    )
    m = compute_core_metrics(empty)
    assert m["n_periods"] == 0
    assert m["total_return"] == 0.0
    assert math.isnan(m["sharpe_ratio"])
    assert m["max_drawdown_pct"] == 0.0


def test_compute_core_metrics_single_point() -> None:
    df = _equity_df([1000.0])
    m = compute_core_metrics(df)
    assert m["n_periods"] == 1
    assert m["total_return"] == 0.0
    assert math.isnan(m["sharpe_ratio"])
    assert math.isnan(m["sortino_ratio"])
    assert m["annual_volatility"] == 0.0


def test_compute_core_metrics_total_return_positive() -> None:
    df = _equity_df([1000.0, 1100.0])
    m = compute_core_metrics(df)
    assert m["total_return"] == pytest.approx(0.1)


def test_compute_core_metrics_total_return_negative() -> None:
    df = _equity_df([1000.0, 800.0])
    m = compute_core_metrics(df)
    assert m["total_return"] == pytest.approx(-0.2)


def test_compute_core_metrics_constant_equity_zero_volatility() -> None:
    """std=0 → sharpe nan, vol 0, drawdown 0."""
    df = _equity_df([1000.0] * 5)
    m = compute_core_metrics(df)
    assert m["annual_volatility"] == 0.0
    assert math.isnan(m["sharpe_ratio"])
    assert m["max_drawdown_pct"] == 0.0


def test_compute_core_metrics_max_drawdown_basic() -> None:
    """1000 → 1200 → 900 → 1100. running_max=[1000,1200,1200,1200].
    drawdown_pct=[0, 0, -0.25, -0.0833]. min = -0.25."""
    df = _equity_df([1000.0, 1200.0, 900.0, 1100.0])
    m = compute_core_metrics(df)
    assert m["max_drawdown_pct"] == pytest.approx(-0.25)
    # duration: 봉 인덱스 2,3 (drawdown<0 연속) → 2
    assert m["max_drawdown_duration_bars"] == 2


def test_compute_core_metrics_calmar_when_drawdown() -> None:
    """total_return = (900-1000)/1000 = -0.1, MDD = -0.1 → calmar = -0.1 / 0.1 = -1.0"""
    df = _equity_df([1000.0, 900.0])
    m = compute_core_metrics(df)
    assert m["total_return"] == pytest.approx(-0.1)
    assert m["max_drawdown_pct"] == pytest.approx(-0.1)
    assert m["calmar_ratio"] == pytest.approx(-1.0)


def test_compute_core_metrics_calmar_nan_when_no_drawdown() -> None:
    """단조 증가 → MDD=0 → calmar nan."""
    df = _equity_df([100.0, 110.0, 120.0])
    m = compute_core_metrics(df)
    assert m["max_drawdown_pct"] == 0.0
    assert math.isnan(m["calmar_ratio"])


def test_compute_core_metrics_sharpe_positive_for_upward_trend() -> None:
    """단조 증가 + 변동성 → sharpe 유한. periods_per_year=252 (일일 주식)."""
    df = _equity_df([100.0, 102.0, 101.5, 103.0, 105.0, 104.0, 107.0])
    m = compute_core_metrics(df, periods_per_year=252)
    assert math.isfinite(m["sharpe_ratio"])
    assert m["sharpe_ratio"] > 0


def test_compute_core_metrics_sortino_handles_no_downside() -> None:
    """수익률이 모두 비음수 → downside std 없음 → sortino nan (관례)."""
    df = _equity_df([100.0, 102.0, 105.0, 108.0])
    m = compute_core_metrics(df)
    assert math.isnan(m["sortino_ratio"])


def test_compute_core_metrics_periods_per_year_scales_volatility() -> None:
    """periods_per_year 가 4배면 annual_volatility 가 sqrt(4)=2배."""
    df = _equity_df([100.0, 102.0, 101.0, 103.0, 100.0])
    m1 = compute_core_metrics(df, periods_per_year=100)
    m2 = compute_core_metrics(df, periods_per_year=400)
    assert m2["annual_volatility"] == pytest.approx(m1["annual_volatility"] * 2)


# ---------- daily_resample --------------------------------------------------


def test_daily_resample_empty_returns_empty_with_schema() -> None:
    empty = pl.DataFrame(
        schema={
            "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "equity": pl.Float64,
        }
    )
    out = daily_resample(empty)
    assert out.height == 0
    assert "timestamp" in out.columns
    assert "equity" in out.columns


def test_daily_resample_picks_last_per_day() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [
                base.replace(hour=0),
                base.replace(hour=12),
                base.replace(hour=23),
                (base + timedelta(days=1)).replace(hour=6),
            ],
            "equity": [100.0, 101.0, 105.0, 110.0],
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )
    out = daily_resample(df)
    # day 1 last = 105 (23:00), day 2 last = 110 (06:00)
    assert out.height == 2
    assert out["equity"].to_list() == [105.0, 110.0]
