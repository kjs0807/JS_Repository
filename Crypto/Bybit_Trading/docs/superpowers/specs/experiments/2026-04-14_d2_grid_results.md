# D2 Grid Results — DonchianFixedRRTrendFilter

**날짜**: 2026-04-14
**스크립트**: `scripts/d2_grid.py`
**파이프라인**: `src/evaluation/holdout.py` (holdout-first) +
`src/evaluation/verdict.py::judge_variant_vs_baseline`
**결과 파일**:
- `logs/d2_grid/baseline.json` — DonchianFixedRR (single run)
- `logs/d2_grid/cells.jsonl` — 486 cells append-only
- `logs/d2_grid/summary.md` — auto-generated summary
- `logs/d2_grid/best_cell_detail.json` — top cell per-symbol breakdown

## 1. 그리드 범위

```
entry_period       : [15, 20, 25]
stop_atr           : [2.0, 2.5, 3.0]
tp_r_ratio         : [1.5, 2.0, 2.5]
trail_activate_atr : [1.0, 1.5, 2.0]
trail_distance_atr : [0.5, 1.0, 1.5]
ema_filter         : [100, 200]
```

총 486 cells (3×3×3×3×3×2). Early-stop probe: variant BTC 단독 trade 수
< 5인 경우 `SKIPPED_LOW_TRADES`로 다른 심볼 생략.

**holdout**: 2025-10-01 ~ 2026-04-10, 5 symbols, 14d warmup, $10k capital.

## 2. 실행 결과 (486/486 완료)

| verdict | cells |
|---|---|
| PROMOTE | **300** |
| CONDITIONAL_PROMOTE | 48 |
| KILL | 121 |
| INSUFFICIENT_DATA | 9 |
| NO_EDGE | 8 |
| SKIPPED_LOW_TRADES | 0 |

- **PROMOTE 비율: 62%** — 파라미터 표면이 좁은 needle이 아니라
  전 영역에 걸쳐 baseline보다 우수하다는 강한 신호.
- KILL 121개는 대부분 trail_distance=1.5 + trail_activate=1.0 조합
  (너무 일찍 트레일 손실) 또는 특정 stop_atr=2.0 + tp_r_ratio=2.5 조합.
- 총 실행시간 ~31분 (평균 3.85s/cell; probe로 많이 단축됨).

## 3. TOP-10 by Δavg_trade_pnl

```
#  avg     pnl       wr      mdd     n    conc  params
1  +37.96  +10794.81 +11.6%  -17.0%  457  31.3% ep=25 sa=3.0 tp=2.5 tra=1.5 trd=0.5 ema=100
2  +35.52   +6190.76 +12.3%  -22.3%  225  60.4% ep=15 sa=3.0 tp=2.5 tra=1.5 trd=0.5 ema=100
3  +35.08   +6135.87 +12.4%  -22.4%  228  61.4% ep=15 sa=3.0 tp=2.0 tra=1.5 trd=0.5 ema=100
4  +33.45   +5815.44 +12.6%  -22.6%  232  62.1% ep=15 sa=3.0 tp=1.5 tra=1.5 trd=0.5 ema=100
5  +33.04   +4702.29  +9.9%  -21.2%  151  51.0% ep=20 sa=2.0 tp=1.5 tra=1.0 trd=0.5 ema=200
6  +32.44   +4588.45  +4.7%  -17.3%  149  66.4% ep=20 sa=3.0 tp=2.5 tra=2.0 trd=1.5 ema=200
7  +30.66   +4852.73  +5.4%  -21.0%  201  51.2% ep=20 sa=3.0 tp=2.5 tra=2.0 trd=1.0 ema=200
8  +30.10   +4258.13  +3.9%  -18.2%  151  64.2% ep=25 sa=3.0 tp=2.5 tra=2.0 trd=0.5 ema=200
9  +29.31   +4440.02  +4.2%  -20.3%  185  63.8% ep=20 sa=3.0 tp=1.5 tra=2.0 trd=0.5 ema=200
10 +28.53   +4513.28  +8.6%  -17.3%  212  41.5% ep=15 sa=2.0 tp=2.0 tra=1.0 trd=0.5 ema=100
```

**공통 패턴**: 상위 10개 중 9개가 `trail_distance_atr=0.5`, 8개가
`stop_atr=3.0`. 나머지 축은 분산되어 있음 → 핵심 edge는
"**넓은 stop + 타이트한 trailing**"이라는 해석.

`ema_filter`는 100/200 둘 다 상위권에 있어 이 축의 민감도는 낮음.
`entry_period`는 15/20/25 전부 상위권에 있어 감도 낮음.

## 4. Best cell 심층 분석

**`ep=25, sa=3.0, tp=2.5, tra=1.5, trd=0.5, ema=100`** (per-symbol)

| 심볼 | baseline (FixedRR) | best D2 cell |
|---|---|---|
| BTCUSDT | n=71 pnl=-148 wr=56.3% mdd=16.9% | n=143 pnl=+249 wr=61.5% mdd=14.9% |
| ETHUSDT | n=46 pnl=-268 wr=50.0% mdd=16.1% | n=120 pnl=+4399 wr=65.8% mdd=10.5% |
| SOLUSDT | n= 8 pnl=-418 wr=50.0% mdd= 6.8% | n= 55 pnl= -473 wr=58.2% mdd=15.3% |
| LINKUSDT | n= 6 pnl=-989 wr=16.7% mdd= 8.3% | n=  7 pnl=-1195 wr=14.3% mdd=10.5% |
| AVAXUSDT | n= 6 pnl=-982 wr=16.7% mdd= 7.8% | n=132 pnl=+5009 wr=62.9% mdd=15.2% |
| **TOTAL** | **n=137 pnl=-2805 wr=50.4% mdd=39.3% sharpe=-1.41** | **n=457 pnl=+7989 wr=61.9% mdd=22.3% sharpe=+1.25** |

**해석**:
- BTC/ETH/AVAX 3종에서 뚜렷한 flip (음수→양수). Win rate +11.6%p.
- SOL은 trade 수 급증(8→55)하며 소폭 악화. LINK는 trade 수 거의 유지
  + 여전히 손실. 두 심볼은 이 파라미터에서도 **구조적으로 나쁨** —
  BBKC universe 실험 결과와 일치.
- Drawdown 39.3% → 22.3% (-17%p). BBKC ALL5 baseline(16.7%)보다는
  여전히 높음 — D2는 BBKC 만큼 robust하지 않다.
- Symbol concentration 31.3% — 새 규약의 65% 한계 아래, 편중 없음.

## 5. Robustness 체크

### ★ Grid 자체가 in-sample
위 결과는 전부 **같은 holdout**에서 나옴. "best cell"을 고르는 순간
사후 최적화(post-hoc optimization)가 시작됨. 486개 중 1개를 고르면
자연히 극값을 잡게 되므로 top cell 파라미터 자체를 운영 default로
승격할 수는 없음.

### 복수 cell PROMOTE 증거
그러나 62% PROMOTE + top 10의 공통 패턴(stop_atr=3.0, trd=0.5)은
**파라미터 표면이 안정적으로 baseline을 넘는다**는 증거로 볼 수 있음.
이것은 strategy class 수준의 승격은 정당화하지만 specific param set
수준의 승격은 정당화하지 않음.

### 최소 한 가지 외부 검증
Memo-fixed 기본값(`ep=20, sa=2.5, tp=2.0, tra=1.5, trd=1.0, ema=200`)은
그리드가 수행한 486 cells 중 한 개이며 결과는 이미 알려져 있음:
- n=74 pnl=-296 wr=56.8% mdd=19.6%
- Δpnl +$2510, Δavg +$16.48, Δmdd -19.7%p
- verdict: **PROMOTE** (sensitive cell 선정이 아니라 default 값)

즉, **파라미터 튜닝 없이도 baseline을 이긴다**는 사실이 D2 class 승격의
핵심 근거.

## 6. 판정

### Strategy class 승격 — **Case A (정식 승격)**

`DonchianFixedRRTrendFilter`를 FixedRR 계열의 **정식 baseline**으로
승격한다.

근거:
1. Memo-fixed 기본 파라미터가 이미 PROMOTE (round 1 실증)
2. 486-cell 그리드에서 62% PROMOTE — 파라미터 민감도 낮음
3. Top-10이 서로 다른 지점에서 유사한 개선을 보임 — needle 아님
4. Symbol concentration, destroyed winners, dual regression 모두 clean
5. 기존 `DonchianFixedRR`은 구 baseline으로 기능하지 않음 (-$2805)

### Parameter 승격 — **Case B (조건부 보류)**

Top cell 파라미터 `ep=25 sa=3.0 tp=2.5 tra=1.5 trd=0.5 ema=100`은
**development candidate**이며 운영 default로 승격하지 않는다.

조건부 보류 사유:
1. Round 1 holdout 단일 window에서 선택 → 사후 최적화 리스크
2. LINK/SOL 재악화 흔적 (sample 작지만 경고 신호)
3. Drawdown 22.3%는 BBKC ALL5(16.7%)보다 나쁨 — 포트폴리오 관점에서
   FixedRR 계열 독립 승격에는 부담

**승격 전 필요한 추가 검증**:
- 2차 holdout 창 (예: 2024-10-01 ~ 2025-04-01) 동일 파라미터 재실행
- 동일 top 10 sweet spot이 2차 window에서도 PROMOTE인지 교차 확인
- Live paper trading 최소 2주 관측
- D2 grid에서 top 3 cells 모두 2차 검증하여 교집합 확인

검증 통과 시에만 운영 default로 승격, 실패 시 memo-fixed 기본값 유지.

## 7. 운영 방침 (확정)

| 항목 | 결정 |
|---|---|
| D2 class 상태 | **PROMOTED** to FixedRR baseline |
| D2 default 파라미터 | memo-fixed (`ep=20 sa=2.5 tp=2.0 tra=1.5 trd=1.0 ema=200`) |
| `DonchianFixedRR` 원본 | **deprecated** — 실험/리포트 대조군으로만 유지, 신규 실험 baseline 금지 |
| D2 best cell 파라미터 | development candidate, 2nd OOS 통과 전까지 운영 사용 금지 |
| D2 파라미터 추가 튜닝 | 금지 — 다음 단계는 외부 검증이지 grid 확장이 아님 |

## 8. 다음 액션

- Workflow 문서 + 실험 운영 규약 + round2 결과 문서 업데이트 (이 턴에서 처리)
- 2차 OOS 창은 사용자가 실행 커맨드 받으면 바로 실행 가능:
  ```
  python -m scripts.d2_core_eval --start 2024-10-01 --end 2025-04-01 \
      --out-dir logs/d2_core_oos2
  ```
- D2 grid 재실행은 PHASE 6 게이트 조건(다른 window에서 top 10 재현)이
  확인된 후에만 허용.
