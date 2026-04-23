"""OverfitDetector — 오버피팅 통계 검증."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Dict, List
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class OverfitVerdict:
    verdict: str  # "CLEAN" | "WARNING" | "OVERFIT"
    p_value: float
    sensitivity: float
    reason: str = ""

class OverfitDetector:
    def permutation_test(self, pnl_list: List[float], n_shuffles: int = 1000) -> float:
        """Bootstrap 기반 단측 p-value: H0(mean=0) 하에서 original mean 이상 확률."""
        pnl_arr = np.array(pnl_list)
        if len(pnl_arr) < 2:
            return 1.0
        original_mean = float(np.mean(pnl_arr))
        # Center around zero to simulate H0: mean=0
        centered = pnl_arr - original_mean
        rng = np.random.default_rng()
        count_above = sum(
            1 for _ in range(n_shuffles)
            if float(np.mean(rng.choice(centered, size=len(centered), replace=True))) >= original_mean
        )
        return (count_above + 1) / (n_shuffles + 1)

    def param_sensitivity(self, scores: Dict[str, float]) -> float:
        if len(scores) < 2: return 0.0
        vals = np.array(list(scores.values()))
        mean, std = np.mean(vals), np.std(vals, ddof=1)
        if abs(mean) < 1e-10: return 1.0 if std > 0 else 0.0
        return float(min(1.0, std / abs(mean)))

    def detect(self, pnl_list: List[float], scores: Dict[str, float],
               n_shuffles: int = 500) -> OverfitVerdict:
        p_value = self.permutation_test(pnl_list, n_shuffles)
        sensitivity = self.param_sensitivity(scores)
        if p_value > 0.10 and sensitivity > 0.5:
            verdict, reason = "OVERFIT", f"p={p_value:.3f} (유의하지 않음) + 민감도={sensitivity:.2f}"
        elif p_value > 0.05 or sensitivity > 0.5:
            verdict, reason = "WARNING", f"p={p_value:.3f}, 민감도={sensitivity:.2f}"
        else:
            verdict, reason = "CLEAN", f"p={p_value:.3f} (유의), 민감도={sensitivity:.2f} (안정)"
        return OverfitVerdict(verdict=verdict, p_value=p_value, sensitivity=sensitivity, reason=reason)

    @staticmethod
    def _calc_sharpe(pnl: np.ndarray) -> float:
        if len(pnl) < 2: return 0.0
        std = np.std(pnl, ddof=1)
        return float(np.mean(pnl) / std) if std > 1e-10 else 0.0

__all__ = ["OverfitDetector", "OverfitVerdict"]
