"""Walk-Forward 분석 (Phase 2 PR 17, spec §16, Decision Backlog).

전략 안정성 평가 용도. 한 백테스트 기간을 ``(train, test)`` 다중 window 로 나눠 각 OOS
구간 metrics 를 산출 → 모든 window 의 분포를 집계해 robustness 판단.

PR 17 minimum 범위:
- ``WalkforwardSplitter`` — ``start/end/train_bars/test_bars/bar_interval/mode`` 입력으로
  연속 windows 생성. ``mode='rolling'`` (train 도 같이 슬라이드) 또는 ``'expanding'``
  (train 시작은 고정, train 길이 누적). spec Decision Backlog "rolling/expanding 둘 다
  후보 유지" 양쪽 채택.
- ``run_walkforward(base_config, strategy_factory, splitter, ...)`` — 각 window 에서
  BacktestEngine 실행. config.start = ``window.train_start`` (warmup 용), config.end =
  ``window.test_end``. 결과 equity 시리즈를 ``test_start`` 이후로 필터 → 순수 OOS metrics.
- ``WalkforwardResult.aggregate_metrics`` — 각 window metric 의 mean/median/std/min/max
  로 분포 요약.

PR 17 한계 / 후속:
- 본 PR 은 strategy_factory 에서 같은 strategy 를 반복 instantiate (no per-window
  hyperparameter optimization). 진짜 walk-forward optimization 은 Phase 4 sweep 에서.
- `WalkforwardResult` 직렬화 / report 는 후속 PR.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

import polars as pl

from backtester.core.config import BacktestConfig
from backtester.core.engine import BacktestEngine
from backtester.events.reader import EventLogReader
from backtester.strategies.base import BaseStrategy
from backtester.viz.equity import build_equity_series
from backtester.viz.metrics import compute_core_metrics

WalkforwardMode = Literal["rolling", "expanding"]


@dataclass(frozen=True)
class WalkforwardWindow:
    """단일 train/test window — index 는 0 부터, end 는 exclusive."""

    index: int
    train_start: datetime
    train_end: datetime  # exclusive
    test_start: datetime
    test_end: datetime  # exclusive


class WalkforwardSplitter:
    """``[start, end)`` 를 연속 windows 로 분할. ``mode='rolling'|'expanding'``.

    rolling: 매 window 마다 train 도 같은 길이로 슬라이드 (i 번째 window 의 train 은
        ``[start + i·test_bars·dt, start + (i·test_bars + train_bars)·dt)``).
    expanding: train 시작은 ``start`` 고정, 길이가 누적 (i 번째 train 길이 = ``train_bars
        + i·test_bars``).

    test 구간은 양쪽 모드에서 동일: ``[train_end, train_end + test_bars·dt)``.
    """

    def __init__(
        self,
        *,
        start: datetime,
        end: datetime,
        train_bars: int,
        test_bars: int,
        bar_interval: timedelta,
        mode: WalkforwardMode = "rolling",
    ) -> None:
        if start >= end:
            raise ValueError(
                f"start must be < end, got start={start}, end={end}"
            )
        if train_bars <= 0:
            raise ValueError(f"train_bars must be > 0, got {train_bars}")
        if test_bars <= 0:
            raise ValueError(f"test_bars must be > 0, got {test_bars}")
        if bar_interval <= timedelta(0):
            raise ValueError(
                f"bar_interval must be > 0, got {bar_interval}"
            )
        if mode not in ("rolling", "expanding"):
            raise ValueError(
                f"mode must be 'rolling' or 'expanding', got {mode!r}"
            )
        self.start = start
        self.end = end
        self.train_bars = train_bars
        self.test_bars = test_bars
        self.bar_interval = bar_interval
        self.mode = mode

    def split(self) -> list[WalkforwardWindow]:
        windows: list[WalkforwardWindow] = []
        i = 0
        while True:
            if self.mode == "rolling":
                train_start = self.start + i * self.test_bars * self.bar_interval
                train_end = train_start + self.train_bars * self.bar_interval
            else:  # expanding
                train_start = self.start
                train_end = self.start + (
                    self.train_bars + i * self.test_bars
                ) * self.bar_interval
            test_start = train_end
            test_end = test_start + self.test_bars * self.bar_interval
            if test_end > self.end:
                break
            windows.append(
                WalkforwardWindow(
                    index=i,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )
            i += 1
        return windows


@dataclass(frozen=True)
class WalkforwardWindowResult:
    """단일 window 의 실행 결과: window 정의 + run_dir + OOS metrics dict."""

    window: WalkforwardWindow
    run_dir: Any  # pathlib.Path — circular import 회피용 Any
    metrics: dict[str, Any]


@dataclass(frozen=True)
class WalkforwardResult:
    """전체 windows 의 실행 결과 + 집계."""

    windows: list[WalkforwardWindowResult] = field(default_factory=list)

    def aggregate_metrics(self) -> dict[str, dict[str, float]]:
        """각 metric key 별로 mean/median/std/min/max 집계.

        nan / non-numeric 값은 제외. 모두 nan 이면 5 키 모두 nan. window 가 1 개 뿐이면
        std=0.
        """
        if not self.windows:
            return {}
        keys: list[str] = list(self.windows[0].metrics.keys())
        agg: dict[str, dict[str, float]] = {}
        for key in keys:
            values: list[float] = []
            for w in self.windows:
                v = w.metrics.get(key)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    fv = float(v)
                    if not math.isnan(fv):
                        values.append(fv)
            if not values:
                agg[key] = {
                    "mean": float("nan"),
                    "median": float("nan"),
                    "std": float("nan"),
                    "min": float("nan"),
                    "max": float("nan"),
                }
            else:
                agg[key] = {
                    "mean": statistics.mean(values),
                    "median": statistics.median(values),
                    "std": (
                        statistics.stdev(values) if len(values) >= 2 else 0.0
                    ),
                    "min": min(values),
                    "max": max(values),
                }
        return agg


StrategyFactory = Callable[[], BaseStrategy]


def run_walkforward(
    *,
    base_config: BacktestConfig,
    strategy_factory: StrategyFactory,
    splitter: WalkforwardSplitter,
    periods_per_year: int = 365,
    verbose: bool = False,
) -> WalkforwardResult:
    """각 window 의 ``[train_start, test_end]`` 로 BacktestEngine 실행 + OOS metrics 추출.

    각 window 의 ``run_id`` = ``f"{base_config.run_id}_wf_{i}"``. ``base_config`` 의 다른
    필드 (instruments, strategy_name 등) 는 그대로. start/end/run_id 만 ``dataclasses.replace``
    로 교체.

    OOS metrics 는 engine 실행 후 events.jsonl → build_equity_series → ``timestamp >=
    test_start`` 필터 → ``compute_core_metrics``. train 구간 equity 는 metrics 에 영향 없음.
    """
    windows = splitter.split()
    results: list[WalkforwardWindowResult] = []
    for window in windows:
        cfg = dataclasses.replace(
            base_config,
            run_id=f"{base_config.run_id}_wf_{window.index}",
            start=window.train_start,
            end=window.test_end,
        )
        engine = BacktestEngine(cfg, strategy_factory(), verbose=verbose)
        result = engine.run()

        reader = EventLogReader(result.events_path)
        equity = build_equity_series(reader, base_config.initial_equity)
        test_equity = equity.filter(pl.col("timestamp") >= window.test_start)
        metrics = compute_core_metrics(
            test_equity, periods_per_year=periods_per_year
        )
        results.append(
            WalkforwardWindowResult(
                window=window,
                run_dir=result.run_dir,
                metrics=metrics,
            )
        )
    return WalkforwardResult(windows=results)
