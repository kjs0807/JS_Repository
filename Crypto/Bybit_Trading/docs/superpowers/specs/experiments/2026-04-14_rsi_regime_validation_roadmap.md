# RSI Divergence Regime — Validation Roadmap

**날짜**: 2026-04-14
**트랙**: Research (protocol §P9). 전략 연결 금지.
**선행**: `2026-04-14_rsi_regime_research_problem.md`,
`2026-04-14_rsi_regime_go_decision.md`
**신규 구현**:
- `src/research/regime/output_schema.py` (RegimeOutput contract)
- `src/research/regime/gating_eval.py` (gating simulator)
- `scripts/run_multi_symbol_regime.py` (multi-symbol driver)
- `scripts/simulate_regime_gating.py` (gating simulation CLI)

## 1. 목표

BTC에서 GO 판정을 받은 RSI regime signal을 **"연구 아이템"에서 "전략
연결 검증이 가능한 후보 signal"로** 끌어올린다. 전략 연결 자체는
여전히 금지 (§P9). 이 단계에서 하는 일은:

1. 다심볼 교차 검증
2. Cross-asset consistency 측정
3. Regime output contract 정의
4. Gating simulation 프레임워크 마련
5. 전략 연결 전 필요한 모든 검증을 "검증 가능한 코드"로 남겨두기

## 2. 검증 질문과 답할 방법

| 질문 | 답할 방법 | 상태 |
|---|---|---|
| BTC만 특수한가? | `run_multi_symbol_regime.py`로 ETH/SOL/LINK/AVAX 전부 실행, `cross_asset_strong` 개수 확인 | **구현 + 일부 실행 완료** (BTC+ETH) |
| Signal 출력 형태는? | `RegimeOutput` dataclass에 state/score/confidence/horizon + lookahead-safe timestamp | 구현 완료 |
| 전략에 어떻게 붙일 것인가? | `gating_eval.py::simulate_gating` — 규칙 기반 long/short/block 정책을 forward return에 적용 | 구현 완료 |
| 직접 entry로 써도 되는가? | **아니오** — simulation은 backtest가 아님. 전략 연결 허가는 §P9 5개 조건 충족 후 | 정책 고정 |
| 현재 BTC 결과는? | h=20 unconditional Sharpe 0.05 → gated 0.29, h=40 0.07 → 0.17, h=60 0.07 → 0.19 | 실행 완료 |

## 3. 다심볼 확장 결과 (2026-04-14 실행)

```bash
python -m scripts.run_multi_symbol_regime --symbols BTCUSDT ETHUSDT
```

| Symbol | Events | IS | OOS | Strong lifts |
|---|---|---|---|---|
| BTCUSDT | 161 | 128 | 33 | 5 |
| ETHUSDT | 147 | 117 | 30 | 8 |

**Cross-asset strong lifts** (BTC AND ETH 양쪽에서 IS/OOS 재현):
- `h=20 regular_bear → UP 억제`
- `h=40 regular_bear → UP 억제`

**핵심 관찰**: **"regular bear → short-TF UP regime 억제"** 는 2개 심볼에서
교차 재현되는 가장 강력한 cross-asset signal. Hidden_bear 관련
ETH 단독 signal이 추가로 있지만 BTC에서는 약하므로 단독 심볼
prior로 해석.

다음 확장 (이 턴 밖):
```bash
python -m scripts.run_multi_symbol_regime
# 5 symbols 전부. SOLUSDT / LINKUSDT / AVAXUSDT는 2021 중후반부터
# 데이터 있으므로 이벤트 수가 적을 수 있음
```

## 4. Regime output contract

```python
@dataclass(frozen=True)
class RegimeOutput:
    asof_ms: int
    valid_from_ms: int
    valid_until_ms: int
    symbol: str
    state: RegimeState  # UP / FLAT / DOWN / UNKNOWN
    score: float        # [-1, +1]
    confidence: float   # [0, 1]
    horizon_bars: int
    source: str         # "rsi_divergence_daily_v1:regular_bear"
```

**Lookahead 원칙**:
1. `valid_from_ms > asof_ms` — 같은 bar에 작용 금지
2. `valid_from_ms = asof_ms + confirmation_bars * 86_400_000`
3. `valid_until_ms = valid_from_ms + horizon_bars * 86_400_000`

**Consumer 제약** (§P9 합류 후에만 적용):
- `valid_from_ms ≤ query_ts ≤ valid_until_ms`인 경우에만 state 조회
- 누락 record는 `UNKNOWN`으로 간주, fallback
- Score 크기로 sizing 하지 말 것 (연구 단계)
- Daily artifact를 1h strategy에 적용 시 그 날의 1h bar들은 모두
  같은 regime state를 공유

## 5. Gating simulation framework

`src/research/regime/gating_eval.py::simulate_gating`:

- 입력: events DataFrame + horizon + `GatingRule` 리스트 + baseline_close
- 동작: `(div_type, horizon) → long/short/block/allow` 정책 적용 후
  forward log-return subset의 mean/std/Sharpe/win_rate 계산
- 출력: `GatingSimulationResult` (unconditional vs gated + per-rule)

**기본 정책** (BTC 연구 prior, `DEFAULT_GATING_RULES_BTC_RESEARCH`):

```python
GatingRule("regular_bear", 20, "short"),
GatingRule("regular_bear", 40, "short"),
GatingRule("hidden_bull",  20, "long"),
GatingRule("hidden_bull",  40, "long"),
GatingRule("regular_bull", 60, "short"),
GatingRule("hidden_bear",  20, "block"),
GatingRule("hidden_bear",  40, "block"),
GatingRule("hidden_bear",  60, "block"),
```

**BTC gating simulation 결과**:

| horizon | unconditional | gated | improvement |
|---|---|---|---|
| 20 | Sharpe +0.05, mean +0.0069, WR 51.2% | Sharpe +0.29, mean +0.0324, WR **66.9%** | ~6x Sharpe |
| 40 | +0.07, +0.0143 | +0.17, +0.0291 | ~2.4x |
| 60 | +0.07, +0.0178 | +0.19, +0.0447 | ~2.7x |

**주의**:
- Unconditional N은 1910 (전체 일봉), gated N은 133. 표본 편차 큼.
- Transaction cost / slippage / execution delay 미반영.
- Rules가 IS 기반에서 유도되었으므로 완전 out-of-sample이 아님.
- **이건 backtest가 아니라 research sketch**. 전략 연결 근거로
  사용하지 말 것.

## 6. 향후 단계 (실제 연결 전)

### R1. 다심볼 전체 확장 ✓ (partial, BTC+ETH 완료)

```bash
python -m scripts.run_multi_symbol_regime
```

### R2. Cross-asset consistency 검증 ✓ (partial)

현재 `run_multi_symbol_regime.py`가 `cross_asset_strong`을 자동
추출. 최소 2개 심볼 교차 재현되는 (horizon, div_type, regime) 조합만
"robust"로 간주.

**다음**: 5개 심볼 전부 돌린 뒤 3+ 심볼 교차 재현되는 조합이 있는지
확인. 3+ symbol consistent signal만 R4로 진출.

### R3. Stability / drift 관찰 (미구현)

이번 턴 scope 밖. 필요 설계:
- 매일 `train_rsi_regime` 재실행
- 전날 대비 cross_window_lifts.json diff 계산
- 30일 동안 lift drift < 20% 유지 시 stability 확인
- 구현안: `scripts/daily_regime_stability.py` (future work)
- 저장: `logs/research/rsi_regime_stability/<date>/`

### R4. Gating simulation 반복 ✓ (BTC 완료)

```bash
python -m scripts.simulate_regime_gating --events-dir logs/research/rsi_regime/
python -m scripts.simulate_regime_gating --events-dir logs/research/rsi_regime_multi/ETHUSDT/
```

**다음**: cross-asset consistent signal만 추려서 custom policy 파일
작성 후 simulate → IS/OOS 분리 stats 확인.

### R5. 전략 연결 조건 (strict)

§P9 기준을 다시 명시:
1. Research 결과가 **3개 이상 심볼**에서 교차 재현 (현재 BTC+ETH
   = 2개만)
2. **30일 이상 stability** (미구현)
3. Protocol 변경 문서 + 승인 (새 P-section)
4. Trade-level bar-level comparator에 연결 전 OOS 검증
5. 메인 트랙이 안정 (BBKC BIGTHREE paper 통과 후)

**현재 만족 조건**: 1/5 (partial — 2 symbols만). 나머지 4가지 미충족.
**따라서 현재 전략 연결은 여전히 금지**.

## 7. 금지 사항 요약

- `src/strategies/` 또는 `src/execution/` 어느 파일도 `src/research/`
  import 금지
- BBKCSqueeze/DonchianTrendFilter의 `on_bar_fast`에 `RegimeOutput`
  조회 코드 추가 금지
- Gating simulation 결과를 근거로 BBKC universe / exit-layer / entry
  파라미터 변경 금지
- Live paper run / BBKC paper run 로그 파이프라인에 regime 산출물
  연결 금지
- RSI regime "score"를 실거래 포지션 사이징에 사용 금지

## 8. 재현 커맨드 (한 장 요약)

```bash
# 1. 다심볼 train + evaluate
python -m scripts.run_multi_symbol_regime

# 2. BTC 기본 정책 gating 시뮬레이션
python -m scripts.simulate_regime_gating

# 3. ETH 기본 정책 gating 시뮬레이션
python -m scripts.simulate_regime_gating \
    --events-dir logs/research/rsi_regime_multi/ETHUSDT

# 4. 커스텀 policy 파일로 gating
python -m scripts.simulate_regime_gating \
    --events-dir logs/research/rsi_regime \
    --policy /path/to/policy.json
```

Policy JSON schema:
```json
[
  {"div_type": "regular_bear", "horizon": 20, "direction": "short"},
  {"div_type": "hidden_bull", "horizon": 20, "direction": "long"},
  {"div_type": "hidden_bear", "horizon": 40, "direction": "block"}
]
```

## 9. 변경 이력

- **2026-04-14**: 초판. BTC+ETH 다심볼 확장, output_schema +
  gating_eval 구현 + 실행 완료. Cross-asset 2-agreement 확인.
  전략 연결 조건 1/5 충족, 나머지 4개 대기.
