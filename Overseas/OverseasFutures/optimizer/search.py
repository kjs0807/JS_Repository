"""조합 탐색 — 전수 탐색 (N≤15) + Beam Search (N>15)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from optimizer.types import Asset, ComboResult
from optimizer.constraints import PortfolioConstraints
from optimizer.scoring import WeightedCompositeScorer
from optimizer.correlation import max_pairwise_correlation
from optimizer.allocation import (
    WeightMethod, compute_weights, allocate,
)


@dataclass
class SearchConfig:
    """조합 탐색 설정."""
    constraints: PortfolioConstraints
    scorer: WeightedCompositeScorer = field(default_factory=WeightedCompositeScorer)
    weight_method: WeightMethod = WeightMethod.RISK_PARITY
    beam_width: int = 20
    top_k: int = 10


def _deduplicate_groups(assets: list[Asset], scores: dict[str, float]) -> list[Asset]:
    """중복 그룹 내에서 최고 점수 자산만 남긴다."""
    best_per_group: dict[str, Asset] = {}
    ungrouped: list[Asset] = []

    for a in assets:
        if a.group is None:
            ungrouped.append(a)
            continue
        current = best_per_group.get(a.group)
        if current is None or scores.get(a.symbol, 0) > scores.get(current.symbol, 0):
            best_per_group[a.group] = a

    return list(best_per_group.values()) + ungrouped


def _check_asset_class_limit(
    combo: list[Asset],
    max_per_class: int,
) -> bool:
    """자산군별 제한 확인."""
    counts: dict[str, int] = {}
    for a in combo:
        counts[a.asset_class] = counts.get(a.asset_class, 0) + 1
        if counts[a.asset_class] > max_per_class:
            return False
    return True


def _evaluate_combo(
    combo: list[Asset],
    config: SearchConfig,
    scores: dict[str, float],
) -> ComboResult | None:
    """단일 조합을 평가한다."""
    c = config.constraints

    # 자산군 제한
    if not _check_asset_class_limit(combo, c.max_per_asset_class):
        return None

    # 마진 체크
    total_margin = sum(a.cost_per_unit for a in combo)
    if total_margin * c.safety_factor > c.budget:
        return None

    # 상관관계 체크
    if len(combo) > 1 and c.max_correlation < 1.0:
        max_corr = max_pairwise_correlation(combo)
        if max_corr > c.max_correlation:
            return None

    # 가중치 계산
    weights = compute_weights(combo, config.weight_method, scores)

    # 배분
    allocs = allocate(combo, c.budget, weights, c.safety_factor)
    if not allocs:
        return None

    # 실제 배분된 자산 기준으로 마진 재계산
    actual_margin = sum(a.asset.cost_per_unit for a in allocs)

    # 가중치 재정규화 (배분된 자산만)
    total_w = sum(a.weight for a in allocs)
    if total_w > 0:
        for a in allocs:
            a.weight = a.weight / total_w
            a.allocated_usd = c.budget * a.weight

    # 포트폴리오 추정 메트릭 (가중 평균)
    w_sharpe = sum(a.asset.metrics.sharpe * a.weight for a in allocs)
    w_return = sum(a.asset.metrics.return_pct * a.weight for a in allocs)
    w_mdd = sum(a.asset.metrics.mdd * a.weight for a in allocs)
    w_calmar = sum(a.asset.metrics.calmar * a.weight for a in allocs)

    # 조합 점수: 배분된 자산들의 가중 점수 합
    combo_score = sum(scores.get(a.asset.symbol, 0) * a.weight for a in allocs)

    asset_classes = sorted(set(a.asset.asset_class for a in allocs))

    return ComboResult(
        allocations=allocs,
        score=combo_score,
        sharpe_est=w_sharpe,
        return_est=w_return,
        mdd_est=w_mdd,
        calmar_est=w_calmar,
        total_margin=actual_margin,
        n_assets=len(allocs),
        asset_classes=asset_classes,
    )


def _exhaustive_search(
    assets: list[Asset],
    config: SearchConfig,
    scores: dict[str, float],
) -> list[ComboResult]:
    """전수 탐색 (N ≤ 15)."""
    c = config.constraints
    results: list[ComboResult] = []
    seen_combos: set[frozenset[str]] = set()

    for n in range(c.min_assets, c.max_assets + 1):
        for combo_tuple in itertools.combinations(assets, n):
            combo = list(combo_tuple)
            result = _evaluate_combo(combo, config, scores)
            if result is not None:
                # 실제 배분된 종목 기준으로 중복 제거
                key = frozenset(result.symbols)
                if key not in seen_combos:
                    seen_combos.add(key)
                    results.append(result)

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:config.top_k]


def _beam_search(
    assets: list[Asset],
    config: SearchConfig,
    scores: dict[str, float],
) -> list[ComboResult]:
    """Greedy Beam Search (N > 15).

    개별 점수 상위 자산부터 시작, 한 종목씩 추가하며 top-K 유지.
    """
    c = config.constraints

    # 개별 점수순 정렬
    ranked = sorted(assets, key=lambda a: scores.get(a.symbol, 0), reverse=True)

    # Beam: 각 원소는 자산 리스트 (조합)
    beam: list[list[Asset]] = [[a] for a in ranked[:config.beam_width]]

    best_results: list[ComboResult] = []
    seen_combos: set[frozenset[str]] = set()

    # 각 단일 자산 조합도 평가
    for combo in beam:
        if len(combo) >= c.min_assets:
            result = _evaluate_combo(combo, config, scores)
            if result is not None:
                key = frozenset(result.symbols)
                if key not in seen_combos:
                    seen_combos.add(key)
                    best_results.append(result)

    # 종목 추가 반복
    for _ in range(c.max_assets - 1):
        next_beam: list[list[Asset]] = []
        seen: set[frozenset[str]] = set()

        for combo in beam:
            combo_syms = {a.symbol for a in combo}
            for candidate in ranked:
                if candidate.symbol in combo_syms:
                    continue

                new_combo = combo + [candidate]
                key = frozenset(a.symbol for a in new_combo)
                if key in seen:
                    continue
                seen.add(key)

                # 조기 프루닝: 마진 체크
                total_margin = sum(a.cost_per_unit for a in new_combo)
                if total_margin * c.safety_factor > c.budget:
                    continue

                if len(new_combo) >= c.min_assets:
                    result = _evaluate_combo(new_combo, config, scores)
                    if result is not None:
                        rkey = frozenset(result.symbols)
                        if rkey not in seen_combos:
                            seen_combos.add(rkey)
                            best_results.append(result)

                next_beam.append(new_combo)

        # Beam 크기 제한: 조합 점수 기준 상위 유지
        if next_beam:
            combo_scores = []
            for combo in next_beam:
                s = sum(scores.get(a.symbol, 0) for a in combo) / len(combo)
                combo_scores.append((s, combo))
            combo_scores.sort(key=lambda x: x[0], reverse=True)
            beam = [c for _, c in combo_scores[:config.beam_width]]
        else:
            break

    best_results.sort(key=lambda r: r.score, reverse=True)
    return best_results[:config.top_k]


def search_optimal_combos(
    assets: list[Asset],
    config: SearchConfig,
) -> list[ComboResult]:
    """최적 종목 조합을 탐색한다.

    N ≤ 15: 전수 탐색, N > 15: Beam Search.

    Args:
        assets: 후보 자산 리스트
        config: 탐색 설정

    Returns:
        Top-K ComboResult 리스트 (score 내림차순)
    """
    c = config.constraints

    # 제외 종목 필터
    filtered = [a for a in assets if a.symbol not in c.excluded_symbols]

    # 점수 계산
    scores = config.scorer.score_assets(filtered)

    # 중복 그룹 제거
    deduped = _deduplicate_groups(filtered, scores)

    # 탐색
    if len(deduped) <= 15:
        return _exhaustive_search(deduped, config, scores)
    else:
        return _beam_search(deduped, config, scores)
