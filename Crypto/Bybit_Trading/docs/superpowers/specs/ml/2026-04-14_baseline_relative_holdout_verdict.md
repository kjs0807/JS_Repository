# Baseline-relative Holdout Verdict (Option D)

**날짜**: 2026-04-14
**브랜치**: `feature/ml-pattern-strategy`
**선행 context**:
- `54fa082 docs(ml): BBKC filter Day 2 -- HOLDOUT_PASS but deployment FAIL vs raw`
**설계 근거**: 위 문서의 "Structural finding: evaluate_holdout verdict의 한계" 섹션

## TL;DR

- `evaluate_holdout`에 두 번째 verdict 축(`filter_verdict`)을 추가했습니다.
  ML threshold가 "threshold=0 (all events)" 베이스라인 대비 per-trade
  quality를 개선하는지 측정합니다.
- 4개 verdict 값: `FILTER_VALUE_ADD` / `FILTER_DESTROYS` / `FILTER_NEUTRAL` /
  `FILTER_NOT_APPLICABLE`
- `_final_verdict`가 `filter_verdict`를 반영하도록 확장:
  `FAIL_FILTER_DESTROYS`, `WARNING_FILTER_NEUTRAL` 신규 top-level verdict 추가
- BBKC filter artifact로 회귀 검증 — 새 체계가 **FILTER_VALUE_ADD**를 리턴.
  이것은 이전 Day 2의 "FILTER_DESTROYS" 판정과 겉보기에 모순이지만,
  **두 가지 서로 다른 베이스라인** 때문임을 확인.
- 알려진 한계: event-level 베이스라인은 bar-level BacktestEngine의
  position-lock 효과를 포착 못함. 미래 확장 여지.

## 구현

### 1. `HoldoutReport` 확장 (`src/ml/validator.py`)

기존 필드(`n_events`, `n_trades`, `total_pnl_R`, `win_rate`, `verdict`)에
아래 필드 추가:

```python
baseline_n_trades: int     # threshold=0에서의 이벤트 전체 수
baseline_n_wins: int
baseline_pnl_R: float
baseline_win_rate: float
delta_win_rate: float      # ml_wr - baseline_wr
delta_pnl_per_trade_R: float  # ml_R/tr - baseline_R/tr
filter_efficiency: float   # ml_n / baseline_n (rejection = 1 - 이 값)
filter_verdict: str        # FILTER_VALUE_ADD / DESTROYS / NEUTRAL / NOT_APPLICABLE
```

### 2. `_derive_filter_verdict` 헬퍼

ML subset과 baseline 두 축(win_rate, R/trade)을 비교해서 4개 verdict 중 하나 반환:

```python
if ml_n_trades < min_trades or baseline_n_trades < min_trades:
    return "FILTER_NOT_APPLICABLE", 0.0, 0.0

delta_wr = ml_win_rate - baseline_win_rate
delta_r_per_trade = (ml_pnl / ml_n) - (baseline_pnl / baseline_n)

wr_up = delta_wr > eps
r_up = delta_r_per_trade > eps
wr_down = delta_wr < -eps
r_down = delta_r_per_trade < -eps

if wr_up and r_up:       return "FILTER_VALUE_ADD"
elif wr_down and r_down: return "FILTER_DESTROYS"
else:                    return "FILTER_NEUTRAL"
```

**핵심 선택**: R/trade(**per-trade**)로 비교, total pnl로 비교 안 함.
이유: 좋은 filter는 정당하게 trade 수를 줄일 수 있음. Total pnl로 비교하면
어떤 정상적인 filter도 "거부했다는 이유만으로" 벌점을 받음. Per-trade quality가
filter 성공의 옳은 측정 기준.

### 3. `_final_verdict` 확장 (`src/ml/report.py`)

우선순위 (상위가 하위를 override):

```
1. WF permutation overfit        -> FAIL
2. HOLDOUT_FAIL                  -> FAIL
3. HOLDOUT_NO_TRADES             -> WARNING_HOLDOUT_NO_TRADES
4. HOLDOUT_PASS + FILTER_DESTROYS  -> FAIL_FILTER_DESTROYS  <- NEW
5. HOLDOUT_PASS + FILTER_NEUTRAL   -> WARNING_FILTER_NEUTRAL  <- NEW
6. HOLDOUT_PASS + FILTER_VALUE_ADD + WF strong -> PASS
7. HOLDOUT_PASS + FILTER_VALUE_ADD + WF mixed  -> WARNING
8. HOLDOUT_PASS + FILTER_NOT_APPLICABLE        -> fallback to absolute
9. No holdout                    -> legacy WF-only
```

비filter 패턴(RSI, Engulfing 같은)은 `FILTER_NOT_APPLICABLE`로 내려가서
legacy fallback path로 이동 — 회귀 없음.

### 4. CLI 출력 추가 (`scripts/train_ml_pattern.py`)

기존 `[train_ml_pattern] holdout: ...` 라인 다음에 한 줄 추가:

```
[train_ml_pattern] filter: verdict=FILTER_VALUE_ADD baseline_n=354
                  baseline_wr=54.8% delta_wr=+1.4% delta_R/tr=+0.0006
                  eff=9.0%
```

### 5. 유닛 테스트 4개 (`tests/test_ml/test_validator.py`)

- `test_evaluate_holdout_filter_value_add` — ML 80% / baseline 50% / 동일 tp sl
- `test_evaluate_holdout_filter_destroys` — ML 30% / baseline 50%
- `test_evaluate_holdout_filter_neutral` — ML 50% / baseline 50% (same)
- `test_evaluate_holdout_filter_not_applicable_when_baseline_small` —
  n_oos=4 < min_trades=5

전체 ml+strategies 스위트: 218/218 통과.

## BBKC filter 회귀 검증 — 두 베이스라인의 충돌

동일 BBKC filter artifact를 새 체계로 재평가:

```
[train_ml_pattern] holdout: verdict=HOLDOUT_PASS
                   events=354 trades=32 pnl=+0.034R win_rate=56.2%
[train_ml_pattern] filter: verdict=FILTER_VALUE_ADD
                   baseline_n=354 baseline_wr=54.8%
                   delta_wr=+1.4% delta_R/tr=+0.0006 eff=9.0%
[train_ml_pattern] verdict: WARNING
```

### 이전 Day 2 결론(FILTER_DESTROYS)과의 차이

| 비교축 | 신규 event-level | Day 2 bar-level |
|---|---|---|
| Baseline trades | 354 (all holdout events) | 176 (raw BBKC BT 실제 체결) |
| Baseline win_rate | 54.8% | 61.9% |
| ML filter trades | 32 | 36 (wrapper BT) |
| ML filter win_rate | 56.25% | 55.6% |
| filter_verdict | **FILTER_VALUE_ADD** | **FILTER_DESTROYS** |

### 왜 모순이 아닌가

**Raw BBKCSqueeze의 position lock 자체가 이미 강력한 필터**입니다. 354 이벤트 중
연속 squeeze release가 발생할 때 첫 번째만 잡고 나머지는 position 잡고 있다는 이유로
자동 거부. 이 단순한 lockout이 **354 → 176**으로 줄이면서 승률을
**54.8% → 61.9% (+7.1%p)** 올립니다.

ML 통계 filter는 더 공격적으로 **354 → 32** (91% 거부)로 줄이지만
승률은 **54.8% → 56.25% (+1.4%p)**에 그칩니다.

- **event-level baseline**(all 354 events) 관점: ML은 54.8%를 56.25%로 올림 → VALUE_ADD (+1.4%p)
- **bar-level baseline**(raw BBKC 176 체결) 관점: ML은 61.9%를 55.6%로 내림 → DESTROYS (-6.3%p)

둘 다 올바른 측정이지만 **다른 질문**에 답함:
- event-level: "ML threshold가 없는 것보다 있는 게 나은가?"
- bar-level: "ML filter가 raw BBKCSqueeze 배포 대비 가치를 추가하는가?"

**BBKC의 경우** bar-level이 진짜 관심사지만, 비용과 구현 복잡도 때문에 이번 PR에서는
event-level만 구현했습니다. event-level은 모든 패턴에 공통으로 적용 가능하고, filter
패턴의 경우에도 "ML이 event dist 학습은 하고 있는가" 최소 신뢰도는 제공합니다.

## 알려진 한계 (정직하게 기록)

`filter_verdict`는 **bar-level position-lock 효과**를 포착 못합니다:

1. 기반 전략이 자체 position management를 갖는 경우 (BBKCSqueeze의
   `if pos is not None: return`), 그 lockout이 이미 강력한 필터일 수 있음.
2. 이번 체계는 event-level만 보기 때문에 lockout이 바닐라 전략에 주는 이점을
   반영 못함.
3. 결과적으로 filter_verdict가 VALUE_ADD라도 bar-level 배포 성능이 raw 전략보다
   나쁠 수 있음 (BBKC 케이스).

### 미래 확장 여지

"진짜 filter_verdict"를 구현하려면:

1. `run_pipeline`에 `baseline_strategy_factory` 인자 추가
2. holdout 기간에 해당 strategy를 BacktestEngine으로 실행 (bar-level)
3. bar-level win_rate/R/trade vs ML filter bar-level 비교
4. 단위 문제 (bar-level PnL $ vs label-level R-multiple) 조정

이 작업은 별도 세션에서 가능. 이번 PR은 event-level 측정만으로도 한 단계
개선이고, 일부 케이스에서 유용한 신호를 줍니다.

## 교훈

1. **Baseline 선택이 결론을 좌우함**. 같은 artifact가 event-level baseline
   에서는 VALUE_ADD, bar-level baseline에서는 DESTROYS. 둘 다 수학적으로 옳음.
2. **자연스러운 lockout이 ML보다 나은 필터일 수 있음**. BBKC처럼 단일 포지션
   강제 전략에서는 position-lock이 노이즈 이벤트를 자동으로 걸러냄. ML filter가
   이걸 이기려면 단순 "threshold 통과" 이상의 뭔가를 해야 함.
3. **event-level axis의 가치**: 비filter 패턴 (RSI/Engulfing)과 filter 패턴
   양쪽에 동일하게 적용 가능. bar-level 비교는 filter 패턴에만 유효.
4. **verdict 체계의 정직성**: 새 축이 VALUE_ADD라고 해서 배포 가능하다는 뜻은
   아님. 여러 verdict가 병렬로 존재하고, 상황에 맞는 해석이 필요.

## 다음 단계

이번 PR은 Option D(baseline-relative verdict)의 **첫 단계**. 뒤이어 고려할 수 있는 작업:

- **D2 (bar-level baseline)**: `baseline_strategy_factory` 추가, BacktestEngine
  실행, bar-level 비교. 진정한 filter-type deployment 판정을 자동화.
- **D3 (position-lock emulation)**: event-level 베이스라인에 간단한 position lock을
  추가 (이전 이벤트가 N bars 내 fire했으면 skip). D2보다 싸고 유사한 효과.
- **ML pattern 탐색 재개**: D2/D3 완료 후 BBKC filter를 restricted baseline과 비교.
  바닐라 lockout을 넘어선 가치 추가가 있는지 측정 가능.

현재 세션 기준 판단: **D는 여기서 잠시 멈추고 다른 실험 또는 코드 정리로 전환해도
괜찮음**. D2/D3는 언제든 추가 가능.
