"""BacktestEngine — Phase 1 통합 오케스트레이터 (spec §4.2, §4.3).

ClockEvent 처리 단계 (§4.3):
1. expire (Phase 1: noop, expire_pending() returns [])
2. market snapshot 생성
3. ledger mark-to-market
4. settlement (Phase 1: 항상 빈 리스트)
5. active order fill → FILL + SNAPSHOT(fill)
6. warmup 이후 strategy.on_bar → INTENT_CREATED → handle_intent → ORDER_ADDED/REJECTED
7. on_pending_orders → handle_action
8. periodic SNAPSHOT (snapshot_every_bars 일치 시)

모든 SNAPSHOT은 `_emit_snapshot(ts, reason)` 헬퍼 경유 — snapshot_reason 필드 자동 부착.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import fields as dc_fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from backtester.core.clock import (
    ClockEvent,
    ClockHelper,
    MultiTimeframeClock,
    SimpleClock,
)
from backtester.core.config import BacktestConfig
from backtester.core.context import BarsView, IndicatorsView, StrategyContext
from backtester.core.errors import RunDirectoryError
from backtester.core.orderbook import OrderBook
from backtester.core.orders import OrderAction, OrderIntent
from backtester.core.result import BacktestResult
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import to_decimal
from backtester.data.base import GapReport, parse_timeframe, sanitize_symbol
from backtester.data.bybit_source import BybitDataSource
from backtester.data.csv_source import CSVDataSource
from backtester.data.parquet_source import ParquetDataSource
from backtester.events.log import EventLog
from backtester.events.serialize import serialize_event_payload
from backtester.events.types import (
    Event,
    EventType,
    IntentCreatedPayload,
    SnapshotReason,
)
from backtester.execution.next_bar import NextBarOpenExecution
from backtester.indicators.engine import IndicatorEngine
from backtester.instruments.registry import InstrumentRegistry
from backtester.portfolio.ledger import Ledger
from backtester.portfolio.position import Position
from backtester.portfolio.risk import RiskManager
from backtester.portfolio.sizer import Sizer
from backtester.strategies.base import BaseStrategy


class BacktestEngine:
    """Phase 1 백테스트 엔진."""

    def __init__(
        self,
        config: BacktestConfig,
        strategy: BaseStrategy,
        verbose: bool = True,
    ) -> None:
        # Config 검증은 BacktestConfig.__post_init__에서 이미 실행됨 — 여기 도달했다는 건
        # 검증 통과 의미.
        self.config = config
        self.strategy = strategy
        self.verbose = verbose

        # 1. Run dir 결정 (resolved_run_id 동시 결정)
        self.run_dir, self.resolved_run_id = self._resolve_run_dir()

        # 2. 서브디렉토리 생성
        for sub in ("bars", "indicators", "results", "charts"):
            (self.run_dir / sub).mkdir(exist_ok=True)

        # 3. Instrument registry 빌드
        self.instrument_registry = InstrumentRegistry()
        for inst in config.instruments:
            self.instrument_registry.register(inst)

        # 4. Config 영속화 (Phase 1: config.json + Phase 1.5: config.yaml).
        # `persist_run_data`는 bars/indicators 영속화 정책 — config.{json,yaml}은
        # audit 산출물로 항상 생성한다. resolved_run_id / run_dir 추적이 PR 7
        # acceptance 조건이며 BacktestResult.config_path가 항상 실재 파일을 가리켜야 한다.
        # config.yaml 은 Phase 1.5 PR 11 부터 run_chart 가 self-contained 로 읽기 위해 추가.
        self._persist_config()
        self._persist_config_yaml()

        # 5. 데이터 소스 + fetch
        self.data_source = self._build_data_source()
        self.bars, self.gap_reports = self._fetch_all_bars()
        if config.persist_run_data != "none":
            self._persist_bars()

        # 6. Timestamp 인덱스 (BarsView O(1) 슬라이싱용)
        self.timestamp_index, self.timestamps = self._build_timestamp_indices()

        # 7. Indicators
        self.indicator_engine = IndicatorEngine()
        strategy.on_init(list(config.instruments))
        indicators_persist = (
            (self.run_dir / "indicators")
            if config.persist_run_data != "none"
            else None
        )
        self.indicator_engine.precompute(
            self.bars,
            strategy.required_indicators(),
            persist_to=indicators_persist,
        )

        # 7.1 gap_policy 활성 (PR 16 전 prep): notify -> stdout 알림 + on_data_gap 호출.
        # ffill 은 명시적 NotImplementedError. 이전엔 gap_reports 파일만 쌓고 정책이 무시됐음.
        self._handle_data_gaps()

        # 8. Warmup
        self.warmup_bars = config.warmup_bars or self.indicator_engine.required_warmup(
            strategy.required_indicators()
        )

        # 9. 핵심 컴포넌트
        self.clock = self._build_clock()
        self.clock_helper = ClockHelper()
        self.orderbook = OrderBook()
        self.ledger = Ledger(initial_equity=config.initial_equity)
        self.sizer = Sizer()
        self.risk = RiskManager(config.risk_limits)
        self.execution = self._build_execution_model()

        self.current_snapshots: dict[str, MarketSnapshot] = {}
        self._bar_count = 0
        self._intent_count = 0
        self._fill_count = 0
        self._event_log: EventLog | None = None

    # ---------- Run dir 정책 ------------------------------------------------

    def _resolve_run_dir(self) -> tuple[Path, str]:
        config = self.config
        target = config.output_dir / config.run_id

        if not target.exists():
            target.mkdir(parents=True)
            return target, config.run_id

        policy = config.on_run_exists
        if policy == "fail":
            raise RunDirectoryError(
                f"Run directory already exists: {target}. "
                f"Set on_run_exists to 'overwrite', 'auto_suffix', "
                f"or 'archive' to handle this."
            )
        if policy == "overwrite":
            shutil.rmtree(target)
            target.mkdir(parents=True)
            self._notify_resolution(
                requested=config.run_id,
                resolved=config.run_id,
                policy="overwrite",
                run_dir=target,
            )
            return target, config.run_id
        if policy == "auto_suffix":
            suffix = 2
            while True:
                candidate = config.output_dir / f"{config.run_id}_{suffix}"
                if not candidate.exists():
                    candidate.mkdir(parents=True)
                    resolved = f"{config.run_id}_{suffix}"
                    self._notify_resolution(
                        requested=config.run_id,
                        resolved=resolved,
                        policy="auto_suffix",
                        run_dir=candidate,
                    )
                    return candidate, resolved
                suffix += 1
        if policy == "archive":
            archive_path = self._unique_archive_path(config.output_dir, config.run_id)
            target.rename(archive_path)
            target.mkdir(parents=True)
            self._notify_resolution(
                requested=config.run_id,
                resolved=config.run_id,
                policy="archive",
                run_dir=target,
                archive_path=archive_path,
            )
            return target, config.run_id
        raise RunDirectoryError(  # pragma: no cover — Config 검증이 차단
            f"Unknown on_run_exists policy: {policy!r}"
        )

    @staticmethod
    def _unique_archive_path(parent: Path, run_id: str) -> Path:
        """archive 디렉토리 경로 생성. microsecond 정밀도 timestamp + 충돌 시 _2/_3 부여.

        `%Y%m%d_%H%M%S_%f` 형식으로 microsecond 단위라 동일 microsecond 충돌은 거의
        불가능하지만, 만약 발생해도 _2/_3 접미사로 안전 처리 (CI 빠른 재실행 등 대비).
        """
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        return BacktestEngine._archive_path_with_suffix(parent, run_id, ts_str)

    @staticmethod
    def _archive_path_with_suffix(parent: Path, run_id: str, ts_str: str) -> Path:
        """주어진 timestamp 문자열로 archive 경로를 생성. 동일 timestamp 충돌 시 _2/_3 부여.

        timestamp 생성과 경로 충돌 분기를 분리해, 테스트에서 timestamp를 주입해
        충돌 분기 자체를 결정적으로 검증할 수 있게 한다.
        """
        base = parent / f"{run_id}_archive_{ts_str}"
        if not base.exists():
            return base
        suffix = 2
        while True:
            candidate = parent / f"{run_id}_archive_{ts_str}_{suffix}"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _notify_resolution(
        self,
        *,
        requested: str,
        resolved: str,
        policy: str,
        run_dir: Path,
        archive_path: Path | None = None,
    ) -> None:
        if not self.verbose:
            return
        print(f"[INFO] Run directory already existed, applied '{policy}'")
        print(f"[INFO] Requested run_id: {requested}")
        if requested != resolved:
            print(f"[INFO] Resolved run_id: {resolved}")
        print(f"[INFO] Run directory: {run_dir}")
        if archive_path is not None:
            print(f"[INFO] Previous content archived to: {archive_path}")

    # ---------- Persistence -------------------------------------------------

    def _persist_config(self) -> None:
        """Phase 1: config.json (감사용 단방향 덤프).

        BacktestConfig 복원에 사용하지 않는다 — 양방향 round-trip은 Phase 1.5 PR 9에서
        YAML로 추가. 핵심 필드(`requested_run_id`, `resolved_run_id`, `run_dir`)는
        명시적으로 추가해 dc_fields의 원본 `run_id`와 구분 가능하게 한다.
        """
        cfg_dict: dict[str, Any] = {}
        for f in dc_fields(self.config):
            cfg_dict[f.name] = serialize_event_payload(getattr(self.config, f.name))
        # 명시적 audit 필드 (spec §20 PR 7 acceptance):
        cfg_dict["requested_run_id"] = self.config.run_id
        cfg_dict["resolved_run_id"] = self.resolved_run_id
        cfg_dict["run_dir"] = str(self.run_dir.absolute())
        with open(self.run_dir / "config.json", "w", encoding="utf-8") as fp:
            json.dump(cfg_dict, fp, indent=2)

    def _persist_config_yaml(self) -> None:
        """Phase 1.5+: config.yaml (양방향 round-trip + audit, spec §6.4).

        ``BacktestConfig.to_dict()`` + ``requested_run_id`` / ``resolved_run_id`` /
        ``run_dir`` audit 필드 (json 영속화와 동일 키). ``BacktestConfig.from_yaml`` 이
        audit 필드를 무시하므로 분석 도구는 직접 yaml dict 를 읽고, 재실행 시
        ``from_yaml`` 로 ``BacktestConfig`` 복원한다.
        """
        import yaml as _yaml

        data = self.config.to_dict()
        # config.json 과 동일한 audit 필드 (test_run_directory acceptance 등)
        data["requested_run_id"] = self.config.run_id
        data["resolved_run_id"] = self.resolved_run_id
        data["run_dir"] = str(self.run_dir.absolute())
        with open(self.run_dir / "config.yaml", "w", encoding="utf-8") as fp:
            _yaml.safe_dump(data, fp, sort_keys=False, default_flow_style=False)

    def _persist_bars(self) -> None:
        """Phase 1: copy만 (symlink는 Windows 호환 이슈로 Phase 1.5+로 미룸)."""
        if self.config.persist_run_data == "none":
            return
        # symlink 요청도 Phase 1에서는 copy로 fallback
        bars_dir = self.run_dir / "bars"
        for symbol, tfs in self.bars.items():
            for tf, df in tfs.items():
                target = bars_dir / f"{sanitize_symbol(symbol)}_{tf}.parquet"
                df.write_parquet(target)

    def _persist_results(self) -> None:
        """results/는 EventLog 캐시 (spec §6.3). Phase 1: equity_curve.parquet만."""
        eq = self.ledger.equity_curve()
        eq.write_parquet(self.run_dir / "results" / "equity_curve.parquet")

    def _export_events_parquet(self) -> None:
        """Phase 1.5: events.jsonl → events.parquet (분석 편의용 캐시, spec §6.2).

        events.jsonl 이 1차 원본이므로 변환 실패해도 백테스트 결과 자체는 영향 없음.
        그러나 정상 백테스트라면 jsonl 이 무결하므로 단순 호출.
        """
        from backtester.events.parquet_export import events_jsonl_to_parquet

        events_jsonl_to_parquet(
            jsonl_path=self.run_dir / "events.jsonl",
            parquet_path=self.run_dir / "events.parquet",
        )

    # ---------- gap_policy --------------------------------------------------

    def _handle_data_gaps(self) -> None:
        """``config.gap_policy`` 활성 (PR 16 전 prep, spec §5.1).

        - ``notify`` (기본): GapReport 가 비어 있지 않은 (symbol, tf) 마다 verbose stdout
          알림 + ``strategy.on_data_gap(symbol, gap_start, gap_end)`` 콜백. 콜백이 반환한
          ``OrderIntent`` 는 알림용으로 로깅만 하고 자동 주입은 안 함 — Phase 2 한정,
          gap-driven intent 주입은 후속 PR (event-loop 내 hook 위치 결정 필요).
        - ``ffill``: ``NotImplementedError``. 이전엔 config 가 통과만 시키고 정책이 무시됐음.
          본 PR 은 명시적 차단. 실제 ffill 보정은 후속 PR.

        gap_reports 는 ``_fetch_all_bars()`` 가 채워둔 ``self.gap_reports`` 를 사용.
        """
        policy = self.config.gap_policy
        if policy == "ffill":
            raise NotImplementedError(
                "gap_policy='ffill' is deferred to a subsequent PR; "
                "use 'notify' (default) until then"
            )
        # notify
        for symbol, tfs in self.gap_reports.items():
            for tf, report in tfs.items():
                if not report.gaps:
                    continue
                if self.verbose:
                    print(
                        f"[WARN] data gap {symbol}/{tf}: "
                        f"{report.total_missing_bars} missing bar(s) "
                        f"across {len(report.gaps)} gap(s)"
                    )
                for gap_start, gap_end in report.gaps:
                    intents = self.strategy.on_data_gap(symbol, gap_start, gap_end)
                    if intents and self.verbose:
                        print(
                            f"[INFO] strategy.on_data_gap returned "
                            f"{len(intents)} intent(s) for {symbol}/{tf} gap "
                            f"[{gap_start.isoformat()} .. {gap_end.isoformat()}] "
                            f"— Phase 2: not auto-injected (deferred to subsequent PR)"
                        )

    # ---------- 빌더 -------------------------------------------------------

    def _build_data_source(
        self,
    ) -> ParquetDataSource | CSVDataSource | BybitDataSource:
        ds = self.config.data_source
        if ds.type == "parquet":
            return ParquetDataSource(ds.base_dir)
        if ds.type == "csv":
            return CSVDataSource(ds.base_dir)
        if ds.type == "bybit":
            return BybitDataSource(ds.base_dir)
        raise NotImplementedError(  # pragma: no cover — Config 검증이 차단
            f"DataSource type {ds.type!r} is Phase 2+"
        )

    def _fetch_all_bars(
        self,
    ) -> tuple[
        dict[str, dict[str, pl.DataFrame]],
        dict[str, dict[str, GapReport]],
    ]:
        bars: dict[str, dict[str, pl.DataFrame]] = {}
        gaps: dict[str, dict[str, GapReport]] = {}
        for symbol, tfs in self.config.timeframes_per_symbol.items():
            bars[symbol] = {}
            gaps[symbol] = {}
            for tf in tfs:
                df, rpt = self.data_source.fetch(
                    symbol, tf, self.config.start, self.config.end
                )
                bars[symbol][tf] = df
                gaps[symbol][tf] = rpt
        return bars, gaps

    def _build_timestamp_indices(
        self,
    ) -> tuple[
        dict[str, dict[str, dict[datetime, int]]],
        dict[str, dict[str, list[datetime]]],
    ]:
        idx: dict[str, dict[str, dict[datetime, int]]] = {}
        ts_lists: dict[str, dict[str, list[datetime]]] = {}
        for symbol, tfs in self.bars.items():
            idx[symbol] = {}
            ts_lists[symbol] = {}
            for tf, df in tfs.items():
                ts_list = df["timestamp"].to_list()
                idx[symbol][tf] = {ts: i for i, ts in enumerate(ts_list)}
                ts_lists[symbol][tf] = ts_list
        return idx, ts_lists

    def _build_clock(self) -> SimpleClock | MultiTimeframeClock:
        """단일 TF 면 SimpleClock, 다중 TF (또는 다중 symbol) 이면 MultiTimeframeClock."""
        primary = self.config.primary_symbol
        primary_tf = self.config.primary_timeframe

        # 다중 TF 또는 다중 symbol 여부 판정
        tf_pairs: list[tuple[str, str]] = []
        for sym, tfs in self.config.timeframes_per_symbol.items():
            for tf in tfs:
                tf_pairs.append((sym, tf))

        if len(tf_pairs) == 1:
            # 단일 (symbol, tf) — Phase 1 호환 경로
            bar_starts = self.timestamps[primary][primary_tf]
            return SimpleClock([primary], primary_tf, bar_starts)

        bar_timestamps: dict[tuple[str, str], list[datetime]] = {}
        for sym, tf in tf_pairs:
            bar_timestamps[(sym, tf)] = self.timestamps[sym][tf]
        return MultiTimeframeClock(bar_timestamps)

    def _build_execution_model(self) -> NextBarOpenExecution:
        em = self.config.execution_model
        bpm = self.config.bar_path_model  # PR 15c 분기 (default PESSIMISTIC)
        if em == "next_bar_open":
            # Phase 1 호환 — slippage 0 + bar_path_model 전달
            return NextBarOpenExecution(bar_path_model=bpm)
        if em == "slippage_bps":
            # Phase 2 PR 15a — config.slippage_bps 를 NextBarOpen 에 주입
            return NextBarOpenExecution(
                slippage_bps=self.config.slippage_bps,
                bar_path_model=bpm,
            )
        if em == "atr_slippage":
            # ATR slippage 는 atr_provider 주입이 필요해 Engine 자동 wiring 불가.
            # 사용자가 명시적으로 ``AtrSlippageExecution`` 을 만들어 ``BacktestEngine`` 의
            # 후속 패치 또는 strategy-level injection 으로 사용 (PR 15+ / PR 16).
            raise NotImplementedError(
                "execution_model='atr_slippage' requires explicit "
                "AtrSlippageExecution construction with atr_provider injection; "
                "Engine auto-wiring is deferred to subsequent PRs"
            )
        raise NotImplementedError(  # pragma: no cover — Config 검증이 차단
            f"execution_model {em!r} is Phase 2+"
        )

    # ---------- 메인 루프 --------------------------------------------------

    def run(self) -> BacktestResult:
        with EventLog(self.run_dir) as event_log:
            self._event_log = event_log
            for clock_event in self.clock:
                self._process_event(clock_event)
        self._event_log = None

        self._persist_results()
        self._export_events_parquet()

        return BacktestResult(
            requested_run_id=self.config.run_id,
            resolved_run_id=self.resolved_run_id,
            run_dir=self.run_dir,
            final_equity=self.ledger.equity,
            total_return=(self.ledger.equity / self.config.initial_equity)
            - Decimal("1"),
            num_fills=self._fill_count,
            num_intents=self._intent_count,
            # Phase 1.5+: config.yaml 이 canonical (양방향 round-trip).
            # config.json 은 Phase 1 audit 형식으로 추가 영속화 유지.
            config_path=self.run_dir / "config.yaml",
            events_path=self.run_dir / "events.jsonl",
        )

    def _emit_snapshot(self, ts: datetime, reason: SnapshotReason) -> None:
        """모든 SNAPSHOT은 이 헬퍼 경유 — snapshot_reason 자동 부착 (spec §11.5)."""
        if self._event_log is None:
            return  # run() 외부에서 호출 방어
        payload = self.ledger.snapshot()
        payload["snapshot_reason"] = reason
        self._event_log.append(
            Event(ts=ts, type=EventType.SNAPSHOT, payload=payload)
        )

    def _process_event(self, event: ClockEvent) -> None:
        ts = event.timestamp
        assert self._event_log is not None  # run() 안에서만 호출됨

        # 1. 만료 주문 (Phase 1: 빈 리스트)
        expired = self.orderbook.expire_pending(ts)
        if expired:  # Phase 1.5+에서 활성
            self.ledger.on_expired(expired)

        # 2. 시장 스냅샷
        snapshots = self._build_snapshots(ts, event.bar_closes)
        self.current_snapshots = snapshots

        # 3. mark-to-market
        self.ledger.on_market(snapshots)

        # 4. Settlement (Phase 1: settlements는 항상 빈 리스트)
        # for symbol, kind in event.settlements: ...

        # 5. 활성 주문 체결
        for order in self.orderbook.get_active():
            snap = snapshots.get(order.intent.symbol)
            if snap is None:
                continue
            instrument = self.instrument_registry.get(order.intent.symbol)
            fill = self.execution.try_fill(order, snap, instrument)
            if fill is None:
                continue
            self.orderbook.fill(order.id, fill)
            self.ledger.on_fill(fill, instrument)
            self._fill_count += 1
            self._event_log.append(Event(ts=ts, type=EventType.FILL, payload=fill))
            self._emit_snapshot(ts, "fill")  # implicit, 주기 무관

        # 6. 봉 마감 시 전략 + periodic SNAPSHOT
        # multi-TF: primary TF 가 닫혔을 때만 strategy 호출 + bar_count 증가.
        # 보조 TF 만 닫힌 ClockEvent (예: primary=4h 인데 1h 만 닫힌 시점) 에서는 mark-to-
        # market 까지만 수행하고 strategy 는 건드리지 않는다 (lookahead 차단).
        primary_sym = self.config.primary_symbol
        primary_tf = self.config.primary_timeframe
        primary_closed = primary_tf in event.bar_closes.get(primary_sym, [])

        if primary_closed:
            self._bar_count += 1
            if self._bar_count > self.warmup_bars:
                self._invoke_strategy(ts, snapshots)

            # 7. 봉 마감 정기 SNAPSHOT
            if self._bar_count % self.config.snapshot_every_bars == 0:
                self._emit_snapshot(ts, "periodic")

    def _invoke_strategy(
        self,
        ts: datetime,
        snapshots: dict[str, MarketSnapshot],
    ) -> None:
        assert self._event_log is not None
        bars_view = BarsView(
            bars=self.bars,
            timestamp_index=self.timestamp_index,
            timestamps=self.timestamps,
            clock_helper=self.clock_helper,
            now=ts,
        )
        indicators_view = IndicatorsView(
            cache=self.indicator_engine.snapshot(),
            timestamp_index=self.timestamp_index,
            timestamps=self.timestamps,
            clock_helper=self.clock_helper,
            now=ts,
        )
        ctx = StrategyContext(
            now=ts,
            primary_symbol=self.config.primary_symbol,
            primary_timeframe=self.config.primary_timeframe,
            bars=bars_view,
            indicators=indicators_view,
        )
        intents = self.strategy.on_bar(ctx)

        primary_snap = snapshots.get(self.config.primary_symbol)
        primary_close = primary_snap.close if primary_snap else Decimal("0")
        primary_bar_ts = primary_snap.timestamp if primary_snap else ts

        for intent in intents:
            self._intent_count += 1
            payload = IntentCreatedPayload(
                intent=intent,
                decision_ts=ts,
                bar_timestamp=primary_bar_ts,
                bar_close_price=primary_close,
            )
            self._event_log.append(
                Event(ts=ts, type=EventType.INTENT_CREATED, payload=payload)
            )

        for intent in intents:
            self._handle_intent(intent, ts)

        # Pending order management
        pending = self.orderbook.get_active()
        actions = self.strategy.on_pending_orders(ctx, pending)
        for action in actions:
            self._handle_action(action, ts)

    def _build_snapshots(
        self,
        ts: datetime,
        bar_closes: dict[str, list[str]],
    ) -> dict[str, MarketSnapshot]:
        snapshots: dict[str, MarketSnapshot] = {}
        for symbol, tfs in bar_closes.items():
            primary_tf = self.config.primary_timeframe
            tf = primary_tf if primary_tf in tfs else tfs[0]
            df = self.bars[symbol][tf]
            idx_map = self.timestamp_index[symbol][tf]
            interval = parse_timeframe(tf)
            bar_start = ts - interval
            idx = idx_map.get(bar_start)
            if idx is None:
                continue  # gap 또는 데이터 범위 밖
            row = df.row(idx, named=True)
            snapshots[symbol] = MarketSnapshot(
                symbol=symbol,
                timestamp=row["timestamp"],
                open=to_decimal(row["open"]),
                high=to_decimal(row["high"]),
                low=to_decimal(row["low"]),
                close=to_decimal(row["close"]),
                volume=to_decimal(row["volume"]),
            )
        return snapshots

    def _handle_intent(self, intent: OrderIntent, ts: datetime) -> None:
        assert self._event_log is not None
        try:
            instrument = self.instrument_registry.get(intent.symbol)
        except Exception as e:  # noqa: BLE001 — InstrumentError를 거부 사유로 기록
            self._event_log.append(
                Event(
                    ts=ts,
                    type=EventType.ORDER_REJECTED,
                    payload={"intent": intent, "reason": f"unknown symbol: {e}"},
                )
            )
            return

        position = self.ledger.positions.get(
            intent.symbol, Position(symbol=intent.symbol)
        )
        market = self.current_snapshots.get(intent.symbol)
        if market is None:
            self._event_log.append(
                Event(
                    ts=ts,
                    type=EventType.ORDER_REJECTED,
                    payload={"intent": intent, "reason": "no market data"},
                )
            )
            return

        try:
            sized_quantity = self.sizer.resolve(
                intent=intent,
                instrument=instrument,
                equity=self.ledger.equity,
                position=position,
                market=market,
            )
        except (NotImplementedError, ValueError) as e:
            self._event_log.append(
                Event(
                    ts=ts,
                    type=EventType.ORDER_REJECTED,
                    payload={"intent": intent, "reason": f"sizer: {e}"},
                )
            )
            return

        if sized_quantity <= 0:
            return  # nothing to trade (예: ClosePosition on flat)

        risk_result = self.risk.check(
            intent=intent,
            sized_quantity=sized_quantity,
            instrument=instrument,
            ledger=self.ledger,
            active_orders=self.orderbook.get_active(),
        )
        if not risk_result.accepted:
            self._event_log.append(
                Event(
                    ts=ts,
                    type=EventType.ORDER_REJECTED,
                    payload={"intent": intent, "reason": f"risk: {risk_result.reason}"},
                )
            )
            return

        order = self.orderbook.add(intent, sized_quantity, ts)
        self._event_log.append(
            Event(
                ts=ts,
                type=EventType.ORDER_ADDED,
                payload={
                    "order_id": order.id,
                    "intent": intent,
                    "sized_quantity": sized_quantity,
                },
            )
        )

    def _handle_action(self, action: OrderAction, ts: datetime) -> None:
        if action.type == "new":
            if action.intent is not None:
                self._handle_intent(action.intent, ts)
            return
        if action.type in ("cancel", "modify"):
            raise NotImplementedError(
                f"OrderAction.type={action.type!r} is Phase 2 "
                f"(Phase 1: 'new'만 지원)"
            )
        raise NotImplementedError(  # pragma: no cover
            f"Unknown OrderAction.type: {action.type!r}"
        )
