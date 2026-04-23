# BBKC ML Filter — Day 2 Holdout vs Raw BBKCSqueeze Comparison

**날짜**: 2026-04-14
**브랜치**: `feature/ml-pattern-strategy`
**관련 커밋**:
- `26f07c1 feat(ml): BBKCFilterPattern — ML entry-approval filter for BBKCSqueeze` (Day 1)
- `9f09872 docs(ml): clarify bb_width_pct std=2.0 vs BBKCSqueeze bb_std=1.5`
**Artifact**: `logs/ml/bbkc_filter/2026-04-14_052551/`

## TL;DR

- BBKC ML filter는 **holdout 시스템 도입 이후 처음으로 `HOLDOUT_PASS` verdict를 받은 패턴**입니다.
- 하지만 raw BBKCSqueeze와 **같은 holdout 기간에 BacktestEngine으로 실제 비교**하면
  ML filter는 **가치를 추가하지 않고 오히려 93% 수익을 파괴**했습니다.
- 4개 판정 기준 중 3개에서 실패: win_rate -6.3%p, R/trade -64%, total PnL -93%.
- 결론: **현재 BBKC filter 구성 KILL**. 다만 SOL에서의 유일한 가치 추가는 기록 가치 있음.
- 부수 발견: 현재 `evaluate_holdout`의 verdict가 "절대적 수익성"만 보고 "baseline 대비 가치 추가"는
  측정하지 않음. **filter 유형 패턴에 대한 별도 verdict 축 필요**.

## 실험 설정 (Day 1 설계 메모에 고정)

| 항목 | 값 |
|---|---|
| pattern | `bbkc_filter` |
| primary_tf | 1h |
| symbols | BTC, ETH, SOL, LINK, AVAX |
| IS 기간 | 2024-04-01 ~ 2025-10-01 (18개월) |
| holdout 기간 | 2025-10-01 ~ 2026-04-10 (6+개월) |
| label mode | `pct` (BBKCSqueeze parity) |
| tp_pct | 0.02 (leverage-adjusted: 0.06/3) |
| sl_pct | 0.0233 (leverage-adjusted: 0.07/3) |
| max_holding_bars | 48 (2일) |
| threshold 범위 | 0.30–0.70 (baseline) |
| HPO trials | 50 |
| CV splits | 5 |

## 학습 결과 (holdout 시스템 관점)

### Walk-forward (IS internal, 11 folds)
- Total trades (fold 합): **259**
- Total PnL: +0.46R
- Positive folds: **63.6%**
- Sharpe mean: +0.155
- Permutation p-value: **0.00**
- Best threshold: 0.6315
- CV expectancy: +0.003

### Holdout (real 2025-10 ~ 2026-04) — event-level
```
n_events:      354
n_trades:       32   (threshold 0.6315 passes ~9%)
n_wins:         18
n_losses:       14
total_pnl_R:   +0.034
win_rate:      56.25%
verdict:       HOLDOUT_PASS  <-- FIRST EVER in this project
```

| symbol | trades | W/L | pnl_R |
|---|---|---|---|
| ETHUSDT | 10 | 8/2 | +0.113 |
| SOLUSDT | 14 | 8/6 | +0.020 |
| BTCUSDT | 2 | 1/1 | -0.003 |
| LINKUSDT | 2 | 0/2 | -0.047 |
| AVAXUSDT | 4 | 1/3 | -0.050 |

### Feature importance top 8

```
symbol_id_ETHUSDT       : 0.141   ← symbol prior 여전히 1위
bb_kc_width_ratio       : 0.072   ← BBKC 고유 squeeze 품질
symbol_id_SOLUSDT       : 0.070
h4_trend_alignment      : 0.066   ← HTF 맥락
dist_roll_high_atr      : 0.065   ← location
breakout_magnitude_atr  : 0.065   ← breakout 강도
symbol_id_AVAXUSDT      : 0.063
squeeze_duration_bars   : 0.063   ← squeeze 길이
```

**Engulfing과 결정적 차이**: Engulfing은 top 5가 `is_long + symbol_id*4`로 symbol prior fallback이었지만,
BBKC는 2-8위가 전부 **BBKC 고유 피처 + regime 피처**. 모델이 실제로 패턴 신호를 학습했다는 증거.

## Raw BBKCSqueeze 비교 실험 (같은 holdout, BacktestEngine 실행)

이 시점에서 중요한 깨달음: **holdout verdict와 실제 deployment parity는 다른 질문**.
`HOLDOUT_PASS`는 "ML filter가 자체적으로 수익을 내는가"를 본다. 진짜 물어야 할 건
"ML filter가 raw BBKCSqueeze 대비 가치를 추가하는가". 후자를 답하려면 같은 holdout 기간에
**raw BBKCSqueeze**를 BacktestEngine으로 돌려서 side-by-side 비교해야 한다.

### Raw BBKCSqueeze holdout (BacktestEngine, 같은 pct TP/SL, leverage=3)

| symbol | trades | pnl ($) | win_rate | Sharpe | max_dd |
|---|---|---|---|---|---|
| BTCUSDT | 42 | **+1535.09** | 64.3% | +2.99 | 12.4% |
| ETHUSDT | 55 | **+2048.36** | 65.5% | +2.99 | 14.6% |
| SOLUSDT | 15 | −517.87 | 46.7% | −2.86 | 10.3% |
| LINKUSDT | 10 | −585.96 | 40.0% | −5.00 | 6.3% |
| AVAXUSDT | 54 | **+1858.18** | 64.8% | +2.77 | 10.2% |
| **TOTAL** | **176** | **+4337.80** | **61.9%** | | |

### ML Filter BT (같은 artifact → BacktestEngine, 같은 설정)

| symbol | trades | pnl ($) | win_rate | approx R/trade |
|---|---|---|---|---|
| BTCUSDT | 8 | −183.71 | 50.0% | −0.115 |
| ETHUSDT | 11 | **+697.12** | 72.7% | +0.317 |
| SOLUSDT | 11 | **+307.29** | 63.6% | +0.140 |
| LINKUSDT | 3 | −252.23 | 33.3% | −0.420 |
| AVAXUSDT | 3 | −252.85 | 33.3% | −0.421 |
| **TOTAL** | **36** | **+315.62** | **55.6%** | |

### 4개 판정 기준

| 기준 | Raw BBKC | ML Filter | Delta | 판정 |
|---|---|---|---|---|
| Total trades | 176 | 36 | **−80%** | (정보용) |
| Total PnL | +$4337.80 | +$315.62 | **−93%** | FAIL |
| Avg win rate | 61.9% | 55.6% | **−6.3%p** | FAIL |
| Avg R/trade | +$24.65 | +$8.77 | **−64%** | FAIL |

**4개 중 3개 실패 → 판정: `FAIL` (Kill)**

## 실패 모드 분석 (per-symbol)

### [파괴] BTC + AVAX
- Raw BBKC 최대 수익원: BTC +$1535 / AVAX +$1858 (합 +$3393)
- ML filter: BTC −$184 (42건 → 8건, 승률 64% → 50%) / AVAX −$253 (54건 → 3건, 승률 65% → 33%)
- **모델이 BTC/AVAX의 좋은 squeeze를 식별하는 피처를 못 찾음**. 80~94%의 진입을 거부했는데 남긴 것조차 승률 50% 아래.
- 가설: BTC/AVAX는 symbol 특유의 squeeze 구조가 있는데, 현재 피처 세트(특히 `symbol_id_AVAXUSDT` 원핫)가 "이 심볼의 패턴은 찍지 마라"를 학습한 것으로 보임.

### [유지] ETH
- Raw: +$2048 (55건, 66%) → ML: +$697 (11건, 73%)
- 승률은 **상승**, trade 수는 **80% 감소**.
- 품질 높은 entry를 골라낸 건 사실이지만, 기회 대부분을 놓치면서 절대 수익이 1/3 이하로 떨어짐.
- 이게 **"filter가 이상적으로 작동할 때의 모습"**이지만, 절대 수익으로는 raw보다 크게 떨어짐.

### [유일한 가치 추가] SOL
- Raw: **−$518** (15건, 47% — 손실 심볼)
- ML: **+$307** (11건, 64%)
- Raw BBKC가 SOL에서 손해를 보고 있었는데, ML filter가 이를 수익 심볼로 전환.
- **이게 filter 컨셉이 원리적으로 작동할 수 있다는 첫 실증**.
- 하지만 BTC/AVAX 파괴로 상쇄되어 전체 수익은 크게 감소.

### [작은 개선] LINK
- Raw: −$586 / 10건 / 40%
- ML: −$252 / 3건 / 33%
- 둘 다 손실이지만 ML이 덜 잃음 (−43%). 하지만 3건은 표본 부족, 승률은 더 낮음.

## 구조적 발견: `evaluate_holdout` verdict의 한계

이번 실험은 현재 holdout 평가 시스템의 구조적 맹점을 드러냈습니다.

### 문제
현재 `evaluate_holdout`의 verdict (`HOLDOUT_PASS` / `HOLDOUT_FAIL` / `HOLDOUT_NO_TRADES`)는:
- n_trades >= min_trades (5)
- total_pnl_R > 0
- win_rate >= min_win_rate (0.35)

→ **절대적 수익성**만 본다. **baseline 대비 가치 추가**는 측정하지 않음.

### BBKC 케이스에서의 왜곡
- BBKC filter의 holdout은 **+0.034R / 56% win rate / 32 trades** → HOLDOUT_PASS
- 같은 기간 raw BBKC는 **+4338R-equivalent / 62% win rate / 176 trades**
- **HOLDOUT_PASS 판정인데 실제로는 raw 대비 93% 수익 파괴**

RSI/Engulfing에서는 raw baseline이 없었기 때문에 (혹은 비교할 자원이 안 됐기 때문에)
이 맹점이 드러나지 않았음. BBKC는 baseline이 있어서 처음으로 노출.

### 해결 방향 (다음 세션에서 검토)
filter 유형 패턴을 위한 **baseline-relative verdict**:
- 학습 시 baseline strategy의 holdout 성과를 같이 계산
- `report.metrics.holdout.baseline_comparison` 섹션 추가:
  - baseline_pnl, baseline_win_rate, baseline_n_trades
  - delta_pnl, delta_win_rate, delta_r_per_trade
- 판정:
  - `HOLDOUT_FILTER_VALUE_ADD`: ML이 baseline 대비 win_rate + R/trade 동시 개선
  - `HOLDOUT_FILTER_NEUTRAL`: 한 쪽만 개선, 다른 쪽 악화
  - `HOLDOUT_FILTER_DESTROYS`: 둘 다 악화 (이번 BBKC 케이스)

이 개선이 들어가 있었다면 현재 BBKC artifact는 `HOLDOUT_FILTER_DESTROYS`로 나왔을 것이고,
Day 2 비교 작업 없이 바로 FAIL 판정 가능했을 것.

## 결정

### 현재 BBKC ML filter 구성: **KILL**
판정 기준 4개 중 3개 실패, 전체 수익 93% 감소, BTC/AVAX 파괴. 피처 확장(옵션 3a)으로
해결될 가능성이 낮음 — 구조적으로 symbol별 signal 차이를 학습 못하고 있음.

### 교훈

1. **holdout verdict는 상대 평가가 필요**: filter 유형 패턴은 baseline과의 비교가 핵심. 현재 시스템은 이걸 못 함.
2. **`HOLDOUT_PASS` ≠ 배포 가능**: 이번이 첫 HOLDOUT_PASS였는데, 실제 deployment parity는 fail. 절대 수익성과 상대 가치는 다른 개념.
3. **Feature importance가 좋아 보인 것은 함정**: symbol_id가 1위지만 2-8위가 BBKC 고유 피처였음. 하지만 per-symbol 결과를 보면 모델이 **잘못 학습**했음 (BTC/AVAX를 체계적으로 거부). feature importance는 "어떤 피처가 분기에 많이 쓰였는가"이지 "분기가 옳은가"를 의미하지 않음.
4. **SOL 케이스는 값진 positive signal**: 전체로는 실패했지만 SOL 한 심볼에서는 ML filter가 -$518 → +$307로 의미 있는 전환을 만들었음. 단일 심볼에서 작동한 건 **ML filter 컨셉 자체는 원리상 가능함**을 보여줌. 단지 현재 5-symbol universe + 11-feature set 조합은 작동 안 함.

### 다음 세션에서 고려할 옵션

| 옵션 | 공수 | 기대값 | 추천 |
|---|---|---|---|
| **A. BBKC ML filter 이 시점에서 종료, 다른 방향 탐색** | 0 | 재사용 가능한 인사이트 | 기본 |
| **D. validator에 baseline-relative 평가 추가** | ~1시간 | 미래 모든 filter 실험에서 자동 판정 | 강력 추천 |
| B. Threshold 완화 (0.40-0.60) 재학습 | ~5분 | 거의 확실히 raw에 수렴, 의미 없음 | 비추 |
| C. 피처 확장 (옵션 3a) | ~2시간 | 구조적 문제 해결 불확실 | 비추 |
| E. SOL-only filter (single-symbol ML filter 실험) | ~1시간 | SOL 케이스 재현 가능성 + 단일 심볼 배포 교훈 | 선택적 |

**제 추천**: **A + D + (선택적으로 E)**.

- A: 현재 BBKC ML filter 종료 (5-symbol universe 기준)
- D: validator 구조 개선 — 이번 실험으로 드러난 맹점을 해결하고, 미래 filter-type 패턴은 자동으로 올바른 판정을 받게 함
- E는 리소스 여유 있을 때만

C (피처 확장)는 BTC/AVAX 파괴 문제가 피처 몇 개로 해결될 것 같지 않음. 그리고 지금 진짜 해결해야 할 건
평가 시스템의 맹점(D)이지, 패턴 성능 자체가 아님.

## 재현 명령

```bash
# BBKC ML filter 1h pct baseline 학습
python -u -m scripts.train_ml_pattern bbkc_filter \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,LINKUSDT,AVAXUSDT \
  --is 2024-04-01:2025-10-01 --oos 2025-10-01:2026-04-10 \
  --primary-tf 1h \
  --label-mode pct --tp 0.02 --sl 0.0233 \
  --max-holding 48 \
  --trials 50 --hpo-timeout 900 \
  --threshold-min 0.30 --threshold-max 0.70 --cv-splits 5

# ML filter BT (holdout only)
python -u -m scripts.backtest_ml_artifact wf-oos-only \
  --run-dir logs/ml/bbkc_filter/<run_id> \
  --symbols artifact --probe

# Raw BBKC holdout baseline — 본 문서의 python one-liner 참조 (BacktestEngine 직접 호출)
```
