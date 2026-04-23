# D2: Bar-level Strategy Baseline Verdict — Design Memo

**날짜**: 2026-04-14
**브랜치**: `feature/ml-pattern-strategy`
**선행 context**:
- `834334e feat(ml): baseline-relative filter_verdict axis in evaluate_holdout` (D1)
- `54fa082 docs(ml): BBKC filter Day 2 -- HOLDOUT_PASS but deployment FAIL vs raw`
**이번 산출물**: 이 문서 자체 — 구현은 다음 filter 실험 시작 전에 수행.

## 요약 (TL;DR)

- D2는 **filter-type ML 패턴의 deployment 판정 자동화**를 위한 bar-level 비교 축.
- D1(event-level)은 필요조건이지만 BBKC 사례에서 드러났듯 충분조건이 아님.
- **추천 설계: 옵션 C — post-processing 방식**. 학습 파이프라인 건드리지 않고,
  `scripts/compare_ml_vs_baseline.py`가 artifact 로드 후 BacktestEngine을 두 번
  돌려서 `baseline_comparison.json`을 artifact 디렉토리에 저장.
- **지원 범위**: filter-type 패턴만 (BBKC filter → BBKCSqueeze). 일반 패턴
  (RSI / Engulfing) 은 D1으로 충분, D2는 "not applicable"로 exit.
- **지금은 설계만 고정**, 구현은 다음 filter 실험 시작 직전에 3~4시간 예상.

## 1. D1 한계 요약

### 코드 근거
- `src/ml/validator.py:290-505` — `evaluate_holdout`, `_derive_filter_verdict`, `HoldoutReport`
- `src/ml/report.py` — `_final_verdict`의 filter_verdict 분기
- `src/strategies/bbkc_squeeze.py:85-89` — `if pos is not None: return` (position lock)
- `src/strategies/bbkc_squeeze.py:69-114` — bar loop 로직
- `src/execution/backtest_broker.py:117-...` — intra-bar TP/SL 체크

### D1이 측정하는 것
- holdout 기간의 모든 pattern event (threshold=0 baseline)
- ML threshold를 통과한 이벤트 subset
- 두 subset의 per-trade 차이(win_rate, R/trade) 기반
  `FILTER_VALUE_ADD` / `DESTROYS` / `NEUTRAL` / `NOT_APPLICABLE`

### D1이 측정 못하는 것

| 효과 | D1에서 잡히는가 | 이유 |
|---|---|---|
| Position lock (`if pos is not None: return`) | ❌ | 모든 이벤트 독립 취급, 연속 squeeze release 전부 "체결" 가정 |
| Intra-bar TP/SL hit 순서 | ❌ | label(TP-first/SL-first)만 봄, 실제 bar high/low 타임라인 없음 |
| Broker sizing (current equity 기반) | ❌ | D1 PnL은 R-multiple, 동적 포지션 크기 반영 안 됨 |
| 체결가 slippage / next-bar-open fill | ❌ | D1은 entry = label 시점 close 가정 |
| max_holding_bars timeout 분포 | ⚠️ 부분 | label builder가 timeout을 negative class로 반영하지만 bar-level 타이밍과 다름 |

### BBKC 사례 구조적 설명

`docs/superpowers/specs/ml/2026-04-14_baseline_relative_holdout_verdict.md` 기록:

```
D1 event-level baseline:
  354 events, 54.8% win_rate
  ML 32 trades, 56.25% win_rate
  delta_wr +1.4%p, delta_R/tr +0.00014
  -> FILTER_VALUE_ADD

Day 2 bar-level comparison (BacktestEngine + position lock):
  Raw BBKCSqueeze: 176 trades, 61.9% win_rate, +$4338 PnL
  ML Filter:        36 trades, 55.6% win_rate, +$316 PnL
  delta_wr -6.3%p, delta PnL -93%
  -> FILTER_DESTROYS (Day 2 수동 판정)
```

둘 다 수학적으로 옳고, 다른 질문에 답함. 핵심 원인: **raw BBKCSqueeze의 position
lock 자체가 강력한 필터**. 354 → 176 자동 거부하면서 승률 +7.1%p 끌어올림. ML
통계 필터는 354 → 32로 더 공격적으로 줄이지만 승률 +1.4%p에 그침.

### 핵심 교훈

D1은 **"ML threshold가 의미 있는 분류를 학습했는가"** 신뢰도 체크.
filter deployment 관점에서 **충분조건이 아니라 필요조건**. 유용하지만
deployment 판정에 단독 사용 불가능.

## 2. D2가 답해야 하는 질문

### 핵심 질문

> **"이 ML filter가 raw baseline strategy의 실제 BacktestEngine 실행 경로
> (position lock + intra-bar TP/SL + broker fill) 대비 deployment 가치를
> 추가하는가?"**

### D1 vs D2 역할 분리

| 축 | D1 (event-level) | D2 (bar-level) |
|---|---|---|
| 질문 | "ML threshold가 no-filter보다 나은가?" | "ML filter가 실제 배포 raw 전략보다 나은가?" |
| Baseline | threshold=0 모든 이벤트 (label 집계) | raw strategy → BacktestEngine 실제 체결 |
| 단위 | R-multiple (label 기반) | dollar PnL (broker 기반) |
| 속도 | 매우 빠름 | 느림 (BacktestEngine 순회) |
| 적용 | 모든 pattern | **filter-type pattern만** |
| 언제 | 학습 리포트 자동 포함 | filter 패턴 평가 시 선택적 후처리 |

### D1은 언제 유용한가
- 모든 패턴 학습 리포트 공통 축 (이미 있음)
- 실험 초기 "모델이 뭔가 학습했는가" 신속 판정
- RSI/Engulfing처럼 raw baseline이 없는 standalone 패턴

### D2는 언제 필수인가
- filter-type 패턴 (기반 전략이 있고 ML이 진입 승인/거부 역할)
- deployment 판정
- 새로운 BBKC filter 변형 또는 다른 filter 패턴 실험

### 사용 시나리오

```
RSI Divergence (standalone):   D1 only  (D2 N/A)
Engulfing MTF (standalone):    D1 only  (D2 N/A)
BBKC Filter (filter-type):     D1 + D2  (D2 is decisive)
[future] Donchian Filter:      D1 + D2
```

## 3. 설계 옵션 비교

### 옵션 A: validator 내부에서 raw strategy backtest 직접 호출
- `evaluate_holdout`에 optional `baseline_strategy_factory` 추가
- 함수 내부에서 BacktestEngine + feed + broker 구성 후 bar-level metrics 저장
- **장점**: 단일 함수에서 모든 축 동시 계산, run_pipeline 자동 적용
- **단점**:
  - `validator.py`가 BacktestEngine, DataFeed, Broker, DB 전부 import → 레이어링 망가짐
  - validator 책임 과대 (원래 label 기반 이벤트 분류 전용)
  - 단위 테스트 무거워짐 (DB, engine dependency)
  - 학습마다 raw backtest → 느려짐
  - pattern-agnostic 유지 어려움
- **난이도**: 중~상 / **테스트 용이성**: 낮음

### 옵션 B: 별도 evaluator 모듈 + report 단계 verdict merge
- `src/ml/deployment_comparison.py` (신규) — BacktestEngine 두 번 호출
- `report.py::build_report`가 optional 파라미터로 결과 dict 받음
- `metrics.bar_level_comparison` + `bar_level_filter_verdict` top-level 반영
- **장점**: validator 순수 유지, 관심사 분리, 선택적 실행
- **단점**: run_pipeline이 학습 후 comparison도 호출, CLI complexity 증가, merge 로직 필요
- **난이도**: 중 / **테스트 용이성**: 중간

### 옵션 C: post-processing script (**추천**)
- 학습 파이프라인 그대로
- `scripts/compare_ml_vs_baseline.py` (신규) 또는 `backtest_ml_artifact.py` 확장
- Script 책임:
  1. artifact 로드
  2. meta에서 primary_tf / symbols / oos_period_ms 읽음
  3. pattern_name → baseline_strategy_factory lookup (dict)
  4. BacktestEngine으로 raw + ml wrapper 둘 다 실행
  5. bar-level metrics 비교 + `bar_level_filter_verdict` 계산
  6. artifact 디렉토리에 `baseline_comparison.json` 저장
  7. 콘솔 판정 출력
- **장점**:
  - 학습 파이프라인 완전히 그대로 (validator, report, run_pipeline 수정 없음)
  - filter 패턴만 선택적 실행 (cost asymmetry 해소)
  - 반복 비교 가능 (다른 baseline strategy로 재평가)
  - artifact 디렉토리가 self-contained
  - 단일 script 범위 내 완결
  - 테스트 쉬움 (script 단위)
- **단점**:
  - 학습 직후 즉시 판정 불가, 별도 script 실행 필요 (1단계 추가)
  - baseline_strategy_factory 연결 위치 결정 필요
- **난이도**: 낮음~중 / **테스트 용이성**: 높음

### 비교 요약 + 추천

| 옵션 | 학습 path 변경 | 레이어링 | 복잡도 | 반복 유연성 | 추천 |
|---|---|---|---|---|---|
| A | 큼 | 나쁨 | 높음 | 낮음 | ✗ |
| B | 중 | 중간 | 중간 | 중간 | ✗ |
| **C** | **없음** | **깨끗** | **낮음** | **높음** | **★** |

**결정 근거**: D2는 filter-type 패턴만 필요하고, filter 실험은 드문 작업.
Common path인 학습에 bar-level backtest 끼워넣는 건 cost-benefit 안 맞음.
Post-processing이 유연하고 lightweight.

## 4. 추천 구조 설계 (옵션 C 기반)

### 4.1. `src/ml/bar_level_comparison.py` (신규)

```python
"""Bar-level comparison: ML filter wrapper vs raw baseline strategy.

Runs both through BacktestEngine on the same holdout window and
computes a bar-level filter verdict that complements the event-level
one from evaluate_holdout.
"""

@dataclass
class BarLevelMetrics:
    n_trades: int
    total_pnl: float         # dollar PnL
    win_rate: float
    sharpe: float
    max_drawdown: float
    per_symbol: Dict[str, Dict[str, float]]


@dataclass
class BarLevelComparison:
    """Filter-type pattern deployment comparison.

    Answers: does the ML filter, when deployed through BacktestEngine
    with position lock and intra-bar TP/SL, beat the raw baseline
    strategy on the same holdout window?
    """
    holdout_period_ms: Tuple[int, int]
    symbols: List[str]
    baseline_strategy_name: str
    raw: BarLevelMetrics
    ml: BarLevelMetrics
    delta_trade_count: int
    delta_win_rate: float
    delta_total_pnl: float
    delta_pnl_per_trade: float
    delta_sharpe: float
    bar_level_filter_verdict: str

    def to_dict(self) -> Dict[str, Any]: ...


def compare_ml_vs_baseline(
    artifact_dir: Path,
    baseline_strategy_factory: Callable[[], Any],
    symbols: Optional[List[str]] = None,
    holdout_period_ms: Optional[Tuple[int, int]] = None,
    initial_capital: float = 10_000.0,
) -> BarLevelComparison:
    """Run BacktestEngine twice on the same window and compute deltas."""
```

### 4.2. Verdict 값 (4가지)

| verdict | 조건 |
|---|---|
| `BAR_FILTER_VALUE_ADD` | ML win_rate 개선 AND ML pnl_per_trade 개선 |
| `BAR_FILTER_DESTROYS` | ML win_rate 악화 AND ML pnl_per_trade 악화 |
| `BAR_FILTER_NEUTRAL` | 한쪽만 개선 (ambiguous) |
| `BAR_FILTER_NOT_COMPARABLE` | raw 또는 ml trade 수 < 5 |

판정 기준: **pnl_per_trade** 기준 (total_pnl 아님). 좋은 filter는 정당하게 trade
수를 줄임. Total pnl로 비교하면 어떤 정상적인 filter도 "거부했다는 이유만으로"
벌점을 받음.

### 4.3. baseline_comparison.json 구조

```json
{
    "holdout_period_ms": [1759244400000, 1775746800000],
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"],
    "baseline_strategy_name": "BBKCSqueeze",
    "raw": {
        "n_trades": 176, "total_pnl": 4337.80,
        "win_rate": 0.619, "sharpe": 0.35, "max_dd": 0.14,
        "per_symbol": {...}
    },
    "ml": {
        "n_trades": 36, "total_pnl": 315.62,
        "win_rate": 0.556, "sharpe": 0.12, "max_dd": 0.08,
        "per_symbol": {...}
    },
    "delta": {
        "trade_count": -140,
        "win_rate": -0.063,
        "total_pnl": -4022.18,
        "pnl_per_trade": -15.88,
        "sharpe": -0.23
    },
    "bar_level_filter_verdict": "BAR_FILTER_DESTROYS"
}
```

### 4.4. Baseline strategy factory 연결

세 후보:

| 위치 | 장단 |
|---|---|
| `BasePattern.baseline_strategy_factory` class attr | pattern 모듈 강결합, filter 패턴만 채움 |
| `scripts/compare_ml_vs_baseline.py` 하드코딩 dict | 단순, 명시적, 패턴 모듈 깨끗 |
| `meta.json::policy.baseline_strategy` | 학습 시점 선택, artifact 영구 기록 |

**추천**: **하드코딩 dict** — 첫 구현 BBKC 한정:

```python
BASELINE_STRATEGY_REGISTRY = {
    "bbkc_filter": lambda: BBKCSqueeze(),
}
```

추후 추가 시 meta.json 경로로 업그레이드 가능.

### 4.5. report.py 통합 (선택적, 최소 버전에서는 skip)

post-processing 스크립트가 저장한 `baseline_comparison.json`이 있으면
`load_run`이 `artifact.report["metrics"]["bar_level_comparison"]`으로 merge.

top-level verdict 재계산은 별도 유틸리티 (skip, YAGNI).

## 5. 최소 구현 전략

### 첫 버전 (P0, 3~4시간)

1. `src/ml/bar_level_comparison.py` 작성 (~200 lines)
   - `BarLevelMetrics`, `BarLevelComparison` dataclass
   - `compare_ml_vs_baseline` 함수
   - `_derive_bar_filter_verdict` 헬퍼

2. `scripts/compare_ml_vs_baseline.py` 작성 (~100 lines)
   - CLI: `--run-dir`, optional `--symbols`, `--start`, `--end`, `--capital`
   - `BASELINE_STRATEGY_REGISTRY` dict (bbkc_filter 만)
   - pattern_name lookup → compare_ml_vs_baseline 호출
   - `baseline_comparison.json` 저장 + 콘솔 판정 출력
   - pattern이 dict에 없으면 "not applicable" 메시지 + exit 0

3. `tests/test_ml/test_bar_level_comparison.py` 작성 (~150 lines)
   - 4~5 테스트 (아래 섹션 6)

### 지원 범위 제한

- filter-type 패턴만 (dict 등록 필요)
- 단일 primary_tf (1h, 확장 시 추가)
- 단일 baseline strategy per pattern

### 일반 패턴 (RSI/Engulfing) 처리

script 실행 시 pattern_name not in dict:
```
[compare_ml_vs_baseline] bar-level comparison not applicable for
  pattern=engulfing_mtf (no baseline strategy registered).
  D1 event-level filter_verdict is sufficient for standalone patterns.
```
exit 0 (실패 아님).

### 유예 항목

- pattern 메타에 strategy factory 기록
- run_pipeline에 compare 자동 호출
- top-level verdict 재계산
- multi-baseline comparison

## 6. 추천 테스트

### 기존 재활용

| 파일 | 재활용 방법 |
|---|---|
| `scripts/backtest_ml_artifact.py` | bar-level replay 템플릿 |
| `tests/test_ml/test_patterns/test_bbkc_filter.py` | `_make_bbkc_fixture`, `_RecordingBroker` |
| `tests/test_ml/test_e2e_bbkc_filter.py` | wrapper 로드 경로 |
| `src/backtester/engine.py::BacktestEngine` | 두 번 호출만 하면 됨 |

### 신규 테스트

**[P0] `test_raw_beats_ml_returns_BAR_FILTER_DESTROYS`**
- Synthetic squeeze fixture
- ML model stub: raw의 "좋은" 진입 거부하도록 설계
- `compare_ml_vs_baseline` 호출 → `BAR_FILTER_DESTROYS` assertion
- 필수 이유: BBKC 실패 케이스 자동 재현

**[P0] `test_ml_beats_raw_returns_BAR_FILTER_VALUE_ADD`**
- Synthetic fixture에서 raw가 손실 보는 심볼
- ML stub: 손실 진입 거부 (BBKC SOL 케이스)
- `BAR_FILTER_VALUE_ADD` assertion
- 필수 이유: false positive "DESTROYS" 방어

**[P1] `test_insufficient_trades_returns_BAR_FILTER_NOT_COMPARABLE`**
- 짧은 holdout (raw 또는 ml < 5 trades)
- `BAR_FILTER_NOT_COMPARABLE` assertion

**[P1] `test_comparison_json_roundtrip`**
- `to_dict` → json 저장 → 재로드
- 필드 완전 일치

**[P2] `test_script_cli_on_bbkc_filter_fixture`**
- subprocess로 `compare_ml_vs_baseline.py` 호출
- `baseline_comparison.json` 생성 확인
- exit code 0, 출력 형식

**[P2] `test_script_skips_non_filter_pattern`**
- rsi_divergence artifact에 스크립트 실행
- "not applicable" 메시지 + exit 0

### 선택 테스트 (skip 가능)

- Real DB integration test
- Multiple symbols divergence (P0에서 커버)
- BBKCSqueeze parameter override

## 7. Go / No-Go 결론

### 판정: **설계 고정 + 구현 보류**

### 이유

**"BBKC 사례 때문에 사실상 필수인가?"**
→ 예, **다음 filter 실험 시점에는 필수**. 그런데 BBKC filter는 이미 KILL됨이고,
당장 계획된 filter 실험이 없음. 구현 시점을 "다음 filter 실험 시작 전"에 맞추는
게 효율적.

**"구현 비용 vs 재사용성"**
- 비용: 3~4시간 (낮음)
- 재사용: 미래 모든 filter 실험 자동 판정 (매우 높음)
- **지금 쓸 filter 실험 없음** → 구현 → 방치 → drift 위험

**"지금 긴급한가?"**
→ 아님. Day 2에서 수동 비교 방식 이미 있음. 자동화는 반복 시 편의성.

**"설계 고정 필요 이유"**
→ 미래 구현자가 설계 판단 재수행 없이 Day 1 코딩부터 들어갈 수 있도록.

### 추천 구현 순서 (실제 구현 세션에서)

1. `src/ml/bar_level_comparison.py` 작성 (~1시간)
   - `BarLevelMetrics`, `BarLevelComparison`
   - `compare_ml_vs_baseline` (BacktestEngine 2회 + deltas)
   - `_derive_bar_filter_verdict`
2. 유닛 테스트 4개 + 통과 확인 (~1시간)
   - DESTROYS, VALUE_ADD, NOT_COMPARABLE, json roundtrip
3. `scripts/compare_ml_vs_baseline.py` 작성 (~30분)
   - CLI + registry dict + save + console output
4. End-to-end 검증 (~30분)
   - 기존 BBKC artifact에 실행
   - Day 2 수동 비교 수치와 ±1% 내 일치 확인
5. (선택) `load_run` 확장 (~20분)
   - `baseline_comparison.json` 있으면 report로 merge
6. 문서화 (~20분)
   - 구현 완료 리포트 + 사용법
7. 새 filter 실험 시작 — D2 자동 판정 받으며 진행

### 한 줄 결론

**D2는 개념적으로 필수지만 구현은 다음 filter 실험 직전이 가장 효율적**.
이 메모가 그 시점의 구현 가이드.
