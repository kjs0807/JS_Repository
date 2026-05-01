"""Analysis layer (Phase 2+).

PR 17: walkforward — rolling/expanding window 분할 + OOS metrics 집계.
PR 19: rebuild — events.jsonl 원본만으로 ``run_dir/results/`` 캐시 재생성.
후속 PR: replay 등.
"""

from backtester.analysis.rebuild import rebuild_equity_curve, rebuild_results
from backtester.analysis.walkforward import (
    WalkforwardResult,
    WalkforwardSplitter,
    WalkforwardWindow,
    WalkforwardWindowResult,
    run_walkforward,
)

__all__ = [
    "WalkforwardResult",
    "WalkforwardSplitter",
    "WalkforwardWindow",
    "WalkforwardWindowResult",
    "rebuild_equity_curve",
    "rebuild_results",
    "run_walkforward",
]
