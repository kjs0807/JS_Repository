"""포트폴리오 제약 조건."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PortfolioConstraints:
    """조합 탐색 시 적용할 제약 조건.

    Args:
        budget: 총 예산 (USD)
        safety_factor: 마진 안전계수 (선물). 마진 합계 × safety_factor ≤ budget
        min_assets: 최소 종목 수
        max_assets: 최대 종목 수
        max_per_asset_class: 자산군별 최대 종목 수
        max_correlation: 상관계수 상한 (초과 시 조합 제외)
        excluded_symbols: 명시적 제외 종목
    """
    budget: float = 10_000.0
    safety_factor: float = 1.5
    min_assets: int = 1
    max_assets: int = 6
    max_per_asset_class: int = 3
    max_correlation: float = 0.95
    excluded_symbols: set[str] = field(default_factory=set)
