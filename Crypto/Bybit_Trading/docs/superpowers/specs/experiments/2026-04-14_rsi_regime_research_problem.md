# RSI Divergence — Daily Regime Research Problem Statement

**날짜**: 2026-04-14
**트랙**: Parallel research (protocol §P9). 전략 연결 금지.
**스크린샷**: `docs/Screenshot/20210106~20210413.png` 외 6개.
**선행 결과**: `docs/superpowers/specs/ml/2026-04-14_rsi_divergence_2x2_tf_threshold_matrix.md`
— trade-level RSI divergence ML은 1h/4h/1d 전부 holdout KILL.

## 1. 이전 trade-level 실패가 무엇을 테스트했는가

이전 RSI divergence ML 파이프라인이 풀던 문제:

> "주어진 RSI divergence event를 진입 시점으로 사용해서 고정된 TP
> (4% pct / ATR mult) 가 SL보다 먼저 닿는 확률을 예측할 수 있는가?"

답: **아니오**.

왜 실패했는가 (docs/superpowers/specs/ml/2026-04-14_*):
- Label이 "진입 후 N bar 내 TP-first"라는 **아주 타이트한 trade
  outcome**에 한정됨.
- 1h/4h에서는 noise가 label을 지배하고 분리력 없음.
- 1d에서도 triple-barrier label은 고정 horizon이라 divergence의
  "regime transition" 성격을 놓침.
- Lookahead 버그, HTF alignment 버그, wrapper ATR parity 등 구조
  문제를 잡은 후에도 edge 없음.
- 결과: event-level holdout pnl_R 거의 0, filter_verdict
  NEUTRAL/DESTROYS 사이, trade-level에서 deployable edge 없음.

## 2. 지금 다시 묻는 것이 무엇이 다른가

같은 detector, 다른 **label**과 다른 **질문**.

> "확인된 daily RSI divergence가 발생한 후 N일 시점의 **forward
> regime** (크게 상승 / 횡보 / 크게 하락) 분포가 unconditional
> base rate와 structurally 다른가?"

차이점:

| 차원 | 이전 (trade-level) | 지금 (regime) |
|---|---|---|
| TF | 1h / 4h / 1d | **1d only** |
| Label | triple barrier (TP/SL/timeout) | forward horizon log return |
| 판정 기준 | per-event TP-first 확률 | per-event **regime class 분포** |
| 출력 | entry trigger / filter | descriptive lift vs base rate |
| 성공 기준 | holdout trade pnl > 0 | IS/OOS 교차 lift 재현 |
| 전략 연결 | 직접 진입/청산 | **없음** (research-only) |
| 시간 규모 | minutes-to-hours | **weeks** |

이전 실패가 "signal → 즉시 trade → TP/SL로 변환"에 대해 아무것도
말해주지 않는다는 것을 증명했다. 이번 질문은 signal이
**market context / regime-change marker**로서 의미가 있는지 묻는다.
두 질문이 동치가 아니므로 이전 KILL이 이번 NO-GO를 함의하지 않는다.

## 3. 스크린샷 사례 — selection bias 경고 포함

사용자가 `docs/Screenshot/` 에 넣은 7개 케이스:

- 2021-01-06 ~ 2021-04-13 (BTC 상승 divergence)
- 2021-05-18 ~ 2021-06-24 (BTC 하락 divergence)
- 2021-10-19 ~ 2021-11-08 (BTC top)
- 2022-06-18 ~ 2022-11-21 (BTC bottom 반전)
- 2023-08-17 ~ 2023-09-11 (BTC 횡보 → 반전)
- 2025-02-25 ~ 2025-04-06 (BTC 최근 bottom 반전)
- 2025-05-22 ~ 2025-10-06 (BTC 조정)

이 케이스들은 **성공 사례가 선택된 것**이다. Selection bias 관점에서:
- 긍정 사례만 있을 때 lift를 측정하면 당연히 인상적.
- Research 질문은 "모든 divergence를 잡았을 때도 lift가 있는가".
- 스크린샷은 **hypothesis source**로만 사용, validation에는 사용 금지.
- 실제 검증은 2021-01-01 ~ 2026-04-14 전체 daily 데이터(1930 bars BTC)
  를 자동 스캔한 결과로 수행.

## 4. Dataset

- Primary symbol: **BTCUSDT** (2021-01-01 ~ 2026-04-14, 1930 일봉)
- Confirm bars: 3 (양쪽 3일 확인) → 1 divergence event당 3일 지연
- Lookback: 30 bars (첫-두번째 pivot 최대 거리 30일)
- Horizons: 20, 40, 60 bars (forward log return)
- Base rate: 같은 기간 BTC 전체 일봉의 forward return 분포
- Class boundary: unconditional std의 ±0.5σ → DOWN / FLAT / UP

## 5. IS / OOS split

- Chronological split: 처음 80% 이벤트 = IS, 마지막 20% = OOS
- 2021-01 ~ 2025-08 근처가 IS, 2025-08 ~ 2026-04가 OOS로 자연 분할
- OOS는 2024-12 이후 시점을 최소 200일 이상 담도록 조정

## 6. 판정 기준

### GO 조건 (이 턴에서 GO면 research 계속)
- 최소 한 horizon에서 **regular_bull** 또는 **regular_bear**의
  lift가 IS와 OOS 양쪽에서 1.2 이상 (또는 0.8 이하) 로 재현
- 이벤트 수 충분 (각 타입 IS ≥ 20, OOS ≥ 5)
- Hidden divergence에 대해 강한 반대 신호 없음

### NO-GO 조건
- IS에서는 lift 있지만 OOS에서 반전 (기존 RSI ML 실패와 같은 패턴)
- 모든 lift가 0.9 ~ 1.1 범위 (no signal)
- 표본 부족 (각 타입 10건 미만)

### GO여도 금지사항
- Trade-level 전략 연결 금지 (P9)
- BBKC/Donchian과 결합 금지
- Live paper 결과 없이 production 배포 금지
- 30일 이상 안정적 재현 없이는 합류 논의 금지

## 7. 이번 턴에서 만드는 것

1. `src/research/regime/divergence_events.py` — detector 재사용
2. `src/research/regime/regime_labels.py` — forward horizon label
3. `src/research/regime/evaluator.py` — IS/OOS lift report
4. `scripts/train_rsi_regime.py` — BTC 데이터셋 빌드 + 저장
5. `scripts/evaluate_rsi_regime.py` — report 생성 + md 저장
6. 이 문서

## 8. 이번 턴에서 하지 않는 것

- 다른 코인 (ETH/SOL/LINK/AVAX) 개별 분석 — BTC 먼저
- ML 모델 학습 — rule-based lift만
- Threshold tuning — k_sigma 고정 0.5
- 전략 연결 / backtest 실행
- Feature 추가 (시장 미시구조, fundings, OI 등)
