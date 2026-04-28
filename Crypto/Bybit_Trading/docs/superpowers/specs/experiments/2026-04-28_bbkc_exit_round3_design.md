# BBKC Exit Strategy Round 3 — Design

**날짜**: 2026-04-28
**상태**: 설계 승인 대기 (브레인스토밍 완료, 구현 미착수)
**선행 작업**: 라운드 2 (`2026-04-25_bbkc_exit_strategy_design.md`, 머지 `ccf82b4`) §12 결과
**목표**: be_trail trigger를 reachable space로 옮긴 뒤 archetype 비교로 "be_trail 컨셉이 BBKC에 살아있는가"에 답한다.

---

## 1. 배경 (라운드 2 §12에서 이월)

라운드 2 결과:
- **be_trail 침묵**: R-단위 thresholds (`trail_be_r=1.0`, `trail_start_r=2.0`)이 BBKC scale에서 unreachable. 이유: `R = entry × sl_pct/lev = 2.33%` > `TP distance = entry × tp_pct/lev = 2.00%`. 따라서 +1R 도달 전에 TP가 먼저 발동. `_manage_position`은 204회 호출됐으나 `update_stop` 0회.
- **time_stop 효과 갈림**: ETH F24 (5/9, R/trade +0.118, +466 PnL) > F0 (4/9, +0.024, +154 PnL). BTC는 악화, AVAX 무영향.
- **모든 셀 KILL**: 7/9 절대 게이트가 baseline F0(3-4/9 OOS+) 자체를 못 통과 → 자동 KILL.

라운드 3은 **be_trail 재구조 + 게이트 재보정**에 집중. time_stop 정밀화는 라운드 4로 이월.

---

## 2. 목표

명시적 질문:
> "be_trail trigger를 TP fraction 단위로 옮기면(`reachable space` 안), BBKC의 fixed baseline 대비 의미 있는 개선이 일어나는가?"

질문이 아닌 것:
- "최적 trail 파라미터는 무엇인가" — round 4 후속
- "time_stop을 어떻게 개선할까" — round 4
- "다른 청산 primitive (partial TP 등)" — 향후

판정 단위: **per-symbol** (BTC/ETH/AVAX 각각 독립).

---

## 3. 스코프

### IN

1. `src/strategies/bbkc_squeeze.py`:
   - **제거**: `trail_be_r`, `trail_start_r`, `trail_distance_r` (라운드 2 dead code)
   - **추가**: `trail_be_at_tp_frac`, `trail_start_at_tp_frac`, `trail_distance_tp_frac`, `drop_tp`
   - `_manage_position`: TP-fraction 단위 수식으로 재작성
   - `on_bar_fast` 진입 분기: `drop_tp=True`일 때 `take_profit=None`으로 broker.buy 호출
   - `__init__`에 invariant 검증
2. `src/strategies/registry_builder.py`:
   - `STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]` 12셀(라운드 2) → **8셀**로 교체
3. `scripts/bbkc_exit_eval.py`:
   - `make_strategy_factory`: 새 파라미터 이름으로 cell→kwargs 매핑 갱신
   - `judge`: 7/9 절대 게이트 → **baseline-relative delta 룰**
4. 단위 테스트 갱신/신규:
   - `tests/test_strategies/test_bbkc_squeeze_exit_modes.py` (재작성)
   - `tests/test_strategies/test_registry_builder_exit_grid.py` (cell_id 갱신)
   - `tests/test_scripts/test_bbkc_exit_eval_judge.py` (신규, 게이트 룰 분기)

### OUT (라운드 4 이후)

- time_stop 정밀 sweep (ETH 한정)
- 심볼별 청산 운영 정책
- legacy `_legacy/` 변경 — 라운드 2 F2 + BBKC trailing gate 그대로 유효
- 13코인 일반화
- broker.update_tp 추가 (YAGNI — drop_tp는 진입 시 None 전달로 충분)
- 다른 전략(RSIMACD 등)에 TP-fraction primitive 적용

---

## 4. Strategy 파라미터 명세 (BBKCSqueeze)

### 4.1 새 파라미터

```python
exit_mode: str = "fixed"            # "fixed" | "be_trail" (기존)
trail_be_at_tp_frac: float = 0.5    # BE 트리거 (TP 거리 비율)
trail_start_at_tp_frac: float = 0.8 # trailing 활성 (TP 거리 비율)
trail_distance_tp_frac: float = 0.3 # trailing SL 거리 (TP 거리 비율)
drop_tp: bool = False               # 진입 시 take_profit=None 여부
time_stop_bars: int = 0             # 기존, time_stop fallback (변경 없음)
```

기본값(0.5/0.8/0.3, drop_tp=False)은 `TF_default` 셀과 일치 → `BBKCSqueeze(exit_mode="be_trail")`만으로 default 동작.

### 4.2 Invariant (`__init__`에서 검증)

```python
if exit_mode == "be_trail":
    if not (0 < trail_be_at_tp_frac < trail_start_at_tp_frac < 1.0):
        raise ValueError(
            f"need 0 < trail_be_at_tp_frac < trail_start_at_tp_frac < 1.0, "
            f"got {trail_be_at_tp_frac}, {trail_start_at_tp_frac}"
        )
    if trail_distance_tp_frac <= 0:
        raise ValueError(
            f"trail_distance_tp_frac must be > 0, got {trail_distance_tp_frac}"
        )
```

엄격 비교 `<` — `0.5/0.5` 같은 동치 케이스는 명시적으로 거부 (immediate 셀은 0.49/0.50으로 회피).

### 4.3 R-unit 제거의 영향

라운드 2 `_pos_meta` 구조 키 일부 의미 변경:
- 기존: `R = abs(entry - initial_sl)` 가격 거리
- 신규: 사용 안 함. 대신 `tp_distance = entry × tp_pct/leverage`를 매 호출 시 계산.
- `_pos_meta` 유지 키: `be_triggered`, `trail_active`, `bars_held` (R, initial_sl 제거)

---

## 5. `_manage_position` 동작

```python
def _manage_position(self, bar: Bar, pos, broker: Broker) -> None:
    sym = bar.symbol
    meta = self._pos_meta[sym]
    if pos.entry_price <= 0 or self.tp_pct <= 0 or self.leverage <= 0:
        return  # safety, 정상 케이스에서 도달 불가

    tp_distance = pos.entry_price * self.tp_pct / self.leverage  # 양수
    close = bar.close
    move = close - pos.entry_price if pos.side == "LONG" else pos.entry_price - close

    if self.exit_mode == "be_trail":
        # BE step (한 번만)
        if not meta["be_triggered"] and move >= self.trail_be_at_tp_frac * tp_distance:
            broker.update_stop(sym, pos.entry_price)
            meta["be_triggered"] = True

        # Trailing step
        if move >= self.trail_start_at_tp_frac * tp_distance:
            offset = self.trail_distance_tp_frac * tp_distance
            new_sl = close - offset if pos.side == "LONG" else close + offset

            if not meta["trail_active"]:
                broker.update_stop(sym, new_sl)
                meta["trail_active"] = True
            else:  # ratchet only
                if pos.side == "LONG" and new_sl > pos.stop_loss:
                    broker.update_stop(sym, new_sl)
                elif pos.side == "SHORT" and new_sl < pos.stop_loss:
                    broker.update_stop(sym, new_sl)

    # time_stop fallback (직교, 변경 없음)
    if self.time_stop_bars > 0 and meta["bars_held"] >= self.time_stop_bars:
        broker.close(sym, reason="time_stop")
```

### 5.1 LONG/SHORT 대칭

LONG: `move = close - entry`, ratchet up (`new_sl > pos.stop_loss`)
SHORT: `move = entry - close`, ratchet down (`new_sl < pos.stop_loss`)

라운드 2 동작과 동일, 단위만 변경.

---

## 6. `drop_tp` 동작

`drop_tp=True`일 때 진입 시점부터 `take_profit=None`. `on_bar_fast` 진입 분기 (LONG 예):

```python
price_tp = self.tp_pct / self.leverage
price_sl = self.sl_pct / self.leverage
sl = close * (1 - price_sl)
tp = close * (1 + price_tp) if not self.drop_tp else None
qty = broker.calc_qty(...)
if qty > 0:
    broker.buy(bar.symbol, qty, stop_loss=sl, take_profit=tp,
               reason=f"BBKCSqueeze LONG ...")
```

SHORT 대칭. `BacktestBroker._check_exit`는 `tp is not None`만 체크하므로 `None` 전달이면 TP 영역 자동 비활성. 별도 broker.update_tp 호출 불필요.

### 6.1 fat-tail 캡처 가설 (TR_* 셀의 의도)

`drop_tp=True` + be_trail = "TP 없는 trailing-only 청산". 가격이 `trail_start_at_tp_frac × TP` 도달 후 추세가 길게 이어지면 trailing SL이 ratchet up하면서 fat-tail 캡처. 추세가 짧으면 BE/trailing 발동 전에 SL hit (drop_tp=False보다 손실 가능성 ↑ — TP가 안전망이었던 케이스).

Sanity check: TR_* 셀의 `exit_reason_dist`에서 `TP` 비율이 0%여야 함. 그렇지 않으면 drop_tp 경로 버그.

---

## 7. 셀 매트릭스 (8셀)

| 셀 | exit_mode | trail_be_at_tp_frac | trail_start_at_tp_frac | trail_distance_tp_frac | drop_tp | time_stop_bars | 의도 |
|---|---|---|---|---|---|---|---|
| **F0** | fixed | — | — | — | False | 0 | Baseline (라운드 2 F0과 동일) |
| **TF_default** | be_trail | 0.50 | 0.80 | 0.30 | False | 0 | Sane middle: BE 50%, trail 80%, tight |
| **TF_wide** | be_trail | 0.50 | 0.80 | 0.50 | False | 0 | 같은 trigger, wider trail |
| **TF_early** | be_trail | 0.30 | 0.60 | 0.30 | False | 0 | Earlier BE/trail (변동성 ↑ 시) |
| **TF_late** | be_trail | 0.70 | 0.90 | 0.30 | False | 0 | TP 직전까지 fixed |
| **TF_immediate** | be_trail | 0.49 | 0.50 | 0.30 | False | 0 | BE plateau 사실상 없음, 즉시 trail |
| **TR_default** | be_trail | 0.50 | 0.80 | 0.30 | **True** | 0 | TR3b: TP 제거, fat-tail 캡처 (default trigger) |
| **TR_immediate** | be_trail | 0.49 | 0.50 | 0.30 | **True** | 0 | TR3b: TP 제거 + 즉시 trail (가장 공격적) |

각 archetype이 답하는 비교 가설:
- `TF_default` vs `F0` — 표준 BE+trailing이 fixed보다 나은가?
- `TF_wide` vs `TF_default` — trail 폭이 결과를 바꾸는가?
- `TF_early` vs `TF_default` — 더 일찍 보호 시작이 도움인가?
- `TF_late` vs `TF_default` — TP 직전까지 보호 미루는 게 나은가?
- `TF_immediate` vs `TF_default` — BE plateau가 의미 있는가?
- `TR_default` vs `TF_default` — TP 제거가 fat-tail 캡처에 효과적인가?
- `TR_immediate` vs `TF_immediate` — TP 제거 + 즉시 trail 조합?

---

## 8. Sweep 범위 (라운드 2와 동일, 직접 비교 위해)

- 심볼: BIGTHREE (BTCUSDT, ETHUSDT, AVAXUSDT)
- 기간: 2024-03-01 ~ 2026-04-30
- TF: 1h
- WF: IS 6m / OOS 2m / step 2m, **9 윈도우**
- 비용: 라운드 2와 동일 가정 (`BacktestConfig` 기본값 사용 — taker 0.055%, slippage 0.03%. maker 수수료는 현재 sweep에서 의미 없음 — Market 주문 only)
- 지표 파라미터: 2026-03-30 winner 고정 (`bb_period=20, bb_std=1.5, kc_mult=1.0, rsi_filter=70.0, tp_pct=0.06, sl_pct=0.07, leverage=3`)

총 평가량: **8 셀 × 3 심볼 × 9 윈도우 = 216 backtest run** (라운드 2 대비 −33%, ~80초 예상).

출력 정책: 라운드 2와 동일 (`logs/research/bbkc_squeeze/exit_round/<timestamp>/` + `latest/`).

---

## 9. Gate (baseline-relative delta 룰)

per-symbol baseline = F0 셀 결과.

```python
def judge_cell(cell_id, m, base):
    if cell_id == "F0":
        return ("BASELINE", False)
    if base is None:
        # F0 없이 부분 실행된 경우 (--cell TF_default 등). baseline 비교 불가.
        return ("UNKNOWN", False)

    warning = m["trade_count"] < base["trade_count"] * 0.5

    pos_delta = m["wf_oos_positive"] - base["wf_oos_positive"]
    r_delta = m["mean_r_per_trade"] - base["mean_r_per_trade"]

    if (pos_delta >= 2
        and r_delta >= 0
        and m["max_dd"] <= base["max_dd"]):
        verdict = "STRONG_PROMOTE"
    elif (pos_delta >= 1
          and r_delta >= 0):
        verdict = "PROMOTE"
    elif (abs(pos_delta) <= 1
          and abs(r_delta) <= 0.05):
        verdict = "NEUTRAL"
    elif (pos_delta < -1
          or r_delta < -0.05):
        verdict = "KILL"
    else:
        verdict = "NEUTRAL"  # safety fallback

    return (verdict, warning)
```

각 verdict 의미:
- **STRONG_PROMOTE**: WF 안정성 +2 이상, R/trade ≥ baseline, DD ≤ baseline → 운영 가능 후보
- **PROMOTE**: WF 안정성 +1, R/trade ≥ baseline → 추가 검증 후 운영 검토
- **NEUTRAL**: baseline과 의미 있는 차이 없음
- **KILL**: 분명한 악화
- **WARNING** (덧붙음): trade count 50% 이상 감소 (표본 부족)

---

## 10. 보조 지표 (라운드 2 그대로 유지)

각 (cell, symbol)에 대해:
1. `exit_reason_dist`: TP / STOP / time_stop / BACKTEST_END 비율
2. `mean_r_win`, `mean_r_loss`
3. `mfe_retention`
4. `mean_holding_bars`

drop_tp sanity check: TR_* 셀의 `TP` 비율 = 0%.

trailing 작동 sanity check: TF_* 셀과 F0의 exit_reason 분포가 다르면 trailing이 일하고 있다는 뜻 (예: TF_default가 F0보다 STOP 비율이 적으면서 약한 win이 늘어나야 함).

---

## 11. 파일 변경 목록

### 새 파일

- `tests/test_scripts/test_bbkc_exit_eval_judge.py` — 신규 게이트 룰 분기 테스트
- `docs/superpowers/specs/experiments/2026-04-28_bbkc_exit_round3_design.md` (이 문서)

### 수정 파일

- `src/strategies/bbkc_squeeze.py`
  - `__init__` 시그니처: R-unit 제거, TP-fraction 추가, drop_tp 추가
  - Invariant 검증
  - `_manage_position`: TP-fraction 수식으로 재작성
  - `on_bar_fast` 진입 분기: drop_tp=True 처리
  - `get_params` / `set_params` 갱신

- `src/strategies/registry_builder.py`
  - `exit_round_grid` 12셀 → 8셀 교체

- `scripts/bbkc_exit_eval.py`
  - `make_strategy_factory`: 새 파라미터 이름 매핑 (R-unit 키 제거, TP-fraction 키 추가)
  - `judge`: baseline-relative delta 룰
  - `build_report`: verdict 컬럼에 `STRONG_PROMOTE`/`NEUTRAL`/`UNKNOWN` 추가 표시
  - 모듈 docstring + Markdown 리포트 제목/설명에서 "Round 2" 문구를 "Round 3"으로 갱신

- `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`
  - R-unit 테스트 제거
  - TP-fraction 테스트 (BE @ trail_be × tp_dist, trail @ trail_start × tp_dist, ratchet)
  - drop_tp=True 테스트 (broker.buy의 take_profit이 None인지)
  - Invariant 검증 테스트
  - immediate 셀(0.49/0.50) 동작 확인

- `tests/test_strategies/test_registry_builder_exit_grid.py`
  - 8 cell_id 검증 (F0, TF_default, TF_wide, TF_early, TF_late, TF_immediate, TR_default, TR_immediate)

### 미변경 (확인용)

- `src/execution/broker.py`, `src/execution/backtest_broker.py` — broker.update_stop 그대로 사용. `update_tp` 추가 안 함.
- `_legacy/`: 라운드 2 F2 + BBKC trailing gate 변경 그대로 유효

---

## 12. 테스트 계획 (TDD)

라운드 2의 TDD 패턴 동일 — failing test → minimal impl → pass → commit.

### 12.1 단위 테스트 (`test_bbkc_squeeze_exit_modes.py`)

신규/갱신:
- `test_default_params_are_tp_fraction_units`
- `test_invariant_rejects_be_geq_start`
- `test_invariant_accepts_immediate_cell` (0.49 < 0.50)
- `test_invariant_rejects_distance_zero_or_negative`
- `test_be_trail_long_below_be_threshold_no_change`
- `test_be_trail_long_at_be_threshold_triggers_BE` (close = entry + 0.5 × tp_dist → SL = entry)
- `test_be_trail_long_at_start_threshold_activates_trailing` (close = entry + 0.8 × tp_dist → SL = close - 0.3 × tp_dist)
- `test_be_trail_long_trailing_ratchets_up`
- `test_be_trail_short_symmetry`
- `test_drop_tp_at_entry_passes_none_to_buy` (mock broker.buy의 take_profit 인자 검증)
- `test_drop_tp_false_keeps_take_profit_set` (회귀)
- `test_immediate_cell_be_and_trail_same_bar` (0.49/0.50 세팅 시 한 봉에 BE+trail 동시 활성)

### 12.2 게이트 테스트 (`test_bbkc_exit_eval_judge.py` 신규)

- baseline 자기 자신 → "BASELINE"
- baseline 대비 +2 OOS, R≥, DD≤ → STRONG_PROMOTE
- baseline 대비 +1 OOS, R≥ → PROMOTE
- baseline 대비 ±1 OOS, |ΔR|≤0.05 → NEUTRAL
- baseline 대비 −2 OOS → KILL
- baseline 대비 ΔR < -0.05 → KILL
- trade_count < baseline × 0.5 → WARNING flag (verdict와 별도)

### 12.3 회귀

- `tests/test_strategies/test_bbkc_squeeze.py` 기존 테스트 — default 파라미터 (`exit_mode="fixed"`)에서 라운드 2와 동일 시그널 생성 확인
- `tests/_legacy/`: 라운드 2 통과 테스트 그대로 통과 (legacy 변경 없음)

---

## 13. 리스크 / 알려진 한계

1. **TF_late가 fixed와 거의 같을 가능성**: trail_start=0.9면 가격이 +1.8% 도달 후 trailing 시작. TP=2%이므로 trailing 활성 직후 다음 봉에 TP hit → trailing이 거의 일 안 함. 라운드 2의 R-unit 문제 재현 위험. → 결과로 확인되면 학습 (TP 직전 trailing은 의미 없다).
2. **TR_* 셀에서 SL hit 증가 가능**: TP가 보호선 역할을 하던 효과 사라짐. 라운드 결과로 검증.
3. **9윈도우 분산이 큼**: 8셀 × 9윈도우 = 72 window-cell 조합. window-level 표본 작음. WF 평균만 보면 분산 놓칠 수 있음 → 보조 지표 std 같이 출력.
4. **MFE retention의 음수 분포**: 라운드 2와 동일. TR_* 셀에서는 더 클 수 있음 (loss 거래의 max_fav가 작은 양수일 때 분모가 작아짐).
5. **drop_tp=True 셀에서 SL이 멀어 손실 폭 ↑**: 진입 직후 역방향 변동이 크면 SL=−2.33% 끝까지 가서 라운드 2 fixed보다 loss 폭 큼.

---

## 14. 다음 단계

1. **본 문서 사용자 검토 + 승인**
2. **writing-plans 스킬로 전환** — 본 설계를 단계별 구현 plan으로 분해
3. 구현은 plan 승인 후 시작
4. 라운드 종료 후 §15 round-up 작성

---

## 15. Round 3 Results (2026-04-28 sweep)

**Run**: `logs/research/bbkc_squeeze/exit_round/2026-04-28_T2104/` + `latest/`
**Coverage**: 8 cells × 3 symbols × 9 WF windows = **216 backtests**, ~46초

### 15.1 판정 결과

24 cell-symbol pair (F0 BASELINE 3개 제외 21 평가):

**Verdict 분포**: STRONG_PROMOTE 1, PROMOTE 0, NEUTRAL 12, KILL 8

| Cell | BTC | ETH | AVAX |
|---|---|---|---|
| F0 (baseline) | wf 3/9, R -0.048 | wf 4/9, R +0.024 | wf 3/9, R -0.145 |
| TF_default | NEUTRAL (3/9, R -0.007) | NEUTRAL (3/9, R +0.005) | KILL (2/9, R -0.257) |
| TF_wide | NEUTRAL (3/9, R -0.008) | NEUTRAL (3/9, R +0.004) | KILL (2/9, R -0.241) |
| **TF_early** | NEUTRAL (3/9, R -0.011) | **STRONG_PROMOTE (6/9, R +0.064, n=154)** | KILL (2/9, R -0.219) |
| TF_late | NEUTRAL (3/9, R -0.004) | NEUTRAL (3/9, R +0.012) | NEUTRAL (3/9, R -0.152) |
| TF_immediate | NEUTRAL (3/9, R -0.029) | NEUTRAL (4/9, R +0.008) | KILL (2/9, R -0.234) |
| TR_default | NEUTRAL (3/9, R -0.032) | KILL (2/9, R -0.031) | KILL (1/9, R -0.341) |
| TR_immediate | NEUTRAL (3/9, R -0.036) | KILL (3/9, R -0.034) | KILL (1/9, R -0.313) |

### 15.2 핵심 발견 1: be_trail 컨셉이 BBKC에 살아있는가? **Conditional Yes (ETH only)**

라운드 2의 R-unit 침묵 문제는 TP-fraction 단위 전환으로 완전 해소. 모든 셀이 baseline과 다른 R/trade·trade count·exit_reason 분포를 산출 (라운드 2처럼 숫자 동일 패턴 0건). `_manage_position` 호출과 `update_stop` 호출이 정상 발생함이 결과로 검증됨.

단 archetype별 효과는 **심볼-특이적**:
- **ETH** — TF_early에서 WF 안정성 +2 (4/9 → 6/9), R/trade 개선 (+0.024 → +0.064), DD ≤ baseline → STRONG_PROMOTE
- **BTC** — 6 archetype 모두 NEUTRAL. trailing이 주는 보호와 잘려나간 이익이 상쇄. fixed가 가장 무난
- **AVAX** — TF_late만 NEUTRAL, 나머지 5 archetype + 2 TR 모두 KILL. 변동성 큰 알트에서 trailing이 fat-tail을 자르는 비용이 보호 이득보다 큼

### 15.3 핵심 발견 2: drop_tp(R3b) 가설 기각

TR_default vs TF_default, TR_immediate vs TF_immediate 비교 결과 모든 심볼에서 TR_*가 TF_*보다 동등 또는 열등:
- BTC: TR_default R -0.032 < TF_default -0.007 (열등)
- ETH: TR_default KILL vs TF_default NEUTRAL (열등)
- AVAX: TR_default KILL (R -0.341) < TF_default KILL (R -0.257) (더 깊은 KILL)

**해석**: 이번 평가 구간(2024-08~2026-02 OOS)에서 TP cap은 noise 보호 역할을 했고, 제거 시 fat-tail 캡처 이득이 SL hit 손실을 상쇄하지 못함. drop_tp 가설은 이 시장 환경에서는 의미 없음. `exit_reason_dist`의 TP=0% sanity check는 모든 TR 셀에서 통과 (drop_tp 동작 자체는 정상).

### 15.4 archetype 비교 학습

- **TF_default vs TF_wide**: 거의 동일 (NEUTRAL/KILL 패턴 동일). trail_distance 0.3 vs 0.5 차이 미미. 이번 구간에서 trailing 폭은 핵심 변수 아님
- **TF_early vs TF_default**: **trigger 위치가 핵심**. ETH에서 0.3/0.6이 0.5/0.8을 큰 차이로 능가 (STRONG_PROMOTE vs NEUTRAL). 더 일찍 보호 시작 → ETH 변동성 패턴에 잘 맞음
- **TF_late**: trail_start=0.9는 TP(=2.0%)에 너무 가까워 trailing 거의 일 안 함 (사전 §13 리스크 #1 예측 적중). fixed와 사실상 동일 결과 (모든 심볼 NEUTRAL)
- **TF_immediate vs TF_default**: BE plateau 제거 효과 미미. ETH/AVAX/BTC 모두 NEUTRAL/KILL 패턴 유지. plateau 자체는 critical 변수 아님

### 15.5 부수 검증

- ✅ TR_default exit_reason_dist 확인: TP 비율 = 0.0% (drop_tp 동작 sanity 통과)
- ✅ TF_default ETH window-level 분산: 6/9 OOS+ 확보. 1윈도우만 음수가 아니라 robust한 신호로 보임
- ❗ TF_early가 ETH에만 특화: BTC NEUTRAL, AVAX KILL이라 cherry-pick 위험 있음. 라운드 4에서 같은 trigger로 fine sweep + 인접 archetype까지 함께 검증 필요

### 15.6 라운드 4 후보 (ETH 중심 정밀 탐색)

라운드 3에서 ETH × TF_early 1셀이 STRONG_PROMOTE를 받았으나, 이는 archetype-level 신호로 cherry-pick 위험이 있다. 라운드 4는 **TF_early 주변 정밀 sweep**으로 검증.

추천 sweep 범위:
- `trail_be_at_tp_frac` ∈ {0.25, 0.30, 0.35}
- `trail_start_at_tp_frac` ∈ {0.50, 0.60, 0.70}
- `trail_distance_tp_frac` ∈ {0.20, 0.30, 0.40}

제약 `trail_be < trail_start` 적용 시 약 9~12셀. ETH BIGTHREE × 9 윈도우 = ~108 runs.

부수:
- BTC/AVAX는 라운드 3 결과로 trailing 비추천 확정 → 라운드 4에서는 fixed 유지
- 결과 적합 시 ETH 한정 라이브 운영 정책 검토 (심볼별 다른 exit_mode 운영)
- time_stop ETH 정밀 sweep (라운드 2 §12.6 보류 항목)도 같은 라운드에 함께 가능

### 15.7 한 줄 요약

**라운드 3은 be_trail 컨셉을 ETH × TF_early에서 살려냈다 (STRONG_PROMOTE). drop_tp는 모든 심볼에서 기각. BTC/AVAX는 fixed 유지. 라운드 4는 ETH × TF_early 주변 정밀 sweep으로 신호 robustness 검증.**
