# Round 5 Audit: src vs legacy live trade path

**Date**: 2026-04-29
**Phase**: Round 5 Phase A
**Output**: src vs legacy 운영 필수 기능 비교 + A/B/C 결정
**Spec**: `docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round5_design.md` §4

## 1. 비교 대상

### src 경로 (Round 5 candidate)

- `scripts/run_bbkc_live_trade.py` (실제 Bybit Demo REST + WebSocket)
- `src/execution/bbkc_demo_broker.py` (LiveBroker subclass + universe guard + lot-step rounding + orders.jsonl audit)
- `src/execution/live_broker.py` (Bybit REST wrapper, 현재 update_stop은 local-only)
- `src/data_manager/gap_filler.py` (시작 시 누락 봉 채우기)
- `src/api/ws_client.py` (Bybit WebSocket)

참고용:
- `scripts/run_bbkc_paper_live.py` — PaperBroker only, "No Bybit order API ever called" (round 5 검증에 부적합, audit reference만)

### legacy 경로 (reference, 현 라이브 운영 중)

- `_legacy/run_bbkc_trade.py`
- `_legacy/paper_engine/trading_engine.py` (1841 lines, 풍부한 운영 기능)

## 2. 비교표

| 항목 | src | legacy | 격차 |
|---|---|---|---|
| **DB persistence (trade_log/signal_log/fill_log)** | ❌ 별도 테이블 없음 | ✅ trade_log/signal_log via `db.insert_trade_log` | src는 orders.jsonl 단일 audit. trade_log 분리된 SQL 테이블은 없음 |
| **signal/trade/fill 상세 로깅** | 부분 ✅ orders.jsonl per-order, 진입/청산 stdout INFO | ✅ DB + stdout 양쪽 | DB 쿼리 기반 분석 불가, jsonl + log grep으로 대체 |
| **WebSocket 15m confirmed feed** | ❌ src는 **1h confirmed 직접 구독** (`run_bbkc_live_trade.py:277` `ws.start(universe, ["60"])`) | ✅ 15m WebSocket | src는 1h 직접 — 15m 합성 안 함 |
| **시작 시 15m/1h/4h gap fill** | 부분 ✅ 1h만 (`run_bbkc_live_trade.py:152-174` `fill_gap_for_universe(interval="60")`) | ✅ 15m/1h/4h 모두 | BBKCSqueeze는 1h만 사용하므로 1h gap만으로 충분 |
| **15m → 1h 정각 경계 리샘플링** | ❌ src는 합성 안 함 (1h direct WS) | ✅ legacy는 15m × 4 → 1h 합성 | 데이터 source 다름. Bybit 1h confirmed가 정답이라면 합성 불필요 |
| **confirmed 1h에서 전략 실행** | ✅ `run_bbkc_live_trade.py:254` `_dispatch_bar` 1h boundary | ✅ legacy 동일 (1h boundary) | 등가 |
| **API 포지션 vs 로컬 포지션 reconcile** | ✅ heartbeat 60s마다 `broker.sync()` (`run_bbkc_live_trade.py:290`) → `sync_positions()` | ✅ legacy `_reconcile_with_api` (place_order 후 즉시) | 동등. 단 src는 60s 주기, legacy는 매 trade event |
| **demo 재시작 후 진행 중 포지션 복구** | ✅ 시작 시 `_initial_sync()` (`run_bbkc_live_trade.py:134-150`) → `broker.sync()` → Bybit 측 포지션 그대로 인식 | ✅ legacy `engine_state.json` + `_reconcile_with_api` | src는 "real exchange IS the state" 철학 — state file 없음, 매 시작마다 Bybit에서 fresh 상태 |
| **manual close / update_stop 운영 도구** | 부분 — `BbkcDemoBroker.manual_close/manual_buy/manual_sell` 메서드 존재. CLI 진입점 없음 (REPL 또는 별도 script 필요) | ✅ legacy `main.py manual close` CLI | src는 Python REPL/별도 ad-hoc script로 운영 가능 |
| **telegram/log 알림** | ✅ `AlertManager` (`run_bbkc_live_trade.py:411`) | ✅ legacy 동일 | 동등 |
| **heartbeat (60s 간격 equity/positions/daily PnL)** | ✅ `run_bbkc_live_trade.py:282-309` 60_000ms 주기 | ✅ legacy 동일 | 동등 |
| **데모 모드 안전 게이트** | ✅ `--force-live` 없으면 mainnet 거부 (`run_bbkc_live_trade.py:361-371`) | ✅ legacy `app.mode==demo` 게이트 | 동등 |
| **lot-step qty rounding** | ✅ `BbkcDemoBroker._round_qty` instrument spec 기반 | ✅ legacy `_round_qty` | 동등 |
| **universe guard** | ✅ `BbkcDemoBroker._check_universe` BIGTHREE 한정 | 부분 — 30+ symbol 가능 | src가 더 안전 (Round 5 forward에 적합) |
| **SIGINT 처리** | ✅ `_install_signal_handler` + final status log | ✅ legacy 동일 | 동등 |
| **`set_trading_stop` API 호출 가능성** | ❌ **현재 미구현** (`live_broker.py:43-45` local-only) | ✅ Round 2 F2 fix로 구현됨 | **Round 5 핵심 갭 — Phase B에서 구현** |
| **be_trail (TP-fraction) 전략 로직** | ✅ `src/strategies/bbkc_squeeze.py` Round 3-4 그대로 | ❌ legacy strategy는 ATR trailing only (Round 2에서 BBKC 한정 비활성화 게이트만 추가됨) | src에 be_trail 코드 모두 있음, legacy는 포팅 필요 |
| **same account/symbol parallel demo conflict** | ⚠️ 같은 BBKC 진입 시 hedge mode positionIdx 충돌 가능 (대응: 한 번에 한 demo만) | ⚠️ 동일 | parallel(C) 옵션 위험 — 보류 정당화 |

## 3. 격차 분석

### Critical (Round 5 forward 차단 가능)

| # | 격차 | 보완 비용 | Round 5 IN/OUT |
|---|---|---|---|
| 1 | `set_trading_stop` API 미구현 | 작음 (Phase B Tasks 2-6) | **IN** (Round 5 핵심 목적) |

### Non-blocking (운영 가능)

| # | 격차 | 운영 영향 | Round 5 IN/OUT |
|---|---|---|---|
| 2 | trade_log DB 테이블 없음, orders.jsonl만 | 분석은 jsonl + log grep + 백테스트 재계산으로 가능 | OUT (Round 6 후보) |
| 3 | 15m → 1h 합성 안 함, 1h direct WS | Round 5 questions과 직교 (be_trail은 1h boundary). Bybit 1h confirmed가 정답 — parity check 도구로 모니터링 가능 | OUT (parity tool은 §6에 IN, 합성 도입은 OUT) |
| 4 | manual_close CLI 없음 | REPL/ad-hoc script로 가능. runbook §2.2에 절차 명시 | OUT |
| 5 | restart 시 strategy `_pos_meta` 비어있음 | Round 3 lazy init 패턴이 broker 포지션 상태로부터 자동 복구 — 추가 작업 불필요 | OUT (이미 처리됨) |

## 4. 결정

### Decision: **A 채택 (src 충분)**

**이유**:

1. **Round 5 핵심 질문이 정확히 src LiveBroker에 매핑됨**: "set_trading_stop이 Bybit Demo SL을 실제 이동시키는가" — Phase B가 LiveBroker.update_stop을 직접 fix한다. legacy는 이미 Round 2 F2로 set_trading_stop이 들어가 있어, legacy로 가면 그냥 검증된 경로 재확인 — Round 5 가치 낮음.

2. **be_trail 전략 로직이 src에만 있음**: Round 3-4에서 TP-fraction be_trail + integrate_label + 28-cell grid 등 모든 결과물이 `src/strategies/bbkc_squeeze.py`에 구축됨. legacy(B)로 가려면 이 로직을 포팅 + 두 곳 동기화 부담.

3. **15m 합성 vs 1h direct는 Round 5 검증과 직교**:
   - be_trail trigger는 1h boundary에서 발동 (Round 3 §5).
   - 1h SL/TP enforcement는 Bybit 서버 측 (set_trading_stop으로 등록).
   - 15m 단위 local stop check는 legacy의 보너스이지 필수가 아님.
   - Bybit 1h confirmed bar는 합성 봉보다 더 정확 (마지막 15m 누락/timing 문제 없음).
   - parity check 도구(§6)는 두 데이터 source가 같은지 확인하는 안전망 — 자동 fallback 아님.

4. **운영 안전성은 src가 더 강함**:
   - BIGTHREE universe 하드 가드 (`BbkcDemoBroker._check_universe`)
   - --force-live 없이 mainnet 거부
   - lot-step 자동 rounding
   - orders.jsonl per-order audit log
   - Round 5 forward에는 이런 강한 안전 가드가 더 적합.

5. **B/C 옵션 부적합**:
   - **B (legacy 포팅)**: be_trail 로직 + integrate_label까지 legacy에 복사 → DRY 위반, 향후 변경 시 두 곳 동기화 부담. legacy 코드를 굳어진 모드로 두고 src를 진화시키는 정책과 정반대.
   - **C (병렬 demo)**: 같은 hedge mode 계정에서 BBKC 두 demo 동시 → positionIdx=1 충돌. 회피하려면 별도 계정 필요. 운영 복잡도 ↑.

### A + 보완 사항 (Round 5 IN scope 추가 없음)

격차 #2~#5는 모두 OUT으로 분류 — Round 5 IN 항목 추가 없음. 단:

- **trade_log DB 테이블** (#2): Round 6에서 운영 모니터링 강화 시 검토. 현재 orders.jsonl + heartbeat log로 충분.
- **manual_close CLI** (#4): runbook §2.2에 REPL 절차 명시 (이미 plan에 포함됨).
- **15m → 1h 합성** (#3): 도입 안 함. 대신 parity tool (§6)이 1h direct vs 15m synth 비교 — drift 발견 시 운영자 수동 결정.

### Round 5 forward 진입점 확정

```
scripts/run_bbkc_live_trade.py
  + BBKC_ROUND5_MODE=true 가드 (Phase C Task 8)
  + config-derived BBKCSqueeze 인스턴스화 (Phase C Task 8)
  + LiveBroker (Phase B Tasks 2-6 적용 후) → 실제 Bybit Demo set_trading_stop 호출
```

`scripts/run_bbkc_paper_live.py`는 audit/reference만 — Round 5 forward에 사용 안 함.

## 5. 검증 (decision 후속)

| 체크 |
|---|
| [x] Plan §4 audit task가 read-only로 작성됐는가 — 맞음, 이 문서 자체가 결과물 |
| [x] A 채택의 5가지 근거가 spec §3 IN 목록과 정합한가 — Phase B-D 그대로 진행 가능 |
| [x] B/C가 거부된 이유가 spec §4.4와 일치 — DRY 위반 / parallel 충돌 위험 |
| [x] Round 5 IN scope 변동 없음 — A+보완 옵션이 아닌 순수 A 채택 |

## 6. Sign-off

- **Decision**: ✓ A 채택 (src 충분)
- **Round 5 Phase B-D**: 원안대로 진행
- **Date**: 2026-04-29
- **Sign-off**: Phase A 자동 audit 후 사용자 confirm 대기

---

다음 단계: 사용자 confirm → Phase B Task 2 (`set_trading_stop` wrapper) 진행
