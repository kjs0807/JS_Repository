"""핵심 타입 정의 — Asset, AssetMetrics, ComboResult 등."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class SizingMode(Enum):
    """포지션 사이징 모드."""
    INTEGER_CONTRACTS = "integer_contracts"  # 선물: 정수 계약
    INTEGER_SHARES = "integer_shares"        # 주식: 정수 주
    FRACTIONAL = "fractional"                # 소수점 배분 (암호화폐 등)


@dataclass
class AssetMetrics:
    """개별 자산의 성과 지표."""
    sharpe: float
    win_rate: float
    return_pct: float
    mdd: float
    calmar: float
    trades: int
    extras: dict = field(default_factory=dict)


@dataclass
class Asset:
    """범용 자산 표현.

    선물/주식/암호화폐 등 어떤 자산이든 이 형태로 변환하면
    옵티마이저가 동일하게 동작한다.
    """
    symbol: str
    name: str
    asset_class: str
    group: str | None               # 중복 그룹 (ES/MES/SIN → "ES")
    cost_per_unit: float             # 선물=마진, 주식=주가
    sizing_mode: SizingMode
    point_value: float               # 선물=tick_value/tick_size, 주식=1.0
    daily_returns: pd.Series         # 상관관계 분석용
    volatility_usd: float            # ATR × point_value
    metrics: AssetMetrics
    meta: dict = field(default_factory=dict)


@dataclass
class ComboAllocation:
    """조합 내 개별 자산 배분 결과."""
    asset: Asset
    units: float                     # 계약 수 또는 주 수
    weight: float                    # 포트폴리오 내 비중 (0~1)
    allocated_usd: float             # 실제 배분 금액


@dataclass
class ComboResult:
    """종목 조합 평가 결과."""
    allocations: list[ComboAllocation]
    score: float
    sharpe_est: float                # 포트폴리오 추정 Sharpe
    return_est: float                # 포트폴리오 추정 수익률
    mdd_est: float                   # 포트폴리오 추정 MDD
    calmar_est: float                # 포트폴리오 추정 Calmar
    total_margin: float              # 총 마진/비용
    n_assets: int
    asset_classes: list[str]

    @property
    def symbols(self) -> list[str]:
        return [a.asset.symbol for a in self.allocations]
