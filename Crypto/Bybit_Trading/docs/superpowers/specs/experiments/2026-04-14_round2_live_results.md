# Round 2 Live Execution Results — 2026-04-14

**스코프**: PHASE 4 (D2 core) + PHASE 5 (BBKC universe) 실제 실행.
**입력**: 동일 holdout `2025-10-01 ~ 2026-04-10`, 14d warmup, $10k capital.
**판정 엔진**: `src/evaluation/verdict.py::judge_variant_vs_baseline`

## 1. D2 core — DonchianFixedRRTrendFilter vs DonchianFixedRR

스크립트: `scripts/d2_core_eval.py`
결과: `logs/d2_core/results.json`, `logs/d2_core/verdict.json`

| 심볼 | FixedRR (baseline) | FixedRR + EMA200 (D2) |
|---|---|---|
| BTCUSDT | n=71 pnl=-148.64 wr=56.3% mdd=16.9% | n=56 pnl=+202.01 wr=58.9% mdd=15.5% |
| ETHUSDT | n=46 pnl=-268.07 wr=50.0% mdd=16.1% | n=16 pnl=-96.66 wr=56.2% mdd= 8.2% |
| SOLUSDT | n= 8 pnl=-417.82 wr=50.0% mdd= 6.8% | n= 0 pnl= 0.00 wr= 0.0% mdd= 0.0% |
| LINKUSDT | n= 6 pnl=-989.37 wr=16.7% mdd= 8.3% | n= 0 pnl= 0.00 wr= 0.0% mdd= 0.0% |
| AVAXUSDT | n= 6 pnl=-981.53 wr=16.7% mdd= 7.8% | n= 2 pnl=-400.84 wr= 0.0% mdd= 2.0% |
| **TOTAL** | **n=137 pnl=-2805.42 wr=50.4% mdd=39.3%** | **n=74 pnl=-295.50 wr=56.8% mdd=19.6%** |

**Delta**: pnl +$2510, avg +$16.48, wr +6.4%p, mdd **-19.7%p**.

**판정: PROMOTE** (`reasons=["avg_trade_pnl better (+16.48) AND mdd not worse (-19.7%)", "total pnl also improves (+2509.93)"]`).

**구조적 해석**:
- 구 FixedRR은 SOL/LINK/AVAX에서 6~8개 trade로 $2,388 손실 (전체 손실의 85%).
  이건 trend filter 부재 때문에 역추세 돌파를 그대로 받은 결과.
- EMA(200) 필터를 추가하면 SOL/LINK에서 아예 진입하지 않음 (trend 여건
  미충족). AVAX는 2 trade로 축소.
- BTC는 붕괴되지 않고 오히려 $-148 → $+202로 회복.
- 여전히 net negative지만 baseline 대비 정량적·정성적으로 더 깨끗.

**결론**: 기존 `DonchianFixedRR`은 **사용 중단**하고 FixedRR 계열 baseline을
`DonchianFixedRRTrendFilter`로 정식 승격. 다음 단계는 grid sweep로
파라미터 감도를 측정할 가치가 있음.

## 2. BBKC universe subset — BBKCSqueeze across 5 symbol sets

스크립트: `scripts/bbkc_universe_eval.py`
결과: `logs/bbkc_universe/results.json`, `logs/bbkc_universe/verdicts.json`

| Universe | n | pnl | wr | avg | sharpe | mdd |
|---|---|---|---|---|---|---|
| **ALL5 (baseline)** | 184 | +3700.10 | 60.9% | +20.11 | +1.60 | 16.7% |
| BTCETH | 96 | +3799.10 | 65.6% | +39.57 | +3.05 | 11.8% |
| BIGTHREE (BTC+ETH+AVAX) | 150 | +5579.28 | 65.3% | +37.20 | +2.96 | 11.8% |
| EXCLUDE_SOL | 168 | +4420.45 | 62.5% | +26.31 | +2.09 | 11.8% |
| EXCLUDE_SOL_LINK | 150 | +5579.28 | 65.3% | +37.20 | +2.96 | 11.8% |

**판정**:

| Universe | Verdict | Reason |
|---|---|---|
| BTCETH | INSUFFICIENT_DATA | active_symbols=2 < 3 (규약 P4) |
| BIGTHREE | **PROMOTE** | Δavg +17.09, Δmdd -4.9%p, Δpnl +$1879 |
| EXCLUDE_SOL | **PROMOTE** | Δavg +6.20, Δmdd -4.9%p |
| EXCLUDE_SOL_LINK | **PROMOTE** | BIGTHREE와 동일 (LINK 기여 음수) |

**구조적 해석**:
- SOL (-$720) + LINK (-$1158) = $1878 손실을 ALL5 aggregate에서 제거하면
  순수 edge가 남는다.
- BIGTHREE와 EXCLUDE_SOL_LINK는 숫자상 동일 (LINK trade가 제외되면
  자동으로 동일한 universe가 된다).
- Sharpe 1.60 → 2.96 (85% 개선), avg_trade_pnl $20 → $37 (85% 개선),
  drawdown 16.7% → 11.8% (5%p 개선).
- 단, universe 축소 자체는 과최적화 위험이 있으므로 "SOL/LINK는
  structurally bad"라는 해석을 **dev**용으로 채택하고, **live**
  배포 결정은 2차 OOS (다른 기간)로 추가 검증이 선행되어야 한다.

**결론**: BBKCSqueeze entry logic은 건드리지 않고 universe를 BIGTHREE
(또는 EXCLUDE_SOL_LINK)로 축소하는 것이 현재 가장 싸고 확실한 개선.
PHASE 7 (BBKC exit-layer)는 이 universe 결정 후에 별도 실험으로 진행
가능.

## 3. D2 grid (486 cells, 2026-04-14 완료)

상세: `docs/.../2026-04-14_d2_grid_results.md`

- PROMOTE 300/486 (62%), CONDITIONAL_PROMOTE 48, KILL 121,
  INSUFFICIENT_DATA 9, NO_EDGE 8, SKIPPED 0
- Best cell: `ep=25 sa=3.0 tp=2.5 tra=1.5 trd=0.5 ema=100`
  - D2 total pnl +$7989 (baseline -$2805, Δ +$10,795)
  - avg_trade_pnl +$37.96, win_rate +11.6%p, mdd -17.0%p
  - concentration 31.3%, 심볼 편중 없음
- Top 10 공통 패턴: **wide stop (stop_atr=3.0) + tight trailing (trail_distance=0.5)**
- memo-fixed 기본 파라미터도 PROMOTE → 파라미터 튜닝 없이도 baseline 이김

**판정**:
- `DonchianFixedRRTrendFilter` **class 승격** → FixedRR 계열 공식 baseline
- Best cell 파라미터는 **development candidate** — 2차 OOS 검증 전까지
  운영 default로 승격 금지
- 운영 default 파라미터 = memo-fixed (`ep=20 sa=2.5 tp=2.0 tra=1.5 trd=1.0 ema=200`)
- `DonchianFixedRR`은 **deprecated baseline** (신규 실험 사용 금지,
  round1 결과 재현 용도로만 보존)

## 4. 업데이트된 전략 상태표 (round 2 기준)

| 전략 | 역할 | 상태 |
|---|---|---|
| `BBKCSqueeze` | control (robust) | 유지 — entry 불변, universe BIGTHREE는 candidate |
| `BBKCSqueezeHTFTrend` | 실험 variant | KILL (destroyed 2 baseline winners) |
| `DonchianTrendFilter` | 보조 축 | 유지 (약함, BTC 단독 prior) |
| `DonchianTrendFilterADX20` | 실험 variant | KILL (dual regression) |
| `DonchianTrendFilterADX25` | 실험 variant | KILL (new symbol prior flip) |
| `DonchianFixedRR` | **deprecated baseline** | 사용 중단, round1 재현 대조군 |
| `DonchianFixedRRTrendFilter` | **PROMOTED FixedRR baseline** | class 승격, default 파라미터 = memo-fixed |
| D2 best cell (`ep=25…`) | development candidate | 2차 OOS 전까지 운영 금지 |
| BBKC universe `BIGTHREE` | candidate universe | 2차 OOS 전까지 정식 승격 보류 |

## 5. 다음 액션 (즉시 실행 가능 / Gate 대기)

### 즉시 실행 가능 — D2 2차 OOS
```bash
python -m scripts.d2_core_eval \
    --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/d2_core_oos2
```
목적: D2 class 승격을 유지할지, top cell 파라미터 승격으로 넘어갈지
결정. 같은 DB의 앞 window 6개월로 이동.

### 즉시 실행 가능 — BBKC BIGTHREE 2차 OOS
```bash
python -m scripts.bbkc_universe_eval \
    --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/bbkc_universe_oos2
```
목적: BIGTHREE를 정식 universe로 승격할지 판정.

### Gate 대기 (조건 충족 전 실행 금지)
- **Gate 1 (D1 extension)** — D2 2차 OOS 통과 + Donchian 확장 가치 남을 때
- **Gate 2 (BBKC exit-layer)** — BIGTHREE 정식 승격 후
- **Gate 3 (filter-type ML comparator)** — 새 filter-type pattern이
  D1 event-level PASS했을 때만

상세 조건: `2026-04-14_experiment_protocol.md §7`.

### 금지 (P5/P7 재확인)
- Standalone ML pattern 재개
- BBKCSqueeze entry logic 수정 (HTF gate, ML filter 재도전)
- D1 ADX variants 재실행
- D2 파라미터 추가 grid 확장 (현재 486 cells로 충분)

## 6. 재현 커맨드 (한 장 요약)

```bash
# 전체 round 2 재생성 (D2 core + BBKC universe + round1 verdict)
python -m scripts.run_rule_based_experiments

# D2 grid 재생성 (resume 가능, 이미 완료 상태)
python -m scripts.d2_grid --resume

# 2차 OOS 검증 (Gate 개방 조건)
python -m scripts.d2_core_eval --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/d2_core_oos2
python -m scripts.bbkc_universe_eval --start 2024-10-01 --end 2025-04-01 \
    --out-dir logs/bbkc_universe_oos2
```
