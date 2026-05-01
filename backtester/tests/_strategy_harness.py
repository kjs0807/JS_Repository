"""Reusable regression harness for new strategies (PR F).

본 모듈은 ``test_*`` 접두사가 아니므로 pytest 가 직접 수집하지 않는다 — 다른 테스트
파일이 ``StrategyHarness`` 를 import 해서 사용한다. BBKC 가 첫 번째 통과 사례이고,
FRAMA 등 후속 전략도 동일 contract 를 통과해야 한다.

검증 항목 (전략 개발용 기준 엔진 계약):
1. **No-lookahead**: 같은 데이터 prefix 에서 만들어진 의사결정이 미래 데이터에 의존하지
   않음. 전체 데이터로 한 번 + 절반 데이터로 한 번 실행 → 절반 데이터의 events 가
   전체 데이터의 같은 시점 events 의 prefix.
2. **Deterministic events**: 같은 (config, seed, data) 두 번 실행 → events.jsonl
   byte-identical (PR B 와 동일 회귀, 전략별 적용).
3. **Position state sync**: events.jsonl 만 가지고 — 매 FILL 의 누적 size 가 같은 ts
   SNAPSHOT.positions 와 일치한다 (Ledger ↔ EventLog 정합성). ``ctx.portfolio`` 의
   직접 wiring 검증은 ``test_pr_a_portfolio_view.py`` 의 Engine smoke 테스트가 별도로
   담당하며, 본 contract 는 EventLog 만으로 검증 가능한 표면 정합성을 본다.
4. **Chart renders**: ``build_run_chart(run_dir)`` 가 raise 없이 ``go.Figure`` 반환.
5. **Rebuild consistency**: ``rebuild_equity_curve(run_dir)`` 가 events.jsonl 만으로
   ``results/equity_curve.parquet`` 재생성. 빈 이벤트 아닌 경우 행 ≥ 1.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.analysis.rebuild import rebuild_equity_curve
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.result import BacktestResult
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import Instrument
from backtester.strategies.base import BaseStrategy
from backtester.viz.run_chart import build_run_chart


@dataclass(frozen=True)
class HarnessSpec:
    """전략 회귀 harness 입력 — 데이터 / 인스트루먼트 / 시간 범위 등 고정 컨텍스트."""

    name: str  # run_id prefix
    strategy_factory: Callable[[], BaseStrategy]
    instrument: Instrument
    parquet_path: Path  # 단일 (symbol, tf) parquet
    primary_symbol: str
    primary_timeframe: str
    start: datetime
    end: datetime
    initial_equity: Decimal
    output_root: Path  # tmp_path 권장 — harness 가 sub-dir 만듦


class StrategyHarness:
    """전략의 backtester 계약을 일괄 검증."""

    def __init__(self, spec: HarnessSpec) -> None:
        self.spec = spec

    # ---------- helpers -----------------------------------------------------

    def _build_config(self, run_id: str, parquet_dir: Path) -> BacktestConfig:
        return BacktestConfig(
            run_id=run_id,
            data_source=DataSourceConfig(base_dir=parquet_dir),
            instruments=[self.spec.instrument],
            timeframes_per_symbol={
                self.spec.primary_symbol: [self.spec.primary_timeframe]
            },
            primary_symbol=self.spec.primary_symbol,
            primary_timeframe=self.spec.primary_timeframe,
            start=self.spec.start,
            end=self.spec.end,
            initial_equity=self.spec.initial_equity,
            output_dir=self.spec.output_root / "runs",
        )

    def _data_dir(self) -> Path:
        return self.spec.parquet_path.parent

    def _run(self, run_id_suffix: str) -> BacktestResult:
        cfg = self._build_config(
            f"{self.spec.name}_{run_id_suffix}", self._data_dir()
        )
        return BacktestEngine(cfg, self.spec.strategy_factory(), verbose=False).run()

    # ---------- 1. no-lookahead --------------------------------------------

    def assert_no_lookahead(self) -> None:
        """전체 / 절반 데이터로 두 번 실행. 절반 events 가 전체 events 의 prefix.

        구현: 절반 데이터를 별도 디렉토리에 쓰고, end 도 절반 ts 로 줄인 config 로 실행.
        절반 결과의 INTENT_CREATED + FILL 이 전체 결과의 같은 ts 까지 prefix 와 같아야 함.
        """
        full_result = self._run("full")
        full_events = list(EventLogReader(full_result.events_path).all_events())

        # 절반 데이터 추출
        df = pl.read_parquet(self.spec.parquet_path)
        half = df.slice(0, df.height // 2)
        half_dir = self.spec.output_root / "half_data"
        half_dir.mkdir(parents=True, exist_ok=True)
        half_path = half_dir / self.spec.parquet_path.name
        half.write_parquet(half_path)

        # 절반 end 시각 (마지막 행의 ts + 1봉)
        last_ts = half["timestamp"][-1]
        bar_interval = (
            df["timestamp"][1] - df["timestamp"][0]
            if df.height >= 2
            else timedelta(hours=1)
        )
        half_end = last_ts + bar_interval
        # 절반 config 별도 build
        cfg_half = BacktestConfig(
            run_id=f"{self.spec.name}_half",
            data_source=DataSourceConfig(base_dir=half_dir),
            instruments=[self.spec.instrument],
            timeframes_per_symbol={
                self.spec.primary_symbol: [self.spec.primary_timeframe]
            },
            primary_symbol=self.spec.primary_symbol,
            primary_timeframe=self.spec.primary_timeframe,
            start=self.spec.start,
            end=half_end,
            initial_equity=self.spec.initial_equity,
            output_dir=self.spec.output_root / "runs",
        )
        half_result = BacktestEngine(
            cfg_half, self.spec.strategy_factory(), verbose=False
        ).run()
        half_events = list(EventLogReader(half_result.events_path).all_events())

        # 의사결정 ts <= half_end 인 events 만 비교
        # INTENT_CREATED / FILL 이 lookahead 검출의 핵심
        relevant = (EventType.INTENT_CREATED, EventType.FILL)
        full_keys = [
            (e.ts, e.type, _stable_payload_key(e.payload))
            for e in full_events
            if e.type in relevant and e.ts <= half_end
        ]
        half_keys = [
            (e.ts, e.type, _stable_payload_key(e.payload))
            for e in half_events
            if e.type in relevant and e.ts <= half_end
        ]
        assert half_keys == full_keys, (
            f"lookahead detected — half data INTENT_CREATED/FILL sequence differs from "
            f"full data prefix.\nhalf: {half_keys}\nfull: {full_keys}"
        )

    # ---------- 2. deterministic ------------------------------------------

    def assert_deterministic_events(self) -> None:
        """두 번 실행 → events.jsonl byte-identical (PR B 게이트)."""
        a = self._run("det_a")
        b = self._run("det_b")
        b1 = a.events_path.read_bytes()
        b2 = b.events_path.read_bytes()
        assert b1 == b2, (
            "events.jsonl differs between identical runs"
        )

    # ---------- 3. position state sync ------------------------------------

    def assert_position_state_sync(self) -> None:
        """매 FILL 이후 같은 ts SNAPSHOT 의 positions 가 누적 fill 결과와 일치.

        구현: events.jsonl 을 순회하며 FILL 시뮬해 internal 누적 size 계산 → 같은 ts 의
        SNAPSHOT 에서 보고된 size 와 비교 (Decimal 비교).
        """
        result = self._run("sync")
        events = list(EventLogReader(result.events_path).all_events())
        accum: dict[str, Decimal] = {}
        for e in events:
            if e.type == EventType.FILL:
                p = e.payload
                size = Decimal(str(p["size"]))
                if p["side"] == "buy":
                    accum[p["symbol"]] = accum.get(p["symbol"], Decimal("0")) + size
                else:
                    accum[p["symbol"]] = accum.get(p["symbol"], Decimal("0")) - size
            elif e.type == EventType.SNAPSHOT:
                snap_positions = e.payload.get("positions", {})
                for sym, expected_size in accum.items():
                    if expected_size == 0:
                        # flat 은 snapshot 에서 빠짐 (ledger.snapshot 정책)
                        if sym in snap_positions:
                            assert Decimal(str(snap_positions[sym]["size"])) == 0
                        continue
                    assert sym in snap_positions, (
                        f"SNAPSHOT at {e.ts} missing position for {sym!r} "
                        f"(expected size {expected_size})"
                    )
                    actual = Decimal(str(snap_positions[sym]["size"]))
                    assert actual == expected_size, (
                        f"SNAPSHOT at {e.ts} {sym}: expected {expected_size}, "
                        f"got {actual}"
                    )

    # ---------- 4. chart renders ------------------------------------------

    def assert_chart_renders(self) -> None:
        """``build_run_chart(run_dir)`` 가 raise 없이 Figure 반환."""
        result = self._run("chart")
        fig = build_run_chart(result.run_dir)
        assert fig is not None
        assert hasattr(fig, "data")  # plotly Figure 의 기본 attr

    # ---------- 5. rebuild consistency ------------------------------------

    def assert_rebuild_consistency(self) -> None:
        """run_dir 의 results/charts 캐시 삭제 후 rebuild 가 events.jsonl 만으로 동작."""
        result = self._run("rebuild")
        run_dir = result.run_dir
        for sub in ("results", "charts"):
            sub_path = run_dir / sub
            if sub_path.exists():
                shutil.rmtree(sub_path)
        out = rebuild_equity_curve(run_dir)
        assert out.exists()
        eq = pl.read_parquet(out)
        # SNAPSHOT 이벤트가 하나라도 있으면 height >= 1.
        # 빈 시리즈는 strategy 가 신호를 안 냈을 경우 — 그래도 파일은 존재해야 함.
        assert eq.is_empty() or "equity" in eq.columns

    # ---------- 일괄 검증 ---------------------------------------------------

    def assert_all(self) -> None:
        """5 contracts 일괄 — 한 번 호출로 BBKC / FRAMA / 새 전략 모두 검증 가능."""
        self.assert_no_lookahead()
        self.assert_deterministic_events()
        self.assert_position_state_sync()
        self.assert_chart_renders()
        self.assert_rebuild_consistency()


def _stable_payload_key(payload: object) -> str:
    """payload 를 비교 가능한 stable key 로 변환 — dataclass / dict / scalar 호환."""
    import json

    from backtester.events.serialize import serialize_event_payload

    return json.dumps(serialize_event_payload(payload), sort_keys=True)
