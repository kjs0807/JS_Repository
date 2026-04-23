# 실험 운영 규약 (Rule-Based Experiment Protocol)

**날짜**: 2026-04-14
**범위**: `Trading/Bybit_Trading` 코드베이스 전체
**상태**: 활성 (ML 라운드 종료 후의 기준 문서)

## 0. 왜 이 문서가 필요한가

ML 패턴 라운드(RSI / Engulfing / BBKC filter) 전 KILL 이후 전략 탐색의
축을 **규칙 기반 변형**으로 옮겼다. 동시에 몇 가지 구조적 발견이
있었다:

- walk-forward만으로는 edge를 판정할 수 없다 (`src/ml/validator.py`의
  `evaluate_holdout` 추가 이유).
- event-level `filter_verdict`는 filter-type 패턴의 배포 가치를
  과대평가한다 (BBKC filter Day 2 사례).
- 규칙 기반 variant는 raw baseline 대비 **delta**로 판단해야 하며,
  전체 aggregate 한 줄로 promote/kill 결정하면 심볼 편중 실패 모드를
  놓친다 (`DonchianTrendFilterADX25`의 LINK/AVAX 편중).

이 문서는 이후 **모든** 실험이 따라야 하는 공통 규칙을 명시한다.

## 1. 필수 원칙

### P1. Holdout-first 판정
- 모든 신규 실험은 공유 holdout 창 (`2025-10-01 ~ 2026-04-10`) 위에서
  판정한다.
- Walk-forward, fine grid, 커버리지 지표는 **보조 정보**일 뿐
  promote/kill 사유가 될 수 없다.
- Holdout 창을 바꾸려면 별도 실험 문서에 명시 (왜 바꾸는지, 이전
  기록과 어떻게 비교하는지).

### P2. Relative comparison
- 가능한 경우 baseline과 variant를 **같은 스크립트 안에서** 실행.
- 판정은 `src/evaluation/verdict.py::judge_variant_vs_baseline`으로
  일원화. 숫자 기준은 `VerdictThresholds` dataclass에 있다.
- aggregate만 보지 않고 per-symbol breakdown과 심볼 편중도를 모두
  확인.

### P3. Control strategy co-execution
- 어떤 실험이든 baseline control을 반드시 같이 실행. 기록된 과거
  숫자로 비교하지 않는다 — 엔진/브로커 변경이 있을 수 있다.

### P4. One variable per variant
- 한 variant는 한 축만 바꾼다.
- "D1에 ADX 20/25 두 개"처럼 같은 실험의 두 점을 같이 보는 건 허용
  (한 실험 단위).
- Exit 철학과 entry 철학을 동시에 바꾸는 건 새 전략으로 취급.

### P5. BBKC entry 불변
- `BBKCSqueeze`는 entry logic을 건드리지 않는다 (round 1 결론).
- 허용되는 실험: universe 구성, exit layer, 포지션 사이징 규칙.
- 금지되는 실험: squeeze trigger 변경, RSI 필터 변경, bb_mid 게이트
  변경, HTF entry gate 재도전, ML filter 재도전.

### P6. Donchian FixedRR family — WINDOW-DEPENDENT, 사용 제한
**2026-04-14 OOS2 검증 결과 이 섹션이 뒤집혔다. 자세한 근거:
`docs/.../2026-04-14_oos2_results.md` §1.**

- `DonchianFixedRR`: 기존대로 **DEPRECATED**. OOS1/OOS2 둘 다 net loss.
- `DonchianFixedRRTrendFilter` (class 자체): **PROMOTED → WINDOW-DEPENDENT**
  로 격하.
  - OOS1 (2025-10 ~ 2026-04): PROMOTE — Δpnl +$2510, Δmdd -19.7%p
  - OOS2 (2024-10 ~ 2025-04): **KILL** — Δpnl -$418, Δmdd +17.5%p
  - 회귀 원인: OOS1의 SOL/LINK 차단 효과가 OOS2 window에서는 없고,
    대신 trend filter가 AVAX/BTC 진입을 허용해 drawdown 폭증.
- D2 best grid cell (`ep=25 sa=3.0 …`): **ARCHIVED** —
  OOS2에서도 KILL (Δmdd +7.5%p). 운영 default 승격 금지.
- 결론: **FixedRR family 전체**를 operational baseline에서 제외.
  Research candidate 상태로만 유지. 신규 실험은 FixedRR family를
  reference baseline으로 사용 금지.
- `DonchianTrendFilter`는 여전히 보조 축 (BTC 단독 prior, 강하지 않음).
- D2 grid 재확장 **금지** — class가 window 의존적임이 확인됨.

### P7. ML 재개 금지 (조건부 해제)
- Standalone ML pattern은 재개 금지.
- Filter-type ML pattern은 **D2 bar-level comparator 완성 후**, 그리고
  `src/evaluation/bar_level_comparison.py`로 판정 가능한 상태에서만
  재개 가능.
- 현재 D2 comparator는 이 문서와 같은 날(`2026-04-14`) 구현됨.

### P9. Parallel research tracks — trade-level과 분리
- Trade-level live 전략 인프라와 **완전히 분리된** research 트랙을
  허용한다. 최초 예시: RSI divergence daily regime research.
- 분리 경계:
  - Code: `src/research/<topic>/` 하위. `src/strategies/`,
    `src/ml/patterns/`, `src/evaluation/`을 건드리지 말 것 (읽기만
    허용).
  - Artifacts: `logs/research/<topic>/` 또는 `logs/regime/<topic>/`.
    Trade-level `logs/d2_*`, `logs/bbkc_*`와 섞지 말 것.
  - Scripts: `scripts/train_*_regime.py`, `scripts/evaluate_*_regime.py`.
    Trade-level 스크립트(`d2_core_eval.py` 등)와 이름을 공유하지 말 것.
- 병합 조건: research 트랙 결과가 **independent holdout**에서 의미
  있는 분리력을 보이고, **운영 인프라에 연결하지 않은 채로** 30일
  이상 안정적으로 재현 가능할 때에만 합류 논의 가능.
- 연결 금지: research 트랙에서 나온 어떤 signal/score도 이번 턴
  이후에도 전략 entry/exit/sizing에 **자동으로 연결 금지**. 연결은
  별도 protocol 변경이 선행되어야 한다.

### P8. BBKC BIGTHREE universe — STAGED PROMOTE
**2026-04-14 OOS2 검증 통과. 자세한 근거:
`docs/.../2026-04-14_oos2_results.md` §2.**

- `BBKCSqueeze[BIGTHREE]` (BTC+ETH+AVAX): **STAGED PROMOTE**
  (paper-ready candidate).
  - OOS1: PROMOTE — Δpnl +$1879, Δmdd -4.9%p
  - OOS2: PROMOTE — Δpnl +$1545, Δmdd -5.8%p
  - 2-window 재현성 확인. SOL/LINK structural loser 패턴 일관.
- 정식 승격 조건:
  1. 2차 OOS PROMOTE 유지 ✓ (2026-04-14)
  2. Live paper trading 2주 — **미충족**, paper 인프라 별도
- Staged promote 의미:
  - development/staging 환경에서는 즉시 BIGTHREE로 교체 가능
  - production 배포 및 real capital 투입은 paper 검증 후에만
- Gate 2 (BBKC exit-layer) 조건부 개방 — staged promote 상태에서
  development only 태그로 exit-layer 실험 가능
- 승격 후에도 entry logic 변경, HTF gate, ML filter 재도전 등 금지 (P5).
- ALL5는 OOS2에서 net loss (-$515) → ALL5 자체는 regime-dependent.
  BIGTHREE가 ALL5보다 structurally 더 안정적.

## 2. 스크립트 현황 (유지 / 수정 / 아카이브)

### 활성 (holdout-first)

| 스크립트 | 역할 |
|---|---|
| `scripts/compare_variants_round1.py` | Round 1의 원본 비교. 앞으로도 재실행 가능한 reference 실행기 |
| `scripts/d2_core_eval.py` | D2 baseline 재평가 (DonchianFixedRR vs DonchianFixedRRTrendFilter) |
| `scripts/d2_grid.py` | D2 소규모 파라미터 sweep (486 cells 상한) |
| `scripts/bbkc_universe_eval.py` | BBKC universe 5개 subset 비교 |
| `scripts/holdout_verdict.py` | 모든 `results.json` 파일을 읽어 PROMOTE/KILL 판정 |
| `scripts/compare_ml_vs_baseline.py` | D2 bar-level filter 판정 (filter-type ML 재개 시 사용) |
| `scripts/run_rule_based_experiments.py` | 위 스크립트들을 순서대로 실행하는 오케스트레이터 |

### Explore 계열 (보존, 용도 변경)

| 스크립트 | 용도 변경 |
|---|---|
| `scripts/explore_strategy.py` | **Grid search 전용**. holdout 판정에 사용하지 말 것 |
| `scripts/explore_donchian.py` | 동일. 과거 coarse/fine 파일 읽을 때만 사용 |
| `scripts/launch_donchian.sh` | 동일. 신규 파이프라인에는 사용 금지 |

### 아카이브 (holdout-first 철학과 충돌 — 사용 금지)

| 스크립트 | 왜 아카이브 |
|---|---|
| `scripts/strategy_verdict.py` | `fine_best.json` + `walkforward.json` + `overfit.json` 기반. Holdout 판정 아님. 남겨두지만 신규 실험에서 사용 금지. |
| `scripts/donchian_verdict.py` | 위와 동일. Donchian breakout round 1(2026-04-11) 산출물에만 적용. |

두 아카이브 스크립트 파일은 **삭제하지 않는다** — 과거 리포트 재생성을
위해 필요할 수 있다. 신규 실험은 반드시 `scripts/holdout_verdict.py`를
사용한다. 이 문서는 그 전이(transition) 의도를 남긴다.

### ML 관련 (현재 사용 계획 없음, 파일은 보존)

| 스크립트 | 상태 |
|---|---|
| `scripts/train_ml_pattern.py` | 향후 filter-type 실험 재개 시 재사용. 현재 계획 없음. |
| `scripts/backtest_ml_artifact.py` | 동일. |
| `scripts/refine_pattern.py` | 동일. |
| `src/ml/validator.py::evaluate_holdout` | 유지 (D1 event-level filter_verdict). |

## 3. 판정 규칙 요약 (`judge_variant_vs_baseline`)

Variant의 aggregate가 baseline의 aggregate에 비해 아래 순서로 검사됨.
첫 매칭 rule이 결과를 결정한다.

1. **INSUFFICIENT_DATA** — `n_trades < 30` 또는 active symbol `< 3`
2. **KILL** (prior flip) — 한 심볼이 variant 전체 trade의 65% 초과
3. **KILL** (dual regression) — avg_trade_pnl 악화 **AND** max_drawdown 악화
4. **PROMOTE** — avg_trade_pnl 개선 **AND** max_drawdown 악화 없음
5. **CONDITIONAL_PROMOTE** — 한 축 개선 / 한 축 악화 but total pnl 개선
6. **CONDITIONAL_PROMOTE** — drawdown 개선 + avg 악화 없음
7. **NO_EDGE** — 위 조건 어디에도 해당 없음

`VerdictThresholds`가 eps (수수료 노이즈)와 edge 기준을 한 곳에 모아
관리한다. 이 값들을 바꾸려면 문서도 같이 업데이트.

## 4. 필수 데이터/환경

- DB: `db/bybit_data.db` (Bybit 1h/4h OHLCV)
- Config: `config.yaml` + `src/core/config.py` → `cfg.app.db_path`
- Python: 3.12, 의존성 기존 `requirements.txt`
- 엔진: `src/backtester/engine.py::BacktestEngine`
- 브로커: `src/execution/backtest_broker.py::BacktestBroker`

## 5. 결과물 저장 규칙

실험 결과는 **절대** 루트에 남기지 않는다. 모든 산출물은 다음 경로로:

    logs/<experiment_name>/
      ├── results.json           -- per-symbol + aggregate
      ├── verdict.json           -- judge_variant_vs_baseline 결과 (선택)
      └── <extra artifacts>

로그 dir 네이밍 규칙:

- `logs/variant_round1/` — 최초 4-variant 비교 (기존, 유지)
- `logs/d2_core/` — D2 재평가
- `logs/d2_grid/` — D2 파라미터 sweep
- `logs/bbkc_universe/` — BBKC universe subset 실험
- `logs/rule_based_runner/` — orchestrator가 찍는 stage 로그
- `logs/bar_level_comparison/` — filter-type 재개 시 사용

`logs/` 디렉토리는 `.gitignore`에 포함되어 있으므로(확인 필요)
결과를 공유하려면 `docs/` 밑에 요약만 남긴다.

## 6. 변경 추적

이 문서 자체가 실험 기준이므로, 기준을 바꾸려면:

1. 이 문서 상단에 변경 이력 섹션을 열고
2. 언제, 왜, 어느 rule을 바꾸는지 남기고
3. `src/evaluation/verdict.py::VerdictThresholds` 변경은 test와
   함께 커밋.

## 7. 다음 단계 게이트 (Gate 1/2/3)

이 세 게이트는 **명시적 조건 만족 전까지는 실행 금지**. 게이트가
열리는 순간에만 해당 실험을 허용한다.

### Gate 1 — D1 extension (DonchianTrendFilter 축 확장)

**진입 조건 (AND)** — 2026-04-14 OOS2 결과 반영:
- D2 grid 완료 ✓
- **D2 OOS2 실패 확인 ✓** → FixedRR family는 operational baseline
  아님 → D1 축을 한 번 점검해 볼 가치가 생김 (유일한 Donchian
  후보)
- Gate 1 진입을 진행한다면 DonchianTrendFilter를 **control**,
  DonchianFixedRRTrendFilter는 **reference only** (baseline 아님)

**허용 실험 축 (한 번에 하나만)**:
- Breakout strength gate: `(close - upper_band) / atr >= k`
- EMA slope gate: `ema(n)[i] - ema(n)[i-slope_period] > slope_eps * atr`
- Entry/exit period 소폭 조정 (±5 범위)

**금지**:
- ADX variants (ADX20/ADX25/ADX30 등 재실행 절대 금지 — 둘 다 KILL
  확정)
- RSI / MACD / Stoch 계열 추가
- 여러 축 동시 변경

**스크립트 템플릿**: `scripts/d2_core_eval.py`를 복사하여
`scripts/d1_extension_<axis>_eval.py`로 만들되 한 axis만 변경.

### Gate 2 — BBKC exit-layer

**진입 조건 (AND)** — 2026-04-14 OOS2 결과 반영:
- BBKC BIGTHREE가 **STAGED PROMOTE** ✓
- Entry parity 유지 검증 가능 — `BBKCSqueeze.on_bar_fast`의 entry
  조건 수정 금지
- baseline은 BBKCSqueeze + BIGTHREE universe로 고정
- Development only 태그: production 배포는 paper 검증 후에만

**허용 실험**:
- ATR-adaptive TP/SL: 고정 pct 대신 `k_tp * atr`, `k_sl * atr`
- Break-even 이후 trailing: 1R 도달 → SL을 entry로 이동
- 단순 1-bit exit enhancement (단일 rule 추가)

**금지**:
- HTF entry gate 재도전 (BBKCSqueezeHTFTrend KILL 재확인)
- ML filter 재도전 (BBKC ML filter KILL 재확인)
- Hybrid entry 철학 변경 (Donchian과의 진입 철학 결합 금지)

**스크립트 템플릿**: `scripts/bbkc_universe_eval.py`를 복사하여
`scripts/bbkc_exit_<variant>_eval.py`. 반드시 entry parity 비교를 위해
baseline과 variant를 같은 스크립트에서 실행.

### Gate 3 — ML filter comparator 사용

**진입 조건 (AND)**:
- 새로운 filter-type pattern이 D1 event-level holdout에서 PASS
- 해당 pattern에 대한 baseline strategy가
  `FILTER_WRAPPER_REGISTRY` (`scripts/compare_ml_vs_baseline.py`)에
  등록 가능

**사용 범위**:
- Filter-type ML pattern 전용 (standalone pattern에는 적용 금지)
- RSI divergence / EngulfingMTF 같은 standalone은 D1 event-level
  판정으로 충분 — D2 comparator 사용 의미 없음

**금지**:
- KILL된 기존 pattern (RSI / Engulfing / BBKC filter) 재학습 및 재판정
  — 새 pattern일 때만 Gate 3 개방
- Standalone pattern을 억지로 filter-type으로 포장해서 comparator
  사용하려는 시도 (registry에 등록 금지)

**스크립트**: `scripts/compare_ml_vs_baseline.py --pattern <new_filter>
--artifact-dir <path>`. `FILTER_WRAPPER_REGISTRY`에 새 entry 추가
필요.

## 8. 변경 이력

- **2026-04-14 (초판)**: ML 라운드 종료 + round 1 결과 반영.
- **2026-04-14 (round 2)**: D2 grid 486 cells 완료,
  `DonchianFixedRRTrendFilter`를 FixedRR 계열 공식 baseline으로 승격.
  `DonchianFixedRR`을 deprecated baseline으로 격하. BBKC BIGTHREE를
  candidate universe로 명시. Gate 1/2/3 추가.
- **2026-04-14 (OOS2 검증)**: D2 class를 PROMOTED에서
  **WINDOW-DEPENDENT**로 격하 (OOS2 KILL). D2 best grid cell
  ARCHIVED. BBKC BIGTHREE를 **STAGED PROMOTE**로 승격 (OOS2 PROMOTE,
  paper 검증 대기). Gate 1/2 진입 조건 업데이트. FixedRR family는
  operational baseline에서 제외.
- **2026-04-14 (RSI regime research track 분리)**: RSI divergence를
  trade-level signal에서 daily regime signal 연구 주제로 재정의.
  `src/research/regime/` 하에 trade-level과 완전 분리된 연구 트랙
  추가. 전략 연결 금지 (P9 참조).
