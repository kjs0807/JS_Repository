# RSI Divergence ML — 1h × 4h × threshold 2×2 Matrix (정화 실험)

**날짜**: 2026-04-14
**브랜치**: `feature/ml-pattern-strategy`
**관련 커밋**: `1e2a94c fix(ml): tighten event cache key + rename *_1h features to *_primary`

## 실험 동기

이전 라운드에서 RSI Divergence가 "4h에서 갑자기 WARNING 판정"을 보였으나, 코드 리뷰 결과 두 가지 오염 요인이 확인됐습니다.

1. **이벤트 캐시 키에 `primary_tf`와 기간/데이터 상태가 빠져있었음** — 같은 라벨 설정이면 1h/4h/1d 이벤트 데이터셋이 충돌할 수 있었음
2. **`*_1h` 피처명이 primary TF에 관계없이 그대로 사용됨** — 4h 실험에서도 `adx_1h`라는 이름으로 기록되어, `--min-adx 20` 같은 필터가 실제로는 "4h ADX ≥ 20"인데 로그/리포트는 "1h ADX ≥ 20"처럼 보였음
3. 추가로, 과거 실험들은 심볼셋이 매 라운드 달라서 (XRP 포함/AVAX·LINK 제외 등) TF 비교가 apples-to-oranges였음

**수정 후**, 같은 유니버스로 2×2 매트릭스(TF × threshold)를 깨끗하게 다시 돌렸습니다.

## 실험 프로토콜 (고정)

| 항목 | 값 |
|---|---|
| 심볼 유니버스 | `BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT, AVAXUSDT` |
| IS 기간 | `2024-04-01 ~ 2025-10-01` (18개월) |
| OOS 기간 | `2025-10-01 ~ 2026-04-10` (약 6개월) |
| 라벨 모드 | `atr`, tp=2.0×ATR, sl=1.0×ATR, atr_period=14 |
| 보유 기간 | **시간 기준 4일 통일** → 1h: 96 bars, 4h: 24 bars |
| HPO trials | 50 |
| CV splits | 5 |
| Purge | `max_holding_bars` (1h=96, 4h=24) |
| 캐시 | 각 실험 전 `cache/ml/rsi_divergence/` 전체 삭제 |

## 결과 요약 테이블

| Cell | Verdict | IS n | OOS n | PnL R | **R/trade** | Sharpe | **%pos folds** | p-value |
|---|---|---|---|---|---|---|---|---|
| 1h baseline (0.30–0.70) | **FAIL** | 5792 | 1315 | **−49.0** | **−0.037** | −0.022 | 36.4% | 1.00 |
| 1h exp4 (0.45–0.80) | WARNING | 5792 | 30 | +6.0 | **+0.200** | +0.213 | 36.4% | 0.00 |
| **4h baseline (0.30–0.70)** | **WARNING** | 1307 | **76** | **+11.0** | +0.145 | +0.104 | **63.6%** | 0.00 |
| 4h exp4 (0.45–0.80) | WARNING | 1307 | 31 | +5.0 | +0.161 | +0.142 | 36.4% | 0.00 |

## 셀별 상세

### 1h baseline (1h, threshold 0.30-0.70, max_holding 96)
- verdict: **FAIL**
- best threshold: 0.441
- CV expectancy: +0.0483
- IS events: 5792
- OOS trades: 1315  |  total PnL: −49.0R  |  R/trade: −0.037
- OOS Sharpe mean: −0.0218 (std 0.0995)
- positive folds: 36.4% (11 total, purge=96 bars)
- overfit p-value: **1.0** ← 실제 점수가 permutation 평균보다 낮음
- per-symbol OOS: AVAXUSDT=261/−24R, ETHUSDT=237/−18R, SOLUSDT=256/−13R, LINKUSDT=275/−2R, BTCUSDT=286/+8R
- top features: dt_regular_bull (0.047), dt_hidden_bull (0.043), dt_hidden_bear (0.040), symbol_id_SOLUSDT (0.039), symbol_id_ETHUSDT (0.035), dist_swing_low_atr (0.033), dist_swing_high_atr (0.032), is_long (0.031)

### 1h exp4 (1h, threshold 0.45-0.80, max_holding 96)
- verdict: **WARNING**
- best threshold: 0.541
- CV expectancy: +0.2000
- IS events: 5792
- OOS trades: 30  |  total PnL: +6.0R  |  R/trade: +0.200
- OOS Sharpe mean: +0.2135 (std 0.3837)
- positive folds: 36.4% (11 total, purge=96 bars)
- overfit p-value: 0.0
- per-symbol OOS: AVAXUSDT=7/−1R, LINKUSDT=5/+7R, SOLUSDT=6/−3R, BTCUSDT=7/−1R, ETHUSDT=5/+4R
- top features: dt_hidden_bear (0.036), dt_regular_bear (0.035), dt_hidden_bull (0.035), is_long (0.034), symbol_id_SOLUSDT (0.034), dist_swing_low_atr (0.031), rsi_slope (0.031), bb_width_pct_primary (0.031)

### 4h baseline (4h, threshold 0.30-0.70, max_holding 24)
- verdict: **WARNING**
- best threshold: 0.645
- CV expectancy: +0.3182
- IS events: 1307
- OOS trades: 76  |  total PnL: +11.0R  |  R/trade: +0.145
- OOS Sharpe mean: +0.1041 (std 0.2344)
- positive folds: **63.6%** (11 total, purge=24 bars)
- overfit p-value: 0.0
- per-symbol OOS: AVAXUSDT=21/+3R, LINKUSDT=18/+0R, ETHUSDT=13/+5R, SOLUSDT=23/+4R, BTCUSDT=1/−1R
- top features: dist_swing_high_atr (0.043), slope_divergence_ratio (0.043), symbol_id_SOLUSDT (0.042), adx_primary (0.039), divergence_strength (0.038), intervening_retracement_ratio (0.038), d1_ema_slope_atr_norm (0.037), dt_regular_bear (0.037)

### 4h exp4 (4h, threshold 0.45-0.80, max_holding 24)
- verdict: **WARNING**
- best threshold: 0.615
- CV expectancy: +0.6000
- IS events: 1307
- OOS trades: 31  |  total PnL: +5.0R  |  R/trade: +0.161
- OOS Sharpe mean: +0.1424 (std 0.2208)
- positive folds: 36.4% (11 total, purge=24 bars)
- overfit p-value: 0.0
- per-symbol OOS: AVAXUSDT=6/+3R, ETHUSDT=2/−2R, SOLUSDT=12/+6R, LINKUSDT=11/−2R
- top features: plus_minus_di_diff_primary (0.045), rsi_slope (0.045), divergence_strength (0.045), slope_divergence_ratio (0.045), rsi_diff_abs (0.044), atr_primary_normalized (0.043), intervening_retracement_ratio (0.040), pivot_distance_bars (0.040)

## 해석

### 1. 1h baseline이 완전히 무너짐

−49R / 1315건 / R/trade −0.037 / p-value 1.00. 이건 엣지가 없는 게 아니라 **역엣지**에 가깝습니다. 과거 실험에서 1h baseline이 애매하게 보였던 이유는 심볼셋 혼입과 캐시 오염 가능성이 겹친 것으로 판단됩니다. 정화된 조건에서는 1h에 느슨한 threshold로 RSI divergence를 돌리면 noise detector가 됩니다.

### 2. TF × threshold 교호작용이 실제로 존재

- **1h는 threshold를 타이트하게(0.45+) 해야만 엣지 발생**: baseline FAIL → exp4 R/trade +0.200
- **4h는 반대로 넓게(0.30+) 잡아야 robust**: baseline 63.6% pos folds → exp4 36.4%

같은 threshold 범위를 두 TF에 그대로 적용하면 안 된다는 걸 보여줍니다.

### 3. 4h baseline이 가장 신뢰 가능한 단일 셀

- **63.6% positive folds** — 4개 셀 중 유일하게 50% 이상
- **OOS 76 trades** — 유의미한 표본 크기를 가진 유일한 WARNING 셀
- R/trade +0.145 + p-value 0.00
- ETH/SOL/AVAX 전부 양수, LINK 플랫, BTC는 1 trade (사실상 자동 제외)

### 4. 1h exp4의 R/trade +0.200은 표본 부족으로 신뢰 불가

OOS 30 trades = 11 folds × 평균 2.7건. 폴드 양수율 36.4%는 "가끔 크게 이기고 자주 작게 진다"는 의미. Sharpe std 0.3837로 분산이 매우 큼. R/trade 숫자만으로 선택하면 안 됨.

### 5. max_holding 시간 통일의 부수 발견

과거 1h 실험은 max_holding=24 (1일) 기준으로 −10 ~ −21R였는데, 4일 통일 기준(max_holding=96)으로 늘리니 **−49R로 더 나빠짐**.

→ **1h RSI divergence는 구조적으로 짧은 홀딩(≤1일)에서만 간신히 버티는 fragile signal**. 홀딩 기간 변화에 이렇게 민감하면 실거래 환경(슬리피지/펀딩/수수료)에도 민감할 가능성이 높습니다.

## 결론

1. **1h는 RSI Divergence에 적합하지 않음** (baseline FAIL, exp4는 표본 부족)
2. **4h baseline이 유일하게 robust한 셀** — 표본 크기 + 폴드 양수율 + permutation test 통과
3. **TF와 threshold는 독립 변수가 아님** — 교호작용 존재
4. BTC 제외 실험은 불필요: 4h baseline에서 BTC는 이미 사실상 자동 제외 (1 trade)

## 다음 단계 후보

- **A. 4h baseline Strategy wrapper 연결** → 메인 BacktestEngine에서 end-to-end 검증 → BBKCSqueeze와 비교
- **B. 4h 구간 확장 재학습** — IS를 2024-04 ~ 2025-12로 늘려 안정성 재확인
- **C. 다른 패턴으로 전환** — EngulfingMTF 또는 BBKC ML 필터

현재 상태: 실험 결과를 외부 리뷰 요청 중 (사용자가 결정 대기).

## 재현 명령

```bash
# 캐시 초기화
rm -rf cache/ml/rsi_divergence/

# 4개 셀 순차 실행
for cfg in \
  "1h 96 0.30 0.70" \
  "1h 96 0.45 0.80" \
  "4h 24 0.30 0.70" \
  "4h 24 0.45 0.80"; do
  set -- $cfg
  python -u -m scripts.train_ml_pattern rsi_divergence \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT,LINKUSDT,AVAXUSDT \
    --is 2024-04-01:2025-10-01 --oos 2025-10-01:2026-04-10 \
    --primary-tf $1 --max-holding $2 \
    --label-mode atr --tp-atr 2.0 --sl-atr 1.0 --atr-period 14 \
    --trials 50 --hpo-timeout 900 --cv-splits 5 \
    --threshold-min $3 --threshold-max $4
done
```
