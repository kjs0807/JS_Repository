"""Runtime layer (PR S+) — forward/paper run 공통 (BacktestEngine 과 동일 계약).

PR S 1차 범위:
- ``forward_runner.ForwardRunner`` — parquet replay 기반 deterministic paper run.
  ``BacktestEngine`` 을 그대로 활용해 같은 ``StrategyContext`` / ``OrderIntent`` /
  ``EventLog`` 계약을 보장. run_id prefix 와 mode 메타데이터로 backtest run 과 구분.
- ``paper_broker.PaperBroker`` — placeholder. 실제 거래소 주문은 내지 않고 ExecutionModel
  체결을 그대로 재사용. 후속 PR 에서 latency/queue 모델 추가.

후속 PR:
- ``live_data_adapter`` REST/WebSocket 어댑터.
- 실시간 polling loop / signal handler.
"""

from backtester.runtime.forward_runner import ForwardRunner
from backtester.runtime.paper_broker import PaperBroker

__all__ = ["ForwardRunner", "PaperBroker"]
