"""Execution models (PR 6 + Phase 1.5 PR 9 + Phase 2 PR 15a).

Phase 1: NextBarOpenExecution (slippage 0).
Phase 1.5: + Funding (FundingModel + FundingProcessor + CashFlow).
Phase 2 PR 15a:
- NextBarOpenExecution(slippage_bps=...) 활성
- AtrSlippageExecution (atr_provider 주입식 minimum interface)
- FeeModel.compute_fee maker/taker 분기 활성 (instruments.base)
Phase 2 PR 15b: limit/stop/stop_limit 처리.
Phase 2 PR 15c: BarPathModel 4종 정책.
"""

from backtester.execution.base import ExecutionModel
from backtester.execution.funding import (
    CashFlow,
    FundingModel,
    FundingProcessor,
    is_funding_boundary,
)
from backtester.execution.next_bar import NextBarOpenExecution
from backtester.execution.slippage_atr import AtrProvider, AtrSlippageExecution
from backtester.execution.slippage_bps import apply_bps_slippage

__all__ = [
    "AtrProvider",
    "AtrSlippageExecution",
    "CashFlow",
    "ExecutionModel",
    "FundingModel",
    "FundingProcessor",
    "NextBarOpenExecution",
    "apply_bps_slippage",
    "is_funding_boundary",
]
