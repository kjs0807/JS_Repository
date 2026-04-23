"""포지션 사이징 — 리스크패리티 / 동일가중 / 점수비례."""

from __future__ import annotations

import math
from enum import Enum

from optimizer.types import Asset, ComboAllocation, SizingMode


class WeightMethod(Enum):
    """가중 방법."""
    EQUAL = "equal"
    RISK_PARITY = "risk_parity"
    SCORE_WEIGHTED = "score_weighted"


def compute_weights(
    assets: list[Asset],
    method: WeightMethod = WeightMethod.RISK_PARITY,
    scores: dict[str, float] | None = None,
) -> dict[str, float]:
    """자산별 가중치를 계산한다.

    Args:
        assets: 대상 자산 리스트
        method: 가중 방법
        scores: score_weighted 방식일 때 필요한 점수 딕셔너리

    Returns:
        {symbol: weight} (합계 1.0)
    """
    if not assets:
        return {}

    n = len(assets)

    if method == WeightMethod.EQUAL:
        return {a.symbol: 1.0 / n for a in assets}

    if method == WeightMethod.RISK_PARITY:
        inv_vols: dict[str, float] = {}
        for a in assets:
            vol = a.volatility_usd
            if vol > 0:
                inv_vols[a.symbol] = 1.0 / vol
            else:
                inv_vols[a.symbol] = 1.0
        total = sum(inv_vols.values())
        return {s: iv / total for s, iv in inv_vols.items()}

    if method == WeightMethod.SCORE_WEIGHTED:
        if scores is None:
            return {a.symbol: 1.0 / n for a in assets}
        s = {a.symbol: max(scores.get(a.symbol, 0.0), 0.0) for a in assets}
        total = sum(s.values())
        if total <= 0:
            return {a.symbol: 1.0 / n for a in assets}
        return {sym: v / total for sym, v in s.items()}

    return {a.symbol: 1.0 / n for a in assets}


def allocate(
    assets: list[Asset],
    budget: float,
    weights: dict[str, float],
    safety_factor: float = 1.5,
) -> list[ComboAllocation]:
    """가중치와 예산으로 실제 배분량을 결정한다.

    INTEGER 모드: floor(alloc_usd / cost_per_unit) — 0이면 제외
    FRACTIONAL 모드: 정확한 달러 비례 배분

    Args:
        assets: 대상 자산 리스트
        budget: 총 예산 (USD)
        weights: {symbol: weight}
        safety_factor: 마진 안전계수

    Returns:
        ComboAllocation 리스트 (units > 0인 것만)
    """
    result: list[ComboAllocation] = []

    for asset in assets:
        w = weights.get(asset.symbol, 0.0)
        if w <= 0:
            continue

        alloc_usd = budget * w

        if asset.sizing_mode == SizingMode.FRACTIONAL:
            units = alloc_usd / asset.cost_per_unit if asset.cost_per_unit > 0 else 0.0
        else:
            # INTEGER_CONTRACTS 또는 INTEGER_SHARES
            effective_cost = asset.cost_per_unit * safety_factor
            units = math.floor(alloc_usd / effective_cost) if effective_cost > 0 else 0

        if units > 0:
            result.append(ComboAllocation(
                asset=asset,
                units=float(units),
                weight=w,
                allocated_usd=alloc_usd,
            ))

    return result
