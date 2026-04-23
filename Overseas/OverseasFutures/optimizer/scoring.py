"""점수 함수 — 가중 합성 스코어러."""

from __future__ import annotations

from dataclasses import dataclass, field

from optimizer.types import Asset


DEFAULT_WEIGHTS = {"sharpe": 0.4, "win_rate": 0.3, "calmar": 0.3}


@dataclass
class WeightedCompositeScorer:
    """Min-Max 정규화 후 가중합으로 자산 점수를 계산한다.

    사용자가 가중치를 자유롭게 설정 가능.
    기본: {"sharpe": 0.4, "win_rate": 0.3, "calmar": 0.3}
    """
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    def score_assets(self, assets: list[Asset]) -> dict[str, float]:
        """후보 풀 내에서 min-max 정규화 → 가중합 점수.

        Args:
            assets: 점수를 매길 자산 리스트

        Returns:
            {symbol: score} 딕셔너리 (높을수록 좋음)
        """
        if not assets:
            return {}

        # 메트릭 값 수집
        metric_values: dict[str, list[float]] = {
            "sharpe": [a.metrics.sharpe for a in assets],
            "win_rate": [a.metrics.win_rate for a in assets],
            "calmar": [a.metrics.calmar for a in assets],
        }

        # Min-Max 정규화
        normalized: dict[str, list[float]] = {}
        for metric, values in metric_values.items():
            min_v = min(values)
            max_v = max(values)
            rng = max_v - min_v
            if rng > 0:
                normalized[metric] = [(v - min_v) / rng for v in values]
            else:
                normalized[metric] = [1.0 for _ in values]

        # 가중합
        scores: dict[str, float] = {}
        for i, asset in enumerate(assets):
            score = 0.0
            for metric, w in self.weights.items():
                if metric in normalized:
                    score += w * normalized[metric][i]
            scores[asset.symbol] = score

        return scores
