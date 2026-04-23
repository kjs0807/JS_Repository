# Rule-Based Experiment Workflow — Execution Guide

**날짜**: 2026-04-14
**대상**: `Trading/Bybit_Trading`
**선행 문서**: `2026-04-14_experiment_protocol.md`
**목적**: "이 순서대로 돌리면 된다" 수준의 재현 가능한 실행 가이드

## 0. 공통 변수

- **holdout 창**: `2025-10-01 ~ 2026-04-10`
- **universe (default)**: `BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT, AVAXUSDT`
- **warmup**: 14 days
- **initial capital**: $10,000
- **timeframe**: 1h (primary)

각 스크립트는 CLI 인자로 바꿀 수 있지만 **기본값을 바꾸지 말 것**.
기본값을 바꾸면 round 1 결과와 비교가 깨진다.

## 1. 실행 순서 (필수)

```
Step 0.  state 확인 (D2 grid 완료 여부 포함)
Step 1.  orchestrator 시동  (D2 core + BBKC universe + round1 verdict)
Step 2.  D2 grid sweep       (2026-04-14 완료, resume으로 재실행 가능)
Step 3.  D2 2차 OOS (다른 window)   [Gate: top cell 승격 결정용]
Step 4.  BBKC BIGTHREE 2차 OOS      [Gate: universe 승격 결정용]
Step 5.  (Gate 2) BBKC exit-layer 실험
Step 6.  (Gate 1) D1 extension 실험
Step 7.  (Gate 3) filter-type ML bar-level comparator
```

**Gate 조건은 `2026-04-14_experiment_protocol.md §7`을 참조**. Gate를
우회하고 실험을 실행하는 것은 금지.

### Step 1. orchestrator 시동 (1 command)

```bash
python -m scripts.run_rule_based_experiments
```

포함된 stages (기본값):
- `d2_core_eval`
- `bbkc_universe_eval`
- `round1_holdout_verdict` (기존 `logs/variant_round1/results.json` 재활용)

생성되는 파일:
- `logs/rule_based_runner/d2_core_eval.log`
- `logs/rule_based_runner/bbkc_universe_eval.log`
- `logs/rule_based_runner/round1_holdout_verdict.log`
- `logs/rule_based_runner/round1_verdict.md`
- `logs/rule_based_runner/round1_verdict.json`
- `logs/d2_core/results.json`, `logs/d2_core/verdict.json`
- `logs/bbkc_universe/results.json`, `logs/bbkc_universe/verdicts.json`
- `logs/rule_based_manifest.json`

옵션:
- `--with-grid` — Step 5 (D2 grid)도 이어서 실행
- `--grid-max-cells 20` — grid smoke test용
- `--skip-d2-core` / `--skip-bbkc-universe` — 특정 stage만 실행

### Step 2. Round 1 verdict 확인

```bash
python -m scripts.holdout_verdict logs/variant_round1/results.json --auto-pairs
```

기대 출력 (사전 결정):

| Variant | Baseline | 예상 판정 |
|---|---|---|
| DonchianFixedRRTrendFilter | DonchianFixedRR | PROMOTE 또는 CONDITIONAL_PROMOTE |
| DonchianTrendFilterADX20   | DonchianTrendFilter | KILL |
| DonchianTrendFilterADX25   | DonchianTrendFilter | KILL (prior flip) |
| BBKCSqueezeHTFTrend        | BBKCSqueeze | KILL |

여기서 숫자가 크게 다르면 이후 모든 단계가 의심스럽다 (엔진/DB 변경).

### Step 3. D2 core 재평가

```bash
python -m scripts.d2_core_eval
```

**검토 포인트**:
- `DonchianFixedRRTrendFilter` aggregate avg_trade_pnl > `DonchianFixedRR`?
- max_drawdown 개선 폭?
- 심볼별 breakdown — SOL/LINK에서 0 trades면 EMA filter가 역할한 것

**통과 조건 (변형 promotion)**:
- `judge_variant_vs_baseline` 결과가 `PROMOTE` 또는
  `CONDITIONAL_PROMOTE`
- 심볼 편중 < 65% (verdict 체크에 포함됨)

**통과 시**: D2를 FixedRR 계열의 새 baseline 후보로 간주하고 Step 5
(grid) 진행 가능.

**실패 시 (KILL/INSUFFICIENT_DATA)**: D2 자체를 접고 PHASE 6 (D1
extension)로 넘어가기 전에 왜 실패했는지 기록.

### Step 4. BBKC universe 실험

```bash
python -m scripts.bbkc_universe_eval
```

5개의 사전 결정된 universe를 ALL5 baseline과 비교한다:
- `ALL5` (baseline)
- `BTCETH`
- `BIGTHREE` (BTC+ETH+AVAX)
- `EXCLUDE_SOL`
- `EXCLUDE_SOL_LINK`

**검토 포인트**:
- avg_trade_pnl 개선이 0.5$ 노이즈 이상인지
- max_drawdown 개선 폭
- trade count 축소 폭 (trade count가 30% 이상 줄면 warning 표본)

**주의**: universe 선택은 본질적으로 과최적화에 가깝다. 이 실험의 목적은
"구조적으로 악한 심볼을 제외하면 robustness가 개선되는가"를 측정하는
것이지 "가장 좋은 조합을 고르는 것"이 아니다. 판정 기준은:

- PROMOTE: aggregate 개선 + drawdown 개선 + 3 symbols 이상 남음
- CONDITIONAL_PROMOTE: 한 쪽만 개선
- KILL: 양쪽 악화 또는 symbol 너무 적음

### Step 2. D2 grid sweep (2026-04-14 완료)

```bash
python -m scripts.d2_grid --resume
```

빠른 smoke:
```bash
python -m scripts.d2_grid --max-cells 20 --resume
```

486 cells (`3*3*3*3*3*2`). 기존 `logs/d2_grid/cells.jsonl`는 이미
486개 행 모두 보존되어 있으므로 위 명령은 resume 상태에서 즉시 완료.

**현재 결과 요약** (`docs/.../2026-04-14_d2_grid_results.md` 참조):
- PROMOTE: 300 / 486 (62%)
- CONDITIONAL_PROMOTE: 48
- KILL: 121
- INSUFFICIENT_DATA: 9
- NO_EDGE: 8

**결론**:
- `DonchianFixedRRTrendFilter` 를 FixedRR 계열 **새 baseline으로 승격**
- `DonchianFixedRR`은 **deprecated** (신규 실험 baseline 사용 금지)
- Top cell 파라미터는 아직 development candidate — 2차 OOS 검증 전까지
  운영 사용 금지

생성물:
- `logs/d2_grid/baseline.json`
- `logs/d2_grid/cells.jsonl`
- `logs/d2_grid/summary.md`
- `logs/d2_grid/best_cell_detail.json`

### Step 3. D2 2차 OOS — **2026-04-14 완료, 결과: KILL**

```bash
python -m scripts.d2_core_eval \
    --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/d2_core_oos2
```

**결과 요약** (상세: `docs/.../2026-04-14_oos2_results.md §1`):
- memo-fixed default: **KILL** — Δpnl -$418, Δmdd +17.5%p
- best grid cell: **KILL** — Δmdd +7.5%p, new symbol prior
- **D2 class는 WINDOW-DEPENDENT로 격하** (정식 승격 취소)
- 구체 원인: OOS1의 SOL/LINK 차단 효과가 OOS2 window에서 없고,
  대신 trend filter가 AVAX/BTC 진입을 허용해 drawdown 폭증.

**후속**: FixedRR family는 operational baseline에서 제외.
`logs/d2_grid/*`는 역사 기록. D2 grid 재확장 금지 (P6 참조).

### Step 4. BBKC BIGTHREE 2차 OOS — **2026-04-14 완료, 결과: PROMOTE**

```bash
python -m scripts.bbkc_universe_eval \
    --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/bbkc_universe_oos2
```

**결과 요약** (상세: `docs/.../2026-04-14_oos2_results.md §2`):
- ALL5 자체: net loss -$515 (regime-dependent)
- BIGTHREE: **+$1030 PROMOTE**, Δpnl +$1545, Δmdd -5.8%p
- 2-window 교차 검증 성공 (OOS1과 OOS2 모두 PROMOTE)
- **BIGTHREE 상태: STAGED PROMOTE (paper 검증만 남음)**

**후속**:
- Development/staging 환경은 즉시 BIGTHREE로 교체 가능
- Production 배포는 paper trading 2주 후에만 (P8)
- Gate 2 (BBKC exit-layer) 조건부 개방 — development only 태그

### Step 5. (Gate 2) BBKC exit-layer

**Gate 2 조건**: Step 4 통과 (BIGTHREE 운영 후보 확정), entry parity
유지 가능, exit만 변경.

**금지**: HTF entry gate / ML filter / hybrid entry 변경. 자세한 조건은
`2026-04-14_experiment_protocol.md §7 Gate 2`.

허용 예시:
- ATR-adaptive TP/SL: `tp = entry ± k_tp * atr`, `sl = entry ± k_sl * atr`
- Break-even 이후 trailing: 1R 도달 시 SL을 entry로 이동

스크립트 생성 경로: `scripts/bbkc_exit_<variant>_eval.py` — 기존
`bbkc_universe_eval.py`의 BIGTHREE spec을 고정 baseline으로 사용.

### Step 6. (Gate 1) D1 extension

**Gate 1 조건**: Step 3 통과 + D2가 추가 개선 여지 적음 + Donchian에
투자 가치 남음. `2026-04-14_experiment_protocol.md §7 Gate 1` 참조.

허용 축 (한 번에 하나만):
- Breakout strength gate
- EMA slope gate
- Entry/exit period 소폭 조정 (±5)

**금지**: ADX variants 재실행 금지.

스크립트 생성 경로: `scripts/d1_extension_<axis>_eval.py`.

### Step 7. (Gate 3) filter-type ML bar-level comparator

**Gate 3 조건**: 새 filter-type pattern이 D1 event-level PASS. 기존
KILL 패턴 재도전 금지. `2026-04-14_experiment_protocol.md §7 Gate 3`.

```bash
python -m scripts.compare_ml_vs_baseline \
    --pattern <new_filter> \
    --artifact-dir logs/ml_<pattern>/<run_id>
```

`FILTER_WRAPPER_REGISTRY`에 새 entry 추가 필요.

## 2. 생성물 위치 (한 장 요약)

```
logs/
├── variant_round1/
│   └── results.json           -- round 1 (이미 존재)
├── d2_core/
│   ├── results.json
│   └── verdict.json
├── d2_grid/
│   ├── baseline.json
│   ├── cells.jsonl
│   └── summary.md
├── bbkc_universe/
│   ├── results.json
│   └── verdicts.json
├── bar_level_comparison/
│   └── <pattern>.json         -- 조건부
├── rule_based_runner/
│   ├── d2_core_eval.log
│   ├── bbkc_universe_eval.log
│   ├── round1_holdout_verdict.log
│   ├── round1_verdict.md
│   └── round1_verdict.json
└── rule_based_manifest.json   -- orchestrator metadata
```

## 3. 판정 → 다음 스텝 Decision Tree (2026-04-14 OOS2 반영)

```
Step 2 (D2 grid)            [완료 2026-04-14, 300/486 PROMOTE]
Step 3 (D2 OOS2)            [완료 2026-04-14, KILL]
    -> D2 class 승격 취소 → WINDOW-DEPENDENT
    -> D2 best cell archived
    -> FixedRR family는 operational baseline에서 제외
    -> Gate 1 진입 조건은 FixedRR 제외 상태에서 독립 판단

Step 4 (BBKC BIGTHREE OOS2) [완료 2026-04-14, PROMOTE]
    -> BIGTHREE STAGED PROMOTE (paper 검증 대기)
    -> ALL5는 OOS2 net loss → regime-dependent
    -> Gate 2 조건부 개방 (development only)

Gate 1 (D1 extension)
  현재 조건: D2 OOS2 KILL이 오히려 D1 축을 "유일 Donchian 후보"로
             만듦. DonchianTrendFilter control로 고정한 실험 가능.
  허용: breakout strength / EMA slope / period 소폭 조정 (하나만)
  금지: ADX variants 재실행

Gate 2 (BBKC exit-layer)
  현재 조건: BIGTHREE STAGED PROMOTE → development 실험만 허용
  허용: ATR-adaptive TP/SL / break-even trailing / 1-bit exit 변경
  금지: HTF entry / ML filter / hybrid entry
  production 배포는 paper 검증 후

Gate 3 (filter-type ML comparator)
  조건: 새 filter-type pattern + D1 event-level PASS
  금지: KILL된 기존 pattern 재학습

RSI regime research (parallel track, P9)
  조건: `src/research/regime/` 완전 분리 / trade-level 연결 금지
  분리 원칙은 protocol §P9
```

## 4. 실험 네이밍 규칙

- **전략 이름**: `STRATEGY_CONFIGS` 키 그대로 (e.g. `DonchianFixedRRTrendFilter`).
- **variant label**: `<STRATEGY>[<qualifier>]` (e.g. `BBKCSqueeze[BTCETH]`).
- **로그 디렉토리**: `logs/<experiment_label>/` 하위. snake_case.
- **판정 파일**: `verdict.json` (단일) 또는 `verdicts.json` (리스트).

## 5. 재현성

모든 experiment script는 다음 CLI 플래그를 공통 지원한다:

- `--start YYYY-MM-DD`
- `--end YYYY-MM-DD`
- `--warmup-days N`

같은 파라미터를 같은 DB에 돌리면 deterministic. 재현이 안 되면
`src/backtester/engine.py` 또는 `src/execution/backtest_broker.py`가
의도치 않게 바뀐 것이므로 우선 diff를 확인.

## 6. 한 줄 요약

```
python -m scripts.run_rule_based_experiments
```

이 한 줄로 Step 1-4가 전부 돌아간다. Step 5는 `--with-grid`, 나머지는
조건부.
