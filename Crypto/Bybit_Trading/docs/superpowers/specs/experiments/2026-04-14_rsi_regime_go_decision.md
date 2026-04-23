# RSI Divergence Regime Research — GO Decision

**날짜**: 2026-04-14
**상태**: **GO** (research track 계속)
**선행 문서**:
- `docs/.../2026-04-14_rsi_regime_research_problem.md` (문제 재정의)
- `logs/research/rsi_regime/{events.csv,unconditional.json,report.json,cross_window_lifts.json,report.md}`

## 1. TL;DR

BTCUSDT daily 2021-01-01 ~ 2026-04-14 에서 confirmed RSI divergence
166건을 detect → 161건에 forward 20/40/60 일 regime label 부여 →
IS 128 / OOS 33 chronological split → lift 계산.

**5개의 (horizon, div_type, regime) triple이 IS/OOS 양쪽에서
동일 방향으로 lift 재현**. Trade-level ML 실패와 달리, **regime
formulation에서는 regularity가 존재**.

결론: **GO**. Research track 유지. 전략 연결은 여전히 **금지** (P9).

## 2. 핵심 cross-window lift (양 window 재현)

| horizon | div_type | regime | IS lift | OOS lift | IS n | OOS n | 해석 |
|---|---|---|---|---|---|---|---|
| 20 | regular_bear | UP | 0.27 | **0.00** | 43 | 6 | bearish divergence 발생 후 20일 내 강한 UP regime 거의 없음 |
| 20 | hidden_bull | DOWN | 0.23 | 0.66 | 34 | 6 | hidden bull 발생 후 20일 내 강한 DOWN regime 매우 드뭄 |
| 40 | regular_bear | UP | 0.73 | **0.00** | 43 | 6 | 40일 horizon에서도 UP 억제 효과 유지 |
| 40 | hidden_bull | DOWN | 0.59 | 0.56 | 34 | 6 | 40일 horizon에서도 DOWN 억제 효과 유지 |
| 60 | regular_bull | DOWN | 1.26 | **1.74** | 33 | 11 | regular bull 발생 후 60일 뒤엔 DOWN regime 확률 상승 (swing 특성) |

**관찰**:
- 2개의 short-horizon 관찰이 특히 강함:
  - regular_bear (bearish divergence at tops) → 20~40일 내 UP regime 억제
  - hidden_bull (trend-continuation long setup) → 20~40일 내 DOWN regime 억제
- 1개의 long-horizon 반전 효과:
  - regular_bull (bullish divergence at bottoms) → 60일 horizon에서는
    DOWN regime 확률이 오히려 증가. 즉 "bottom 이후 충분한 시간이
    지나면 다음 swing down이 base rate보다 자주 찾아옴".
- 이 3가지는 전통적인 divergence 해석과 일치 — **20일 regime 효과는
  pattern school의 기대와 같은 방향**.

## 3. Trade-level ML 실패와의 구조적 차이

| 차원 | trade-level ML (KILL) | regime research (GO) |
|---|---|---|
| Label | TP-first / SL-first / timeout (triple barrier) | forward horizon regime class |
| Horizon | ATR/pct 기반 tight SL/TP 도달 | 20/40/60 일 고정 |
| Signal resolution | binary per event | 3-class distribution |
| 측정 대상 | per-event entry quality | marker의 population bias |
| IS/OOS 결과 | IS 유의미, OOS 무의미 | IS/OOS 모두 같은 방향 lift |
| n 요구 | 많음 (수백+) | 수십이면 방향성 가능 |

Trade-level ML은 divergence가 **즉시 거래 entry**로 사용 가능한지 물었고
답은 NO였다. Regime research는 divergence가 **다음 몇 주 시장 분포에
대한 context marker**인지 물었고 답은 YES (특히 20~40일 horizon).

두 질문의 답이 다른 것이 모순이 아니다. Divergence는 "지금 당장
entry로 삼을 수는 없지만 근시일 내 regime distribution을 shift하는
event"로 작동한다. Label과 horizon이 바뀌면 edge가 드러난다.

## 4. Selection bias 재확인

사용자 스크린샷 7건은 전부 성공 사례 (2021-01, 2021-05, 2021-10,
2022-06, 2023-08, 2025-02, 2025-05 케이스). 이들만 보면 divergence
가 "항상 맞다"고 착각하기 쉬움.

하지만 이번 research는 **스크린샷에 없는 나머지 159건**을 **자동으로
포함**했다 (166 detected - 7 screenshot = 159). Selection bias 없이
계산한 lift도 양 window에서 같은 방향으로 재현되었으므로, 스크린샷
사례가 "cherry pick"이 아니라 "밑바닥 패턴의 극단 예시"였다는 해석이
더 정합적이다.

단, OOS n이 6~11로 **작다**. 이 한계는 GO 판정에 제한 조건으로
기록.

## 5. 한계 / 경고

1. **OOS sample이 작음**: horizon 20 기준 각 type당 OOS n = 6~11.
   효과 크기는 크지만 통계적 유의성은 제한적. 충분한 bootstrap
   confidence interval을 잡으려면 다른 심볼 / 다른 period도 필요.
2. **BTC only**: ETH/SOL/LINK/AVAX 별도 검증 필요. P9 기준 다음 research
   iteration에서 확장.
3. **Selection by daily TF**: 1h/4h 실패의 원인 중 하나가 TF noise이면,
   1d에서의 성공은 단순히 smoothing 효과일 수 있음. 이 경우 "edge가
   signal에 있다"가 아니라 "edge가 TF에 있다"로 해석됨. 구별을 위해
   나중에 weekly TF 실험도 필요.
4. **Lift ≠ tradable edge**: lift가 1.5라고 해서 진입/청산 규칙이
   즉시 유효하다는 뜻이 아님. 규모/비용/타이밍이 별도 문제. P9 기준
   전략 연결 금지는 이 이유에서 유지.
5. **Stability 30일 관측 없음**: Research track 합류 조건 중 하나인
   "30일 이상 안정적 재현"은 아직 만족하지 않음.

## 6. GO 결정

GO 조건 (`research_problem.md §6`) 체크:

| 조건 | 충족 |
|---|---|
| 최소 한 horizon의 regular_bull 또는 regular_bear에서 IS/OOS lift ≥1.2 또는 ≤0.8 양쪽 재현 | ✓ (regular_bear h=20/40 UP, regular_bull h=60 DOWN) |
| IS 타입별 n ≥ 20 | ✓ (IS regular_bull 33, regular_bear 43, hidden_bull 34) |
| OOS 타입별 n ≥ 5 | ✓ (OOS 각 타입 6~11) |
| Hidden divergence 반대 신호 없음 | ✓ (hidden_bull h=20/40 DOWN 억제 일관) |

**판정: GO**

## 7. 다음 research iteration (이번 턴 밖)

GO 이후의 research 단계 — **전략 연결 금지 상태에서** 진행:

1. **더 많은 심볼**: ETH (2021-03-15~), SOL, LINK, AVAX 각각 동일 파이프라인
2. **Aggregation 전략**: BTC에서 signal fire 시 다른 심볼 forward regime도 살핌 (cross-asset)
3. **Feature conditioning**: rsi_zscore_200d, price_trend_100d_pct 같은
   feature로 divergence를 구간화 → 어떤 regime에서 divergence가 가장
   의미있는가
4. **Stability test**: 매일 report 재실행 → 30일 동안 lift drift 관찰
5. **다른 TF**: weekly divergence (7d confirmation) 실험

## 8. 금지사항 (이번 턴 + 다음 턴 모두)

- Live 전략과 연결 금지. 어떤 strategy 파일, execution 파일, backtester
  파일에도 `src/research/regime/` 를 import하지 말 것.
- BBKC/Donchian 진입/청산에 regime score를 inject하지 말 것.
- OOS n이 6~11에 불과한 현 결과를 근거로 어떤 operational parameter도
  바꾸지 말 것.
- Research 결과를 문서 이외의 장소 (realtime_monitor, paper trading)
  에 노출하지 말 것.
