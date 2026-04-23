# EngulfingMTF 1h ATR Baseline — Holdout Fail (패턴 종료)

**날짜**: 2026-04-14
**브랜치**: `feature/ml-pattern-strategy`
**관련 커밋**:
- `2b796bb fix(ml): EngulfingMTF Gate 1 — *_primary rename + HTF gate + schema tests`
- `dc054d4 feat(ml): true-holdout evaluation + verdict override (validator blind spot fix)`
**Artifact**: `logs/ml/engulfing_mtf/2026-04-14_044048/`

## 배경

RSI Divergence가 holdout 기준으로 배포 불가 판정을 받은 뒤, 다음 후보로 EngulfingMTF를 검증했습니다. 실험 전 설계 메모에 따라:

1. **Gate 1 수정 완료** (`2b796bb`):
   - `*_1h` → `*_primary` 피처명 리네임
   - HTF 피처에 `_is_higher_tf` 가드 추가 (RSI와 동일 패턴 적용)
   - P1 schema test + P2 HTF gate test (4h/1d 경로) + P4 holdout assertion 추가

2. **옵션 3b 전략**: 현 상태 8 피처로 1h ATR baseline 1회만 돌려서
   얇은 피처 세트가 학습 가능한지 빠르게 확인.
   실패 시 옵션 3a(피처 확장) 시도하지 않고 BBKC ML filter로 이동.

## 실험 설정

| 항목 | 값 |
|---|---|
| pattern | `engulfing_mtf` |
| primary_tf | `1h` |
| symbols | BTC/ETH/SOL/LINK/AVAX |
| IS 기간 | 2024-04-01 ~ 2025-10-01 (18개월) |
| holdout 기간 | 2025-10-01 ~ 2026-04-10 (6+개월) |
| label 모드 | `atr`, tp=2.0×ATR, sl=1.0×ATR, atr_period=14 |
| max_holding_bars | 48 (2일) |
| threshold 범위 | 0.30–0.70 (baseline) |
| HPO trials | 50 |
| CV splits | 5 |

## 결과

### Walk-Forward (IS 내부 sliding window) — 표면적 지표

| 지표 | 값 |
|---|---|
| n_folds | 11 |
| IS events | **16,528** (RSI Divergence의 약 12배) |
| oos_total_trades (fold 합) | 156 |
| oos_total_pnl | **+15R** |
| oos_pos_pct | 54.5% |
| oos_sharpe_mean | +0.077 |
| permutation p-value | 0.00 |
| best threshold | 0.6018 |
| CV expectancy | +0.9545 |

### Holdout (real 2025-10 ~ 2026-04) — 진짜 OOS 지표

| 지표 | 값 |
|---|---|
| OOS 이벤트 수 (패턴 발화) | **5,777** |
| **threshold 통과 trades** | **0** |
| total PnL | 0 R |
| win_rate | 0% |
| per_symbol | {} |
| **verdict** | **`HOLDOUT_NO_TRADES`** |

### Final Verdict: `WARNING_HOLDOUT_NO_TRADES`

## 실패 모드 분석

### 1. 피처 importance가 모든 것을 말함 (Top 8)

```
is_long                 : 0.1152    ← 1위, direction 원핫
symbol_id_SOLUSDT       : 0.0906
symbol_id_LINKUSDT      : 0.0784
h4_trend_up             : 0.0781
symbol_id_AVAXUSDT      : 0.0780
symbol_id_BTCUSDT       : 0.0770
d1_body_ratio           : 0.0756
engulf_size_ratio       : 0.0711    ← 실제 패턴 강도 피처는 8위
```

모델이 학습한 건 "엔걸핑 패턴의 품질"이 아니라 **"어느 심볼이 어느 방향으로 기우는가"**입니다.
Top 5 중 4개가 `is_long` + `symbol_id_*` 원핫. 이것은 설계 메모의 BLOCKER-3(피처 세트가 얇음) 가설을
정확히 확인하는 결과입니다. 모델이 학습할 패턴 정보가 부족해서 symbol/direction priors로 fallback했습니다.

### 2. 분포 이동 (distribution shift)

IS 기간에는 심볼 prior + direction prior로 +15R / 54.5% pos folds를 만들어낼 수 있었지만,
holdout 기간에는 그 priors가 깨졌습니다. 최종 threshold 0.6018은 IS 분포에 최적화되었고,
holdout 분포에서는 5,777 이벤트 중 **어느 하나도 그 proba를 넘지 못했습니다**.

### 3. RSI 4h baseline과 실패 방식 비교

| 지표 | RSI 4h baseline | Engulfing 1h baseline |
|---|---|---|
| IS 이벤트 수 | 1,307 | 16,528 |
| WF total R | +11R | +15R |
| WF pos folds | 63.6% | 54.5% |
| WF p-value | 0.00 | 0.00 |
| 진짜 holdout trades | 2 | **0** |
| 진짜 holdout pnl | −2R | 0 |
| holdout verdict | HOLDOUT_NO_TRADES | HOLDOUT_NO_TRADES |

두 패턴 모두 WF 표면 지표는 "WARNING으로 배포 후보급"이지만, holdout에서는 완전히 무너집니다.
실패 방식은 다릅니다:
- **RSI**: 모델이 너무 selective → 2 trades만 fire, 모두 loser
- **Engulfing**: 모델이 symbol priors로 fallback → threshold 너무 빡빡, 0 trades

공통점은 **IS 내부 sliding window가 실제 OOS 성능을 전혀 반영하지 못한다**는 것입니다.
holdout 평가 없이 RSI나 Engulfing을 배포했다면 두 경우 모두 "학습 시점 지표"만 보고 배포해서
실거래에서 실패했을 것입니다.

## 결정

합의된 규칙에 따라 **EngulfingMTF 종료**:

- holdout n_trades=0 + 구조적 실패 모드(심볼 prior 학습) 확인
- 옵션 3a(피처 확장)은 "아깝게 실패" 케이스에만 적용하기로 약속했는데, 이번 결과는
  피처 엔지니어링으로 보완 가능한 범위를 벗어남 — 몇 개 피처를 추가해도 symbol prior에
  계속 붙을 가능성이 높음
- 다음: **BBKCSqueeze ML filter** 설계로 이동

## 새 평가 체계 검증

이번 실험은 `dc054d4`에서 도입한 true holdout 평가 체계의 **첫 실전 샘플**이었습니다.

**검증된 것**:
1. `evaluate_holdout`이 pattern-agnostic이라 EngulfingMTF 파이프라인에 수정 없이 작동
2. `report.metrics.holdout` 섹션이 자동으로 기록됨
3. `_final_verdict`가 `WARNING_HOLDOUT_NO_TRADES`로 올바르게 승격
4. 만약 기존 체계였다면 top-level verdict는 단순히 "WARNING"이었고, 외부에서는
   "WARNING이지만 +15R p=0.00 54.5% pos면 deploy할 만함"으로 오해할 수 있었음
5. 이제는 console과 report에 "holdout 0 trades" 정보가 자동으로 기록되어 이 오해가 원천 차단됨

**평가 체계는 제대로 작동하고, Engulfing에서 가치를 증명했습니다.**

## 다음 단계 — BBKC ML filter (설계 단계)

BBKCSqueeze는 현재 Bybit 시스템의 유일한 생존 전략입니다. ML filter는 기존 BBKCSqueeze 엔트리
신호 중 ML이 낮은 확률로 판정한 신호를 걸러내서 승률을 끌어올리는 방식입니다.

### 설계 원칙
1. **패턴 정의**: BBKCSqueeze의 squeeze-fire bar를 pattern event로 래핑
2. **피처 세트**:
   - BBKCSqueeze 내부 상태: squeeze 강도, 이전 squeeze 지속 기간, 돌파 방향, KC/BB 거리 등
   - 변동성 regime: atr_primary_pct, bb_width_pct_primary
   - 추세 regime: ADX, EMA slope
   - HTF 컨텍스트: h4/d1 EMA slope (strictly-higher TF만)
   - Location: 현재 가격의 rolling N-bar 극값 대비 위치
3. **라벨**: BBKCSqueeze의 자체 청산 규칙(TP/SL/max_holding)대로 청산한 결과를 triple-barrier로 인코딩
4. **ML 역할**:
   - "진입 승인/거부"만 결정 (방향은 BBKCSqueeze가 결정)
   - threshold가 너무 엄격해서 승률 개선이 없으면 FAIL
   - 충분한 샘플 수 보장이 필수 (holdout ≥ 10 trades 권장)

### 우선순위
- 지금 당장 구현 X
- 설계 명세(spec) 먼저 작성 → 사용자 리뷰 → 구현

## 재현 명령

```bash
python -u -m scripts.train_ml_pattern engulfing_mtf \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,LINKUSDT,AVAXUSDT \
  --is 2024-04-01:2025-10-01 --oos 2025-10-01:2026-04-10 \
  --primary-tf 1h \
  --label-mode atr --tp-atr 2.0 --sl-atr 1.0 --atr-period 14 \
  --max-holding 48 \
  --trials 50 --hpo-timeout 900 \
  --threshold-min 0.30 --threshold-max 0.70 --cv-splits 5
```
