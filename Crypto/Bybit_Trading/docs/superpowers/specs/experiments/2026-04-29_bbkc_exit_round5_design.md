# BBKC Exit Strategy Round 5 — Design

**날짜**: 2026-04-29
**상태**: 설계 승인 대기 (브레인스토밍 완료, 구현 미착수)
**선행 작업**:
- Round 4 (`2026-04-29_bbkc_exit_round4_design.md`, 머지 `ffbd0f8`) §14 결과
- Round 2 set_params invariant fix (`a3dd4fe`)
- Round 2 _legacy F2 fix (`0b9c7a2`) — set_trading_stop은 _legacy/api/rest_client.py 한정

**한 줄 요약**: Round 5는 새 전략 탐색이 아니라, **Round 4에서 선정한 `be25_st60_di30`을 실제 forward/paper-live 환경에서 검증 가능한 상태로 만드는 인프라 라운드**. 빠진 연결(`LiveBroker.update_stop` API 미연동)을 보완하고, 운영 정책을 확정하고, forward 시작 절차를 정의한다.

---

## 1. 배경

### 1.1 Round 4 결과 (이월)

ETH 27 fine cells 모두 STRONG_PROMOTE. ROBUST_PROMOTE 10셀. 운영 후보:

- **1순위**: `be25_st60_di30` — ETH wf 7/9, R/trade +0.097, mean PnL +387, BTC/AVAX 둘 다 NEUTRAL (가장 안전)
- 비교 후보: `be25_st60_di20` — ETH wf 7/9, BTC PROMOTE + AVAX STRONG_PROMOTE (3 심볼 동시 개선, 단 dist=0.20은 더 타이트)

청산 조건 탐색은 닫힘. 이제 **운영 가능성 점검** 단계.

### 1.2 가장 critical한 기술 갭

`src/execution/live_broker.py:43-45`:
```python
def update_stop(self, symbol: str, new_stop: float) -> None:
    pos = self._positions.get(symbol)
    if pos: pos.stop_loss = new_stop
```

→ 로컬 `pos.stop_loss`만 갱신. **Bybit API 호출 없음**.

`src/api/rest_client.py`:
- `set_trading_stop` 메서드 **없음** (grep 0 hit).

`src/execution/live_broker.py:85-97 sync_positions`:
- `stop_loss=0.0, take_profit=None` 하드코드. Bybit 측 SL/TP 정보 유실.

→ **현재 상태에서 src 라이브 경로에 be_trail를 배포하면 BE/trailing이 silently 작동 안 함**. Round 2 F2 fix는 `_legacy/`에만 적용했고, Round 2 spec §10 OUT에서 src LiveBroker는 명시적으로 제외.

### 1.3 현재 라이브 운용 경로

`_legacy/run_bbkc_trade.py`:
- 15m WebSocket confirmed candle → `TradingEngine.on_new_bar_15m()`
- 시작 시 `fill_data_gap()`이 15m/1h/4h gap을 Bybit public API로 채움
- `_prefill_buffers()`가 DB의 1h 봉 직접 로드해 워밍업
- 실시간: 15m 4개 → 정각 경계에서 1h 봉 리샘플링 → confirmed 1h 기준으로 전략 실행
- SL/TP 체크: 15m 단위

이 구조는 유지하는 편이 안전 — 15m 단위 SL/TP 체크 + gap 복구가 촘촘 + 1h 전략은 confirmed 1h에서만.

---

## 2. 목적 + 완료 정의

### 2.1 명시적 질문

> "be25_st60_di30 운영을 위한 코드/설정/절차가 모두 갖춰졌는가? Bybit 실거래소 SL이 BE/trail에 따라 실제로 이동하는가?"

### 2.2 Round 5 완료 정의

다음 5개 충족:

1. **src 경로 audit 완료** — A(src 전환) / B(legacy 포팅) / C(병렬) 결정
2. **코드 변경 완료** — `set_trading_stop` wrapper + `LiveBroker.update_stop` API 연동 + `update_tp` 추가 + `sync_positions` SL/TP 파싱 + `positionIdx` 헬퍼
3. **config + env var override 구현** — `bbkc_exit:` 섹션 + `BBKC_EXIT_MODE=fixed` kill switch
4. **운영 정책 문서화** — `be25_st60_di30` default + kill switch 절차 + 기존 포지션 처리 방침
5. **Forward test 절차 정의 + 시작 신호** — 사용자가 demo 재시작 → 라이브 진입

실제 forward 1-3개월 모니터링 + 결과 평가 = **라운드 외부 관찰 기간**. 1개월 mid-review와 3개월 final review는 별도 라운드(R6) 또는 운영 보고로 진행.

### 2.3 자동 종료 금지 원칙 ⚠️

**과거 사고 방지**: 이전 라운드에서 "2주 점검"을 코드가 실제 2주 후 자동 중단으로 구현해서 시스템이 죽은 적 있음.

**Round 5 절대 금지 사항**:
- `end_date` 또는 `expires_at` 같은 자동 종료 타이머
- 특정 날짜 이후 신규 진입 차단 코드 (단, env var `BBKC_DISABLE_NEW_ENTRY`는 Round 6 후보로 별도 — 사용자 명시적 토글)
- "1개월 후 자동 평가 모드 전환" 같은 schedule 기반 동작
- 캘린더 기반 자동 fallback

**1개월/3개월 review = 사람의 점검 일정**. 시스템은 사용자가 수동 중지하거나 kill switch를 켤 때까지 계속 실행.

리뷰 도구는 운영 체크리스트(문서) + DB 쿼리 + 백테스트 비교 — **코드에 박는 게 아님**.

---

## 3. 스코프

### IN

1. src 라이브 경로 audit (legacy 대비 운영 필수 기능 비교)
2. `src/api/rest_client.py`: pybit 기반 `set_trading_stop` wrapper 추가
3. `src/execution/live_broker.py`:
   - `update_stop()` API 연동 (성공 시에만 로컬 갱신)
   - `update_tp()` 신규 + `manual_update_tp()` API 경유
   - `sync_positions()` SL/TP 파싱
   - `_position_idx_for_side()` 헬퍼 (hedge mode 가정 명시)
4. `src/execution/broker.py`: `Broker` Protocol에 `update_tp(symbol, new_tp)` 추가
5. `src/execution/backtest_broker.py` + `position_tracker.py`: `update_tp` 구현
6. `config.yaml` + `src/core/config.py`: `bbkc_exit:` 섹션 + env var override 로딩
7. **15m synthetic 1h vs Bybit direct 1h parity check** — 운영 도구로 추가
8. 운영 정책 문서 (Round 5 설계 문서 §7 + 운영 체크리스트)
9. Forward test 절차 정의 + 시작 신호

### OUT (Round 6 이후)

- 실제 forward 1-3개월 모니터링 + 평가 (라운드 외부 관찰 기간)
- `BBKC_DISABLE_NEW_ENTRY=true` 신규 진입 차단 kill switch (Round 6 후보)
- legacy `_legacy/`의 추가 변경 (Round 2 F2 + BBKC trailing gate 그대로 유지)
- 13코인 일반화
- 라운드 4 결과를 다른 시장 환경(예 다른 거래소)에 일반화
- 자동 schedule 기반 평가 (위 §2.3 자동 종료 금지)

---

## 4. Audit task — A/B/C 결정 gate

Round 5 첫 단계에서 src 라이브 경로가 forward 운영에 충분한지 audit.

### 4.1 비교 대상 파일

**src 경로**:
- `scripts/run_bbkc_paper_live.py`
- `src/execution/paper_runner.py`
- `src/execution/paper_broker.py`
- `src/execution/live_broker.py`

**legacy 경로** (reference):
- `_legacy/run_bbkc_trade.py`
- `_legacy/paper_engine/trading_engine.py`

### 4.2 비교 항목

운영 필수 기능 체크리스트:

| 항목 | 설명 |
|---|---|
| DB persistence | trade_log / signal_log / fill_log 테이블 기록 |
| signal/trade/fill log | 진입/청산 단위 상세 로깅 |
| WebSocket feed | Bybit 15m confirmed candle 실시간 수신 |
| gap fill | 시작 시 15m/1h/4h 누락 봉을 public API로 채움 |
| position reconcile | API 포지션 vs 로컬 포지션 정합성 점검 (legacy의 `_reconcile_with_api`) |
| restart recovery | demo 재시작 후 진행 중 포지션 상태 복구 |
| manual close/update | 운영자가 수동으로 포지션 종료/SL 조정 |
| alert/logging | telegram 또는 로그 파일 기반 거래 알림 |
| 15m → 1h resampling | 15m × 4 → 정각 경계 1h confirmed |
| 15m synthetic 1h vs Bybit direct 1h parity | 합성한 1h가 직접 받은 1h와 일치하는지 검증 |
| 같은 계정/심볼 동시 실행 시 conflict | demo 두 개 동시 실행 시 PositionIdx 충돌 |

### 4.3 결정 게이트

| audit 결과 | 결정 |
|---|---|
| src가 운영 필수 기능 모두 충족 | **A 채택** — Round 5에서 src path에 be25_st60_di30 forward 준비 |
| src에 작은 누락 (1-2개) | **A 채택 + 누락 보완** — Round 5 scope에 보완 항목 추가 |
| src 누락이 큼 (다수 또는 critical) | **B 또는 C 재검토** — 현 시점 결정 보류, 사용자에게 상의 후 확정 |
| 같은 계정/심볼 충돌 위험 큼 | **C 보류** (parallel demo) |

기본 목표는 A. B는 전략 로직을 _legacy에 복붙하게 돼서 장기적으로 별로. C는 실험적으로는 좋지만 주문/포지션 충돌 관리가 귀찮음.

audit은 **읽기 전용**. 코드 변경 0건. 산출물은 비교표 (Markdown) — Round 5 design doc 또는 별도 audit 문서.

---

## 5. 코드 변경 사항

A 채택을 가정한 변경. B/C 채택 시 §5 일부 다시 정의.

### 5.1 `src/api/rest_client.py` — `set_trading_stop` wrapper

```python
def set_trading_stop(
    self,
    symbol: str,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    position_idx: int = 0,
) -> dict:
    """Bybit /v5/position/trading-stop. pybit SDK 경유.

    Args:
        symbol: USDT perpetual 심볼
        stop_loss: 새 stop loss price (None이면 변경 안 함)
        take_profit: 새 take profit price (None이면 변경 안 함)
        position_idx: 0=OneWay, 1=Hedge Buy, 2=Hedge Sell

    Returns:
        API 응답 dict.

    Raises:
        Bybit/HTTP 예외 — caller가 잡아 WARN 로그로 처리할 것.
    """
    params: Dict[str, Any] = {
        "category": "linear",
        "symbol": symbol,
        "tpslMode": "Full",
        "positionIdx": position_idx,
    }
    if stop_loss is not None:
        params["stopLoss"] = str(stop_loss)
    if take_profit is not None:
        params["takeProfit"] = str(take_profit)
    return self._session.set_trading_stop(**params)
```

`_legacy`의 raw `_post` 구현은 참고만. src는 pybit 경유.

### 5.2 `src/execution/live_broker.py` — `update_stop` API 연동 + 헬퍼

```python
def _position_idx_for_side(self, side: str) -> int:
    """Hedge mode 가정: LONG=1, SHORT=2.

    NOTE: 계정이 one-way mode이면 0을 반환해야 함. 현재 src 경로는
    rest_client.place_order의 자동 도출 로직(side='Buy' → 1)과 동일하게
    hedge를 가정. one-way 전환 시 이 헬퍼 + place_order 모두 수정 필요.
    """
    return 1 if side == "LONG" else 2


def update_stop(self, symbol: str, new_stop: float) -> None:
    pos = self._positions.get(symbol)
    if pos is None:
        return
    pos_idx = self._position_idx_for_side(pos.side)
    try:
        self._rest.set_trading_stop(
            symbol=symbol, stop_loss=new_stop, position_idx=pos_idx,
        )
        pos.stop_loss = new_stop   # 성공 시에만 로컬 갱신 (서버 ↔ 로컬 일치)
    except Exception as exc:
        logger.warning(
            "set_trading_stop(stop_loss) failed for %s: %s "
            "— local stop_loss not updated to keep server/local consistent",
            symbol, exc,
        )
```

API 실패 시 로컬 미갱신 → 서버 값 유지. 다음 sync 사이클에서 재시도되도록 함 (재시도 로직은 이번 라운드 OUT, sync는 자연스럽게 다음 update_stop 호출에서 실행).

### 5.3 `src/execution/live_broker.py` — `update_tp` + `manual_update_tp` API 경유

```python
def update_tp(self, symbol: str, new_tp: Optional[float]) -> None:
    pos = self._positions.get(symbol)
    if pos is None:
        return
    pos_idx = self._position_idx_for_side(pos.side)
    try:
        self._rest.set_trading_stop(
            symbol=symbol, take_profit=new_tp, position_idx=pos_idx,
        )
        pos.take_profit = new_tp
    except Exception as exc:
        logger.warning(
            "set_trading_stop(take_profit) failed for %s: %s "
            "— local take_profit not updated",
            symbol, exc,
        )


def manual_update_tp(self, symbol: str, new_tp: float) -> None:
    """Round 5: 로컬만 변경하던 기존 동작 → API 경유로 변경."""
    self.update_tp(symbol, new_tp)
```

### 5.4 `src/execution/live_broker.py` — `sync_positions` SL/TP 파싱

```python
def sync_positions(self) -> None:
    raw_positions = self._rest.get_positions()
    new_positions: Dict[str, Position] = {}
    for raw in raw_positions:
        size = float(raw.get("size", 0))
        if size <= 0:
            continue
        symbol = raw["symbol"]
        side = "LONG" if raw.get("side") == "Buy" else "SHORT"
        # Round 5: stopLoss/takeProfit 필드 파싱. 빈 문자열 또는 None은 0/None로.
        sl_raw = raw.get("stopLoss")
        tp_raw = raw.get("takeProfit")
        new_positions[symbol] = Position(
            symbol=symbol, side=side, qty=size,
            entry_price=float(raw.get("avgPrice", 0)),
            entry_time=0,
            stop_loss=float(sl_raw) if sl_raw not in (None, "", "0") else 0.0,
            take_profit=float(tp_raw) if tp_raw not in (None, "", "0") else None,
            unrealized_pnl=float(raw.get("unrealisedPnl", 0)),
            strategy_name="SYNCED",
        )
    self._positions = new_positions
```

재시작 후 BE/trail로 이동된 SL을 잃지 않도록 함.

### 5.5 `src/execution/broker.py` — Protocol에 `update_tp` 추가

```python
@runtime_checkable
class Broker(Protocol):
    # ... 기존 ...
    def update_stop(self, symbol: str, new_stop: float) -> None: ...
    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None: ...   # 신규
```

### 5.6 `src/execution/backtest_broker.py` + `src/execution/position_tracker.py` — `update_tp` 구현

```python
# backtest_broker.py
def update_tp(self, symbol: str, new_tp: Optional[float]) -> None:
    self._positions.update_tp(symbol, new_tp)

# position_tracker.py — 이미 update_tp 존재 (manual_update_tp 경유). 그대로 사용.
```

### 5.7 테스트 mock 영향

`Broker` Protocol에 `update_tp` 추가하면 `runtime_checkable`로 isinstance 체크하는 곳이 깨질 수 있음. 테스트 검증:

- `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`의 `_MockBroker` — `update_tp` 메서드 추가
- `tests/test_strategies/test_bbkc_squeeze.py`의 `MockBroker` — 동일
- 다른 strategy 테스트의 Mock — grep으로 일괄 점검 (`grep -rn "class.*Broker" tests/`)
- `tests/test_execution/` 전체 회귀 통과 확인

---

## 6. 15m → 1h structure + parity check

### 6.1 구조 유지

15m WebSocket confirmed → `on_new_bar_15m()` → 정각 경계에서 1h 합성 → 1h confirmed에서 전략 실행.

이유:
- 15m 단위 SL/TP 체크 가능
- gap 복구가 촘촘
- 1h 전략은 confirmed 1h에서만 의미

### 6.2 Parity check 도구 추가

15m × 4 합성 1h vs Bybit direct 1h가 일치하는지 주기적 점검. **운영 도구로 추가**, 자동 fallback 아님.

```python
# scripts/check_15m_to_1h_parity.py (신규)
"""최근 N개 1h 봉에 대해:
  - 15m 봉 4개를 합성한 1h 봉 (open=첫 15m open, high=max, low=min, close=마지막 15m close, volume=sum)
  - Bybit /v5/market/kline category=linear interval=60으로 직접 받은 1h 봉
두 값을 비교. 차이가 있으면 봉별로 출력.
"""
```

운영 프로세스:
- demo 시작 직후 + 1주에 1회 수동 실행
- 차이 발견 시 결정:
  - 미세한 floating point/timing 차이 → 무시
  - 반복적·구조적 차이 → 전략 신호는 Bybit confirmed 1h 직접봉, 포지션 관리는 15m로 분리하는 대안 검토 (이번 라운드 OUT, Round 6 후보)

### 6.3 자동 fallback 안 함

자동 schedule 기반 동작 금지 원칙 (§2.3). parity 차이가 발견되어도 운영자가 수동으로 결정.

---

## 7. 운영 정책

### 7.1 Config + Env var override

`config.yaml` 추가:
```yaml
bbkc_exit:
  # Round 4 ROBUST_PROMOTE 1순위 (ETH wf 7/9 R+0.097, BTC/AVAX NEUTRAL)
  mode: be_trail
  trail_be_at_tp_frac: 0.25
  trail_start_at_tp_frac: 0.60
  trail_distance_tp_frac: 0.30
  drop_tp: false
  time_stop_bars: 0
```

`src/core/config.py` 로딩 시 env var override:

```python
def load_bbkc_exit_config() -> Dict[str, Any]:
    cfg = self._config.get("bbkc_exit", {})
    # env var override (kill switch)
    env_mode = os.getenv("BBKC_EXIT_MODE")
    if env_mode:
        cfg["mode"] = env_mode
        logger.warning(
            "BBKC_EXIT_MODE env override active: mode=%s "
            "(this is a kill-switch path; check rollback procedure)",
            env_mode,
        )
    return cfg
```

`scripts/run_bbkc_paper_live.py` 또는 audit 후 채택된 진입점이 이 config로 전략 인스턴스화.

### 7.2 Kill switch 절차

**시나리오 1: 신규 진입 fixed 롤백**
```
$ export BBKC_EXIT_MODE=fixed
$ python -m scripts.run_bbkc_paper_live   # 또는 데모 재시작
```
다음 전략 인스턴스 생성 시 fixed 모드. 신규 진입은 fixed SL/TP로 들어감.

**시나리오 2: 이미 열린 포지션**

자동 rollback **하지 않음**. 이유:
- BE/trail로 이동된 SL이 Bybit에 등록되어 있을 수 있음
- 자동 되돌리면 운영자가 의도하지 않은 SL 변경 발생 위험
- 안전 default = "건드리지 않음"

운영자 선택지:
1. **자연 종료 대기** — 현 SL/TP가 트리거할 때까지 둠
2. **수동 close** — `LiveBroker.manual_close(symbol, reason="rollback")` 호출
3. **수동 SL 되돌리기** — `LiveBroker.manual_update_stop(symbol, original_fixed_sl)` 호출

이 정책은 **운영 체크리스트(문서)에 명시**. 코드에 박지 않음.

**시나리오 3: 긴급 신규 진입 차단** — 이번 라운드 OUT, Round 6 후보 (`BBKC_DISABLE_NEW_ENTRY=true`).

### 7.3 운영 후보

| 후보 | 사용처 |
|---|---|
| **be25_st60_di30 (default)** | Round 5 forward 운영 1순위. config.yaml의 default. |
| be25_st60_di20 | 비교용. 수동 config 변경으로 forward 1주 등 시도 가능. 단 dist=0.20이라 더 타이트. |
| F0 (fixed) | env var rollback 시 활성. baseline 검증용. |

기본은 **공통 be25_st60_di30** (모든 심볼 같은 cell). 심볼별 다른 cell은 Round 6 이후 검토.

---

## 8. Forward test 절차

### 8.1 명목 기간

- **1개월 mid-review**: 기술 검증 중심
- **3개월 final-review**: 성과 평가 중심

⚠️ **이건 코드에 구현하지 않는 리뷰 일정**. 자동 중지 없음 (§2.3).

### 8.2 1개월 mid-review (기술 검증)

| 항목 | PASS 조건 | FAIL 조건 |
|---|---|---|
| `set_trading_stop` 호출 | BE/trail trigger 시 호출 로그 발생 | 로그 0건 → 코드/연동 버그 |
| Bybit 측 stopLoss 변화 | sync_positions로 확인된 stopLoss가 BE/trail 따라 이동 | 진입 시 fixed SL 그대로 → API 실패 silent |
| `sync_positions` SL/TP 유지 | 재시작 후 stop_loss/take_profit이 0/None이 아닌 실제 값 | 재시작 후 0으로 리셋 → 파싱 버그 |
| demo 재시작 복구 | 재시작 후 자연스럽게 포지션 상태 인식 | 반복 crash → fix 필요 |
| ETH 진입 | 최소 1건 이상 (있으면 좋음) | 0건은 FAIL이 아니라 **WATCH/기술 검증 미완료**로 분류 |

1개월 동안 거래 0건이면: 시그널 환경 문제일 수도 있고 (squeeze 발생률 낮음) 실제 코드 버그일 수도 있음. 진입 0건 = 자동 FAIL이 아니라 추가 1개월 관찰.

### 8.3 3개월 final 평가 (성과 비교)

baseline:
- **L1**: Round 4 F0 ETH 백테스트 (wf 4/9, R/trade +0.024, mean PnL +154/2mo OOS)
- **L2** (의심 시): forward 3개월과 같은 캘린더 기간의 fixed 백테스트 재계산 — 시장 환경 차이 보정용

forward 3개월 데이터를 단일 OOS 윈도우 취급하여 비교:

| 판정 | 조건 |
|---|---|
| **PASS** | ETH R/trade ≥ 0 AND mean PnL ≥ L1 baseline |
| **STRONG PASS** | 위 + R/trade > +0.04 (Round 4 backtest 7셀 평균보다 약간 낮은 보수 기준선) |
| **WATCH** | -0.05 < ETH R/trade < 0 → 추가 1개월 관찰 |
| **FAIL** | ETH R/trade < -0.05 OR mean PnL < 0 → env rollback + 원인 분석 |

### 8.4 평가는 사람의 일정

1개월/3개월 시점은 운영자가 캘린더 기준으로 점검. 시스템은 사용자가 수동 중지하거나 kill switch를 켤 때까지 계속 실행.

평가 도구:
- DB 쿼리: forward 거래 metrics 추출
- 백테스트 재계산: 같은 기간 fixed로 paper 재계산 (L2 baseline)
- 결과는 별도 운영 문서 또는 Round 6 spec 입력으로 사용

---

## 9. 파일 변경 목록

### 새 파일

- `docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round5_design.md` (이 문서)
- `scripts/check_15m_to_1h_parity.py` — parity check 도구
- `docs/operations/bbkc_exit_round5_runbook.md` (신규) — Round 5 운영 체크리스트 (kill switch 절차, 1개월 mid-review 절차, 기존 포지션 처리)

### 수정

- `src/api/rest_client.py` — `set_trading_stop` 메서드 추가
- `src/execution/live_broker.py`:
  - `_position_idx_for_side` 헬퍼 추가
  - `update_stop`: API 연동
  - `update_tp` 신규 + `manual_update_tp` API 경유
  - `sync_positions`: SL/TP 파싱
- `src/execution/broker.py` — Protocol에 `update_tp` 추가
- `src/execution/backtest_broker.py` — `update_tp` 메서드 추가
- `config.yaml` — `bbkc_exit:` 섹션 추가
- `src/core/config.py` — env var override 로딩
- `scripts/run_bbkc_paper_live.py` (또는 audit 후 결정된 진입점) — config에서 exit 파라미터 읽어 BBKCSqueeze 인스턴스화
- 테스트 (위 §5.7 영향 받는 mock들)

### 미변경 (확인용)

- `src/strategies/bbkc_squeeze.py` — Round 3/4 그대로
- `src/strategies/registry_builder.py` — Round 4 28셀 그대로
- `_legacy/` — 변경 없음 (Round 2 F2 + BBKC trailing gate 유지)

---

## 10. 테스트

### 10.1 단위 테스트

- `tests/test_api/test_rest_client.py` — `set_trading_stop` 새 케이스 (LONG/SHORT, SL only, TP only, both, position_idx)
- `tests/test_execution/test_live_broker.py`:
  - `update_stop` 성공 시 로컬 갱신
  - `update_stop` 실패 시 로컬 미갱신 + WARN 로그
  - `update_tp` 동일 패턴
  - `_position_idx_for_side` LONG=1, SHORT=2
  - `sync_positions` Bybit 응답에 stopLoss="99.5" → Position.stop_loss = 99.5
  - `sync_positions` stopLoss="0" 또는 None → 0.0
- `tests/test_execution/test_backtest_broker.py` — `update_tp` 추가
- `tests/test_execution/test_broker_protocol.py` (신규 또는 기존 확장) — Protocol 적합성 (runtime_checkable isinstance)

### 10.2 통합 테스트

- 실제 Bybit demo API에 set_trading_stop 호출하는 가벼운 e2e 테스트 (옵션, 환경변수 가드)

### 10.3 회귀

- 전체 src 테스트: `python -m pytest tests/test_strategies/ tests/test_execution/ tests/test_scripts/ tests/test_api/ -q`
- legacy 테스트: `python -m pytest tests/_legacy/ -q`
- 모든 mock에 `update_tp` 추가 후 통과

### 10.4 Audit 산출물 (코드가 아닌 문서)

- `docs/operations/src_vs_legacy_audit.md` — §4.2 비교표 + 결정

---

## 11. 리스크 / 알려진 한계

1. **pybit `set_trading_stop` SDK 가용성**: pybit 버전 따라 메서드 이름/시그니처 다를 수 있음. 구현 시 pybit 버전 확인 + 필요 시 raw signed POST fallback.
2. **계정 모드 가정**: 현재 코드 hedge mode 가정. one-way 전환 시 `_position_idx_for_side` + `place_order` 자동 도출 로직 둘 다 수정 필요. 이번 라운드는 hedge 가정만 명시화.
3. **API rate limit**: Bybit /v5/position/trading-stop은 10 req/s 한도. be_trail의 trailing ratchet은 매 1h 봉마다 (BIGTHREE 3개 × 1회 = 3 req/h) — 무시 가능 수준.
4. **API 실패 silent risk**: update_stop 실패 시 로컬 미갱신 — 다음 trail trigger 호출 시 재시도. 하지만 영구 실패 (예: 권한 변경) 발생 시 trailing이 무력화될 수 있음. WARN 로그 모니터링 권장.
5. **동시 두 demo 실행**: hedge 모드라도 같은 심볼/방향(LONG)을 두 demo가 진입하면 positionIdx=1 충돌. → audit에서 C 옵션 보류 결정 시 자연 회피.
6. **Round 5 자체가 forward 결과 평가를 포함하지 않음**: 1-3개월 결과는 라운드 외부. Round 6에서 평가 → 다음 액션.
7. **15m/1h parity 도구는 자동화 안 됨**: 사용자가 잊으면 silent drift. runbook에 1주 1회 수동 실행 명시.

---

## 12. Out of Scope (Round 6 이후)

1. **`BBKC_DISABLE_NEW_ENTRY=true` 신규 진입 차단 kill switch** — 운영 유용성 높지만 구현 scope 추가 (§2.3 자동 종료 금지와 다름; 이건 사용자 명시적 토글)
2. **forward 1-3개월 모니터링 + 평가 → Round 6 spec 입력**
3. **legacy → src 완전 전환** (audit 결과 A 채택 시 일부 진행, B/C면 이월)
4. **One-way mode 전환 지원** — 현재 hedge 가정만 명시, 전환은 별도 라운드
5. **15m synthetic vs Bybit direct 1h 자동 fallback** — parity 차이 발견 시 운영자 수동 결정 (이번 라운드는 도구만 제공)
6. **13코인 일반화** — Round 4 백테스트 결과를 BIGTHREE 외 코인에 적용
7. **다른 청산 primitive** (partial TP, time_stop ETH 정밀화 등) — Round 4 §14.8 이월

---

## 13. 다음 단계

1. **본 문서 사용자 검토 + 승인**
2. **writing-plans 스킬로 전환** — 본 설계를 단계별 구현 plan으로 분해
3. 구현은 plan 승인 후 시작
4. 라운드 종료 후 §14 round-up 작성

---

## 14. Round 5 Results (라운드 종료 후 채울 placeholder)

**Status**: TBD

### 14.1 Audit 결과 (A/B/C 결정)

(src 경로 audit 비교표 요약 + 채택안)

### 14.2 코드 변경 완료 사항

(set_trading_stop, update_stop API 연동, update_tp, sync_positions, helper 등 — 머지된 파일/커밋 목록)

### 14.3 운영 정책 확정 사항

(config.yaml 적용된 값, kill switch 절차, 기존 포지션 처리 방침)

### 14.4 Forward 시작 신호

(forward 시작 일시, 첫 진입 트랜잭션, 초기 ETH/BTC/AVAX 포지션 상태)

### 14.5 Round 6 후보

(forward 1개월 mid-review 후 결정. Round 5 시점에서는 enumerate만)

### 14.6 한 줄 요약

(라운드 5 결론 + forward 모니터링 시작 + Round 6 입력 예고)
