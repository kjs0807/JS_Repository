"""전략 스코어링 및 Top N 선정 모듈.

여러 전략의 BacktestResult를 수집해 가중 스코어를 산출하고
상위 N개 전략을 선정한다.
"""

import logging
from typing import List

import numpy as np
import pandas as pd

from engine.backtest import BacktestResult

logger = logging.getLogger(__name__)

# 스코어링 가중치 정의
SCORE_WEIGHTS: dict = {
    "sharpe_ratio": 0.25,
    "profit_factor": 0.20,
    "win_rate": 0.15,
    "max_drawdown_inv": 0.15,   # 낙폭 역수 (낮을수록 좋음)
    "oos_retention": 0.15,      # Walk-Forward OOS 성과 유지율
    "multi_asset_consistency": 0.10,
}


class StrategyScorer:
    """전략 가중 스코어링 및 Top N 선정.

    각 지표를 0~10으로 정규화한 뒤 가중합으로 종합 점수를 산출한다.
    oos_retention과 multi_asset_consistency는 외부에서 주입 가능하다.

    Attributes:
        weights: 지표별 가중치 딕셔너리
    """

    def __init__(self, weights: dict = None) -> None:
        """StrategyScorer 초기화.

        Args:
            weights: 커스텀 가중치 딕셔너리. None이면 기본값 사용.
        """
        self.weights = weights if weights is not None else dict(SCORE_WEIGHTS)

    def score(
        self,
        results: List[BacktestResult],
        oos_retention: dict = None,
        multi_asset_consistency: dict = None,
    ) -> pd.DataFrame:
        """BacktestResult 목록으로 스코어 DataFrame을 생성한다.

        Args:
            results: BacktestResult 리스트 (심볼별 집계 포함)
            oos_retention: 전략명 → OOS 유지율 딕셔너리. None이면 0.5 가정.
            multi_asset_consistency: 전략명 → 멀티자산 일관성. None이면 0.5 가정.

        Returns:
            전략별 스코어 DataFrame.
            컬럼: strategy_name, sharpe_raw, profit_factor_raw, win_rate_raw,
                  max_drawdown_raw, oos_retention_raw, multi_asset_consistency_raw,
                  sharpe_score, ..., total_score
        """
        if not results:
            logger.warning("스코어링할 결과가 없음")
            return pd.DataFrame()

        # 전략별 집계 (심볼별 평균)
        strategy_groups: dict = {}
        for r in results:
            name = r.strategy_name
            if name not in strategy_groups:
                strategy_groups[name] = []
            strategy_groups[name].append(r)

        rows = []
        for strategy_name, result_list in strategy_groups.items():
            # 심볼별 평균 지표
            sharpe_avg = float(np.mean([r.sharpe_ratio for r in result_list]))
            pf_avg = float(np.mean([
                min(r.profit_factor, 10.0) for r in result_list  # 무한대 캡핑
            ]))
            wr_avg = float(np.mean([r.win_rate for r in result_list]))
            dd_avg = float(np.mean([r.max_drawdown for r in result_list]))

            oos = float((oos_retention or {}).get(strategy_name, 0.5))
            mac = float((multi_asset_consistency or {}).get(strategy_name, 0.5))

            rows.append({
                "strategy_name": strategy_name,
                "sharpe_raw": sharpe_avg,
                "profit_factor_raw": pf_avg,
                "win_rate_raw": wr_avg,
                "max_drawdown_raw": dd_avg,
                "oos_retention_raw": oos,
                "multi_asset_consistency_raw": mac,
                "total_trades": int(sum(r.total_trades for r in result_list)),
                "total_pnl": float(sum(r.total_pnl for r in result_list)),
            })

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        # 정규화 (0~10)
        df["sharpe_score"] = self._normalize(df["sharpe_raw"], higher_better=True)
        df["profit_factor_score"] = self._normalize(df["profit_factor_raw"], higher_better=True)
        df["win_rate_score"] = self._normalize(df["win_rate_raw"], higher_better=True)
        df["max_drawdown_score"] = self._normalize(df["max_drawdown_raw"], higher_better=False)
        df["oos_retention_score"] = self._normalize(df["oos_retention_raw"], higher_better=True)
        df["multi_asset_consistency_score"] = self._normalize(
            df["multi_asset_consistency_raw"], higher_better=True
        )

        # 가중 합산
        df["total_score"] = (
            df["sharpe_score"] * self.weights.get("sharpe_ratio", 0.25)
            + df["profit_factor_score"] * self.weights.get("profit_factor", 0.20)
            + df["win_rate_score"] * self.weights.get("win_rate", 0.15)
            + df["max_drawdown_score"] * self.weights.get("max_drawdown_inv", 0.15)
            + df["oos_retention_score"] * self.weights.get("oos_retention", 0.15)
            + df["multi_asset_consistency_score"] * self.weights.get("multi_asset_consistency", 0.10)
        )

        df = df.sort_values("total_score", ascending=False).reset_index(drop=True)
        logger.info("스코어링 완료:\n%s", df[["strategy_name", "total_score", "total_trades"]].to_string())
        return df

    def select_top(self, scores: pd.DataFrame, n: int = 3) -> List[str]:
        """스코어 DataFrame에서 상위 N개 전략명을 반환한다.

        최소 거래 수(30건) 미달 전략은 제외한다.

        Args:
            scores: score() 반환 DataFrame
            n: 선택할 전략 수

        Returns:
            상위 N개 전략명 리스트 (점수 내림차순)
        """
        if scores.empty:
            return []

        # 최소 거래 수 필터
        filtered = scores[scores["total_trades"] >= 30]
        if filtered.empty:
            logger.warning("최소 거래 수(30건) 충족 전략 없음 - 필터 해제")
            filtered = scores

        top_n = filtered.head(n)["strategy_name"].tolist()
        logger.info("Top %d 전략 선정: %s", n, top_n)
        return top_n

    @staticmethod
    def _normalize(series: pd.Series, higher_better: bool = True) -> pd.Series:
        """시리즈를 0~10으로 Min-Max 정규화한다.

        Args:
            series: 정규화할 수치 시리즈
            higher_better: True이면 높은 값이 10, False이면 낮은 값이 10

        Returns:
            0~10 정규화된 시리즈
        """
        min_val = series.min()
        max_val = series.max()

        if max_val == min_val:
            # 모두 동일하면 중간값(5) 부여
            return pd.Series([5.0] * len(series), index=series.index)

        normalized = (series - min_val) / (max_val - min_val) * 10.0

        if not higher_better:
            normalized = 10.0 - normalized

        return normalized


def calc_calmar_ratio(equity_curve: list, initial_capital: float) -> float:
    """Calmar Ratio를 계산한다 (연환산 수익률 / 최대 낙폭).

    Args:
        equity_curve: 시간순 에퀴티 리스트
        initial_capital: 초기 자본

    Returns:
        Calmar Ratio. MDD가 0이면 0.0 반환.
    """
    if not equity_curve or len(equity_curve) < 2:
        return 0.0

    arr = np.array(equity_curve, dtype=float)
    total_return = (arr[-1] - initial_capital) / initial_capital

    # 최대 낙폭 (MDD)
    peak = np.maximum.accumulate(arr)
    drawdown = (peak - arr) / np.where(peak > 0, peak, 1.0)
    mdd = float(np.max(drawdown))

    if mdd <= 0:
        return 0.0

    return float(total_return / mdd)


def calc_monthly_winrate_std(trades: list) -> float:
    """월별 승률의 표준편차를 계산한다 (안정성 지표).

    Args:
        trades: 거래 딕셔너리 리스트. 각 항목에 'exit_time'(ISO), 'net_pnl'(float) 필요.

    Returns:
        월별 승률 표준편차 (0~1). 데이터 부족 시 1.0 반환.
    """
    if not trades or len(trades) < 5:
        return 1.0

    monthly_wins: dict = {}
    monthly_total: dict = {}

    for t in trades:
        exit_time = t.get("exit_time", "")
        if not exit_time or len(exit_time) < 7:
            continue
        month_key = exit_time[:7]  # "YYYY-MM"
        monthly_total[month_key] = monthly_total.get(month_key, 0) + 1
        if t.get("net_pnl", 0) > 0:
            monthly_wins[month_key] = monthly_wins.get(month_key, 0) + 1

    if len(monthly_total) < 2:
        return 1.0

    winrates = []
    for month, total in monthly_total.items():
        wins = monthly_wins.get(month, 0)
        winrates.append(wins / total)

    import statistics
    return float(statistics.stdev(winrates))


def calc_oos_retention(is_sharpe: float, oos_sharpe: float) -> float:
    """OOS Sharpe 유지율을 계산한다.

    Args:
        is_sharpe: In-Sample Sharpe Ratio
        oos_sharpe: Out-of-Sample Sharpe Ratio

    Returns:
        OOS 유지율 (0~1). IS Sharpe가 0이면 0.0.
    """
    if is_sharpe <= 0:
        return 0.0
    return max(0.0, min(1.0, oos_sharpe / is_sharpe))


__all__ = [
    "StrategyScorer",
    "SCORE_WEIGHTS",
    "calc_calmar_ratio",
    "calc_monthly_winrate_std",
    "calc_oos_retention",
]
