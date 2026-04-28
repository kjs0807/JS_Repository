"""오버피팅 방어 모듈 — Deflated Sharpe Ratio, White's Reality Check, Neighbor Robustness.

참고:
  - Bailey & López de Prado (2014): Deflated Sharpe Ratio
  - White (2000): Reality Check Bootstrap
  - López de Prado (2018): Advances in Financial Machine Learning, Ch. 11
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm


def deflated_sharpe_ratio(
    sr_observed: float,
    n_trials: int,
    n_observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

    다중 그리드 탐색에서 발생하는 다중 가설 검정 문제를 보정한다.
    N개 trial 중 우연히 최고 Sharpe가 나올 기대값을 제거한 후
    관측 Sharpe의 통계적 유의성을 Phi CDF로 반환.

    Args:
        sr_observed: 후보의 OOS Sharpe (연환산).
        n_trials: 전체 그리드 trial 수 (N).
        n_observations: 관측 수 (daily 봉 수, T).
        skew: 수익률 분포의 왜도 (기본 0.0).
        kurtosis: 수익률 분포의 첨도 (기본 3.0, 정규).

    Returns:
        0.0~1.0 (Phi CDF 값). 높을수록 통계적으로 유의한 Sharpe.
        n_trials < 2 또는 n_observations < 30이면 0.0 반환.
    """
    if n_trials < 2 or n_observations < 30:
        return 0.0

    # 우연한 최고 Sharpe 기대값 (Euler-Mascheroni 근사)
    log_n = np.log(n_trials)
    if log_n <= 0:
        return 0.0
    sr_max_expected = np.sqrt(2 * log_n) - 0.5772 / np.sqrt(2 * log_n)

    # 표준편차 계수 (skew/excess_kurtosis 보정)
    excess_kurt = kurtosis - 3
    variance_adj = 1 - skew * sr_observed + (excess_kurt / 4) * sr_observed ** 2
    if variance_adj <= 0:
        return 0.0
    denom = np.sqrt(variance_adj)

    # T-statistic
    t_stat = ((sr_observed - sr_max_expected) * np.sqrt(n_observations - 1)) / denom

    return float(norm.cdf(t_stat))


def whites_reality_check(
    returns_matrix: np.ndarray,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict:
    """White's Reality Check (Bootstrap).

    최고 후보 Sharpe가 우연인지 검정한다.
    행 단위 복원 추출로 max-Sharpe 분포를 생성하여 p-value를 계산.

    Args:
        returns_matrix: shape [T, N] — T=일수, N=trial 수.
                        각 열은 한 trial의 daily returns 시계열.
        n_bootstrap: bootstrap 반복 횟수 (기본 1000).
        seed: 난수 시드.

    Returns:
        dict with keys:
          - sr_best: float  — 관측 최고 Sharpe
          - best_idx: int   — 최고 Sharpe trial 인덱스
          - p_value: float  — (max_resampled >= sr_best) 비율
          - n_bootstrap: int
    """
    returns_matrix = np.asarray(returns_matrix, dtype=float)
    if returns_matrix.ndim != 2 or returns_matrix.shape[0] < 2 or returns_matrix.shape[1] < 1:
        return {"sr_best": 0.0, "best_idx": 0, "p_value": 1.0, "n_bootstrap": n_bootstrap}

    T, N = returns_matrix.shape
    rng = np.random.default_rng(seed)

    # 관측 Sharpe (각 trial)
    means = returns_matrix.mean(axis=0)
    stds = returns_matrix.std(axis=0, ddof=1)
    sr_observed = means / (stds + 1e-9) * np.sqrt(365)

    sr_best = float(sr_observed.max())
    best_idx = int(sr_observed.argmax())

    # Bootstrap — 행 단위 복원 추출
    max_resampled = np.zeros(n_bootstrap)
    for b in range(n_bootstrap):
        idx = rng.integers(0, T, T)
        sample = returns_matrix[idx]
        sr_sample = (
            sample.mean(axis=0)
            / (sample.std(axis=0, ddof=1) + 1e-9)
            * np.sqrt(365)
        )
        max_resampled[b] = sr_sample.max()

    p_value = float((max_resampled >= sr_best).mean())
    return {
        "sr_best": sr_best,
        "best_idx": best_idx,
        "p_value": p_value,
        "n_bootstrap": n_bootstrap,
    }


def neighbor_robustness(
    grid_results: list[dict],
    best_idx: int,
    metric_key: str = "sharpe",
) -> dict:
    """인접 그리드 점들의 metric 안정성 분석.

    best 파라미터 주변 그리드 점들(각 파라미터 ±1 step)의
    metric 평균/std를 계산하여 robustness를 판별.

    Args:
        grid_results: list of dict. 각 dict에 'params' (dict) + metric 키가 있어야 함.
            예: [{"params": {"bb_period": 20, ...}, "sharpe": 1.5}, ...]
        best_idx: grid_results에서 best 조합의 인덱스.
        metric_key: 비교할 지표 키 (기본 'sharpe').

    Returns:
        dict with keys:
          - best: float        — best 조합의 metric 값
          - neighbors_avg: float — 인접 점들의 평균 metric
          - neighbors_std: float — 인접 점들의 std
          - ratio: float       — neighbors_avg / best (0이면 0.0)
          - passes: bool       — ratio >= 0.70 (best의 70% 이상)
    """
    if not grid_results or best_idx < 0 or best_idx >= len(grid_results):
        return {"best": 0.0, "neighbors_avg": 0.0, "neighbors_std": 0.0, "ratio": 0.0, "passes": False}

    best_result = grid_results[best_idx]
    best_value = float(best_result.get(metric_key, 0.0))
    best_params = best_result.get("params", {})

    if not best_params:
        return {"best": best_value, "neighbors_avg": 0.0, "neighbors_std": 0.0, "ratio": 0.0, "passes": False}

    # 파라미터별 후보값 목록 수집
    param_candidates: dict[str, list] = {}
    for r in grid_results:
        params = r.get("params", {})
        for k, v in params.items():
            if k not in param_candidates:
                param_candidates[k] = []
            if v not in param_candidates[k]:
                param_candidates[k].append(v)
    for k in param_candidates:
        param_candidates[k] = sorted(param_candidates[k])

    # 인접 점 탐색: 각 파라미터를 ±1 step으로 변경한 조합
    neighbor_values: list[float] = []
    for param_name, best_val in best_params.items():
        candidates = param_candidates.get(param_name, [])
        if best_val not in candidates:
            continue
        idx_in_cands = candidates.index(best_val)
        neighbor_indices = []
        if idx_in_cands > 0:
            neighbor_indices.append(idx_in_cands - 1)
        if idx_in_cands < len(candidates) - 1:
            neighbor_indices.append(idx_in_cands + 1)

        for ni in neighbor_indices:
            neighbor_val = candidates[ni]
            # 이 파라미터만 다르고 나머지는 동일한 결과 탐색
            for r in grid_results:
                r_params = r.get("params", {})
                match = True
                for k, v in best_params.items():
                    if k == param_name:
                        if r_params.get(k) != neighbor_val:
                            match = False
                            break
                    else:
                        if r_params.get(k) != v:
                            match = False
                            break
                if match:
                    neighbor_values.append(float(r.get(metric_key, 0.0)))
                    break

    if not neighbor_values:
        return {
            "best": best_value,
            "neighbors_avg": 0.0,
            "neighbors_std": 0.0,
            "ratio": 0.0,
            "passes": False,
        }

    neighbors_arr = np.array(neighbor_values)
    neighbors_avg = float(neighbors_arr.mean())
    neighbors_std = float(neighbors_arr.std(ddof=0)) if len(neighbors_arr) > 1 else 0.0
    ratio = neighbors_avg / best_value if abs(best_value) > 1e-9 else 0.0

    return {
        "best": best_value,
        "neighbors_avg": neighbors_avg,
        "neighbors_std": neighbors_std,
        "ratio": ratio,
        "passes": ratio >= 0.70,
    }


__all__ = ["deflated_sharpe_ratio", "whites_reality_check", "neighbor_robustness"]
