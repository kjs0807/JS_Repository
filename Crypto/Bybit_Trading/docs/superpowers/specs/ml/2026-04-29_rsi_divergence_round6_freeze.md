# RSI Divergence — Round 6 Kickoff Freeze

**Date**: 2026-04-29
**Status**: 동결 (Round 6 우선순위 하향, forward 결과 + 다른 후보 우선)
**Trigger**: 사용자가 `docs/Screenshot/` 에 1d 7건 + 4h 6건의 visual divergence 사례를 표시
하고 "ML이 왜 이런 명확한 패턴을 못 잡았는지" 재검토 요청.

## 1. 작업 개요

이전 RSI divergence ML 결론 (`2026-04-14_rsi_divergence_2x2_tf_threshold_matrix.md`):
- 1h baseline FAIL (R/trade −0.037)
- **4h baseline WARNING — 단일 robust cell** (R/trade +0.145, 76 trades, 63.6% pos folds)
- 1d 영역 미실험

가설: 1d 영역에 visual divergence가 강하게 잡히는 이유가 있을 수 있다 (max_holding 부족,
TF 부재 등).

## 2. 검증 단계 (3 단계)

### A. Detector 시각 대응력 (case study)

산출물: `logs/research/rsi_divergence/case_study_round6_kickoff.md`
스크립트: `scripts/rsi_divergence_case_study.py`

13 visual case (BTCUSDT, 1d 7 + 4h 6) 각 시점 윈도우에서 detector가 이벤트를 발생시켰는지
+ 방향 일치 + strength threshold 통과 여부:

| 결과 | 1d | 4h | 합 |
|---|---|---|---|
| MATCH | 6 | 5 | **11/13** |
| WEAK | 0 | 0 | 0 |
| EARLY_LATE | 1 | 0 | 1 (D1) |
| MISMATCH | 0 | 1 | 1 (H1: 시각 bear, detector hidden_bull) |
| MISS | 0 | 0 | 0 |

**Detector 시각 대응 게이트 통과** (11/13 MATCH). 단 1d 178 events / 4h 378 events 중
13개에 매칭된 best events가 시각 마킹과 일치한다는 것만 보여줌 — 통계적 엣지와는 별개.

### B. 1d ML 매트릭스

산출물: `logs/ml/rsi_divergence/2026-04-29_*` (BTC smoke 4 + 5심볼 2 = 6 artifact)

**B1. BTC 단일 smoke 4 셀**:
- max_holding ∈ {60, 120}
- threshold ∈ {0.30-0.70, 0.45-0.80}
- IS 2021-01-01:2025-04-01, OOS 2025-04-01:2026-04-14

| Cell | max_hold | threshold | holdout | filter | walk-forward / overfit | final |
|---|---:|---|---|---|---|---|
| 1 | 60 | 0.30-0.70 | PASS | DESTROYS | — | FAIL |
| 2 | 60 | 0.45-0.80 | PASS | VALUE_ADD (+0.33 R/tr, 9 trades) | — | WARNING |
| 3 | 120 | 0.30-0.70 | PASS | DESTROYS | — | FAIL |
| 4 | 120 | 0.45-0.80 | PASS | VALUE_ADD | walk-forward 2 folds, OOS −4R; overfit p=1.0 | FAIL |

→ max_holding 120은 holdout만 cell 2와 비슷할 뿐, walk-forward 와 overfit에서 무너짐.
60만 살리고 120 폐기.

**B2. 5심볼 (BTC,ETH,SOL,LINK,AVAX) 2 cell × max_holding=60**:

| Cell | threshold | holdout | filter | walk-forward | overfit | final |
|---|---|---|---|---|---|---|
| A | 0.30-0.70 | PASS | VALUE_ADD (+0.51 R/tr, 6 trades) | 11 folds, 30% pos, +1R sum | OVERFIT (p=0.5) | **FAIL** |
| B | 0.45-0.80 | PASS | VALUE_ADD (+0.72 R/tr, 7 trades) | 11 folds, 20% pos, **−9R sum**, 7 loss folds | OVERFIT (p=1.0) | **FAIL** |

→ holdout이 PASS이고 filter가 VALUE_ADD인 것은 7-9 trade에 한정된 cherry-pick.
walk-forward 11 fold 분포에선 양수 비율 20-30%, overfit p-value 0.5-1.0 (random shuffle이
ML보다 잘 함) → 실제 통계적 엣지 없음.

### C. 4h baseline 재현

산출물: `logs/ml/rsi_divergence/2026-04-29_130823`

이전 결론 (R/trade +0.145, 76 trades, 63.6% pos folds, p=0.0) 의 같은 설정 재실행 결과:

| 지표 | 이전 (2026-04-14) | 현재 재현 (2026-04-29) |
|---|---|---|
| IS events | 1307 | **446 (-66%)** |
| WF positive folds | 63.6% | 63.6% (동일) |
| WF sum_pnl | +11R | **+1R** |
| **R/trade (WF)** | **+0.145** | **+0.0105** |
| overfit p-value | 0.0 | **0.1 (WARNING)** |
| 최종 verdict | WARNING | WARNING |

→ 같은 설정, 같은 데이터인데 **events 1/3로 줄고 R/trade 1/14로 약화**. 코드 변경
(detector confirmation/lookback/pivot 정의 변화 가능성) 또는 4h 데이터 갱신의 영향으로
보임. **이전 4h baseline의 "유일한 robust 셀" 지위가 현 시점에 더 이상 성립하지 않음**.

### D. Metadata 분포 분석 (visual vs all)

산출물: `logs/research/rsi_divergence/metadata_analysis_round6.md` + 3 csv
스크립트: `scripts/rsi_divergence_metadata_analysis.py`

전체 detected events vs 13 visual 사례의 metric 분포:

**핵심 발견**: visual 13개의 metric 값(특히 divergence_strength)이 **all events 분포의
중하위 (12-29 percentile)** 에 위치.

| metric (1d) | visual p25 | all_pct@visual_p25 | selectivity |
|---|---|---|---|
| divergence_strength | 10.0 | 12.4% | 87.6% |
| pivot_prominence | 2326 | 39.3% | 60.7% |
| intervening_retracement_ratio | 2.4 | 56.7% | **43.3%** ★ |

→ visual_p25 를 hard filter threshold 로 잡아도 selectivity 43-90% (많은 noise 통과).
**단일 metric으로 visual vs noise 분리 불가**.

**Regular only filter 추가 분석**:
- div_type ∈ {regular_bull, regular_bear} 만 통과시키면 noise 41-48% 제거
- 단 visual 13/13 → 9/13 (D1·D7·H1·H2 모두 hidden type → 빠짐)
- regular only 후에도 단일 metric 분리력은 여전히 약함 (selectivity 41-91%)

**결론**: detector metadata로 시각 사례 vs noise를 분리하는 단일/2-metric 룰베이스 wrapper
구성 불가능. 시각으로 인지되는 "큰 swing"이 detector metric에 직접 매핑되지 않음 — 가능성:
metadata 외 컨텍스트 (RSI 절대값, 추세 컨텍스트, 더 넓은 시간 윈도우) 필요.

## 3. 종합 결론 — RSI Divergence 동결

세 가지 단계 모두에서 운영 가능한 통계적 엣지를 못 찾음:

| 단계 | 결과 |
|---|---|
| Detector 시각 대응 | 11/13 MATCH ✓ (게이트 통과) |
| 1d ML 매트릭스 (6 cells) | 모두 FAIL (overfit + WF 불안정) |
| 4h baseline 재현 | R/trade +0.145 → +0.0105 (재현 실패) |
| Metadata 룰베이스 | 단일/2-metric 분리 불가 |

→ **현 시점 RSI Divergence를 라이브 후보로 진행할 근거 없음.** Round 6 우선순위에서 후순위.

### 동결의 의미

- **detector 코드 (`src/ml/patterns/rsi_divergence.py`) 는 그대로 유지** — 시각 대응력은
  유효하므로 추후 재개 시 자산.
- **새 변경 작업 없음** — ML 학습/룰베이스 wrapper 신규 시도 없음.
- **forward 시점에 다시 검토** (BBKC forward 1-3개월 결과 후 Round 6 brainstorming
  시점에 우선순위 재평가).

## 4. 산출물 위치

| 분류 | 경로 |
|---|---|
| 신규 분석 스크립트 | `scripts/rsi_divergence_case_study.py`, `scripts/rsi_divergence_metadata_analysis.py` |
| Case study 리포트 | `logs/research/rsi_divergence/case_study_round6_kickoff.md` |
| Metadata 분석 리포트 | `logs/research/rsi_divergence/metadata_analysis_round6.md` |
| Metadata CSV (raw) | `logs/research/rsi_divergence/metadata_analysis_visual_events.csv`, `_all_events_1d.csv`, `_all_events_4h.csv` |
| ML 학습 artifact | `logs/ml/rsi_divergence/2026-04-29_125315 ~ 2026-04-29_130823` (7건) |

## 5. 재개 시 후보 시도

운영 우선순위 (BBKC forward 등) 정리 후 RSI divergence 재개 시 검토할 방향:

1. **시각 마킹 추가 컨텍스트 분석**: RSI 절대 level (oversold/overbought), 큰 추세 컨텍스트 (200d EMA 위치 등) — metadata 외부 정보 추가
2. **시간 컨텍스트 확장**: lookback 60+ (현재 30), confirmation_bars 5+ (현재 3)으로 detector 자체 재정의 후 visual 사례 재매칭
3. **visual 사례 확대**: 13건은 통계적으로 부족. 50건 이상 확보 후 분포 분석 재시도
4. **multi-metric ensemble**: 현재 metadata 단일 axis 분리 안 됨 → strength × prominence × distance 의 곱/log space에서 visual 사례가 cluster 되는지 확인
5. **다른 자산군**: BTC 외 universe 에서 같은 visual case study (사용자 시각 마킹) 후 metadata 비교

## 6. Round 6 우선순위 재정렬 (참고)

지금 시점 Round 6 후보 enumerate (forward 결과 후 정식 brainstorming에서 결정):

1. **forward 1-3개월 결과 평가** (BBKC be25_st60_di30) — 최우선
2. **13코인 일반화** (BBKC) — 우선
3. **공통 vs 심볼별 cell 차등** (BBKC) — 우선
4. **`BBKC_DISABLE_NEW_ENTRY` 가드, manual_close CLI** — 운영 강화
5. **`instruments_info` parser fix** (현재 WARN 발생) — 작은 fix
6. **One-way mode 지원** — 작은 fix
7. ~~RSI Divergence 재개~~ — 본 문서 결과로 **후순위 (동결)**

---

**Sign-off**: Round 6 kickoff RSI divergence 재검토 종료. 사용자 confirm 후 main 머지.
