"""Top 3 전략 자동 선정 모듈.

전체 전략 × 전체 심볼 백테스트 → Walk-Forward 검증 → 스코어링 → Top 3 선정.
선정 결과는 JSON 파일로 저장한다.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from strategies.base import BaseStrategy
from engine.backtest import BacktestEngine, BacktestResult
from engine.scorer import StrategyScorer
from engine.walk_forward import WalkForwardAnalyzer, WalkForwardResult
from config.settings import BacktestConfig

logger = logging.getLogger(__name__)

# 선정 결과 저장 경로
_DEFAULT_SAVE_PATH = "logs/backtest_results/selection_result.json"


@dataclass
class SelectionResult:
    """전략 선정 결과 데이터 클래스.

    Attributes:
        top_strategies: 선정된 Top 3 전략명 리스트
        scores: 전체 전략 스코어 DataFrame
        backtest_results: 전략별 심볼별 BacktestResult 목록
        wf_results: 전략별 Walk-Forward 결과
        selection_reason: 선정 근거 딕셔너리 {전략명: 근거 설명}
    """

    top_strategies: List[str] = field(default_factory=list)
    scores: Optional[pd.DataFrame] = None
    backtest_results: List[BacktestResult] = field(default_factory=list)
    wf_results: Dict[str, WalkForwardResult] = field(default_factory=dict)
    selection_reason: Dict[str, str] = field(default_factory=dict)


class StrategySelector:
    """전체 전략 평가 후 Top 3 자동 선정.

    실행 순서:
        1. 각 전략 × 각 심볼 백테스트
        2. Walk-Forward 검증 (심볼별 OOS 유지율 계산)
        3. 종합 스코어링
        4. Top 3 선정 + 근거 출력
        5. 결과 JSON 저장

    Attributes:
        engine: BacktestEngine 인스턴스
        scorer: StrategyScorer 인스턴스
        wf_analyzer: WalkForwardAnalyzer 인스턴스
    """

    def __init__(
        self,
        save_path: Optional[str] = None,
    ) -> None:
        """StrategySelector 초기화.

        Args:
            save_path: 선정 결과 JSON 저장 경로. None이면 기본 경로 사용.
        """
        self.engine = BacktestEngine()
        self.scorer = StrategyScorer()
        self.wf_analyzer = WalkForwardAnalyzer()
        self.save_path = save_path or _DEFAULT_SAVE_PATH

    def select(
        self,
        all_strategies: List[BaseStrategy],
        data: Dict[str, pd.DataFrame],
        config: Optional[BacktestConfig] = None,
        top_n: int = 3,
        run_walk_forward: bool = True,
        wf_is_months: int = 6,
        wf_oos_months: int = 2,
    ) -> SelectionResult:
        """전략 선정 파이프라인을 실행한다.

        Args:
            all_strategies: 평가할 전략 목록
            data: 심볼 → OHLCV DataFrame 딕셔너리
            config: 백테스트 설정
            top_n: 선정할 전략 수
            run_walk_forward: Walk-Forward 검증 실행 여부
            wf_is_months: Walk-Forward IS 개월 수
            wf_oos_months: Walk-Forward OOS 개월 수

        Returns:
            SelectionResult 객체
        """
        if config is None:
            config = BacktestConfig()

        if not all_strategies:
            logger.warning("전략 목록이 비어있음")
            return SelectionResult()

        if not data:
            logger.warning("데이터 딕셔너리가 비어있음")
            return SelectionResult()

        symbols = list(data.keys())
        logger.info(
            "전략 선정 시작: %d개 전략 × %d개 심볼",
            len(all_strategies), len(symbols)
        )

        # ── Step 1: 전략 × 심볼 백테스트 ──────────────────────────
        all_results: List[BacktestResult] = []
        for strategy in all_strategies:
            logger.info("백테스트 중: %s", strategy.name)
            for symbol, df in data.items():
                try:
                    result = self.engine.run(strategy, df.copy(), config, symbol)
                    all_results.append(result)
                    logger.debug(
                        "  %s/%s: 거래%d건 Sharpe=%.2f",
                        strategy.name, symbol, result.total_trades, result.sharpe_ratio
                    )
                except Exception as exc:
                    logger.warning(
                        "백테스트 실패 %s/%s: %s", strategy.name, symbol, exc
                    )

        # ── Step 2: Walk-Forward 검증 ──────────────────────────────
        wf_results: Dict[str, WalkForwardResult] = {}
        oos_retention: Dict[str, float] = {}

        if run_walk_forward:
            # 가장 데이터가 많은 심볼로 WF 검증 (대표 심볼: BTCUSDT 우선)
            wf_symbol = "BTCUSDT" if "BTCUSDT" in data else symbols[0]
            df_wf = data[wf_symbol]

            for strategy in all_strategies:
                logger.info("Walk-Forward 검증 중: %s", strategy.name)
                try:
                    wf_result = self.wf_analyzer.run(
                        strategy, df_wf.copy(), wf_is_months, wf_oos_months,
                        config, wf_symbol
                    )
                    wf_results[strategy.name] = wf_result
                    oos_retention[strategy.name] = wf_result.avg_oos_retention
                except Exception as exc:
                    logger.warning(
                        "Walk-Forward 실패 %s: %s", strategy.name, exc
                    )
                    oos_retention[strategy.name] = 0.5  # 기본값

        # ── Step 3: 멀티자산 일관성 계산 ──────────────────────────
        multi_asset_consistency = self._calc_multi_asset_consistency(
            all_results, all_strategies, symbols
        )

        # ── Step 4: 종합 스코어링 ──────────────────────────────────
        scores = self.scorer.score(
            all_results,
            oos_retention=oos_retention,
            multi_asset_consistency=multi_asset_consistency,
        )

        # ── Step 5: Top N 선정 ────────────────────────────────────
        top_strategies = self.scorer.select_top(scores, n=top_n)

        # ── Step 6: 선정 근거 생성 ───────────────────────────────
        selection_reason = self._build_selection_reason(scores, top_strategies)

        # 결과 출력
        self._print_summary(scores, top_strategies, selection_reason)

        # ── Step 7: JSON 저장 ─────────────────────────────────────
        result = SelectionResult(
            top_strategies=top_strategies,
            scores=scores,
            backtest_results=all_results,
            wf_results=wf_results,
            selection_reason=selection_reason,
        )
        self._save_result(result)

        return result

    @staticmethod
    def _calc_multi_asset_consistency(
        results: List[BacktestResult],
        strategies: List[BaseStrategy],
        symbols: List[str],
    ) -> Dict[str, float]:
        """심볼별 Sharpe 일관성을 계산한다.

        모든 심볼에서 양수 Sharpe를 보이면 1.0, 모두 음수면 0.0.

        Args:
            results: BacktestResult 목록
            strategies: 전략 목록
            symbols: 심볼 목록

        Returns:
            전략명 → 멀티자산 일관성 딕셔너리
        """
        consistency: Dict[str, float] = {}

        for strategy in strategies:
            strategy_results = [r for r in results if r.strategy_name == strategy.name]
            if not strategy_results:
                consistency[strategy.name] = 0.0
                continue

            positive_sharpe = sum(
                1 for r in strategy_results if r.sharpe_ratio > 0
            )
            consistency[strategy.name] = positive_sharpe / max(len(strategy_results), 1)

        return consistency

    @staticmethod
    def _build_selection_reason(
        scores: pd.DataFrame, top_strategies: List[str]
    ) -> Dict[str, str]:
        """선정 근거 텍스트를 생성한다.

        Args:
            scores: 스코어 DataFrame
            top_strategies: 선정된 전략명 목록

        Returns:
            전략명 → 근거 설명 딕셔너리
        """
        reasons: Dict[str, str] = {}
        if scores.empty:
            return reasons

        for name in top_strategies:
            row = scores[scores["strategy_name"] == name]
            if row.empty:
                reasons[name] = "데이터 없음"
                continue

            r = row.iloc[0]
            reasons[name] = (
                f"종합점수={r.get('total_score', 0):.2f}/10 | "
                f"Sharpe={r.get('sharpe_raw', 0):.2f} | "
                f"PF={r.get('profit_factor_raw', 0):.2f} | "
                f"WR={r.get('win_rate_raw', 0)*100:.1f}% | "
                f"MDD={r.get('max_drawdown_raw', 0)*100:.1f}% | "
                f"거래수={r.get('total_trades', 0)}건"
            )

        return reasons

    @staticmethod
    def _print_summary(
        scores: pd.DataFrame,
        top_strategies: List[str],
        reasons: Dict[str, str],
    ) -> None:
        """선정 결과를 콘솔에 출력한다.

        Args:
            scores: 스코어 DataFrame
            top_strategies: 선정된 전략명 목록
            reasons: 선정 근거 딕셔너리
        """
        print("\n" + "=" * 60)
        print("전략 선정 결과")
        print("=" * 60)

        if not scores.empty:
            display_cols = [
                "strategy_name", "total_score", "sharpe_raw",
                "profit_factor_raw", "win_rate_raw", "total_trades"
            ]
            available = [c for c in display_cols if c in scores.columns]
            print(scores[available].to_string(index=False))

        print("\n[Top 전략 선정]")
        for i, name in enumerate(top_strategies, 1):
            print(f"  {i}위: {name}")
            print(f"     근거: {reasons.get(name, '-')}")

        print("=" * 60 + "\n")

    def _save_result(self, result: SelectionResult) -> None:
        """선정 결과를 JSON 파일로 저장한다.

        Args:
            result: SelectionResult 객체
        """
        try:
            save_path = Path(self.save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "top_strategies": result.top_strategies,
                "selection_reason": result.selection_reason,
                "scores": (
                    result.scores[
                        ["strategy_name", "total_score", "total_trades", "total_pnl"]
                    ].to_dict(orient="records")
                    if result.scores is not None and not result.scores.empty
                    else []
                ),
            }

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("선정 결과 저장: %s", save_path)
        except Exception as exc:
            logger.warning("선정 결과 저장 실패: %s", exc)


__all__ = ["StrategySelector", "SelectionResult"]
