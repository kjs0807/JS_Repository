# BBKC Exit Strategy Round 4 — Design

**날짜**: 2026-04-29
**상태**: 설계 승인 대기 (브레인스토밍 완료, 구현 미착수)
**선행 작업**:
- 라운드 3 (`2026-04-28_bbkc_exit_round3_design.md`, 머지 `047dfd9`) §15 결과
- `set_params` invariant fix (`a3dd4fe`)

**목표**: 라운드 3에서 STRONG_PROMOTE 받은 ETH × TF_early(`be=0.30/start=0.60/dist=0.30`)가 robust 영역인지 cherry-pick인지 확인. 주변 fine grid sweep으로 답한다.

---

## 1. 배경 (라운드 3에서 이월)

라운드 3 결과 (8 archetype × BIGTHREE × 9 WF):
- **ETH × TF_early**: wf 4/9 → 6/9, R/trade +0.024 → +0.064, 단일 STRONG_PROMOTE
- BTC: 6 archetype 모두 NEUTRAL (trailing-neutral)
- AVAX: TF_late만 NEUTRAL, 나머지 5 + TR 2 = 7개 KILL (trailing-hostile)
- drop_tp 가설 기각 (모든 TR_*가 TF_*와 동등 또는 열등)

⚠️ **Cherry-pick 위험**: TF_early 한 셀만 ETH에서 작동. 인접 셀들도 같이 좋으면 robust, TF_early만 외딴섬이면 우연.

---

## 2. 목표

명시적 질문:
> "ETH × TF_early가 robust 영역(인접 셀 다수 promote)의 한 점인가, 아니면 우연히 좋은 외딴섬인가?"

질문이 아닌 것:
- BTC/AVAX 최적화 (라운드 3에서 trailing-neutral/hostile 확정)
- time_stop 정밀화 (라운드 5로 이월)
- 새 청산 primitive (partial TP 등)
- drop_tp 추가 검증 (라운드 3에서 기각)

판정 단위: per-symbol verdict (라운드 3 동일) + **per-cell 통합 라벨** (신규).

---

## 3. 스코프

### IN

1. `src/strategies/registry_builder.py`:
   - `exit_round_grid` 라운드 3의 8셀 → **28셀로 교체**
   - 정책: `exit_round_grid`는 항상 **최신 라운드의 fine sweep 매트릭스**를 가리킴. 이전 라운드의 archetype grid는 코드에 보존하지 않음 (결과 파일 + 설계 문서 §15에만 보존). `bbkc_exit_eval.py`는 이 키를 무조건 참조하므로, 라운드 갱신 시 이 키를 통째로 교체하는 것이 의도된 흐름.
2. `scripts/bbkc_exit_eval.py`:
   - `judge`에 per-cell 통합 라벨 함수 추가 (per-symbol verdict는 라운드 3 그대로)
   - `build_report`에 reproducibility sanity 블록 + per-symbol 3×3 heatmap 9개 + 통합 라벨 컬럼
   - 모듈 docstring + report 제목 "Round 3" → "Round 4"
3. 단위 테스트 갱신/신규:
   - `tests/test_strategies/test_registry_builder_exit_grid.py` (28셀 + cell_id naming)
   - `tests/test_scripts/test_bbkc_exit_eval_judge.py` (통합 라벨 6 케이스 + warning 플래그)

### OUT (라운드 5 이후)

- ETH time_stop 정밀 sweep
- 라이브 운영 정책 결정 (심볼별 다른 exit_mode)
- BTC/AVAX 별도 청산 전략 탐색
- legacy `_legacy/` 변경
- 13코인 일반화

---

## 4. 셀 매트릭스 (28셀)

Full 3×3×3 grid + F0 baseline. 셀 ID: systematic naming `be{XX}_st{YY}_di{ZZ}` (2자리, 소수점 없음).

### 4.1 27 fine cells

```
trail_be_at_tp_frac     ∈ {0.25, 0.30, 0.35}
trail_start_at_tp_frac  ∈ {0.50, 0.60, 0.70}
trail_distance_tp_frac  ∈ {0.20, 0.30, 0.40}
```

`trail_be < trail_start` invariant 자동 만족 (max trail_be=0.35 < min trail_start=0.50).

전체 셀:
```
be25_st50_di20, be25_st50_di30, be25_st50_di40,
be25_st60_di20, be25_st60_di30, be25_st60_di40,
be25_st70_di20, be25_st70_di30, be25_st70_di40,
be30_st50_di20, be30_st50_di30, be30_st50_di40,
be30_st60_di20, be30_st60_di30, be30_st60_di40,   ← be30_st60_di30 = TF_early 재현
be30_st70_di20, be30_st70_di30, be30_st70_di40,
be35_st50_di20, be35_st50_di30, be35_st50_di40,
be35_st60_di20, be35_st60_di30, be35_st60_di40,
be35_st70_di20, be35_st70_di30, be35_st70_di40,
```

### 4.2 F0 baseline (1셀)

`exit_mode="fixed"`, `time_stop_bars=0`, `drop_tp=False`. 라운드 2/3과 동일 baseline.

### 4.3 고정 파라미터 (모든 셀 공통)

- `exit_mode = "be_trail"` (F0 제외)
- `drop_tp = False` (라운드 3에서 기각)
- `time_stop_bars = 0` (라운드 5로 이월)
- 지표 파라미터: 2026-03-30 winner (`bb_period=20, bb_std=1.5, kc_mult=1.0, rsi_filter=70.0, tp_pct=0.06, sl_pct=0.07, leverage=3`)

---

## 5. Sweep 범위 (라운드 3과 동일)

| 항목 | 값 |
|---|---|
| 심볼 | BIGTHREE (BTCUSDT, ETHUSDT, AVAXUSDT) |
| 기간 | 2024-03-01 ~ 2026-04-30 |
| TF | 1h |
| WF | IS 6m / OOS 2m / step 2m, 9 윈도우 |
| 비용 | taker 0.055%, maker 0.02%, slippage 0.03% (BacktestConfig 기본) |

**총 평가량: 28 × 3 × 9 = 756 backtest run** (~3분 예상, 라운드 3 216 runs ~46초의 3.5배).

ETH는 primary, BTC/AVAX는 damage check.

출력: `logs/research/bbkc_squeeze/exit_round/<timestamp>/` + `latest/` (라운드 2/3과 동일).

---

## 6. Gate (per-symbol + per-cell 통합)

### 6.1 per-symbol verdict (라운드 3 baseline-relative delta 그대로)

`judge()`의 per-symbol 분기는 변경 없음:
- BASELINE (cell == "F0")
- UNKNOWN (F0 없이 부분 실행)
- STRONG_PROMOTE / PROMOTE / NEUTRAL / KILL — **Δwf_oos+, Δr 중심 판정. DD ≤ baseline은 STRONG_PROMOTE의 추가 guard** (Δwf_oos+ ≥ 2 AND Δr ≥ 0 AND DD ≤ baseline 모두 충족 시 STRONG_PROMOTE; DD 단독으로는 등급 결정 안 함)
- WARNING (덧붙음, trade_count < baseline × 0.5)

### 6.2 per-cell 통합 라벨 (신규)

라운드 4의 핵심. 셀 단위로 per-symbol verdict 3개를 합쳐 1개 운영 라벨 산출.

```python
def integrate_label(cell_id: str, by_sym: dict[str, dict]) -> str:
    if cell_id == "F0":
        return "BASELINE"

    eth = by_sym.get("ETHUSDT", {})
    others = [by_sym.get("BTCUSDT", {}), by_sym.get("AVAXUSDT", {})]

    eth_promote = eth.get("verdict") in ("STRONG_PROMOTE", "PROMOTE")
    eth_warning = eth.get("warning") is True
    has_kill = any(o.get("verdict") == "KILL" for o in others)
    has_unknown_or_warning = any(
        o.get("verdict") == "UNKNOWN" or o.get("warning") is True
        for o in others
    )

    if eth_promote:
        # ETH가 promote라도 warning(샘플 부족 등)이면 ROBUST로 보내지 않음
        if eth_warning:
            return "ETH_PROMOTE_MIXED"
        if has_kill:
            return "ETH_ONLY_PROMOTE"
        if has_unknown_or_warning:
            return "ETH_PROMOTE_MIXED"
        return "ROBUST_PROMOTE"
    else:
        if has_kill:
            return "DAMAGING"
        return "NO_SIGNAL"
```

판정 우선순위 (위에서부터 매칭):

| 순위 | 라벨 | 조건 | 운영 의미 |
|---|---|---|---|
| 1 | BASELINE | cell_id == "F0" | F0 자기 자신 |
| 2 | ETH_PROMOTE_MIXED | ETH ∈ {SP, P} AND ETH warning=True | ETH 자체에 sample-size 우려 → 추가 검증 |
| 3 | ETH_ONLY_PROMOTE | ETH ∈ {SP, P} (warning=False) AND BTC 또는 AVAX 중 하나 KILL | ETH 전용 운영 후보 |
| 4 | ETH_PROMOTE_MIXED | ETH ∈ {SP, P} (warning=False) AND BTC 또는 AVAX에 UNKNOWN/warning AND KILL 없음 | 추가 검증 필요 |
| 5 | ROBUST_PROMOTE | ETH ∈ {SP, P} AND ETH/BTC/AVAX 모두 KILL/UNKNOWN/warning 아님 | 공통 BBKC exit 후보 |
| 6 | DAMAGING | ETH ∉ {SP, P} AND BTC 또는 AVAX 중 하나 KILL | 폐기 |
| 7 | NO_SIGNAL | 그 외 (ETH 이득 없음, 다른 심볼 손해도 없음) | 무시 |

**중요**: warning 플래그는 verdict 문자열과 분리됨. `m.get("warning") is True`로 별도 체크 (per-symbol m dict에 `verdict`와 `warning` 두 키 존재).

### 6.3 통합 라벨 분포가 라운드 4의 답

- ROBUST_PROMOTE 셀이 **여럿** = ETH × TF_early가 robust 영역. 채택 강력 후보
- ROBUST_PROMOTE 셀이 0개, ETH_ONLY_PROMOTE만 여럿 = ETH 전용 후보로 한정
- ROBUST_PROMOTE/ETH_ONLY_PROMOTE 둘 다 1-2개 = cherry-pick 위험. 라운드 5에서 추가 검증
- 위 모두 없음 = TF_early 자체가 우연. 청산 최적화는 다른 방향으로 전환

---

## 7. Reproducibility Sanity Check

`be30_st60_di30` × ETHUSDT 결과는 라운드 3의 TF_early × ETH와 정확히 같은 파라미터 집합. 같은 코드가 같은 데이터를 같은 WF 윈도우로 돌리므로 **결과가 거의 동일해야 함**.

### 7.1 기대값 (라운드 3 `2026-04-28_T2104/summary.json`의 `TF_early.ETHUSDT` 정확 float)

```python
EXPECTED_REPRODUCE = {
    "wf_oos_positive": 6,
    "mean_r_per_trade": 0.0635821965450038,    # report.md 반올림: +0.064
    "trade_count": 154,
    "max_dd": 0.11123736375303807,
    "mean_oos_pnl": 325.6180389395652,
}
```

구현 상수에는 위 exact float를 그대로 사용. 문서/리포트의 `+0.064`는 표시용 반올림.

### 7.2 Tolerance

완전 동일보다 작은 허용오차 둠 (수수료 부동소수점, 데이터 말단 처리, slippage 정밀도 등에서 ±1 trade 단위 차이는 무시):

```python
TOLERANCE = {
    "wf_oos_positive_exact": 6,           # 정수 비교, 정확히 일치
    "mean_r_per_trade_abs": 0.005,        # |Δ| ≤ 0.005
    "trade_count_abs": 2,                 # |Δ| ≤ 2
}

def sanity_match(reproduce: dict, expected: dict, tol: dict) -> tuple[bool, list[str]]:
    diffs = []
    if reproduce["wf_oos_positive"] != tol["wf_oos_positive_exact"]:
        diffs.append(f"wf {reproduce['wf_oos_positive']} != 6")
    if abs(reproduce["mean_r_per_trade"] - expected["mean_r_per_trade"]) > tol["mean_r_per_trade_abs"]:
        diffs.append(f"R {reproduce['mean_r_per_trade']:+.4f} vs {expected['mean_r_per_trade']:+.4f}")
    if abs(reproduce["trade_count"] - expected["trade_count"]) > tol["trade_count_abs"]:
        diffs.append(f"n {reproduce['trade_count']} vs {expected['trade_count']}")
    return len(diffs) == 0, diffs
```

### 7.3 Report 출력

`build_report` 첫 섹션:

```markdown
## Reproducibility Sanity (be30_st60_di30 × ETHUSDT vs Round 3 TF_early)

  Round 3 TF_early ETH: wf 6/9, R/trade +0.064, n=154
  Round 4 reproduce:    wf X/9, R/trade Y, n=Z
  Match: ✓        (또는 ✗ + diff list)
```

**불일치 시**: report 상단에 `⚠️ MATCH FAIL` 표시. 가능 원인:
- 코드 변경이 sweep 결과에 영향 (예: set_params fix가 새로 깨진 경로 도입)
- 데이터 갱신 (db에 새 봉 추가됨)
- 의존성 패치
디버그 시 라운드 3의 wf_results.jsonl과 셀 단위로 diff하면 어느 윈도우에서 갈라지는지 보임.

---

## 8. 출력 형식

### 8.1 파일 구조 (라운드 3과 동일)

```
logs/research/bbkc_squeeze/exit_round/
  <timestamp>/
    wf_results.jsonl
    auxiliary.json
    summary.json
    report.md
  latest/  ← 위 4개 파일 복사
```

### 8.2 report.md 구조 (확장)

```markdown
# BBKC Exit Round 4 — Sweep Report

Generated: <timestamp>

## Reproducibility Sanity (be30_st60_di30 × ETHUSDT vs Round 3 TF_early)
...

## Per-Cell Integrated Labels
| cell | label | ETH verdict | BTC verdict | AVAX verdict |
|---|---|---|---|---|
| F0 | BASELINE | ... | ... | ... |
| be25_st50_di20 | ROBUST_PROMOTE | PROMOTE | NEUTRAL | NEUTRAL |
| ...

## Label Distribution
ROBUST_PROMOTE: N1
ETH_ONLY_PROMOTE: N2
ETH_PROMOTE_MIXED: N3
DAMAGING: N4
NO_SIGNAL: N5
BASELINE: 1

## Per-Symbol × Distance Heatmaps (3×3 grid of be × start, per-symbol verdict per cell)

### ETHUSDT, dist=0.20
|        | st=0.50 | st=0.60 | st=0.70 |
|--------|---------|---------|---------|
| be=0.25| LABEL   | LABEL   | LABEL   |
| be=0.30| LABEL   | LABEL   | LABEL   |
| be=0.35| LABEL   | LABEL   | LABEL   |

### ETHUSDT, dist=0.30
... (same shape)

### ETHUSDT, dist=0.40
...
### BTCUSDT, dist=0.20
...
(9 heatmaps total = 3 symbols × 3 dist values)

## Per-Symbol Verdicts (라운드 3 형식 그대로)

### BTCUSDT
| Cell | WF OOS+/9 | R/trade | Max DD | Trades | Mean PnL | Verdict |
|---|---|---|---|---|---|---|
... (28 rows)

(ETHUSDT, AVAXUSDT 동일)

## Auxiliary Metrics (per cell × symbol)
... (라운드 3과 동일)
```

heatmap 셀 표시는 **per-symbol verdict 약자** 사용 (slice 단위로 의미 있음):
- `SP` = STRONG_PROMOTE
- `P`  = PROMOTE
- `N`  = NEUTRAL
- `K`  = KILL
- `B`  = BASELINE
- `U`  = UNKNOWN
- 뒤에 `*` 붙으면 warning 플래그 ON (e.g., `N*` = NEUTRAL with warning)

per-cell 통합 라벨은 §8.2 위쪽의 별도 "Per-Cell Integrated Labels" 표에 한 번만 표시. heatmap은 각 (symbol, distance) 슬라이스의 verdict 표면을 보여주는 도구.

---

## 9. 파일 변경 목록

### 새 파일

- `docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round4_design.md` (이 문서)

### 수정

- `src/strategies/registry_builder.py`
  - `STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]` 8셀 → 28셀
  - 라운드 3 archetypes 코드에서 제거 (결과 파일 + §15에 보존)

- `scripts/bbkc_exit_eval.py`
  - `judge()` per-symbol 그대로, **`integrate_label()` 헬퍼 추가**
  - `build_summary` 또는 `build_report`에 per-cell 통합 라벨 첨부
  - `build_report` 추가 섹션:
    - Reproducibility Sanity 블록 (§7.3)
    - Per-Cell Integrated Labels 테이블
    - Label Distribution 카운터
    - 9개 heatmap (3 symbols × 3 dist)
  - 모듈 docstring + 리포트 제목 "Round 3" → "Round 4"

- `tests/test_strategies/test_registry_builder_exit_grid.py`
  - 28셀 검증
  - cell_id 패턴 (`F0` 또는 `be{XX}_st{YY}_di{ZZ}`) 검증
  - 모든 fine cell의 drop_tp=False, time_stop_bars=0
  - 모든 fine cell이 invariant 통과 (be < start 항상 만족)

- `tests/test_scripts/test_bbkc_exit_eval_judge.py`
  - per-symbol delta 룰 8 케이스 (라운드 3 그대로 유지)
  - **신규**: integrate_label 6 케이스
    - F0 → BASELINE
    - ETH PROMOTE + BTC/AVAX NEUTRAL → ROBUST_PROMOTE
    - ETH PROMOTE + BTC KILL → ETH_ONLY_PROMOTE
    - ETH PROMOTE + BTC warning=True (verdict NEUTRAL) → ETH_PROMOTE_MIXED
    - ETH NEUTRAL + AVAX KILL → DAMAGING
    - ETH KILL + BTC/AVAX NEUTRAL → NO_SIGNAL

### 미변경 (확인용)

- `src/strategies/bbkc_squeeze.py` — 라운드 3 + set_params fix 그대로
- `_legacy/` — 영향 없음

---

## 10. 테스트 (TDD)

라운드 2/3 패턴 동일.

### 10.1 단위 테스트 추가

- registry exit_grid 28셀 검증 (셀 수, naming 패턴, 고정 파라미터)
- integrate_label 6 케이스 (위 §9에 명시)

### 10.2 통합 sanity (smoke run)

두 단계로 분리. `--smoke`는 첫 cell × 첫 symbol × 첫 window만 돌므로 grid 순서상 F0 × BTCUSDT × window 0 만 실행됨 (TF_early 셀 검증 불가).

1. **smoke**: `python -m scripts.bbkc_exit_eval --smoke`
   - 정상 종료 + 출력 4개 파일 생성 (wf_results.jsonl/auxiliary.json/summary.json/report.md)
   - F0 × BTCUSDT × window 0 결과로 파이프라인 동작 확인

2. **reproduction quick check**: `python -m scripts.bbkc_exit_eval --cell be30_st60_di30 --symbol ETHUSDT`
   - 9 윈도우 × 1 셀 × 1 심볼만 실행 (~수초)
   - report.md의 Reproducibility Sanity 섹션이 라운드 3 TF_early와 매치되는지 육안 확인
   - 단 F0 baseline이 없어 verdict는 UNKNOWN으로 출력됨 (정상). 비교는 wf/R/n 절댓값 기준

### 10.3 회귀

- 라운드 3 통과 테스트들이 그대로 통과해야 함 (set_params, exit_mode 등)
- `python -m pytest tests/ -q` 전부 PASS

---

## 11. 리스크 / 알려진 한계

1. **27 셀이 모두 ETH에서 NEUTRAL/KILL이면 라운드 3 결과 자체가 우연**: 그 경우 라운드 5는 청산 최적화 외 다른 axis 검토 (e.g., 지표 파라미터 sweep, 진입 조건 변경, 다른 데이터 구간)
2. **TF_early 재현 실패**: §7 sanity check 실패 시 어딘가에 비결정성. 디버그 우선
3. **9 윈도우의 표본 한계**: 윈도우당 trade 수 100여 개라 통계적 유의성 제한. 통합 라벨로 voting (ETH + BTC + AVAX)을 통해 보완하지만 여전히 power 부족
4. **ROBUST_PROMOTE 셀이 여럿이지만 라운드 5에서 다시 검증 안 하면 결과의 stationarity 가정 강함**: 라이브 운영 전에 forward test 1-3개월 권장

---

## 12. 라운드 5 후보 (이번 라운드 OUT)

라운드 4 결과에 따라:

- **ROBUST_PROMOTE 셀 여럿 발견**: 라이브 운영 정책 결정 (공통 BBKC exit 후보 확정), forward test
- **ETH_ONLY_PROMOTE만 발견**: 심볼별 다른 exit_mode 운영 (BTC/AVAX는 fixed, ETH는 be_trail)
- **TF_early만 cherry-pick으로 판명**: 청산 외 axis 검토 — 진입 조건, 지표 파라미터, 시장 regime gating

부수:
- ETH time_stop 정밀 sweep (라운드 2 §12.6, 라운드 3 §15.6 보류)
- 13코인 일반화 (라운드 4 ROBUST_PROMOTE 후보의 generalization 확인)

---

## 13. 다음 단계

1. **본 문서 사용자 검토 + 승인**
2. **writing-plans 스킬로 전환**
3. 구현은 plan 승인 후 시작
4. 라운드 종료 후 §14 round-up 작성

---

## 14. Round 4 Results (2026-04-29 sweep)

**Run**: `logs/research/bbkc_squeeze/exit_round/2026-04-29_T0925/` + `latest/`
**Coverage**: 28 cells × 3 symbols × 9 WF windows = **756 backtests**, ~3분

### 14.1 Reproducibility 검증 ✓

`be30_st60_di30 × ETHUSDT` 결과가 라운드 3 TF_early와 **bit-perfect 일치**:

| 메트릭 | Round 3 expected | Round 4 actual | Δ |
|---|---|---|---|
| wf_oos_positive | 6 | 6 | 0 |
| mean_r_per_trade | 0.0635821965450038 | 0.0635821965450038 | 0.00e+00 |
| trade_count | 154 | 154 | 0 |

set_params invariant fix(`a3dd4fe`)가 trail 동작 경로에 영향 없음을 검증. tolerance 미사용으로 통과.

### 14.2 판정 결과 — per-cell integrated label 분포

| 라벨 | 셀 수 | 비율 (27 fine 기준) |
|---|---|---|
| **ROBUST_PROMOTE** | **10** | 37% |
| ETH_ONLY_PROMOTE | 17 | 63% |
| ETH_PROMOTE_MIXED | 0 | — |
| DAMAGING | 0 | — |
| NO_SIGNAL | 0 | — |
| BASELINE (F0) | 1 | — |

ETH_PROMOTE_MIXED와 DAMAGING이 모두 0 — be_trail 도입이 어떤 셀에서도 sample-size 문제나 ETH 이득 없는 손해를 만들지 않음.

### 14.3 핵심 발견 1: TF_early는 외딴섬이 아니라 robust plateau

**ETH heatmap 27셀 모두 STRONG_PROMOTE**.

```
ETHUSDT, dist={0.20, 0.30, 0.40} 모두:
        st=0.50 | st=0.60 | st=0.70
be=0.25  SP        SP        SP
be=0.30  SP        SP        SP
be=0.35  SP        SP        SP
```

→ ETH에 trailing은 **광범위하게 효과적**. trail 파라미터를 어디에 두어도 ETH는 STRONG_PROMOTE. 라운드 3의 TF_early는 cherry-pick이 아니라 plateau 위 한 점.

→ ETH 한정 운영 결정은 **세부 파라미터에 robust** (운영 위험 낮음).

### 14.4 핵심 발견 2: BTC도 fine grid에서 PROMOTE 가능

라운드 3 8 archetype에서 BTC는 모두 NEUTRAL이었으나, 라운드 4 fine grid에서:
- BTC dist=0.20: be ≥ 0.30 + start ≥ 0.50 영역 대부분 P/SP
- BTC dist=0.30, dist=0.40: be=0.35 또는 start=0.70에서 P
- 27셀 중 BTC PROMOTE/SP = **다수**

→ 라운드 3 archetype 8개가 BTC sweet spot을 비켜갔던 것. fine grid가 더 좋은 영역 발견.

### 14.5 핵심 발견 3: AVAX는 very early BE에서만 견딤

```
AVAXUSDT, dist=0.20:
        st=0.50 | st=0.60 | st=0.70
be=0.25  SP        SP        N
be=0.30  K         K         K
be=0.35  K         N         K

AVAXUSDT, dist=0.30, 0.40: be=0.25 모두 N (KILL 없음)
                            be ≥ 0.30 모두 K
```

→ AVAX는 **be=0.25 (TP 절반보다 짧은 BE 트리거)** 에서만 살아남음. be ≥ 0.30은 거의 모두 KILL. AVAX는 변동성 패턴상 BE를 빨리 켜야 trailing이 도움 됨.

### 14.6 ROBUST_PROMOTE 10셀 (3 심볼 모두 안전 + ETH 개선)

| 셀 | ETH wf | ETH R | BTC | AVAX |
|---|---|---|---|---|
| **be25_st60_di30** | **7/9** | **+0.097** | NEUTRAL | NEUTRAL |
| be25_st50_di30 | 7/9 | +0.095 | NEUTRAL | NEUTRAL |
| be25_st60_di40 | 7/9 | +0.093 | NEUTRAL | NEUTRAL |
| be25_st50_di40 | 7/9 | +0.091 | NEUTRAL | NEUTRAL |
| be25_st60_di20 | 7/9 | +0.096 | PROMOTE | STRONG_PROMOTE |
| be25_st50_di20 | 7/9 | +0.088 | NEUTRAL | STRONG_PROMOTE |
| be25_st70_di30 | 6/9 | +0.077 | PROMOTE | NEUTRAL |
| be25_st70_di40 | 6/9 | +0.077 | PROMOTE | NEUTRAL |
| be25_st70_di20 | 6/9 | +0.076 | PROMOTE | NEUTRAL |
| be35_st60_di20 | 6/9 | +0.072 | PROMOTE | NEUTRAL |

**상위 후보**:
- **`be25_st60_di30`** — ETH wf 7/9 R+0.097 + BTC/AVAX 둘 다 NEUTRAL (안전한 개선)
- **`be25_st60_di20`** — 더 공격적 후보. ETH wf 7/9 + BTC PROMOTE + AVAX STRONG_PROMOTE (3 심볼 동시 개선)

### 14.7 부수 검증

- ✅ Reproducibility match (bit-perfect, tolerance 미사용)
- ✅ Round 3 baseline F0 결과 동일 재현 (BTC wf 3/9 R-0.048, ETH wf 4/9 R+0.024, AVAX wf 3/9 R-0.145)
- ✅ ETH_PROMOTE_MIXED = 0 (ETH warning 케이스 발생 안 함, 충분한 trade count)
- ✅ DAMAGING = 0 (어떤 셀도 ETH 이득 없으면서 다른 심볼 망가뜨리지 않음)
- 모든 fine 셀에서 trail이 활발히 작동 — `update_stop` 호출 다수 발생, R-unit dead path 완전 해소 확인

### 14.8 라운드 5 후보

라운드 4가 강한 robust 영역을 발견했으므로 라운드 5는 **운영 적용**으로 무게 중심 이동:

1. **라이브 운영 정책 결정 (우선)**:
   - 공통 BBKC exit으로 `be25_st60_di30` 채택 후보 (ROBUST_PROMOTE, 가장 안전)
   - 또는 더 공격적: `be25_st60_di20` (3 심볼 동시 개선)
   - Forward test 1-3개월 권장 (라이브 데모 환경)
2. **AVAX 별도 계열**: be=0.25 + dist=0.20에서 SP. AVAX 한정 다른 sweet spot 정밀 sweep 가능
3. **ETH time_stop 정밀 sweep** (라운드 2/3 §15.6 보류): 이번 라운드 ETH wf 7/9이 천장인지, time_stop과 결합으로 8/9까지 끌어올릴 수 있는지
4. **13코인 일반화**: be25_st60_di30이 BIGTHREE 외 코인에서도 robust인지 확인

### 14.9 한 줄 요약

**라운드 4는 ETH × TF_early가 27셀 plateau 위 한 점임을 확인 + ROBUST_PROMOTE 10셀 발견. 가장 안전한 운영 후보는 `be25_st60_di30` (ETH wf 7/9 R+0.097, BTC/AVAX 둘 다 NEUTRAL). 라운드 5는 라이브 운영 정책 결정 + Forward test로 진입 가능.**
