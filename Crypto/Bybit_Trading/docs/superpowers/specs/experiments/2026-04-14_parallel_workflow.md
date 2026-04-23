# Parallel Workflow Summary — Main Track + RSI Regime Research

**날짜**: 2026-04-14
**목적**: 메인 트랙 (규칙 기반 전략 승격) 과 보조 RSI regime research
트랙이 동시에 진행될 때의 운영 원칙과 다음 스텝을 한 장으로 확정.

## A. 메인 트랙

### 현재 확정 상태 (2026-04-14 기준)

| 항목 | 상태 |
|---|---|
| D2 class (`DonchianFixedRRTrendFilter`) | **WINDOW-DEPENDENT** (OOS2 KILL, 승격 취소) |
| D2 best grid cell | **ARCHIVED** |
| DonchianFixedRR family 전체 | operational baseline에서 제외 |
| BBKCSqueeze[ALL5] | CONTROL / DEGRADED (OOS2 net loss) |
| BBKCSqueeze[BIGTHREE] | **STAGED PROMOTE** (2-window PROMOTE, paper 대기) |
| DonchianTrendFilter | 보조 control 유지 |
| DonchianTrendFilterADX20/25 | KILL 유지 |
| BBKCSqueezeHTFTrend | KILL 유지 |
| ML patterns (RSI/Engulfing/BBKC filter) | trade-level KILL 유지 |

### 다음 즉시 실행 가능 단계

1. **BBKC BIGTHREE development 교체** (코드 변경 없음, universe
   선택만). 즉시 수행 가능.
2. **BBKC BIGTHREE paper trading 2주** (별도 인프라). Gate 2
   production 배포 조건.
3. **Gate 1 D1 extension** — DonchianTrendFilter에 breakout strength
   gate 실험 (ADX 재도전 금지).
4. **Gate 2 BBKC exit-layer development 실험** — ATR-adaptive TP/SL
   등. production 배포 금지.

### 절대 금지 (protocol P5/P6/P7/P8)
- D2 grid 재확장
- D2 파라미터를 운영 default로 승격
- BBKC entry logic 수정
- BBKC HTF gate / ML filter 재도전
- DonchianTrendFilterADX variants 재실행
- `DonchianFixedRR`을 신규 실험 baseline으로 사용

## B. RSI regime research 트랙

### 현재 확정 상태

| 항목 | 상태 |
|---|---|
| 문제 재정의 (trade-level → daily regime) | 문서화 완료 |
| 데이터 (BTC daily 2021-01-01 ~ 2026-04-14, 1930 bars) | 수집 완료 |
| Event detection (166 events, 161 labeled) | 구현 완료 |
| IS/OOS lift report | 구현 완료 |
| Cross-window strong lift (5 triples) | 확인됨 |
| GO/NO-GO 판정 | **GO** |

### 분리 원칙 (protocol §P9)

- **Code 경계**: `src/research/regime/` 하위만 건드림.
  `src/strategies/`, `src/execution/`, `src/backtester/`, `src/evaluation/`
  에서 import 금지.
- **Artifact 경계**: `logs/research/rsi_regime/`. 트레이드 artifact
  (`logs/d2_*`, `logs/bbkc_*`)와 섞지 말 것.
- **Script 경계**: `scripts/train_rsi_regime.py`,
  `scripts/evaluate_rsi_regime.py`. Trade-level 스크립트와 이름 공유
  금지.
- **실행 흐름 경계**: orchestrator (`run_rule_based_experiments.py`)에
  연결 금지.
- **의사결정 경계**: GO/NO-GO는 lift 수치와 OOS 재현성만 기준.
  strategy 성과에 영향 주지 않음.

### 다음 research iteration 후보 (여전히 분리)

1. ETH / SOL / LINK / AVAX 각각 동일 파이프라인
2. Feature conditioning (rsi_zscore_200d 등)
3. Weekly TF divergence
4. 30일 stability test (매일 재실행)
5. Cross-asset aggregation (BTC signal fire 시 다른 심볼 forward regime 관찰)

### 전략 연결이 허용되는 조건 (모두 AND)

1. Research 결과가 **2개 이상 심볼**에서 교차 재현
2. **30일 이상** 매일 재실행했을 때 lift drift 없음
3. Protocol 변경 문서 (new P-section) 작성 + 승인
4. Trade-level comparator (D2 style)에 연결 전 OOS bar-level 검증
5. 메인 트랙이 안정 (BBKC BIGTHREE paper trading 통과 후)

**현재는 이 조건들 하나도 만족하지 않음**. 따라서 전략 연결 금지.

## C. 병렬 운영 원칙

1. **메인 트랙 우선**: 메인 트랙 의사결정이 막히면 research 트랙을
   그 의사결정 자리에 끼워넣지 말 것. Research는 독립 정보원이지
   대체 의사결정기가 아니다.
2. **Shared 코드 수정 금지**: research가 `src/ml/helpers/divergence.py`를
   사용한다고 해서 이 파일의 시그니처를 바꾸지 말 것. 읽기 전용 의존.
3. **시간 budget 분리**: research는 메인 트랙 완료 후의 남은 시간에만.
   메인 트랙 작업 중 research가 떠올라도 중단하지 말 것.
4. **승격 경로 완전 분리**: 메인 트랙 승격은 holdout + verdict + paper.
   Research 결과가 "좋아 보여서" 메인 트랙 승격 단축 금지.
5. **문서 분리**: 이 문서와 protocol §P9가 유일한 공식 경계. 메인
   트랙 문서에서 research 결과를 근거로 판정 변경 금지.

## D. 재현 커맨드 (한 장 요약)

```bash
# 메인 트랙 — 전체 재생성
python -m scripts.run_rule_based_experiments
python -m scripts.d2_core_eval    --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/d2_core_oos2
python -m scripts.bbkc_universe_eval --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/bbkc_universe_oos2
python -m scripts.holdout_verdict logs/variant_round1/results.json --auto-pairs

# 메인 트랙 — D2 grid (이미 완료, resume 즉시 종료)
python -m scripts.d2_grid --resume

# Daily 데이터 백필 (이미 완료, 재실행 시 upsert 중복 없음)
python -m scripts.collect_daily_history

# RSI regime research 트랙
python -m scripts.train_rsi_regime
python -m scripts.evaluate_rsi_regime
```

## E. 변경 이력

- **2026-04-14 (초판)**: D2 OOS2 KILL, BBKC BIGTHREE STAGED PROMOTE,
  RSI regime GO 판정. 이 세 결과를 한 문서에 고정.
