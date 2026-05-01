"""Core 메트릭 (Phase 2 PR 18, spec §10.5).

``compute_core_metrics(equity_series, *, periods_per_year)`` —
``build_equity_series`` (PR 10) 결과를 입력으로 받아 통계 dict 반환:

- ``total_return`` (float, 단위: 비율 — 0.05 = +5%)
- ``sharpe_ratio`` (float — annualized)
- ``sortino_ratio`` (float — annualized, MAR=0 표준 downside deviation 기반)
- ``max_drawdown_pct`` (float, 단위: 비율 — -0.10 = -10%)
- ``max_drawdown_duration_bars`` (int — 연속 drawdown 봉 수)
- ``calmar_ratio`` (float — CAGR / |MDD|, periods_per_year 사용)
- ``annual_volatility`` (float, 비율 — std × sqrt(periods_per_year))
- ``n_periods`` (int — equity_series.height)

수익률은 봉 단위 ``pct_change``. ``periods_per_year`` 가 봉 빈도와 일치해야 한다:
- 1d crypto: 365 (default)
- 1d 주식: 252
- 1h crypto: 24*365 = 8760
- 1h 주식: 24*252 = 6048

**Sortino 정의 (MAR=0)**: downside deviation = sqrt(mean(min(r, 0)²)) — 모든 봉의
음수 초과수익을 제곱·평균·제곱근. 단순히 음수 수익률 부분집합의 std 가 아님 (그
방식은 손실폭이 일정하면 분산 ≈ 0 으로 sortino 가 비정상적으로 커짐).

**Calmar 정의**: CAGR / |MDD|. CAGR = ``(eq_last/eq_first)^(1/years) − 1`` 에서
``years = (n_periods − 1) / periods_per_year``. ``n_periods − 1`` 은 ``pct_change`` 가
실제로 만드는 return 구간 수 — Sharpe/Sortino/Vol 가 같은 ``n−1`` returns 위에서
``sqrt(periods_per_year)`` 로 연환산하는 정의와 정렬된다.

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

            # 표준 Sortino (MAR=0): downside deviation = sqrt(mean(min(r, 0)^2))
            # 음수 수익률 부분집합의 std 가 아니라 전체 봉에 대한 negative-excess 제곱평균.
            neg_excess = returns.clip(upper_bound=0.0)  # min(r, 0)
            sq_neg = neg_excess * neg_excess
            mean_sq_val = cast(float | None, sq_neg.mean())
            mean_sq_neg = float(mean_sq_val) if mean_sq_val is not None else 0.0
            if mean_sq_neg > 0:
                downside_dev = math.sqrt(mean_sq_neg)
                sortino = (mean_ret / downside_dev) * ann_factor
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

    # Calmar = CAGR / |MDD|. CAGR 는 returns 구간 수 (n - 1) 로 환산해 Sharpe/
    # Vol 와 동일한 시간 기반을 쓴다 (pct_change 는 n - 1 개 return 을 만든다).
    # eq_first <= 0 은 BacktestConfig 가 차단 (initial_equity > 0). eq_last <= 0
    # (catastrophic loss) 은 power 가 정의되지 않아 nan 처리.
    if (
        n >= 2
        and eq_first > 0
        and eq_last > 0
        and periods_per_year > 0
    ):
        years = (n - 1) / periods_per_year
        cagr = (eq_last / eq_first) ** (1.0 / years) - 1.0
    else:
        cagr = float("nan")

    if mdd_pct < 0 and not math.isnan(cagr):
        calmar = cagr / abs(mdd_pct)
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
