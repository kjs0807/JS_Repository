# Implementation Advice — Paper Broker + RSI Regime

**날짜**: 2026-04-14
**대상 독자**: 다음 구현자 (사용자 본인 또는 후임 AI)
**목적**: 이번 turn에서 만든 paper trading 인프라와 RSI regime
research track을 **현재 `src/` 아키텍처에 맞게 유지/확장**하기 위한
구체 제언. 코드 단 근거 포함.

## 1. Paper trading layer — 재사용/분리 원칙

### 1.1 레이어 역할 (확정)

```
src/execution/broker.py          ─ Protocol + dataclass (Position, Order, Fill, Portfolio)
src/execution/position_tracker.py ─ in-memory position store
src/execution/order_manager.py    ─ pending order store + fill record
src/execution/risk.py             ─ RiskManager (daily_pnl/drawdown guard)
src/execution/backtest_broker.py  ─ 순수 시뮬레이션, lookahead-safe 체결
src/execution/paper_broker.py     ─ backtest 상속 + 영속성 + universe guard + jsonl log
src/execution/live_broker.py      ─ Bybit REST 호출, 실거래
```

**중요**: `PaperBroker`는 `BacktestBroker`를 **subclass**한다. Compose
가 아니다. 이유:
- `BacktestBroker.process_bar`가 intra-bar TP/SL + order fill + equity
  curve를 이미 구현하고 검증 대상 code path. 다시 감싸면 200줄
  중복 + 버그 risk 2배.
- 모든 backtest_broker 테스트가 paper에도 암묵적으로 적용됨 (인터
  페이스 동일).
- 향후 `backtest_broker.py`에 버그 픽스가 들어가면 paper도 무료로
  혜택.

### 1.2 PaperRunner와 BacktestEngine 차이

- `BacktestEngine.run(strategy, feed, config, symbol)` — **단일 심볼**
  per 호출. 포트폴리오 accounting 없음.
- `PaperRunner.run()` — **다심볼 portfolio**. 모든 심볼의 bar를
  timestamp 순으로 merge 하고 하나의 PaperBroker로 흘린다.
- 이유: BIGTHREE universe 같은 3 심볼 동시 실행이 필요한데
  BacktestEngine을 3번 돌리면 broker가 분리되어 portfolio MDD 계산
  불가.

### 1.3 Live paper subclass 포인트 (확장 지점)

- `PaperRunner._prepare_symbols`를 override: 히스토리컬 plan 대신
  빈 plan 반환.
- `PaperRunner.run()` 내부 for 루프를 event-driven 형태로 덮어쓰기:
  websocket callback → `self._broker.process_bar(bar)` →
  `strategy.on_bar_fast(bar, i, cache, broker)`.
- 이때 `i`는 symbol-local 카운터가 필요. cache는 그 symbol의
  `full_series`로 사전 계산. Live 환경에서는 full_series를 어떻게
  refresh할지 결정 필요 (그대로 최초 계산 유지 + 새 bar만 cache
  바깥에서 처리).

### 1.4 `_legacy/paper_engine`에서 가져올 것 / 버릴 것

**참고한 개념**:
- **State persistence (`save_state` / `load_state`)**: legacy는
  `logs/engine_state.json`에 통합 상태 저장. 현재 구현도 같은 개념
  (`paper_state.json`), 단 run_dir per run으로 분리.
- **정상 종료 직전 state 저장**: legacy는 save_state를 매 거래
  이벤트마다 호출. 현재는 checkpoint_every_bars + 종료 시 1회. 비용
  낮춤.
- **Strategy 별 on/off**: legacy는 `set_strategy_enabled` API 가짐.
  현재는 단일 strategy만 취급. BIGTHREE 용도에서는 over-engineering.

**버린 개념**:
- **다중 strategy 오케스트레이션 (legacy TradingEngine)**: legacy의
  TradingEngine은 4개 strategy (Pairs, BBKC, Ichimoku, RSIMACD)를
  공유 bar 스트림에 같이 물려서 돌림. 3개가 KILL 된 지금은 불필요.
  단일 strategy loop가 더 명확.
- **Pair trading 관련 코드 (pair_selector.py)**: Pairs trading은
  이미 KILL. 끌어오지 않음.
- **DB 기반 engine_state.json 통합 경로**: 현재 구조는 per-run
  디렉토리 분리가 더 적절. 한 파일에 통합하면 multi-run 관리 어려움.
- **Buffer prefill (`_prefill_buffers`)**: legacy는 시작 시 DB에서
  과거 bar를 읽어 버퍼에 넣음. 현재는 `HistoricalDataFeed` +
  `strategy.prepare`가 그 역할을 더 깔끔하게 대체.
- **`_reconcile_with_api` / `sync_positions_with_api`**: live
  broker 전용. paper에서 필요 없음.

### 1.5 지금 건드리면 안 되는 파일

- `src/strategies/bbkc_squeeze.py` — entry 불변 (P5)
- `src/execution/backtest_broker.py` — 시뮬 정확성 변경 시 모든
  variant 결과 재검증 필요. 수정 전에 변경 영향 범위 조사 필수.
- `src/execution/live_broker.py` — live 주문 경로. paper 변경이 이
  파일에 영향 주면 안 됨.
- `src/evaluation/verdict.py::VerdictThresholds` — round 1/2 재현성
  근거. 수치 변경 시 `tests/test_evaluation/test_verdict.py`의
  Round1 replay 테스트 재검증.

### 1.6 리팩터링 포인트 (향후 고려)

- **`PaperBroker.process_bar`의 fills_seen 커서**: 현재는 `len(self._trades)` 차이로 detect. 간단하지만 trade가 과거에 append되면 놓칠 수 있음. BacktestBroker가 trades를 순서대로만 append하는 invariant에 의존.
- **Pending order persistence**: 현재 save_state는 pending 버림.
  강한 intra-bar 보존이 필요하면 OrderManager를 dict로 serialize
  추가 필요.
- **Broker Protocol 확장**: `log_signal`을 Protocol에 추가할지
  고민. 현재는 PaperBroker 전용 메소드. Strategy 코드가 type-safe
  하게 호출하려면 Protocol에 올려야 하나, BacktestBroker와 LiveBroker
  에도 빈 구현을 넣어야 함. 과도할 수 있음.
- **CLI multi-run management**: 현재 run_bbkc_paper는 run_id 하나.
  `scripts/manage_paper_runs.py` 같이 run 목록/summary/정리 CLI를
  추가하면 편의성 ↑.

## 2. RSI regime — 현재 경계와 연결 조건

### 2.1 src/research/regime 의존성

**허용 import**:
- `src/ml/helpers/divergence.py` (순수 알고리즘)
- `src/core/config.py` (config load)
- `src/data_manager/db.py` (DB read)
- 표준 pandas / numpy

**금지 import**:
- `src/strategies/*`
- `src/execution/*`
- `src/backtester/*`
- `src/evaluation/*`

**역방향 import 금지** (더 중요):
- `src/strategies/*`에서 `src/research/*` import 금지
- `src/execution/*`에서 `src/research/*` import 금지
- `main_live.py`, `main_paper.py`, `run_bbkc_paper.py`에서 연결 금지

이 경계는 파일 상단 docstring에 명시되어 있다
(`src/research/__init__.py`).

### 2.2 Regime artifact → strategy 연결 방식 (미래)

허용되는 연결 패턴 (§P9 조건 충족 시):

#### 패턴 A: Universe preference (가장 안전)
- 매일 BIGTHREE 중 어느 심볼이 long-허용 / short-허용 / block인지
  regime artifact에서 결정
- strategy 코드는 unchanged, BIGTHREE 구성만 하루 단위로 조정
- Implementation point: `PaperRunner._prepare_symbols`에서 spec.symbols를
  regime artifact와 교집합

#### 패턴 B: Direction gate (중간 위험)
- BBKCSqueeze.on_bar_fast의 long/short 진입 시 regime state 조회 →
  UP이면 long만, DOWN이면 short만 허용
- 1h bar에 daily regime 주입하는 adapter 필요
- Entry 로직 수정을 **허용하지 않음** (P5) → 별도 wrapper 필요
- 예: `BBKCSqueezeWithRegimeGate` subclass 추가. 테스트 추가. OOS
  2-window 검증 필요.

#### 패턴 C: Risk scaling (장기 고려)
- regime score → risk_pct 곱하는 비선형 sizing
- 현재 risk_pct는 strategy 내부에서 하드코딩 (2%). broker level에서
  global scale을 받도록 Protocol 수정 필요.
- 가장 invasive. 마지막으로 고려.

#### 금지 패턴 (영구)
- **Direct entry trigger**: divergence detect 시점에 entry
  → 이미 trade-level ML에서 KILL 확인
- **Low-timeframe trigger**: 1h divergence를 1h trade로
  → 같은 실패 모드
- **Unvalidated score threshold**: 학습되지 않은 score 기준으로
  hard-coded threshold. BTC-only research 결과 1개만으로 전략
  연결 금지

### 2.3 Daily regime artifact → 1h strategy alignment

daily artifact를 1h strategy에 consume 시킬 때:

- 1h bar의 timestamp `t`를 UTC 자정 기준 day bucket으로 변환
- 해당 day bucket의 regime state를 조회 (`valid_from_ms ≤ t`)
- confirmation lag 때문에 "오늘 발생한 divergence"는 오늘의 1h
  bar에 즉시 적용 금지
- 안전한 rule: `effective_day = floor((t - valid_from_ms) / 86_400_000)`,
  `effective_day >= 0`인 경우에만 state 사용

### 2.4 RSI regime code의 리팩터링 포인트

- `divergence_events.py::_rsi` / `_atr_pct` / `_trend_100d_pct` —
  현재 pure numpy loop. pandas rolling으로 교체하면 5-10x 빠름.
  현재 bars ≤ 2000 수준이라 불필요.
- `regime_labels.py::compute_unconditional_stats` — 전체 시리즈
  기준. Rolling window 기반으로 교체하면 "trailing base rate"가
  가능해져 regime drift 체크에 도움.
- `gating_eval.py` — 현재는 단순 bucket 평균. Future returns의
  sharp confidence interval (bootstrap) 추가하면 더 honest.

## 3. Config / registry / artifact path naming 제안

### 3.1 Paper trading
- run_dir: `logs/paper/<strategy_label>/<run_id>/`
- state: `<run_dir>/paper_state.json`
- fills: `<run_dir>/fills.jsonl`
- equity: `<run_dir>/equity_curve.csv`
- signals: `<run_dir>/signals.jsonl`

### 3.2 Research (RSI regime)
- 단일 심볼: `logs/research/rsi_regime/`
- 다심볼: `logs/research/rsi_regime_multi/<SYMBOL>/`
- Cross-asset summary: `logs/research/rsi_regime_multi/cross_asset_summary.json`
- Gating sim: `<events-dir>/gating_simulation.json`
- Stability (future): `logs/research/rsi_regime_stability/<YYYY-MM-DD>/`

### 3.3 Strategy registry 확장 금지 (현재)
- `src/strategies/registry_builder.py::STRATEGY_CONFIGS`에 regime-gated
  variant 추가 금지. 연결 조건 충족 후에만 + 별도 실험 문서 연결.

### 3.4 Config 추가 금지 (현재)
- `src/core/config.py::AppSettings` / `AppConfig`에 regime 관련 설정
  추가 금지. `regime_research_enabled` 같은 플래그를 operational
  config에 넣으면 P9 분리 원칙이 새는 시작점이 된다.

## 4. 빠른 체크리스트 (다음 구현자가 읽을 것)

```
[ ] Paper trading 구조 이해
    - PaperBroker = BacktestBroker + (persistence, universe guard, jsonl)
    - PaperRunner = multi-symbol bar merge + checkpoint + signal handler
    - scripts/run_bbkc_paper.py = CLI

[ ] Live paper로 확장할 때:
    - LivePaperRunner subclass로 분리
    - PaperBroker 자체는 변경 금지
    - WebSocket bar-close 이벤트만 주입
    - RiskConfig는 CLI 플래그로 외부에서

[ ] RSI regime 트랙:
    - src/research/regime/* (strategies/execution 금지)
    - RegimeOutput contract 준수 (timestamps, horizon metadata)
    - gating_eval는 research sketch, backtest 아님
    - 전략 연결은 §P9 5개 조건 모두 충족 후

[ ] 절대 금지:
    - BBKCSqueeze.on_bar_fast 수정
    - backtest_broker.py를 paper용으로 special-case
    - live_broker.py에서 paper 플래그 추가
    - strategy.py에서 research/regime import
    - main_live.py에 paper 코드 삽입

[ ] 리팩터링 포스트잇:
    - PaperBroker Protocol에 log_signal 추가 여부 결정
    - pending order persistence 필요 시 OrderManager serialize
    - gating_eval에 bootstrap CI 추가
    - stability monitor 구현 (daily re-run + diff)
```

## 5. 가장 중요한 한 줄 원칙

**"메인 전략 path와 research path는 import 방향을 절대 바꾸지 말 것.
research가 strategies를 읽는 것은 허용되지만, strategies가 research를
읽는 것은 금지. 이 한 줄이 P9 전체의 실질적 enforcement이다."**
