"""BacktestEngine — Phase 1+ 통합 오케스트레이터 (spec §4.2, §4.3).

ClockEvent 처리 단계 (§4.3, PR G 정렬):
1. expire (PR D 활성: ts >= expires_at 인 active 주문 → expired + ORDER_EXPIRED + SNAPSHOT)
2. market snapshot 생성 (직전 봉의 OHLC)
3. **active order fill** (PR G: 같은 봉 open 가격에 체결 — funding/mark 보다 먼저)
   → FILL + SNAPSHOT(fill)
4. ledger mark-to-market (post-fill 포지션을 봉 close 로 mark + equity_history 적재)
5. settlement / funding (PR E, PR G: post-fill + post-mark 포지션으로 계산) →
   SETTLE + SNAPSHOT(settlement)
6. warmup 이후 strategy.on_bar → INTENT_CREATED → handle_intent → ORDER_ADDED/REJECTED
7. on_pending_orders(ctx, fresh OrdersView) → handle_action (cancel/modify)
8. periodic SNAPSHOT (snapshot_every_bars 일치 시)

PR G 변경 (이전 버전과 다름):
- 이전: snapshot → mark → funding → fill — 같은 봉의 fill 이 mark/funding 에 누락됨.
- 이제: snapshot → fill → mark → funding — fill 결과가 같은 봉의 mark + funding 에 반영.
- on_pending_orders 가 ``list[Order]`` 가 아닌 ``tuple[OrderView, ...]`` (read-only)
  를 받음. 새 intent 가 처리된 후 fresh OrdersView 를 주입 — ctx.orders 와 동일한
  스냅샷이 아니라 "그 시점 active 주문" 기준.

모든 SNAPSHOT은 `_emit_snapshot(ts, reason)` 헬퍼 경유 — snapshot_reason 필드 자동 부착.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import fields as dc_fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import polars as pl

from backtester.core.clock import (
    ClockEvent,
    ClockHelper,
    MultiTimeframeClock,
    SimpleClock,
)
from backtester.core.config import BacktestConfig
from backtester.core.context import (
    BarsView,
    IndicatorsView,
    OrdersView,
    OrderView,
    PortfolioView,
    PositionView,
    StrategyContext,
)
from backtester.core.errors import RunDirectoryError
from backtester.core.orderbook import Order, OrderBook
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
from backtester.execution.funding import FundingProcessor
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
        # PR H: Sizer 에 short/flip 정책 전달.
        self.sizer = Sizer(
            allow_short=config.allow_short,
            allow_flip=config.allow_flip,
        )
        self.risk = RiskManager(config.risk_limits)
        self.execution = self._build_execution_model()

        # PR E + PR Q: FundingProcessor (config.funding_models 비어있으면 None).
        # ``funding_source_dir`` 가 설정돼 있으면 ``ParquetFundingRateSource`` 를 만들어
        # FundingProcessor 에 주입 — ``rate_source="from_data_source"`` 모델 활성.
        funding_rate_source = None
        if config.funding_source_dir is not None:
            from backtester.data.funding_source import ParquetFundingRateSource

            funding_rate_source = ParquetFundingRateSource(config.funding_source_dir)
        self.funding_processor: FundingProcessor | None = (
            FundingProcessor(config.funding_models, rate_source=funding_rate_source)
            if config.funding_models
            else None
        )

        # PR Q — funding parquet 을 run_dir/funding/ 으로 self-contained 복사 (있을 때만).
        if (
            config.funding_source_dir is not None
            and config.persist_run_data != "none"
        ):
            self._persist_funding_artifacts()

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

    def _persist_funding_artifacts(self) -> None:
        """PR Q: funding parquet 을 run_dir/funding/ 으로 복사 — rebuild/walkforward 가
        외부 cache 없이 funding 을 재현할 수 있도록 self-contained.
        """
        from backtester.data.base import sanitize_symbol

        src_dir = self.config.funding_source_dir
        if src_dir is None or not src_dir.exists():
            return
        target_dir = self.run_dir / "funding"
        target_dir.mkdir(exist_ok=True)
        for symbol in self.config.timeframes_per_symbol:
            src = src_dir / f"funding_{sanitize_symbol(symbol)}.parquet"
            if src.exists():
                shutil.copy2(src, target_dir / src.name)

    def _persist_results(self) -> None:
        """results/는 EventLog 캐시 (spec §6.3). PR U: rebuild_equity_curve 사용 —
        ``Ledger.equity_curve()`` 는 ``on_market`` 시점만 적재해서 같은 ts 의 funding /
        liquidation 처리 결과가 누락된다. ``rebuild_equity_curve`` 는 EventLog SNAPSHOT
        이벤트 (post-fill / post-funding / post-liquidation 모두 포함) 에서 추출하므로
        cache 와 1차 원본이 항상 일치한다.
        """
        from backtester.analysis.rebuild import rebuild_equity_curve

        # rebuild_equity_curve 가 results/ 디렉토리에 직접 write.
        rebuild_equity_curve(self.run_dir)

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
        """``config.gap_policy`` 활성 (PR 16 prep + PR C 강화, spec §5.1).

        - ``notify`` (기본): GapReport 가 비어 있지 않은 (symbol, tf) 마다 verbose stdout
          알림 + ``strategy.on_data_gap(symbol, gap_start, gap_end)`` 콜백. 콜백이 반환한
          ``OrderIntent`` 는 알림용으로 로깅만 하고 자동 주입은 안 함 — Phase 2 한정,
          gap-driven intent 주입은 후속 PR (event-loop 내 hook 위치 결정 필요).
        - ``strict`` (PR C): 데이터 갭이 하나라도 있으면 즉시 ``DataError``. 백테스트가
          시작도 안 됨. crypto 전략처럼 가격 연속성이 신뢰되어야 하는 도메인 권장.
          ``strategy.on_data_gap`` 콜백은 호출하지 않음 (전략에 책임 떠넘기지 않음).

        gap_reports 는 ``_fetch_all_bars()`` 가 채워둔 ``self.gap_reports`` 를 사용.
        """
        from backtester.core.errors import DataError

        policy = self.config.gap_policy
        if policy == "strict":
            details: list[str] = []
            for symbol, tfs in self.gap_reports.items():
                for tf, report in tfs.items():
                    if report.gaps:
                        details.append(
                            f"{symbol}/{tf}: {report.total_missing_bars} missing "
                            f"bar(s) across {len(report.gaps)} gap(s)"
                        )
            if details:
                raise DataError(
                    "gap_policy='strict' detected data gaps: "
                    + "; ".join(details)
                    + " — switch to 'notify' or fix the data source"
                )
            return
        # notify (default)
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
            return BybitDataSource(ds.base_dir, category=ds.bybit_category)
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
        # ``atr_slippage`` 는 PR 16 prep 2차 에서 config-level 로 차단 (ConfigError) —
        # atr_provider 주입은 코드 레벨 작업이며, config 표현 방식이 결정되기 전까지
        # ``BacktestConfig`` 만으로는 해당 모델을 활성화하지 않는다.
        raise NotImplementedError(  # pragma: no cover — Config 검증이 차단
            f"execution_model {em!r} is not config-supported "
            f"(use direct AtrSlippageExecution injection or future PR config schema)"
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

        # 1. 만료 주문 (PR D: expires_at 활성). 각 만료 주문에 ORDER_EXPIRED 이벤트 발행
        # + Ledger.on_expired (Phase 1 noop, Phase 2+ 마진 해제 등 활성 가능) + SNAPSHOT
        # reason='expire'.
        expired = self.orderbook.expire_pending(ts)
        for order in expired:
            self._event_log.append(
                Event(
                    ts=ts,
                    type=EventType.ORDER_EXPIRED,
                    payload={
                        "order_id": order.id,
                        "symbol": order.intent.symbol,
                        "remaining": str(order.remaining),
                    },
                )
            )
        if expired:
            self.ledger.on_expired(expired)
            self._emit_snapshot(ts, "expire")

        # 2. 시장 스냅샷 (직전 봉 OHLC)
        snapshots = self._build_snapshots(ts, event.bar_closes)
        self.current_snapshots = snapshots

        # 3. PR G: 활성 주문 체결 — 봉 open 가격으로 (NextBarOpenExecution).
        # mark/funding 보다 먼저 처리 → 같은 봉에서 fill 된 포지션이 같은 봉의 mark
        # 와 funding 계산에 반영된다.
        # PR K: 체결 후 entry 가 ``intent.bracket`` 을 가지면 reduce-only TP/SL child
        # 자동 생성. children 도 같은 봉의 후속 체결 시도가 일어날 수 있도록 같은
        # iteration 의 snapshot list 만 사용하여 무한 루프 방지.
        # PR L: OCO group 내부 우선순위는 BarPathModel — same-bar TP/SL 양쪽 도달 시
        # PESSIMISTIC = stop 먼저, OPTIMISTIC = limit 먼저. 한쪽이 fill 되면 sibling
        # 자동 cancel + ORDER_CANCELLED(reason="oco_sibling_filled").
        active_at_start = self._order_oco_aware_active_list(
            self.orderbook.get_active()
        )
        spawned_in_iteration: list[Order] = []
        for order in active_at_start:
            if not order.is_active:
                continue  # PR L: sibling fill 이 이 주문을 cancel 했을 수 있음
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

            # PR L: OCO sibling 자동 cancel.
            if order.oco_group_id is not None:
                self._cancel_oco_siblings(order, ts)

            # PR K: bracket child 생성 (entry 만, 즉 parent_order_id is None).
            if (
                order.state == "filled"
                and order.parent_order_id is None
                and order.intent.bracket is not None
                and order.intent.bracket.has_any()
            ):
                children = self._spawn_bracket_children(order, fill, ts)
                spawned_in_iteration.extend(children)

        # PR U: 같은 봉 entry-bar bracket child 체결 시도. 진입 봉의 high/low 가
        # TP/SL 에 도달하면 같은 봉에서 체결 가능하도록 — crypto 급변 봉에서 SL 보다
        # 다음 봉까지 살아남는 갭을 차단. OCO + BarPathModel 로 같은 봉 양쪽 도달 시
        # 우선순위 결정 (PR L 정책 동일).
        if spawned_in_iteration:
            children_ordered = self._order_oco_aware_active_list(
                [c for c in spawned_in_iteration if c.is_active]
            )
            for order in children_ordered:
                if not order.is_active:
                    continue  # OCO sibling cancel 로 비활성화 가능
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
                self._event_log.append(
                    Event(ts=ts, type=EventType.FILL, payload=fill)
                )
                self._emit_snapshot(ts, "fill")
                if order.oco_group_id is not None:
                    self._cancel_oco_siblings(order, ts)

        # 4. mark-to-market — post-fill 포지션을 봉 close 로 mark + equity_history 적재.
        self.ledger.on_market(snapshots)

        # 4.5 PR P — Liquidation check. on_market 직후, funding 전. position 의
        # liquidation_price 가 설정돼 있고 봉의 high/low 가 도달했으면 강제 close +
        # LIQUIDATION 이벤트 + bracket child cancel + SNAPSHOT(reason='liquidation').
        self._process_liquidations(ts, snapshots)

        # 5. PR E + PR G: Funding/Settlement — post-fill, post-mark 포지션으로 계산.
        # funding_processor 가 있으면 봉 마감 시각이 funding boundary 인지 검사.
        # CashFlow → Ledger.on_settle (cash 반영) + SETTLE 이벤트 +
        # SNAPSHOT(reason='settlement').
        if self.funding_processor is not None:
            for symbol in self.config.timeframes_per_symbol:
                snap = snapshots.get(symbol)
                if snap is None:
                    continue
                instrument = self.instrument_registry.get(symbol)
                position = self.ledger.positions.get(
                    symbol, Position(symbol=symbol)
                )
                cashflow = self.funding_processor.process(
                    symbol, ts, instrument, position, snap
                )
                if cashflow is None:
                    continue
                self.ledger.on_settle(cashflow)
                self._event_log.append(
                    Event(
                        ts=ts,
                        type=EventType.SETTLE,
                        payload={
                            "symbol": cashflow.symbol,
                            "amount": str(cashflow.amount),
                            "kind": cashflow.kind,
                            "rate": (
                                str(cashflow.rate)
                                if cashflow.rate is not None
                                else None
                            ),
                        },
                    )
                )
                self._emit_snapshot(ts, "settlement")

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
        portfolio_view = self._build_portfolio_view()
        orders_view = self._build_orders_view()
        ctx = StrategyContext(
            now=ts,
            primary_symbol=self.config.primary_symbol,
            primary_timeframe=self.config.primary_timeframe,
            bars=bars_view,
            indicators=indicators_view,
            portfolio=portfolio_view,
            orders=orders_view,
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

        # Pending order management (PR G: read-only OrdersView, post-intent 시점 빌드).
        # 새 intent 처리로 추가된 주문도 ``fresh_orders`` 에 포함된다 — 전략이 방금 발행
        # 한 limit/stop 을 같은 on_bar 안에서 cancel/modify 하는 경로도 가능.
        fresh_orders = self._build_orders_view()
        actions = self.strategy.on_pending_orders(ctx, fresh_orders.open_orders())
        for action in actions:
            self._handle_action(action, ts)

    # ---------- PR A: portfolio / orders view 빌더 -------------------------

    def _build_portfolio_view(self) -> PortfolioView:
        """``Ledger`` 의 mutable 상태를 frozen ``PortfolioView`` snapshot 으로 복제.

        flat position 은 제외 — 전략이 ``ctx.positions.items()`` 로 iterate 할 때
        실제로 가진 심볼만 보이게 하기 위함. ``ctx.has_position(symbol)`` 도 flat 인
        경우 False 를 반환.
        """
        from types import MappingProxyType

        snapshot_positions: dict[str, PositionView] = {}
        for sym, p in self.ledger.positions.items():
            if p.is_flat:
                continue
            snapshot_positions[sym] = PositionView(
                symbol=p.symbol,
                size=p.size,
                avg_price=p.avg_price,
                realized_pnl=p.realized_pnl,
                unrealized_pnl=p.unrealized_pnl,
                opened_at=p.opened_at,
            )
        return PortfolioView(
            equity=self.ledger.equity,
            cash=self.ledger.cash,
            realized_pnl=self.ledger.realized_pnl,
            unrealized_pnl=self.ledger.unrealized_pnl,
            positions=MappingProxyType(snapshot_positions),
        )

    def _build_orders_view(self) -> OrdersView:
        """``OrderBook.get_active()`` 결과를 frozen ``OrderView`` tuple 로 복제."""
        active = self.orderbook.get_active()
        order_views = tuple(
            OrderView(
                id=o.id,
                symbol=o.intent.symbol,
                side=o.intent.side,
                type=o.intent.type,
                state=o.state,
                sized_quantity=o.sized_quantity,
                remaining=o.remaining,
                submitted_at=o.submitted_at,
                limit_price=o.intent.limit_price,
                stop_price=o.intent.stop_price,
            )
            for o in active
        )
        return OrdersView(_orders=order_views)

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
            market_close=market.close,
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
        self._emit_order_added(order, sized_quantity, ts)

    def _emit_order_added(
        self,
        order: Order,
        sized_quantity: Decimal,
        ts: datetime,
    ) -> None:
        """ORDER_ADDED 이벤트 발행. PR K: parent_order_id / oco_group_id payload 포함."""
        assert self._event_log is not None
        self._event_log.append(
            Event(
                ts=ts,
                type=EventType.ORDER_ADDED,
                payload={
                    "order_id": order.id,
                    "intent": order.intent,
                    "sized_quantity": sized_quantity,
                    "parent_order_id": order.parent_order_id,
                    "oco_group_id": order.oco_group_id,
                },
            )
        )

    # ---------- PR P: Liquidation -----------------------------------------

    def _process_liquidations(
        self,
        ts: datetime,
        snapshots: dict[str, MarketSnapshot],
    ) -> None:
        """on_market 직후 호출 — 각 non-flat 포지션의 liquidation_price 도달 검사.

        검출:
        - long (size > 0) + low <= liq_price → 강제 close at liq_price
        - short (size < 0) + high >= liq_price → 강제 close at liq_price

        처리 (도달 시):
        1. 강제 close Fill 생성 (price=liq_price, side=close).
        2. ``Ledger.on_fill`` 으로 cash 반영 + realized PnL.
        3. ``MarginModel.liquidation_fee_rate`` 적용 — notional × rate 차감.
        4. LIQUIDATION 이벤트 발행 (symbol, liq_price, size, fee).
        5. 같은 symbol 의 active bracket child 모두 cancel (reason="liquidation").
        6. SNAPSHOT(reason='liquidation').
        """
        from backtester.core.types import Fill

        assert self._event_log is not None

        for symbol, position in list(self.ledger.positions.items()):
            if position.is_flat or position.liquidation_price is None:
                continue
            snap = snapshots.get(symbol)
            if snap is None:
                continue
            liq = position.liquidation_price
            triggered = False
            if position.size > 0 and snap.low <= liq:
                triggered = True
            elif position.size < 0 and snap.high >= liq:
                triggered = True
            if not triggered:
                continue

            instrument = self.instrument_registry.get(symbol)
            close_size = abs(position.size)
            close_side: Literal["buy", "sell"] = (
                "sell" if position.size > 0 else "buy"
            )
            # Liquidation fee
            fee_rate = (
                instrument.margin_model.liquidation_fee_rate
                if instrument.margin_model is not None
                else Decimal("0")
            )
            notional = close_size * liq
            fee = notional * fee_rate

            forced_fill = Fill(
                timestamp=snap.timestamp,
                symbol=symbol,
                price=liq,
                size=close_size,
                side=close_side,
                fee=fee,
                fee_currency=instrument.quote_currency,
                order_id="liquidation",
                intent_reason="liquidation",
            )
            self.ledger.on_fill(forced_fill, instrument)
            self._fill_count += 1
            self._event_log.append(
                Event(ts=ts, type=EventType.FILL, payload=forced_fill)
            )
            self._event_log.append(
                Event(
                    ts=ts,
                    type=EventType.LIQUIDATION,
                    payload={
                        "symbol": symbol,
                        "liquidation_price": str(liq),
                        "size": str(close_size),
                        "side": close_side,
                        "fee": str(fee),
                    },
                )
            )
            # Cancel active bracket children for this symbol
            for sibling in list(self.orderbook.get_active()):
                if sibling.intent.symbol != symbol:
                    continue
                cancelled = self.orderbook.cancel(sibling.id, ts)
                if cancelled:
                    self._event_log.append(
                        Event(
                            ts=ts,
                            type=EventType.ORDER_CANCELLED,
                            payload={
                                "order_id": sibling.id,
                                "reason": "liquidation",
                            },
                        )
                    )
            self._emit_snapshot(ts, "liquidation")

    # ---------- PR L: OCO + Same-Bar Path 우선순위 -------------------------

    def _order_oco_aware_active_list(self, active: list[Order]) -> list[Order]:
        """OCO group 내부 fill 시도 순서 결정 (BarPathModel 기반).

        - 같은 ``oco_group_id`` 안에서 PESSIMISTIC (default) → stop 먼저, limit 나중.
        - OPTIMISTIC → limit 먼저, stop 나중.
        - OPEN_TO_CLOSE / OHLC_ORDER → PESSIMISTIC 과 동일하게 보수적으로 stop 먼저
          (후속 PR 에서 정밀 모델링).
        - OCO group 외 주문은 원래 순서 유지. group 들은 고유 group_id sort 로 결정성 보장.

        반환: 평탄화된 새 리스트.
        """
        from collections import defaultdict

        from backtester.core.types import BarPathModel as _BPM

        bpm = self.config.bar_path_model
        groups: dict[str | None, list[Order]] = defaultdict(list)
        for o in active:
            groups[o.oco_group_id].append(o)

        def _sort_key(o: Order) -> int:
            # PESSIMISTIC / 기본: stop 우선 (불리한 가격 먼저)
            if bpm == _BPM.OPTIMISTIC:
                return 0 if o.intent.type == "limit" else 1
            return 0 if o.intent.type == "stop" else 1

        out: list[Order] = []
        # 결정성: ungrouped (None) 먼저, 그 다음 group_id 알파벳 정렬.
        ungrouped = groups.pop(None, [])
        out.extend(ungrouped)
        # group_id is non-None 으로 좁힘 (None 은 위에서 pop 됨)
        non_null_keys: list[str] = [k for k in groups if k is not None]
        for gid in sorted(non_null_keys):
            out.extend(sorted(groups[gid], key=_sort_key))
        return out

    def _cancel_oco_siblings(self, filled: Order, ts: datetime) -> None:
        """PR L: 한 OCO sibling 이 fill 되면 같은 group 의 다른 active 주문 cancel."""
        assert self._event_log is not None
        gid = filled.oco_group_id
        if gid is None:
            return
        # snapshot 후 iterate — cancel 이 dict mutate 일으킴
        for sibling in list(self.orderbook.get_active()):
            if sibling.id == filled.id:
                continue
            if sibling.oco_group_id != gid:
                continue
            cancelled = self.orderbook.cancel(sibling.id, ts)
            if cancelled:
                self._event_log.append(
                    Event(
                        ts=ts,
                        type=EventType.ORDER_CANCELLED,
                        payload={
                            "order_id": sibling.id,
                            "reason": "oco_sibling_filled",
                            "filled_sibling_id": filled.id,
                        },
                    )
                )

    def _spawn_bracket_children(
        self,
        parent_order: Order,
        parent_fill: Any,
        ts: datetime,
    ) -> list[Order]:
        """PR K: entry fill 직후 reduce-only TP/SL child 생성.

        - long entry (buy fill): TP = sell limit, SL = sell stop. 둘 다 reduce_only=True.
        - short entry (sell fill): TP = buy limit, SL = buy stop.
        - children 은 같은 ``oco_group_id = "oco_{parent.id}"`` 공유 — PR L 에서 한쪽
          체결 시 sibling 자동 cancel.
        - child size = ``parent_fill.size`` (전체 부분체결 수량).
        - reason = ``"bracket_tp:{parent.id}" / "bracket_sl:{parent.id}"``.
        """
        from backtester.core.orders import OrderIntent, TargetUnits

        bracket = parent_order.intent.bracket
        assert bracket is not None
        symbol = parent_order.intent.symbol
        # close 방향: parent buy → sell child / parent sell → buy child.
        close_side: Literal["buy", "sell"] = (
            "sell" if parent_fill.side == "buy" else "buy"
        )
        qty = abs(parent_fill.size)
        oco_group_id = f"oco_{parent_order.id}"
        spawned: list[Order] = []

        if bracket.take_profit_price is not None:
            tp_intent = OrderIntent(
                symbol=symbol,
                side=close_side,
                type="limit",
                size_spec=TargetUnits(units=qty),
                limit_price=bracket.take_profit_price,
                reason=f"bracket_tp:{parent_order.id}",
                reduce_only=True,
            )
            tp_order = self.orderbook.add(
                tp_intent,
                qty,
                ts,
                parent_order_id=parent_order.id,
                oco_group_id=oco_group_id,
            )
            self._emit_order_added(tp_order, qty, ts)
            spawned.append(tp_order)

        if bracket.stop_loss_price is not None:
            sl_intent = OrderIntent(
                symbol=symbol,
                side=close_side,
                type="stop",
                size_spec=TargetUnits(units=qty),
                stop_price=bracket.stop_loss_price,
                reason=f"bracket_sl:{parent_order.id}",
                reduce_only=True,
            )
            sl_order = self.orderbook.add(
                sl_intent,
                qty,
                ts,
                parent_order_id=parent_order.id,
                oco_group_id=oco_group_id,
            )
            self._emit_order_added(sl_order, qty, ts)
            spawned.append(sl_order)

        return spawned

    def _handle_action(self, action: OrderAction, ts: datetime) -> None:
        """PR D: cancel / modify / new 모두 활성. cancel/modify 는 EventLog 에 기록."""
        assert self._event_log is not None
        if action.type == "new":
            if action.intent is not None:
                self._handle_intent(action.intent, ts)
            return
        if action.type == "cancel":
            if action.order_id is None:
                self._event_log.append(
                    Event(
                        ts=ts,
                        type=EventType.ORDER_REJECTED,
                        payload={"reason": "cancel: order_id is None"},
                    )
                )
                return
            cancelled = self.orderbook.cancel(action.order_id, ts)
            if cancelled:
                self._event_log.append(
                    Event(
                        ts=ts,
                        type=EventType.ORDER_CANCELLED,
                        payload={
                            "order_id": action.order_id,
                            "reason": "user_cancel",
                        },
                    )
                )
            return
        if action.type == "modify":
            if action.order_id is None:
                self._event_log.append(
                    Event(
                        ts=ts,
                        type=EventType.ORDER_REJECTED,
                        payload={"reason": "modify: order_id is None"},
                    )
                )
                return
            try:
                ok = self.orderbook.modify(
                    action.order_id,
                    limit_price=action.modify_limit_price,
                    stop_price=action.modify_stop_price,
                )
            except ValueError as e:
                self._event_log.append(
                    Event(
                        ts=ts,
                        type=EventType.ORDER_REJECTED,
                        payload={
                            "order_id": action.order_id,
                            "reason": f"modify: {e}",
                        },
                    )
                )
                return
            if ok:
                self._event_log.append(
                    Event(
                        ts=ts,
                        type=EventType.ORDER_MODIFIED,
                        payload={
                            "order_id": action.order_id,
                            "limit_price": (
                                str(action.modify_limit_price)
                                if action.modify_limit_price is not None
                                else None
                            ),
                            "stop_price": (
                                str(action.modify_stop_price)
                                if action.modify_stop_price is not None
                                else None
                            ),
                        },
                    )
                )
            return
        raise NotImplementedError(  # pragma: no cover
            f"Unknown OrderAction.type: {action.type!r}"
        )
