# OOS2 Results — D2 Demotion + BBKC BIGTHREE Promotion

**날짜**: 2026-04-14
**2차 OOS window**: `2024-10-01 ~ 2025-04-01` (round 1 holdout 직전 6개월)
**스크립트**: `scripts/d2_core_eval.py`, `scripts/bbkc_universe_eval.py`
**결과 파일**:
- `logs/d2_core_oos2/{results.json, verdict.json, best_cell_detail.json}`
- `logs/bbkc_universe_oos2/{results.json, verdicts.json}`

## TL;DR

| 항목 | 상태 변화 |
|---|---|
| `DonchianFixedRRTrendFilter` class | **PROMOTED → WINDOW-DEPENDENT** (승격 취소) |
| D2 best grid cell | **dev candidate → ARCHIVED** |
| `BBKCSqueeze[BIGTHREE]` | **candidate → STAGED PROMOTE** (paper 검증 전 최종 관문만 남음) |

## 1. D2 2차 OOS — class 승격 취소

### 1.1 memo-fixed default (`ep=20 sa=2.5 tp=2.0 tra=1.5 trd=1.0 ema=200`)

| 심볼 | DonchianFixedRR baseline | D2 memo-fixed |
|---|---|---|
| BTC | n=0 pnl=0 | n=48 pnl=-924 wr=52.1% mdd=17.0% |
| ETH | n=1 pnl=-198 | n=1 pnl=-200 |
| SOL | n=55 pnl=-606 wr=60.0% mdd=13.6% | n=52 pnl=-675 wr=57.7% mdd=13.5% |
| LINK | n=126 pnl=-124 wr=54.8% mdd=15.7% | n=10 pnl=-603 wr=40.0% mdd= 8.3% |
| AVAX | n=0 pnl=0 | n=110 pnl=+1057 wr=55.5% mdd=15.5% |
| **TOTAL** | n=182 pnl=-928 wr=56.0% **mdd=16.8%** | n=221 pnl=**-1345** wr=54.3% **mdd=34.3%** |

- **Verdict**: `KILL` (new symbol prior AVAX 0% → 49.8%)
- Δpnl -$418, Δavg -$0.99, Δmdd **+17.5%p** (catastrophic drawdown)

### 1.2 Best grid cell (`ep=25 sa=3.0 tp=2.5 tra=1.5 trd=0.5 ema=100`)

| 심볼 | baseline | D2 best cell OOS2 |
|---|---|---|
| BTC | n=0 | n=0 |
| ETH | n=1 pnl=-198 | n=0 pnl=0 |
| SOL | n=55 pnl=-606 | n=56 pnl=-350 wr=60.7% mdd=16.4% |
| LINK | n=126 pnl=-124 | n=49 pnl=-815 wr=57.1% mdd=13.7% |
| AVAX | n=0 | n=110 pnl=+369 wr=60.9% mdd=16.0% |
| **TOTAL** | n=182 pnl=-928 mdd=16.8% | n=215 pnl=**-795** wr=60.0% **mdd=24.3%** |

- **Verdict**: `KILL` (new symbol prior AVAX 0% → 51.2%)
- Δpnl +$133 (소폭 개선), Δmdd **+7.5%p** (악화)
- Round1 결과와 정반대:

| window | Δpnl vs baseline | Δmdd vs baseline |
|---|---|---|
| OOS1 (2025-10 ~ 2026-04) | **+$10,795** | -17.0%p |
| OOS2 (2024-10 ~ 2025-04) | +$133      | +7.5%p |

### 1.3 해석

OOS1에서 D2가 이긴 이유는 **그 window에서 SOL/LINK가 structural loser**였고
filter가 그들을 깨끗이 차단한 덕. OOS2 window에서는:
1. Baseline FixedRR 자체가 breakout을 거의 잡지 못함 (BTC/AVAX n=0)
2. Baseline은 SOL/LINK 두 심볼에 집중 — 결과는 근접 flat
3. D2가 EMA filter로 BTC/AVAX 진입을 허용 → AVAX는 trend 잡고 +$1057,
   BTC는 -$924로 순손실
4. Drawdown이 17% → 34%로 두 배 — entry 증가 + stop 넓힘의 부작용

**결론**: D2 class가 baseline을 이기는 논리는 window-specific. OOS1의
"SOL/LINK 차단"이 기대된 효과였는데 OOS2 window에서는 baseline이
이미 해당 심볼에만 집중되어 차단 효과 자체가 없음. 반대로 BTC/AVAX로
진입 허가하면서 drawdown 증가.

### 1.4 판정

- **D2 class 승격 취소**: protocol §P6 업데이트 필요. `DonchianFixedRRTrendFilter`를
  "PROMOTED" 상태에서 **"WINDOW-DEPENDENT / NOT ROBUST"** 로 격하.
- **D2 best grid cell archived**: 2차 OOS 실패로 운영 default 승격 금지.
  `logs/d2_grid/` artifact는 역사 기록 용도로만 보존.
- **Donchian FixedRR family 전체를 현시점 operational baseline에서 제외**:
  `DonchianFixedRR` 자체도 이미 unusable이고, `DonchianFixedRRTrendFilter`도
  window 의존성 때문에 신뢰 불가. FixedRR 계열은 research candidate로만 유지.
- **`DonchianTrendFilter`**: 기존 보조 축 유지 (변경 없음).

## 2. BBKC BIGTHREE 2차 OOS — STAGED PROMOTE

### 2.1 OOS2 universe 비교

| Universe | n | pnl | wr | avg | sharpe | mdd |
|---|---|---|---|---|---|---|
| ALL5 (baseline) | 170 | **-515.48** | 55.3% | -3.03 | -0.26 | 20.9% |
| BTCETH | 87 | +1162.16 | 59.8% | +13.36 | +1.12 | 15.1% |
| BIGTHREE (BTC+ETH+AVAX) | 93 | **+1029.90** | 59.1% | +11.07 | +0.93 | 15.1% |
| EXCLUDE_SOL | 120 | +231.51 | 56.7% | +1.93 | +0.16 | 15.1% |
| EXCLUDE_SOL_LINK | 93 | +1029.90 | 59.1% | +11.07 | +0.93 | 15.1% |

### 2.2 Verdict

- `BBKCSqueeze[BTCETH]`: **INSUFFICIENT_DATA** (active_symbols=2 < 3)
- `BBKCSqueeze[BIGTHREE]`: **PROMOTE** (Δpnl +$1545, Δavg +$14.11, Δmdd -5.8%p)
- `BBKCSqueeze[EXCLUDE_SOL]`: **PROMOTE** (Δpnl +$747, Δavg +$4.96, Δmdd -5.8%p)
- `BBKCSqueeze[EXCLUDE_SOL_LINK]`: **PROMOTE** (BIGTHREE와 동일)

### 2.3 Cross-window 검증 (2 holdouts)

| Universe | OOS1 pnl | OOS1 mdd | OOS2 pnl | OOS2 mdd | 2-win 재현성 |
|---|---|---|---|---|---|
| ALL5 | +$3700 | 16.7% | -$515 | 20.9% | ALL5 자체는 window 의존 |
| **BIGTHREE** | **+$5579** | **11.8%** | **+$1030** | **15.1%** | **양 window PROMOTE** |
| EXCLUDE_SOL | +$4420 | 11.8% | +$232 | 15.1% | 양 window PROMOTE (약함) |

**핵심 관찰**:
1. ALL5 baseline 자체가 OOS2에서 **net loss** (-$515). BBKCSqueeze는
   regime-dependent.
2. **BIGTHREE는 양쪽 window에서 baseline보다 우수**. 같은 reason
   (SOL/LINK structural loss)이 reproducible.
3. LINK는 OOS1과 OOS2 모두 큰 음수 PnL (OOS1 -$1158, OOS2 -$798).
   SOL도 마찬가지 (-$720, -$747).
4. BTCETH도 강함이지만 active_symbols=2로 규약 미달.
5. EXCLUDE_SOL은 약함 (OOS2 +$232만).

### 2.4 판정

- **BBKCSqueeze[BIGTHREE]**: **STAGED PROMOTE** (paper-ready candidate).
  - P8 §1 (2차 OOS PROMOTE 유지) ✓ 충족
  - P8 §2 (live paper 2주) **미충족** → 이번 턴에서 처리 불가
  - 실제 운영 배포는 paper trading 결과 확인 후에만
  - Development / staging 환경에서는 즉시 BIGTHREE로 교체 가능
- **EXCLUDE_SOL / EXCLUDE_SOL_LINK**: 여전히 candidate, 운영 기본값 아님
- **BBKCSqueeze entry logic**: 절대 불변 (P5 재확인)
- **Gate 2** (BBKC exit-layer): **조건부 개방** — BIGTHREE staged
  promote 상태에서 실행 가능. 단, paper 검증 완료 전까지는
  "development only" 플래그로 표시.

## 3. 업데이트된 전략 상태표 (round 2 + OOS2 기준)

| 전략 | 상태 | 주석 |
|---|---|---|
| `BBKCSqueeze` ALL5 | **CONTROL / DEGRADED** | OOS2에서 net loss, regime-dependent |
| `BBKCSqueeze[BIGTHREE]` | **STAGED PROMOTE** | 2-window PROMOTE, paper 2주 남음 |
| `BBKCSqueeze[BTCETH]` | candidate | active_symbols < 3 규약 미달 |
| `BBKCSqueeze[EXCLUDE_SOL]` | weak candidate | 2-window PROMOTE but 소폭 |
| `BBKCSqueezeHTFTrend` | KILL | destroyed 2 baseline winners |
| `DonchianTrendFilter` | 보조 control 유지 | BTC 단독 prior 약함 |
| `DonchianTrendFilterADX20/25` | KILL | dual regression / symbol prior flip |
| `DonchianFixedRR` | **DEPRECATED** | holdout -$2805, OOS2 -$928 |
| `DonchianFixedRRTrendFilter` (default) | **WINDOW-DEPENDENT (demoted)** | OOS1 PROMOTE, OOS2 KILL |
| D2 best grid cell | **ARCHIVED** | OOS2에서도 KILL |
| RSI / Engulfing / BBKC filter ML | archived | trade-level ML round 종료 |

## 4. 즉시 실행 가능한 후속 액션

### 메인 트랙
1. BBKC BIGTHREE **development 환경 교체** (코드 변경 없음 — universe
   결정은 config/runtime 선택 문제)
2. BBKC BIGTHREE paper trading 세팅 (별도 인프라 필요, 이 턴 밖)
3. BBKC universe에 대한 Gate 2 **조건부 열림** — exit-layer 실험은
   paper 검증 완료 전까지 development only
4. D2 grid artifact는 `logs/d2_grid/`에 보존, `2026-04-14_d2_grid_results.md`에
   "OOS2 검증 실패" 메모 추가

### 금지 사항
- D2 grid 재확장 금지 (class 자체 비robust)
- BBKC entry logic 수정 금지 (P5)
- BBKC exit-layer 실험도 paper 검증 전까지 production 연결 금지

## 5. 변경 이력 요약

- **2026-04-14 round 1**: D2 single-cell PROMOTE, ADX20/25 KILL, HTFTrend KILL
- **2026-04-14 round 2 grid**: D2 class 486-cell grid 62% PROMOTE → 정식 승격 (잠정)
- **2026-04-14 round 2 OOS2**: D2 class KILL (memo + best cell 둘 다),
  **승격 취소**. BBKC BIGTHREE 2-window PROMOTE → **STAGED PROMOTE**.
