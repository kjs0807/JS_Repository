"""Sharpe 연환산 표준 모듈.

timeframe-agnostic daily 집계 방식으로 Sharpe를 계산한다.
bar-level equity curve → daily returns → sqrt(365) 연환산.

모든 스크립트/엔진에서 이 모듈을 단일 소스로 사용해야 한다.
"""

import math
from typing import Dict

import numpy as np
import pandas as pd

# timeframe 문자열 → 연간 봉 수 매핑 (24/7 암호화폐 기준)
_BARS_PER_YEAR: Dict[str, int] = {
    "15m": 35040,   # 365 * 24 * 4
    "30m": 17520,   # 365 * 24 * 2
    "1h":   8760,   # 365 * 24
    "4h":   2190,   # 365 * 6
    "1d":    365,
}


def bars_per_year(timeframe: str) -> int:
    """timeframe 문자열 → 연간 봉 수.

    Args:
        timeframe: '15m', '30m', '1h', '4h', '1d' 중 하나.

    Returns:
        연간 봉 수 (정수).

    Raises:
        ValueError: 지원하지 않는 timeframe 문자열.
    """
    tf = timeframe.lower().strip()
    if tf not in _BARS_PER_YEAR:
        raise ValueError(
            f"지원하지 않는 timeframe: {timeframe!r}. "
            f"허용값: {sorted(_BARS_PER_YEAR.keys())}"
        )
    return _BARS_PER_YEAR[tf]


def daily_sharpe(equity_curve_df: pd.DataFrame) -> float:
    """bar-level equity curve → daily 집계 → sqrt(365) 연환산 Sharpe.

    Args:
        equity_curve_df: 인덱스가 tz-aware UTC DatetimeIndex이고
                         'equity' 컬럼을 포함하는 DataFrame.

    Returns:
        연환산 daily Sharpe (float).
        일별 수익률 데이터가 30일 미만이거나 std == 0이면 0.0 반환.
    """
    if "equity" not in equity_curve_df.columns:
        return 0.0

    equity = equity_curve_df["equity"]

    if len(equity) < 2:
        return 0.0

    # bar-level → daily 합산 수익률
    bar_returns = equity.pct_change().dropna()
    daily_returns = bar_returns.resample("1D").sum()

    if len(daily_returns) < 30:
        return 0.0

    mean_r = daily_returns.mean()
    std_r = daily_returns.std(ddof=1)

    if std_r == 0 or math.isnan(std_r) or math.isnan(mean_r):
        return 0.0

    return float(mean_r / std_r * math.sqrt(365))


def annualized_metrics(equity_curve_df: pd.DataFrame) -> dict:
    """sharpe, calmar, max_drawdown, annual_return을 모두 계산한다.

    Args:
        equity_curve_df: 인덱스가 tz-aware UTC DatetimeIndex이고
                         'equity' 컬럼을 포함하는 DataFrame.

    Returns:
        dict with keys: sharpe, calmar, max_drawdown, annual_return.
        계산 불가 항목은 0.0.
    """
    result = {
        "sharpe": 0.0,
        "calmar": 0.0,
        "max_drawdown": 0.0,
        "annual_return": 0.0,
    }

    if "equity" not in equity_curve_df.columns or len(equity_curve_df) < 2:
        return result

    equity = equity_curve_df["equity"]

    # Sharpe
    result["sharpe"] = daily_sharpe(equity_curve_df)

    # Max Drawdown
    cummax = equity.cummax()
    drawdown = (equity - cummax) / (cummax + 1e-9)
    max_dd = float(drawdown.min())   # 음수 또는 0
    result["max_drawdown"] = max_dd

    # Annual Return (단순 총 수익률 / 보유 연수)
    n_days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0
    if n_days > 0:
        total_return = (equity.iloc[-1] - equity.iloc[0]) / (equity.iloc[0] + 1e-9)
        annual_return = total_return / (n_days / 365.0)
        result["annual_return"] = float(annual_return)

    # Calmar
    if abs(max_dd) > 1e-9:
        result["calmar"] = float(result["annual_return"] / abs(max_dd))

    return result


__all__ = ["bars_per_year", "daily_sharpe", "annualized_metrics"]
