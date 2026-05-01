"""Core 메트릭 (Phase 2 PR 18, spec §10.5).

``compute_core_metrics(equity_series, *, periods_per_year)`` —
``build_equity_series`` (PR 10) 결과를 입력으로 받아 통계 dict 반환:

- ``total_return`` (float, 단위: 비율 — 0.05 = +5%)
- ``sharpe_ratio`` (float)
- ``sortino_ratio`` (float)
- ``max_drawdown_pct`` (float, 단위: 비율 — -0.10 = -10%)
- ``max_drawdown_duration_bars`` (int — 연속 drawdown 봉 수)
- ``calmar_ratio`` (float)
- ``annual_volatility`` (float, 비율)
- ``n_periods`` (int — equity_series.height)

수익률은 봉 단위 ``pct_change``. ``periods_per_year`` 가 봉 빈도와 일치해야 한다:
- 1d crypto: 365 (default)
- 1d 주식: 252
- 1h crypto: 24*365 = 8760
- 1h 주식: 24*252 = 6048

quantstats 의존성을 추가하지 않는다 — polars + stdlib 만 사용. 더 풍부한 메트릭은 추후
PR 또는 별도 통합 레이어에서 도입.

``daily_resample`` (spec §10.6) — UTC 00:00 origin daily 리샘플 헬퍼.
"""

from __future__ import annotations

import math
from typing import Any, cast

import polars as pl


def _safe_div(numerator: float, denominator: float) -> float:
    """0 / 0 = nan, x / 0 = nan (수익률 통계에서 fail-loud 대신 nan)."""
    if denominator == 0:
        return float("nan")
    return numerator / denominator


def _max_drawdown_duration(equity_series: pl.DataFrame) -> int:
    """``drawdown_pct < 0`` 이 연속으로 유지된 최대 봉 수.

    drawdown_pct == 0 (또는 null) 인 봉에서 카운터 리셋.
    """
    dd = equity_series["drawdown_pct"].to_list()
    max_dur = 0
    cur = 0
    for d in dd:
        if d is None or d >= 0:
            cur = 0
        else:
            cur += 1
            if cur > max_dur:
                max_dur = cur
    return max_dur


def compute_core_metrics(
    equity_series: pl.DataFrame,
    *,
    periods_per_year: int = 365,
) -> dict[str, Any]:
    """``equity_series`` (``build_equity_series`` 출력) 으로부터 핵심 통계 dict 반환.

    빈 시리즈 또는 1봉 시리즈에서는 의미 있는 통계 산출 불가 → 0/nan 기본값.
    """
    n = equity_series.height
    if n == 0:
        return {
            "total_return": 0.0,
            "sharpe_ratio": float("nan"),
            "sortino_ratio": float("nan"),
            "max_drawdown_pct": 0.0,
            "max_drawdown_duration_bars": 0,
            "calmar_ratio": float("nan"),
            "annual_volatility": 0.0,
            "n_periods": 0,
        }

    eq_first = float(equity_series["equity"][0])
    eq_last = float(equity_series["equity"][-1])
    total_return = _safe_div(eq_last - eq_first, eq_first)

    if n < 2:
        sharpe = float("nan")
        sortino = float("nan")
        ann_vol = 0.0
    else:
        returns = equity_series.with_columns(
            pl.col("equity").pct_change().alias("_ret")
        )["_ret"].drop_nulls()

        if returns.len() == 0:
            sharpe = float("nan")
            sortino = float("nan")
            ann_vol = 0.0
        else:
            mean_val = cast(float | None, returns.mean())
            mean_ret = float(mean_val) if mean_val is not None else 0.0
            std_val = cast(float | None, returns.std())
            std_ret = float(std_val) if std_val is not None else 0.0
            ann_factor = math.sqrt(periods_per_year)
            ann_vol = std_ret * ann_factor

            if std_ret > 0:
                sharpe = (mean_ret / std_ret) * ann_factor
            else:
                sharpe = float("nan")

            downside = returns.filter(returns < 0)
            if downside.len() > 0:
                ds_std_val = cast(float | None, downside.std())
                ds_std = float(ds_std_val) if ds_std_val is not None else 0.0
                if ds_std > 0:
                    sortino = (mean_ret / ds_std) * ann_factor
                else:
                    sortino = float("nan")
            else:
                # downside 없음 = 모든 기간 비음수 수익 → sortino 무한 (관례적으로 nan)
                sortino = float("nan")

    if "drawdown_pct" in equity_series.columns:
        mdd_val = cast(float | None, equity_series["drawdown_pct"].min())
        mdd_pct = float(mdd_val) if mdd_val is not None else 0.0
        mdd_duration = _max_drawdown_duration(equity_series)
    else:
        mdd_pct = 0.0
        mdd_duration = 0

    if mdd_pct < 0:
        # Calmar 통상 정의: annualized return / |MDD|. 단순화: total_return / |MDD|.
        # 여러 기간 비교는 caller 가 periods_per_year 로 보정.
        calmar = _safe_div(total_return, abs(mdd_pct))
    else:
        calmar = float("nan")

    return {
        "total_return": total_return,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": mdd_pct,
        "max_drawdown_duration_bars": mdd_duration,
        "calmar_ratio": calmar,
        "annual_volatility": ann_vol,
        "n_periods": n,
    }


def daily_resample(equity_series: pl.DataFrame) -> pl.DataFrame:
    """UTC 자정 기준 일별 리샘플 — 각 날짜의 마지막 equity (spec §10.6).

    출력 컬럼: ``timestamp`` (Datetime UTC us, 자정), ``equity`` (Float64).
    """
    if equity_series.height == 0:
        return pl.DataFrame(
            schema={
                "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
                "equity": pl.Float64,
            }
        )
    return (
        equity_series.sort("timestamp")
        .group_by_dynamic(
            "timestamp",
            every="1d",
            period="1d",
            closed="left",
            label="left",
        )
        .agg(pl.col("equity").last())
        .select(["timestamp", "equity"])
    )
