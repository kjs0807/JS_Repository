"""PaperBroker placeholder (PR S).

PR S 1차 범위:
- ``PaperBroker`` 는 placeholder. ``BacktestEngine`` 의 ``ExecutionModel`` 이 이미
  paper-style 체결을 담당하므로, 별도 broker 객체는 필요 없다 — 후속 PR 에서 latency
  / 거래소 응답 시뮬레이션이 필요해지면 활성.

후속 PR 후보:
- BybitPaperBroker (REST 호환 응답 모형).
- LatencyModel (signal → fill 지연).
- OrderQueueModel (taker queue 지연).
"""

from __future__ import annotations


class PaperBroker:
    """PR S 시점 placeholder — ``BacktestEngine`` 의 ExecutionModel 이 paper-style 체결
    을 처리하므로 별도 broker 가 필요 없다. 후속 PR 에서 latency / queue 모델 활성.
    """

    def __init__(self) -> None:
        pass
