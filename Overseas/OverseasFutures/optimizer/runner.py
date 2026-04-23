"""메인 진입점 — optimize() 함수."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime

from optimizer.types import Asset, ComboResult
from optimizer.constraints import PortfolioConstraints
from optimizer.scoring import WeightedCompositeScorer
from optimizer.search import SearchConfig, search_optimal_combos
from optimizer.allocation import WeightMethod


@dataclass
class OptimizerConfig:
    """옵티마이저 전체 설정."""
    constraints: PortfolioConstraints = field(default_factory=PortfolioConstraints)
    scorer: WeightedCompositeScorer = field(default_factory=WeightedCompositeScorer)
    weight_method: WeightMethod = WeightMethod.RISK_PARITY
    beam_width: int = 20
    top_k: int = 10


def optimize(
    assets: list[Asset],
    config: OptimizerConfig | None = None,
) -> list[ComboResult]:
    """최적 포트폴리오 조합을 탐색한다.

    Args:
        assets: 후보 자산 리스트 (어댑터에서 생성)
        config: 옵티마이저 설정 (None이면 기본값)

    Returns:
        Top-K ComboResult 리스트 (score 내림차순)
    """
    if config is None:
        config = OptimizerConfig()

    search_config = SearchConfig(
        constraints=config.constraints,
        scorer=config.scorer,
        weight_method=config.weight_method,
        beam_width=config.beam_width,
        top_k=config.top_k,
    )

    return search_optimal_combos(assets, search_config)


def print_results(
    results: list[ComboResult],
    budget: float,
    top_n: int = 10,
) -> list[str]:
    """결과를 포맷팅하여 출력한다.

    Returns:
        출력된 라인 리스트
    """
    lines: list[str] = []

    def p(text: str = ""):
        print(text)
        lines.append(text)

    p("=" * 110)
    p("PORTFOLIO OPTIMIZER RESULTS")
    p(f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p(f"예산: ${budget:,.2f}")
    p("=" * 110)

    if not results:
        p("조건을 만족하는 조합이 없습니다.")
        return lines

    p(f"\n총 {len(results)}개 유효 조합 중 Top {min(top_n, len(results))}")
    p(f"\n{'#':>3} {'Symbols':<30} {'N':>2} {'Margin':>8} {'Score':>7} "
      f"{'Sharpe':>7} {'Ret%':>7} {'MDD%':>7} {'Calmar':>8} {'Assets':<20}")
    p("-" * 120)

    for rank, r in enumerate(results[:top_n], 1):
        syms_str = "+".join(r.symbols)
        assets_str = ",".join(r.asset_classes)
        p(f"{rank:>3} {syms_str:<30} {r.n_assets:>2} ${r.total_margin:>6,.0f} "
          f"{r.score:>7.4f} {r.sharpe_est:>7.3f} {r.return_est:>+6.2f}% "
          f"{r.mdd_est:>6.2f}% {r.calmar_est:>8.2f} {assets_str:<20}")

    # Top 1 상세
    best = results[0]
    p(f"\n{'='*110}")
    p(f"BEST: {' + '.join(best.symbols)}")
    p(f"{'='*110}")
    p(f"\n{'Symbol':<8} {'Units':>6} {'Weight':>8} {'Alloc($)':>12} "
      f"{'Sharpe':>8} {'WR%':>7} {'Ret%':>8} {'Class':<12}")
    p("-" * 80)

    for a in best.allocations:
        m = a.asset.metrics
        p(f"{a.asset.symbol:<8} {a.units:>6.0f} {a.weight*100:>7.1f}% "
          f"${a.allocated_usd:>10,.2f} {m.sharpe:>8.3f} {m.win_rate*100:>6.1f}% "
          f"{m.return_pct:>+7.2f}% {a.asset.asset_class:<12}")

    p(f"\n총 마진: ${best.total_margin:,.0f}  여유: ${budget - best.total_margin:,.0f}")

    return lines
