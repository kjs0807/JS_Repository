# Wrapper ↔ Walk-Forward Parity Analysis (4h RSI Divergence)

**날짜**: 2026-04-14
**브랜치**: `feature/ml-pattern-strategy`
**관련 artifact**: `logs/ml/rsi_divergence/2026-04-14_010319/`
**관련 커밋**: `95592af test(ml): pin wrapper 4h path + broker ATR-order path`

## 배경

wrapper ATR-parity 패치 후 "4h baseline artifact로 BacktestEngine end-to-end 실행 →
walk-forward 리포트와 대조" 단계를 실행했습니다. 허용 오차는 trade count / per-symbol
PnL / R/trade가 ±10~15%.

## 수행한 작업

1. `scripts/backtest_ml_artifact.py` 작성 — artifact 로드 →
   `PatternMLFilterStrategy.from_artifact` → `BacktestEngine.run()` 심볼별 루프
2. 4h baseline artifact로 BTC/ETH/SOL/LINK/AVAX, 2024-04-01 ~ 2026-04-10 실행
3. 결과가 WF 리포트와 크게 어긋나서 이벤트 레벨 직접 비교 probe 작성

## 1차 결과 (WF ↔ BT)

| symbol | WF trades | WF pnl (R) | BT trades | BT pnl (USD) | BT R/trade |
|---|---|---|---|---|---|
| AVAXUSDT | 21 | +3.0 | **3** | +575.75 | +0.960 |
| BTCUSDT | 1 | -1.0 | **2** | +175.05 | +0.438 |
| ETHUSDT | 13 | +5.0 | **3** | +1214.87 | +2.025 |
| LINKUSDT | 18 | +0.0 | **5** | +2121.34 | +2.121 |
| SOLUSDT | 23 | +4.0 | **0** | +0.00 | +0.000 |
| **Total** | **76** | **+11.0** | **13** | **+$4087** | **+1.54 (avg)** |

Trade count: 13 vs 76 → 82% 괴리. R/trade: 1.54 vs 0.145 → 10×. **허용 오차 실패**.

## 원인 파악 — validator 구현 재확인

`src/ml/validator.py`를 다시 읽어보니 walk-forward는 **bar-level 체결 시뮬레이션을
하지 않습니다**. 대신:

```python
for fold in folds:
    model = _fit_xgb(xgb_params, X[is_slice], y[is_slice], w[is_slice])
    proba = model.predict_proba(X[oos_slice])[:, 1]
    taken = proba >= threshold
    pnl = wins * tp_pct - losses * sl_pct  # R-multiple aggregation
```

- 각 fold는 **별도 재학습된 모델**로 OOS slice의 이벤트를 평가
- "trades" = threshold를 통과한 이벤트 카운트 (holding lock 없음, 중복 체결 허용)
- pnl 단위 = R-multiple, 라벨을 oracle truth로 사용

즉 WF "76 trades"는 **"11개 재학습 모델 앙상블의 fold별 OOS 통과 이벤트 합계"**이고,
wrapper BT는 **"단일 최종 모델 + bar-level 시뮬레이션 + holding lock + intra-bar TP/SL"**.
두 수치는 애초에 비교 대상이 아닙니다.

## 이벤트 레벨 직접 비교 (wrapper parity 검증)

BacktestEngine + holding lock을 제거하고, 같은 artifact의 pattern + model + threshold를
심볼별로 직접 평가한 probe:

| symbol | bars(4h) | events fired | passed threshold | wrapper BT trades |
|---|---|---|---|---|
| BTCUSDT | 4449 | 361 | **2** | **2** ✓ |
| ETHUSDT | 4449 | 373 | **3** | **3** ✓ |
| SOLUSDT | 4449 | 356 | **0** | **0** ✓ |
| LINKUSDT | 4449 | 320 | **5** | **5** ✓ |
| AVAXUSDT | 4449 | 345 | **3** | **3** ✓ |
| **Total** | | **1755** | **13** | **13** ✓ |

**완전 일치.** wrapper는 pattern + 모델 + threshold를 순수하게 실행하고 있으며,
holding lock도 실제로 blocker가 아님 (threshold=0.6452에서 이벤트가 드물어서
24-bar 홀딩 안에 겹치는 케이스가 없음).

## 왜 13 vs 76인가 (이론적 해석)

- WF의 각 fold는 261개 이벤트로 재학습된 **작은 표본 모델**. 작은 표본 모델은 예측
  분포가 극단값 쪽으로 더 퍼짐 → threshold 통과 비율 ↑
- 최종 단일 모델은 1307개 이벤트로 학습됨. 큰 표본 → 예측이 prior(0.5) 쪽으로 수렴 →
  threshold 0.6452를 넘는 이벤트가 극적으로 줄어듦
- 이벤트 통과율: WF ~5.3% (76/1430) vs. single-model ~0.7% (13/1755)

즉 WF의 76 trades는 "deployment time에 실제로 잡히는 trade 수"가 아니라
"retrain 이벤트 앙상블이 만들어내는 upper bound"에 가깝습니다.

## 재해석 — 배포 관점에서의 wrapper 성능

| 지표 | WF (리포트) | BT (wrapper 배포) |
|---|---|---|
| Trades | 76 | **13** |
| Total R | +11.0 | **~+20** ($4087 / $200 risk) |
| R/trade | +0.145 | **+1.54** |
| Win rate | ~45% | **85%** (11W/2L) |
| Sharpe | +0.104 | N/A |

wrapper 배포 경로는 훨씬 선택적(selective)이고, 통과하는 13개는 상위 품질 trade.
WF가 추가로 집계한 63개는 저품질 앙상블 평균에 의한 low-conviction 이벤트로,
WF의 낮은 R/trade(+0.145)에 기여하던 잡음에 가깝습니다.

## Parity 기준 재정의 제안

사용자가 제시한 "WF 숫자와 ±15% 매칭"은 **구조적으로 달성 불가능**합니다. 두 경로가
측정하는 대상 자체가 다릅니다. 올바른 parity 기준:

> **wrapper가 실행하는 trade 집합이 (pattern + model + threshold)를 직접 적용한
> 이벤트 집합과 1:1 일치한다.**

이는 위 probe 표에서 BTC 2/2, ETH 3/3, SOL 0/0, LINK 5/5, AVAX 3/3 로 **완전 일치** 확인됨.

## 판정

- **wrapper ATR parity: PASS** (이벤트 레벨 1:1 일치)
- **WF ↔ BT 숫자 일치: N/A** (비교 불가, 두 지표가 다른 것을 측정)

## 다음 단계 후보

- **A. BBKCSqueeze 직접 비교** — 같은 2년 구간에 두 전략 backtest 돌려서
  R/trade, 총수익, Sharpe, drawdown 비교. wrapper는 준비됨.
- **B. Walk-forward 모드 확장** — `validator.py`에 bar-level 시뮬레이션 옵션을
  추가해서 "배포 시뮬레이션 WF 리포트"를 별도로 생성. 큰 작업.
- **C. Threshold 재튜닝** — 단일 모델 기반으로 다시 HPO하되 "최소 trade 수" 제약
  추가. wrapper 문제가 아니라 학습 정책 문제.

현재 상태: **(A)를 가장 추천**. wrapper correctness는 확인됨. 이제 중요한 건 "13
trades / +20R / R/trade 1.54"가 BBKCSqueeze와 비교해 경쟁력이 있는가.

## Addendum — OOS-only 검증 (외부 리뷰 후속 보강)

외부 리뷰 지적: full-period deployment BT는 IS 구간 포함이라 비교 오염이 남아있음.
`backtest_ml_artifact.py`에 `wf-oos-only` 모드 + `--probe` 플래그 추가 후,
artifact의 `meta.data.oos_period_ms` (2025-10-01 ~ 2026-04-10)만 분리해서 재실행.

### OOS-only 결과 (holdout 2025-10-01 ~ 2026-04-10)

| symbol | OOS bars | 이벤트 | threshold 통과 | BT trades | BT PnL ($) |
|---|---|---|---|---|---|
| BTCUSDT | 1160 | 76 | **1** | **1** | −209.73 |
| ETHUSDT | 1160 | 88 | **0** | **0** | 0 |
| SOLUSDT | 1160 | 91 | **0** | **0** | 0 |
| LINKUSDT | 1160 | 64 | **0** | **0** | 0 |
| AVAXUSDT | 1160 | 84 | **1** | **1** | −207.17 |
| **Total** | | **403** | **2** | **2** | **−416.90** |

- **wrapper parity는 OOS에서도 완벽 유지**: probe `passed` = 2, BT trades = 2
- 실제 홀드아웃에서 **2 trades / 0W / 2L / −417**
- Win rate 0%, 두 trade 모두 1R 손실

### 4h baseline 재해석 (중대)

어제 deployment 모드에서 본 "2년 구간 13 trades / 11W / 2L / ~+20R / R/trade +1.54"는
다음과 같이 분해됨:

| 구간 | trades | wins | losses | 비고 |
|---|---|---|---|---|
| IS 내부 (2024-04 ~ 2025-10) | 11 | 11 | 0 | 최종 모델이 학습 시 이미 라벨을 본 이벤트 |
| OOS 홀드아웃 (2025-10 ~ 2026-04) | 2 | 0 | 2 | 모델이 처음 보는 데이터 |

즉 "11W/0L" 성과는 in-sample re-evaluation으로 사실상 **leakage에 가까운 overfit
지표**. 진짜 홀드아웃 성과는 0W/2L.

### Walk-forward 리포트 재해석

train_ml_pattern.py의 validator는 IS 범위에서만 sliding window walk-forward를 돌리고,
OOS 홀드아웃 기간은 전혀 사용하지 않습니다. 따라서 walk-forward 리포트의
"63.6% positive folds, +11R, p=0.00"은 **전부 IS 내부 metric**이고,
실제 미래 성능에 대한 예측력이 없었음이 OOS 결과로 확인됨.

### 결론 (업데이트)

- **wrapper ATR parity**: PASS — deployment 모드와 OOS-only 모드 양쪽에서
  probe ↔ BT 완전 일치
- **walk-forward 리포트**: WARNING 판정은 **IS 내부 성과**였고, 실제 OOS에서는 fail
- **4h baseline artifact 자체**: 배포 후보로 **부적합** — OOS에서 2 trades / 0% win
- RSI Divergence는 1h·4h·1d 모두 실제 홀드아웃에서 실패 확인

### 다음 단계 (업데이트)

BBKCSqueeze 비교는 **의미 없어짐** — 비교할 수익 데이터가 없음. 대안:

- **A. RSI Divergence 종료 + 다른 패턴으로 전환** (EngulfingMTF 또는 새 카탈로그 후보)
- **B. validator 재설계** — 현재 IS 내부 walk-forward만 하고 있음. 별도의 "진짜
  holdout test"를 run_pipeline에 추가해서 meta에 `holdout_report`로 기록
- **C. 4h baseline은 여기서 종료**. 다음 모델/패턴으로 이동

추천: **B(validator 개선) + A(새 패턴)**. B는 지금 당장 고치지 않으면 다음 패턴에서도
동일한 해석 오염이 반복됨.

## 재현 명령

Deployment (full period, includes IS — **do not** interpret as OOS):
```bash
python -m scripts.backtest_ml_artifact deployment \
  --run-dir logs/ml/rsi_divergence/2026-04-14_010319 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,LINKUSDT,AVAXUSDT \
  --start 2024-04-01 --end 2026-04-10
```

Proper OOS holdout (the one that actually matters):
```bash
python -m scripts.backtest_ml_artifact wf-oos-only \
  --run-dir logs/ml/rsi_divergence/2026-04-14_010319 \
  --symbols artifact --probe
```
