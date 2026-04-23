"""범용 포트폴리오 옵티마이저.

어떤 자산(선물/주식/암호화폐)이든 Asset으로 변환하면
동일한 엔진으로 최적 조합을 탐색한다.

Usage:
    from optimizer import optimize, OptimizerConfig
    from optimizer.adapters.futures import load_futures_assets
    from optimizer.constraints import PortfolioConstraints

    assets = load_futures_assets("summary.json")
    config = OptimizerConfig(
        constraints=PortfolioConstraints(budget=6849, max_assets=6),
    )
    results = optimize(assets, config)
"""

from optimizer.types import (
    Asset,
    AssetMetrics,
    ComboAllocation,
    ComboResult,
    SizingMode,
)
from optimizer.constraints import PortfolioConstraints
from optimizer.scoring import WeightedCompositeScorer
from optimizer.allocation import WeightMethod
from optimizer.runner import optimize, OptimizerConfig, print_results

__all__ = [
    "Asset",
    "AssetMetrics",
    "ComboAllocation",
    "ComboResult",
    "SizingMode",
    "PortfolioConstraints",
    "WeightedCompositeScorer",
    "WeightMethod",
    "OptimizerConfig",
    "optimize",
    "print_results",
]
