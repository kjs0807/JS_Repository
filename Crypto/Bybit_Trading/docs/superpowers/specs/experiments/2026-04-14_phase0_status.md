# PHASE 0 사실 확인 요약 — 2026-04-14

**목적**: 이후 실험 로드맵(PHASE 1~9)의 출발점이 되는 코드베이스 상태를
한 장으로 고정한다. 대화 의존성을 없애 나중에 그대로 다시 읽을 수 있게 한다.

## 1. 현재 라이브 전략 파일

| 파일 | 클래스 | 상태 |
|---|---|---|
| `src/strategies/bbkc_squeeze.py` | `BBKCSqueeze` | 활성 (control) |
| `src/strategies/bbkc_squeeze_htf_trend.py` | `BBKCSqueezeHTFTrend` | KILL (round 1) |
| `src/strategies/donchian_trend_filter.py` | `DonchianTrendFilter` | 활성 (약함, BTC 편중) |
| `src/strategies/donchian_fixed_rr.py` | `DonchianFixedRR` | 사실상 unusable (holdout -$2805) |
| `src/strategies/donchian_fixed_rr_trend_filter.py` | `DonchianFixedRRTrendFilter` | 새 baseline 후보 (round 1) |
| `src/strategies/donchian_trend_filter_adx.py` | ADX20, ADX25 | 둘 다 KILL |
| `src/strategies/pattern_ml_filter.py` | 랩퍼 | 보존만 (현재 사용 계획 없음) |
| `src/strategies/registry_builder.py` | (메타) | 활성 |

테스트:
- 전략별 유닛 테스트 + `test_variants_engine_smoke.py` (4 variants).
- `test_registry_builder.py`는 `EXPECTED_STRATEGIES = 7`로 하드코딩됨.

## 2. Round 1 holdout 결과 (`logs/variant_round1/results.json`)

holdout: `2025-10-01 ~ 2026-04-10`, 5 symbols, 14d warmup, $10k initial.

| 전략 | n | pnl | WR | MDD | 해석 |
|---|---|---|---|---|---|
| DonchianFixedRR            | 137 | -$2805 | 50.4% | 39.3% | broken — trend filter 부재 |
| DonchianFixedRRTrendFilter |  74 |  -$296 | 56.8% | 19.6% | cleaner, 여전히 net loss |
| DonchianTrendFilter        |  76 |  -$174 | 25.0% | 20.8% | BTC 단독 (+$428), 나머지 손실 |
| DonchianTrendFilterADX20   |  76 | -$1412 | 23.7% | 27.6% | ADX 게이트 역효과 |
| DonchianTrendFilterADX25   | 212 | +$1362 | 34.9% | 27.0% | LINK/AVAX prior flip |
| BBKCSqueeze                | 184 | +$3700 | 60.9% | 16.7% | **유일한 robust** |
| BBKCSqueezeHTFTrend        | 102 |  +$732 | 57.8% | 13.6% | BTC (+$1616 → -$516) destroyed |

## 3. 평가 인프라 현황

| 파일 | 역할 | 상태 |
|---|---|---|
| `src/ml/validator.py::evaluate_holdout` | event-level holdout + D1 filter_verdict | 활성 |
| `src/ml/validator.py::HoldoutReport` | event-level report dataclass | 활성 |
| `src/ml/report.py::_final_verdict` | event-level top-level verdict | 활성 |
| `scripts/compare_variants_round1.py` | 5x7 holdout 실행기 | 활성 (reference) |
| `scripts/strategy_verdict.py` | 구형 fine/walkforward/overfit verdict | **아카이브** (holdout-first 철학과 충돌) |
| `scripts/donchian_verdict.py` | 구형 donchian용 verdict | **아카이브** |
| `scripts/explore_strategy.py` | coarse/fine/wf/overfit explore pipeline | 유지 (grid search 전용) |
| `scripts/explore_donchian.py` | 동일 | 유지 |

**신규로 필요했던 것** (PHASE 2에서 구현됨):
- holdout-first rule-based verdict CLI → `scripts/holdout_verdict.py`
- bar-level filter 판정 (D2) → `src/evaluation/bar_level_comparison.py` + `scripts/compare_ml_vs_baseline.py`
- 공통 holdout 실행 헬퍼 → `src/evaluation/holdout.py`
- judge 규칙 공통화 → `src/evaluation/verdict.py`
- 오케스트레이터 → `scripts/run_rule_based_experiments.py`

## 4. 즉시 실행 가능한 실험

PHASE 2 구현 완료 후:
- D2 재평가 → `scripts/d2_core_eval.py`
- BBKC universe subset → `scripts/bbkc_universe_eval.py`
- D2 grid sweep → `scripts/d2_grid.py`
- 위 전체 오케스트레이션 → `scripts/run_rule_based_experiments.py`
- round 1 verdict 재생성 → `scripts/holdout_verdict.py logs/variant_round1/results.json --auto-pairs`

## 5. 코드 보완이 먼저 필요한 실험

| 실험 | 왜 보완 먼저 |
|---|---|
| filter-type ML 패턴 재개 | D2 bar-level comparator가 있어야 판정 가능 (완료) |
| D1 extension (breakout strength / ema slope gate) | D2 결과 확정 이후 스크립트 추가 필요 |
| BBKC exit-layer 실험 (ATR-adaptive TP/SL) | 새 strategy 서브클래스 + 테스트가 필요. baseline unchanged |

## 6. Verdict 체계의 기존 문제점과 해결

| 문제 | 증거 | 해결 |
|---|---|---|
| fine/walkforward 기반 verdict가 holdout-first 철학 무시 | `strategy_verdict.py` / `donchian_verdict.py` | **아카이브** 처리 + `holdout_verdict.py` 신규 |
| round 1 결과가 수동 해석에 의존 | "D2 PROMOTE 조건부" 같은 판정이 글자로만 존재 | `judge_variant_vs_baseline` 규칙 코드화 |
| filter-type 실험 시 event-level / bar-level 충돌 | BBKC filter Day 2 사례 | D2 comparator 구현 |
| 공통 metric 계산이 scripts 사이에 중복됨 | `compare_variants_round1.py` 내부 함수들 | `src/evaluation/holdout.py`로 공통화 |

## 7. 결론

- Round 1 결과는 그대로 유효.
- 규칙 기반 실험을 위한 holdout-first 인프라가 이 라운드에서 완성됨.
- 다음 단계는 `docs/superpowers/specs/experiments/2026-04-14_rule_based_experiment_workflow.md` 참조.
