"""ForwardRunner — paper / forward replay (PR S).

목표: 같은 데이터 / 전략에 대해 backtest 와 paper run 이 byte-identical events.jsonl
을 만들도록 하는 게 1차 — 검증/디버그/회귀가 동일 토대로 가능해야 한다.

PR S 1차 범위:
- ``ForwardRunner.run(config, strategy)`` — ``BacktestEngine`` 을 그대로 호출 (replay
  데이터가 parquet 으로 이미 있으면 backtest 와 같은 결과).
- run_id prefix 는 사용자가 결정. 별도 mode 메타데이터는 ``BacktestResult`` 의
  ``resolved_run_id`` 로 식별 가능.
- 후속 PR: 실시간 polling / WebSocket 어댑터, latency 모델, paper broker queue 등.
"""

from __future__ import annotations

from backtester.core.config import BacktestConfig
from backtester.core.engine import BacktestEngine
from backtester.core.result import BacktestResult
from backtester.strategies.base import BaseStrategy


class ForwardRunner:
    """parquet replay 기반 deterministic paper / forward run.

    BacktestEngine 을 그대로 사용하므로 같은 ``BacktestConfig`` + ``BaseStrategy``
    + 같은 데이터에 대해 backtest 와 byte-identical events.jsonl 을 보장.
    """

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose

    def run(
        self,
        config: BacktestConfig,
        strategy: BaseStrategy,
    ) -> BacktestResult:
        """``BacktestEngine`` 을 호출해 paper run 실행."""
        engine = BacktestEngine(config, strategy, verbose=self.verbose)
        return engine.run()
