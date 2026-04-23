# BBKC / Donchian Rule-Based Improvement Experiments — Memo

**날짜**: 2026-04-14
**브랜치**: `main`
**선행 context**:
- ML pattern round 종료 (RSI / Engulfing / BBKC ML filter 전부 KILL)
- 살아남은 규칙 기반 전략: `BBKCSqueeze`, `DonchianTrendFilter`, `DonchianFixedRR`
- 평가 체계: true holdout (2025-10-01 ~ 2026-04-10) + filter_verdict 축 정비됨
**이번 산출물**: 분석/제안만. 코드 수정 없음.

## 요약 (TL;DR)

- 살아남은 BBKC + Donchian 2종의 공통 강점은 **parameter 표면이 작고 position
  lock 자연 필터링**. 공통 약점은 **HTF 맥락 부재, ADX/regime 게이트 부재,
  breakout 강도 구분 부재**.
- 개선은 **"새 전략 개발"보다 "기존 전략에 빠진 1-bit 규칙 채우기"** 쪽이
  low-hanging fruit. BBKC에 HTF 맥락 없음, Donchian FixedRR에 trend filter 없음,
  둘 다 regime gate 없음 — 3가지 "구조적 빠진 조각"이 가장 명확.
- **ML은 다시 가지 않음**. rule-based만으로도 의미 있는 개선 여지가 있음.
- **추천 실행 순서**: **D2 → D1 → B1**. 그 다음 결과 보고 Donchian 쪽이 좋으면 확장,
  BBKC 쪽이 좋으면 2라운드 B2/B4, 둘 다 부족할 때만 hybrid 고려.

## 1. 현재 전략 요약

### BBKCSqueeze (`src/strategies/bbkc_squeeze.py:16-147`)

**진입 조건** (`:85-114`):
- `squeeze_prev >= 1 AND squeeze_now < 1` (BB가 KC 안에 있다가 빠져나오는 edge)
- LONG: `close > bb_mid AND rsi < 70`
- SHORT: `close < bb_mid AND rsi > 30`
- position 있으면 skip

**청산**: 고정 pct TP/SL (tp_pct=0.06, sl_pct=0.07, leverage=3 → 실효 0.02/0.0233).
broker가 intra-bar에서 자동 처리.

**지표**: BB(20, 1.5), KC(20, ATR 14, 1.0), RSI(14).

**강점**:
- squeeze → release edge는 변동성 압축/팽창 전환점
- "close vs bb_mid" 방향 + RSI overextension 필터는 최소 sanity check
- Day 2 raw baseline 2025-10 ~ 2026-04 holdout: 61.9% win rate, +$4338 / 176 trades

**약점**:
- **HTF 맥락 전혀 없음** — 추세와 반대 방향 release도 받음
- 고정 pct TP/SL은 변동성에 adapt 안 함 (BTC 2% vs AVAX 2%는 다른 의미)
- RSI 필터는 "과열 아님" 뿐, "추세 방향 일치" 확인 안 함
- squeeze 길이/강도 구분 없음

### DonchianTrendFilter (`src/strategies/donchian_trend_filter.py`)

**진입 조건**:
- LONG: `close > donchian(20).upper AND close > EMA(200)`
- SHORT: `close < donchian(20).lower AND close < EMA(200)`

**청산**: `close < donchian(10).lower OR close < EMA(200)` (long). 초기 SL =
`close - 2×ATR(14)`. TP 없음 — trailing exit channel이 TP 역할.

**강점**:
- **EMA(200) regime filter가 이미 있음** — 3개 전략 중 유일하게 명시적 추세 정렬
- Donchian(20) 진입 + Donchian(10) 청산 비대칭 — 빠른 exit이 추세 반전 일찍 잡음
- parameter 4개 (entry, exit, ema, stop_atr)

**약점**:
- **ADX 같은 trend strength 측정이 없음** — chop 구간에서 Donchian breakout은 false
- EMA(200)은 1h 기준 ~8일. 1h 추세 판정으로 coarse할 수 있음
- 진입 시점 breakout 강도 구분 없음

### DonchianFixedRR (`src/strategies/donchian_fixed_rr.py`)

**진입 조건**:
- LONG: `close > donchian(20).upper` (**trend filter 없음**)
- SHORT: `close < donchian(20).lower` (**trend filter 없음**)

**청산**: 고정 RR TP (stop=2.5×ATR, tp=5.0×ATR, RR=1:2) + ATR trailing (활성화
+1.5×ATR, trailing 1.0×ATR).

**강점**:
- **ATR-adaptive 진입/청산** — 변동성 자동 스케일링
- **Trailing stop** — 추세가 오래 이어지면 이익 확대
- RR 1:2 고정 → 승률 33% 넘으면 수익

**약점**:
- **trend filter가 빠짐** — 역추세 breakout 다수 흡수. **가장 명확한 missing piece**
- 모든 돌파를 동일하게 취급
- trailing 활성화 임계값 1.5×ATR은 보수적

### 공통 해석 — "왜 살아남았는가"

1. **Position lock이 자연 필터**: BBKC/Donchian 둘 다 `broker.get_position` 체크로
   연속 signal 거부. ML filter보다 효과적 (BBKC Day 2 검증됨).
2. **Trend-following + fat-tail**: crypto 분포는 heavy-tailed. tail이 수익원.
3. **Parameter 표면이 작음**: 과최적화 여지 제한.
4. **규칙 해석 가능성**: 디버깅 쉽고 실패 원인 명확.

### 공통 한계

- **단일 TF** (1h primary)
- **고정 parameter** (변동성 regime별 adaptive 없음)
- **Entry 강도 무시**
- **Re-entry 규칙 부재**
- **심볼 간 차별화 없음**

## 2. 개선 가능한 축 분해

| 축 | 설명 | 기존 코드 재사용 | 난이도 | 과최적화 위험 | 우선도 |
|---|---|---|---|---|---|
| **HTF 추세 정렬** | 4h EMA, 4h Donchian 등 | `indicators/trend.py::ema`, `channel.py::donchian` | 낮음 | 낮음 | ★★★ |
| **ADX regime 게이트** | chop 회피 | `indicators/momentum.py::adx` | 낮음 | 낮음 | ★★★ |
| **변동성 regime 필터** | ATR percentile, BB width percentile | BBKC 자체 BB width | 낮음 | 중 | ★★ |
| **Breakout 강도 필터** | `(close - upper) / ATR` 최소값 | ATR 재사용 | 낮음 | 낮음 | ★★ |
| **Squeeze 기간 게이트** | 선행 squeeze N bars 이상 | squeeze_on run-length | 낮음 | 낮음 | ★★ |
| **청산 규칙 변화** | fixed pct → ATR / trailing / 단계 청산 | broker update_stop | 중 | 중 | ★★ |
| **재진입 규칙** | pullback re-entry | 신규 로직 | 중~상 | 중 | ★ |
| **MTF breakout 확인** | 1h + 4h 동시 방향 | 동일 지표 재계산 | 낮음 | 낮음 | ★★ |
| **심볼 universe 튜닝** | per-symbol 파라미터 | config | 낮음 | **높음** | ✗ |
| **Hybrid** | 두 전략 조합 | 두 코드 재사용 | 중 | 중 | ★ (나중) |

**낮은 비용 / 높은 정보량 순**:
1. ADX regime gate (Donchian)
2. FixedRR에 EMA trend filter 복구
3. HTF trend filter (BBKC)
4. Squeeze duration gate (BBKC)
5. ATR-adaptive TP/SL (BBKC)

## 3. 구체 실험 아이디어

### BBKC 실험

**[B1] BBKC + 4h EMA(50) trend filter**
- **변경**: RSI 필터에 추가로 4h EMA(50) 방향 체크. LONG은 1h close > 4h EMA(50),
  SHORT 반대
- **기대**: 역추세 release 거부 → win_rate 상승, trade 수 감소
- **리스크**: 너무 엄격해서 squeeze-driven 심볼 trade 감소
- **난이도**: 낮음

**[B2] BBKC + squeeze duration gate**
- **변경**: 최소 squeeze 지속 bar N (예: 5) 미만 release 거부
- **기대**: noise squeeze 제거 → win_rate 소폭 상승
- **리스크**: 뉴스 driven 짧은 squeeze도 함께 거부
- **난이도**: 낮음

**[B3] BBKC + breakout strength gate**
- **변경**: 진입 시 `|close - bb_mid| / ATR >= 0.5` 요구
- **기대**: 약한 돌파 거부
- **리스크**: 큰 돌파는 이미 늦은 진입
- **난이도**: 낮음

**[B4] BBKC + ATR-adaptive TP/SL**
- **변경**: `tp = entry ± k_tp * ATR`, k_tp=2, k_sl=1로 pct 대체
- **기대**: 변동성 regime 간 일관된 R
- **리스크**: 저변동성에서 TP가 수수료/슬리피지에 먹힘
- **난이도**: 중

**[B5] BBKC + 1R trailing stop after break-even**
- **변경**: 1R에서 SL을 entry로, 2R에서 trailing 시작
- **기대**: reversal 손실 회피 + 추세 연장 시 이익 확대
- **리스크**: 평균 수익 감소 가능
- **난이도**: 중

**[B6] BBKC + 4h Donchian breakout alignment**
- **변경**: 1h squeeze release + 4h Donchian(20) 방향 일치
- **기대**: 약한 signal 필터링
- **리스크**: trade 수 급감
- **난이도**: 낮음~중

**[B7] Hybrid — BBKC 진입 + Donchian-style trailing 청산**
- **변경**: fixed pct TP 제거, Donchian(10) trailing exit channel로 교체
- **기대**: BBKC 진입 타이밍 + Donchian의 긴 tail 활용
- **리스크**: 사실상 새 전략 — 평가 프레임 변경 필요
- **난이도**: 중

### Donchian 실험

**[D1] DonchianTrendFilter + ADX regime gate (ADX ≥ 20 + ADX ≥ 25 동시 비교)**
- **변경**: 진입 시 `adx_14 >= 20` 조건 + 같은 실험에 `adx_14 >= 25` variant
  하나 더 만들어서 **동시에 비교**. 2점 regime cut
- **기대**: chop false breakout 거부 → win_rate +5~10%p, trade 수 30-40% 감소.
  두 임계값을 같이 보면 "ADX 감도"의 non-monotonicity를 직접 읽을 수 있음
- **리스크**: ADX는 지연 지표라 일찍 잡지 못할 수 있음
- **난이도**: 낮음 (1줄 추가, variant 하나 더)

**[D2] DonchianFixedRR + EMA(200) trend filter**
- **변경**: FixedRR에 빠진 trend filter를 TrendFilter에서 복사. LONG은 close >
  EMA(200), SHORT 반대
- **기대**: 가장 명확한 missing piece 복구. 거의 실험이 아니라 **안전장치 복구**
  수준 — 역추세 breakout 흡수를 막음
- **리스크**: 매우 낮음 — TrendFilter에서 이미 검증된 로직
- **난이도**: 매우 낮음 (몇 줄 복사)

**[D3] Donchian + pullback re-entry**
- **변경**: 첫 breakout 놓친 경우, EMA(20)까지 pullback 시 재진입
- **기대**: 추세 지속 시 2차 entry
- **리스크**: pullback이 reversal 시작일 수 있음, state 복잡
- **난이도**: 중~상

**[D4] Donchian + 2단계 청산 (half at 2R, half trailing)**
- **변경**: 포지션 50%는 2R TP 청산, 나머지 50%는 `close < donchian(10).lower`까지
- **기대**: drawdown 감소
- **리스크**: broker partial close API 확인 필요
- **난이도**: 중

**[D5] Donchian + HTF Donchian breakout 확인**
- **변경**: 1h + 4h 동시 Donchian(20) 돌파
- **기대**: 약한 돌파 필터링
- **리스크**: trade 수 급감
- **난이도**: 낮음~중

**[D6] Donchian adaptive period by ATR percentile**
- **변경**: ATR percentile 상위 30% → entry=10, 하위 70% → entry=55
- **기대**: 고변동성에서 기회, 저변동성에서 보수적
- **리스크**: 임계값 튜닝 함정
- **난이도**: 중

**[D7] Donchian + EMA slope gradient filter**
- **변경**: EMA(200) 방향 뿐 아니라 기울기 임계값도 요구
- **기대**: "살아있는 추세" 측정
- **리스크**: ADX와 중복 정보
- **난이도**: 낮음

### Hybrid (나중)

**[H1] Donchian 진입 + BBKC-style squeeze 사전 조건**
- Donchian(20) 돌파 직전 N bar squeeze였을 때만 진입
- 지금은 이르다 — 살아남은 전략의 약한 고리 메우는 단계 이후에 검토

**[H2] BBKC 진입 + Donchian trailing 청산**
- B7과 동일
- 지금은 이르다

## 4. 우선순위 Top 5 (사용자 보정 반영)

| 순위 | 실험 | 왜 먼저? |
|---|---|---|
| **1** | **[D2] Donchian FixedRR + EMA(200) trend filter** | 거의 "실험"이 아니라 **빠진 안전장치 복구**. TrendFilter에서 입증된 로직을 FixedRR로 포팅. 실패 리스크 거의 0. D1보다도 먼저 해볼 가치 |
| **2** | **[D1] DonchianTrendFilter + ADX ≥ 20 및 ≥ 25 동시** | 2점 regime cut은 과최적화가 아님. chop 회피는 Donchian breakout 전략의 고전적 개선. 두 임계값을 같이 보면 감도도 확인 |
| **3** | **[B1] BBKC + 4h EMA(50) trend filter** | BBKC의 가장 큰 구조적 빈틈 (HTF 맥락 부재) 채움. 1-bit gate, 해석 쉬움 |
| **4** | **[B4] BBKC + ATR-adaptive TP/SL** | **B1 결과가 괜찮을 때만**. 청산 철학이 꽤 바뀌기 때문에 B1이 baseline 개선을 확인한 후에만 의미 있음 |
| **5** | **[B2] BBKC + squeeze duration gate** | 추가 비용 거의 없이 새로운 signal axis. B1보다 덜 구조적이지만 차순위로 ok |

### 사용자 보정 요약

- **D2가 최우선** (빠진 안전장치 복구, 실험보다는 복구)
- **D1에 ADX 두 임계값 동시 비교** (20, 25)
- **B4는 2차 라운드**, B1 결과 긍정 확인 후에만
- **Hybrid는 지금 이르다** — 살아남은 전략의 약한 고리 메우는 단계 이후

## 5. 추천 실험 프로토콜

### 원칙

1. **Baseline 유지**: BBKC / DonchianTrendFilter / DonchianFixedRR 원본을 control로
2. **한 번에 한 변수**: D1의 ADX 두 임계값은 "같은 실험의 2 variant"로 취급 — 한 실험
3. **holdout 우선 판정**: 2025-10-01 ~ 2026-04-10
4. **raw baseline 대비 delta 기준** 판정

### 비교 지표

- Total trades (baseline 대비 delta)
- Total PnL ($)
- Win rate (delta)
- R/trade (delta)
- Max drawdown (delta)
- per-symbol breakdown

### 판정 기준

| 조건 | 판정 |
|---|---|
| R/trade 개선 AND drawdown 악화 없음 | **PROMOTE** |
| R/trade 개선 AND drawdown 개선 | **STRONG PROMOTE** |
| R/trade 하락 but drawdown 개선 | 조건부 PROMOTE |
| R/trade 하락 AND drawdown 하락 | **KILL** |
| trade count 50% 이상 감소 | **WARNING** — 표본 부족, 2라운드 |

### "어떤 변경이 small unit, 어떤 것이 새 전략인가"

**Small unit**:
- 진입 조건에 게이트 추가 (ADX, EMA direction, squeeze duration)
- 청산 파라미터 변경 (stop_atr, exit_period)
- regime/volatility 필터 추가
- 단일 TF 다변수 튜닝

**새 전략**:
- 청산 철학 변경 (fixed TP → trailing, partial close)
- 진입 철학 변경 (첫 breakout → pullback re-entry)
- 두 전략의 entry+exit 조합 (hybrid)
- 새 indicator family 도입

### "규칙 기반만으로 충분히 개선 여지가 있는가"

**있음.** ML filter가 실패한 이유는 "이미 rule-based로 잘 정의된 signal space에
통계적 신호를 덧붙이기 어렵다"였음. 반대로 rule-based 개선은 signal space 자체를
더 정확하게 정의하는 것. BBKC/Donchian 같은 단순 전략에는 여전히 room이 있음.
특히 **HTF 맥락, 변동성 regime, breakout 강도**는 아직 rule로 편입 안 된 3가지
차원이고 각각 개선 여지 있음.

## 6. 최종 결론

### 판정: Donchian 2개 먼저 + BBKC 1개 병렬. **순서는 D2 → D1 → B1**

### 이유

1. **D2는 "실험"이라기보다 "복구"**: FixedRR에 빠진 EMA(200) trend filter를 채우는
   건 TrendFilter에서 이미 검증된 로직을 포팅하는 것. 실패 리스크 거의 0. 가장 싸고
   가장 확실한 개선
2. **D1은 정통 개선**: Donchian breakout에 ADX regime gate는 breakout 전략의 고전적
   개선 기법. ADX ≥ 20과 ≥ 25 동시 비교는 과최적화가 아니라 감도 확인
3. **B1은 BBKC의 구조적 빈틈 보강**: BBKC에 HTF 맥락이 전혀 없는 게 최대 약점.
   4h EMA 1개로 1-bit gate 걸면 역추세 release 거부 가능
4. **Donchian 2개 먼저**: tooling이 성숙해 있고 (`scripts/explore_donchian.py`),
   비교 대조군 (TrendFilter vs FixedRR)이 자연스럽게 존재. BBKC는 병렬 1개만
5. **B4는 2차 라운드**: 청산 철학 변경이라 B1 baseline 개선 확인 후에만
6. **Hybrid는 지금 이르다**: 약한 고리 메우는 단계를 먼저 완수

### 다음 액션 (5단계)

1. **[D2] Donchian FixedRR + EMA(200) trend filter 복구**
   - TrendFilter의 trend 로직을 FixedRR에 포팅
   - 같은 holdout 기간, baseline (FixedRR 원본) 대조
   - 판정: R/trade + drawdown 개선 확인
2. **[D1] DonchianTrendFilter + ADX (20, 25) 2 variant**
   - ADX ≥ 20, ADX ≥ 25 두 variant를 같이 실행
   - baseline (TrendFilter 원본) 대조
   - ADX 감도 곡선 관측
3. **[B1] BBKC + 4h EMA(50) trend filter 병렬**
   - 1h squeeze release + 4h EMA 방향 일치 조건
   - baseline (BBKC 원본) 대조
   - 역추세 release 거부 효과 확인
4. **결과 리포트 + PROMOTE 대상 선정**
   - 3개 실험 중 R/trade + drawdown 기준으로 1-2개 후보 추출
5. **후속 실험**
   - Donchian 쪽이 좋으면 추가 확장 (D5 HTF 확인, D7 slope gradient 등)
   - BBKC 쪽이 좋으면 [B2] squeeze duration 또는 [B4] ATR-adaptive TP/SL
   - 둘 다 부족할 때만 hybrid 검토

### 예상 소요

- Day 1: D2 + D1 구현/실행 (~3~4시간)
- Day 2: B1 구현/실행 + 3개 결과 정리 리포트 (~3~4시간)
- Day 3: 선정된 변형에 추가 실험 (조건부)

### 한 줄 결론

**D2(복구) → D1(정통 개선) → B1(구조적 빈틈 보강)** — 이 순서가 가장 낮은 리스크로
가장 큰 정보량을 얻는 경로. rule-based 개선 여지는 아직 남아 있음.
