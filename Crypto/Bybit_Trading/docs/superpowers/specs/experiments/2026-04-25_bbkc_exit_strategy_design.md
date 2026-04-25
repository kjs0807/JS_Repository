# BBKC Exit Strategy Round 2 — Design

**날짜**: 2026-04-25
**상태**: 설계 승인 대기 (브레인스토밍 완료, 구현 미착수)
**범위**: S2 + F2 (스코프 정의는 §3 참조)
**선행 작업**:
- 2026-03-30 그리드 (3,564조합, 청산 axis 32종) — fixed TP6%/SL7% 1h가 WF 7/9, ATR TP8x/SL2x 1h는 WF 2/9
- 2026-04-14 BBKC/Donchian 개선 메모 — B4/B5/B7/D4 후보 보류 상태
- 2026-04-25 라이브 SL/TP 흐름 조사 (architect 보고서)

---

## 1. 목적

BBKC 1h 전략의 청산을 **고정 TP/SL 외 동적 primitive로 확장**하고, 같은 그리드/WF 프레임에서 fixed baseline과 **head-to-head 비교**한다. 이번 라운드는 **be_trail (B5) + time_stop**만 다루며, B7/B4/D4는 차후 라운드.

부수 목표: 라이브(legacy) 경로의 SL/TP 재계산 버그를 같이 수정해, 평가-라이브 간 RR 정의 일관성을 회복한다.

질문은 명시적으로:
> "현재 winner(`fixed + tp_pct=0.06 / sl_pct=0.07`)에 대해, BE+1R trailing이나 time_stop이 추가되면 WF 안정성과 R/trade가 더 좋아지는가?"

질문이 아닌 것:
- "더 좋은 BBKC가 있는가" (지표 파라미터는 sweep 안 함)
- "심볼 universe를 어떻게 늘릴까" (1라운드는 BIGTHREE)
- "라이브에 트레일링 API를 어떻게 연동할까" (다음 라운드)

---

## 2. 배경 (요약)

### 2.1 코드 현황 (architect 조사)

**연구 경로** (`src/strategies/bbkc_squeeze.py`):
- `tp_pct=0.06`, `sl_pct=0.07`, `leverage=3` 하드코드
- `on_bar_fast()` (`src/strategies/bbkc_squeeze.py:69-114`) — 진입 시 `tp = close * (1 + 0.06/3)`, `sl = close * (1 - 0.07/3)` 계산 후 `broker.buy(stop_loss, take_profit)` 호출
- 포지션 보유 중에는 `pos is not None` 시 즉시 `return` (line 88-89) — trailing/BE/time hook 없음
- 청산은 `BacktestBroker._check_exit` (`src/execution/backtest_broker.py:153-166`)가 intra-bar high/low 기반으로 SL/TP 트리거

**라이브 경로** (`_legacy/run_bbkc_trade.py` → `_legacy/paper_engine/trading_engine.py`):
- WebSocket은 15m 봉만 구독, 매 1H 경계에서 `_run_1h_strategies` 호출
- SL/TP는 `_process_signal` (`_legacy/paper_engine/trading_engine.py:921-`)에서 신호 entry × slip 기준으로 계산 → `place_order(stopLoss, takeProfit)`로 Bybit에 전송 (line 1028-1036)
- API 응답에서 실제 체결가 수신 시 `entry_price_actual`만 갱신, **SL/TP는 미재계산** (line 1064-1071) — 이번 라운드의 F2 수정 대상
- 매 15m 봉 close에서 `_check_open_positions_for_symbol` (line 1280-1327)가 로컬 SL/TP를 다시 체크 — Bybit 서버측 SL/TP와 이중

**라이브-연구 일치성**:
- 두 경로의 SL/TP 계산 수식은 같음
- 단 라이브 측에서 signal close → 실제 체결 사이 시차 가격 변동(실측 0.82%)이 RR 비율을 비틀어 평가 결과를 라이브에 그대로 옮기기 어려움

### 2.2 기존 인프라

- 그리드 sweep: `scripts/d2_grid.py`, `scripts/bbkc_universe_eval.py`
- 결과 저장: `logs/research/bbkc_squeeze/{coarse,fine,walkforward,overfit}_results.jsonl`
- 전략 레지스트리: `src/strategies/registry_builder.py`
- WF 프로토콜: IS 6m / OOS 2m / step 2m, 9 윈도우

---

## 3. 스코프 (S2 + F2)

### IN

1. `src/strategies/bbkc_squeeze.py` 확장 — `exit_mode` 파라미터 추가, `be_trail` 모드 + `time_stop_bars` 파라미터 (§4). `_pos_meta`는 `on_fill()` 의존하지 않고 `on_bar_fast()`에서 broker 포지션 변화 감지로 lazy init (§4.5)
2. `src/strategies/registry_builder.py` 그리드에 새 axis 추가 (§5)
3. 새 sweep 스크립트 `scripts/bbkc_exit_eval.py` (§5)
4. `_legacy/api/rest_client.py`에 `set_trading_stop()` 추가 (§4.6)
5. `_legacy/paper_engine/trading_engine.py:1064-1071` 직후 SL/TP 재계산 + `set_trading_stop()` 호출 추가 (§4.6)
6. **Legacy 전역 ATR trailing을 BBKCSqueeze 한정 비활성화** — `_legacy/paper_engine/trading_engine.py:1297-1310` 게이트 추가 (§4.7). 평가 fixed와 라이브 fixed 의미 일치 회복
7. `src/execution/backtest_broker.py` 확장 — Position에 `max_favorable` 추적, `TradeRecord`에 `max_favorable` 필드 추가 (§4.8). MFE retention 계산 가능
8. WF 결과 리포트 — 셀별 메인 + 보조 지표 (§6)

### OUT (이번 라운드 제외, 명시)

- B7 (Donchian-style trailing exit)
- B4 (ATR-adaptive TP/SL)
- D4 (partial take-profit)
- 슬리피지 가드레일 (F1/F3) — F2 실패 케이스용 fallback은 다음 라운드
- `src/execution/live_broker.py:43-44` `update_stop()` Bybit API 전파 — 라이브 트레일링 적용은 평가 통과 후 별도 라운드
- 지표 파라미터 (BB/KC/RSI/tp_pct/sl_pct) sweep — 2026-03-30 winner 그대로 고정
- 심볼 13개 일반화 — 2라운드
- Legacy 전역 ATR trailing의 **다른 전략(IchimokuCloud OFF / RSIMACD)**에 대한 정리/검토 — 이번 라운드는 BBKC 한정 게이트만 (§4.7)

---

## 4. Strategy 설계

### 4.1 `BBKCSqueeze` 확장 (single class, mode parameter)

새 strategy 클래스를 만들지 않고 기존 `src/strategies/bbkc_squeeze.py:BBKCSqueeze`에 파라미터 추가. 진입 로직 동일, 청산만 다르므로 자연스러움.

**추가 파라미터** (`__init__`):

```python
exit_mode: str = "fixed"            # "fixed" | "be_trail"
trail_be_r: float = 1.0             # BE 트리거: +1R 도달 시 SL → entry
trail_start_r: float = 2.0          # trailing 트리거: +2R 도달 시 시작
trail_distance_r: float = 0.5       # SL = close - trail_distance_r * R (LONG)
time_stop_bars: int = 0             # 0 = disabled, N > 0 = N개 1h 봉 후 강제 청산
```

기존 파라미터(`tp_pct`, `sl_pct`, `leverage` 등)는 변경 없음.

### 4.2 R 정의

진입 시점 1회 계산하여 포지션과 함께 저장:

```python
# LONG
R = entry_price - initial_sl   # 예: 78237.76 - 76412.20 = 1825.56
# SHORT
R = initial_sl - entry_price
```

`R`은 entry_price와 initial SL(= `entry × (1 - sl_pct/leverage)` for LONG)의 절대 거리. 진입 후 변경 없음 — trailing이 갱신되어도 R은 진입 시 기준 그대로.

### 4.3 `be_trail` 동작

`on_bar_fast()`에서 `pos is not None`일 때 `_manage_position()` 호출 (현 라인 88-89의 `return`을 분기로 교체):

| 가격 위치 | 동작 |
|---|---|
| `close - entry < trail_be_r × R` (LONG) | initial SL 유지 |
| **`close - entry ≥ trail_be_r × R`** (LONG, 한 번만 트리거) | `broker.update_stop(symbol, entry_price)` — BE |
| **`close - entry ≥ trail_start_r × R`** (LONG, 한 번만 트리거) | trailing 활성 플래그 ON, 첫 trailing SL = `close - trail_distance_r × R` |
| trailing 활성 후 매 1h close | `new_sl = max(current_sl, close - trail_distance_r × R)`, 변동 시 `broker.update_stop(symbol, new_sl)` |
| TP | 진입 시 설정한 `entry × (1 + tp_pct/leverage)` 그대로 유지, 변경 없음 |

트리거 조건은 1봉 안에 1R/2R을 모두 넘는 갭 케이스도 자연스럽게 처리 (둘 다 같은 봉에서 활성화 가능).

SHORT는 부호 반전:
- BE 조건: `close ≤ entry - 1R`
- trailing 조건: `close ≤ entry - 2R`
- `new_sl = min(current_sl, close + trail_distance_r * R)`

상태 보관: 전략 인스턴스의 `_pos_meta: Dict[str, dict]`에 `{R, initial_sl, be_triggered, trail_active, bars_held}`. Position 객체를 건드리지 않아 broker/engine 영향 없음. 초기화/정리 패턴은 §4.5 참조.

### 4.4 `time_stop` 동작

`exit_mode`와 직교. 모든 모드(fixed, be_trail)에 공통 적용.

```python
# 매 on_bar_fast 호출 시 (pos 있을 때):
#   _pos_meta[symbol]["bars_held"] += 1
# bars_held >= time_stop_bars > 0 이면:
#   broker.close(symbol, reason="time_stop")
```

**체결 시점 의미** (BacktestBroker 동작 검증 결과 — `src/execution/backtest_broker.py:59-61, 117-127`):
`broker.close()`는 즉시 청산이 아니라 `_close_requests` 큐에 적재. **다음 1h 봉의 `process_bar`가 호출될 때 `bar.open` 가격으로 `_execute_close` 실행**. 따라서 의미는:
- threshold 도달 봉의 close에서 close 요청
- 실제 체결은 다음 1h 봉의 open 가격
- 평가-라이브 일관성: 라이브에서도 1h 경계 후 즉시 market order로 청산되므로 ~수 초 차이지만 의미는 동일

우선순위: 같은 봉에서 BacktestBroker `_check_exit`(line 224-254)가 intra-bar SL/TP를 먼저 트리거 → 포지션 사라지면 time_stop 미발동. SL/TP 미트리거 시 `on_bar_fast`에서 time_stop 발동 → 다음 봉 open 청산.

봉 카운트는 1h 단위 (`timeframe="1h"`이라 on_bar_fast가 1h마다 1회 호출). 라이브에서는 `_run_1h_strategies`가 매 1h 경계에서 호출되므로 동일.

### 4.5 `_pos_meta` 초기화/정리 (on_fill 비의존)

**검증 사실** (코드 조사):
- `src/backtester/engine.py:44-83`은 `prepare`/`on_bar_fast`/`on_bar`만 호출
- `src/execution/paper_runner.py:175-180`은 `on_bar_fast`만 호출
- 즉 **`Strategy.on_fill()`은 src 백테스트/페이퍼 런타임에서 호출되지 않음**

따라서 `_pos_meta` 초기화는 `on_fill()`에 의존할 수 없음. **broker 포지션 변화 감지로 lazy init**:

```python
def on_bar_fast(self, bar, i, cache, broker):
    pos = broker.get_position(bar.symbol)
    sym = bar.symbol

    # 정리: 포지션 없는데 meta 남아있으면 제거
    if pos is None and sym in self._pos_meta:
        del self._pos_meta[sym]

    # 신규 진입 감지: 포지션 있는데 meta 없음 → lazy init
    if pos is not None and sym not in self._pos_meta:
        if pos.side == "LONG":
            R = pos.entry_price - pos.stop_loss
        else:
            R = pos.stop_loss - pos.entry_price
        self._pos_meta[sym] = {
            "R": R,
            "initial_sl": pos.stop_loss,
            "be_triggered": False,
            "trail_active": False,
            "bars_held": 0,
        }

    # 진입/관리 분기
    if pos is None:
        # ... 기존 진입 로직 ...
    else:
        self._manage_position(bar, pos, broker)
```

이 패턴의 장점:
- `on_fill()`/`on_bar` 호출 여부와 무관
- 라이브에서 broker 포지션이 sync로 갑자기 나타나는 경우(`live_broker.sync_positions`)도 자동 처리
- 정리도 깔끔 (포지션 사라지면 meta 자동 제거)

prepare 캐시는 `fixed`, `be_trail` 모드 모두 추가 indicator 불필요 — 기존 cache 그대로 사용.

### 4.6 Legacy 버그 수정 (F2)

**위치**: `_legacy/paper_engine/trading_engine.py:1064-1071` 직후

**기존 흐름**:
```
1. signal_entry = signal.close × slip_mult  (line 997-998)
2. SL/TP = signal_entry 기준으로 계산        (line 1001-1010)
3. place_order(stopLoss, takeProfit)         (line 1028-1036)
4. API 응답에서 avgPrice 받아 entry_price_actual 덮어쓰기 (line 1064-1071)
5. SL/TP 미재계산 ← BUG
```

**수정 후 흐름**:
```
1. signal_entry = signal.close × slip_mult
2. SL/TP = signal_entry 기준 계산  → desired_sl_signal, desired_tp_signal
3. place_order(stopLoss=desired_sl_signal, takeProfit=desired_tp_signal)
   → 즉시 Bybit 서버에 1차 SL/TP 등록 (체결 직후 무보호 구간 방지)
4. API 응답에서 avgPrice 받아 entry_price_actual = avgPrice
5. **NEW**: actual entry 기준으로 SL/TP 재계산
   - BBKC fixed: sl_actual = avgPrice * (1 - sl_pct/leverage), tp_actual = avgPrice * (1 + tp_pct/leverage)
   - 다른 전략의 ATR 기반 등은 동일 원칙 (entry_price만 avgPrice로 교체)
   - _PositionInfo에 desired_stop_loss=sl_actual, desired_take_profit=tp_actual 기록
6. **NEW**: rest_client.set_trading_stop(symbol, sl_actual, tp_actual) 호출
   - 호출 정책: **항상 호출** (idempotent). avgPrice == signal_entry로 값이 동일해도 호출 — 분기 단순화
7. **NEW**: 응답 분기:
   - 성공:
     - _PositionInfo.stop_loss = sl_actual, take_profit = tp_actual (서버 = 로컬 = avgPrice 기준)
     - sl_tp_resync_failed = False
   - 실패:
     - logger.warning("set_trading_stop failed for %s: %s", symbol, exc)
     - **_PositionInfo.stop_loss / take_profit는 갱신하지 않음** (signal_entry 기준 그대로 유지)
       → 서버측 원본 SL/TP와 일치 → 로컬 체크(`_check_open_positions_for_symbol`)도 같은 값으로 트리거 → 분기 없음
     - desired_stop_loss / desired_take_profit는 따로 보관 → 다음 sync 사이클이나 수동 재시도에 사용 가능
     - sl_tp_resync_failed = True 플래그 기록
     - 포지션은 유지 (Bybit 서버측 원본 SL/TP가 보호 중)
```

**`_legacy/api/rest_client.py`에 추가**:

```python
def set_trading_stop(
    self,
    symbol: str,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    position_idx: int = 0,
) -> dict:
    """Bybit set-trading-stop endpoint.

    Args:
        symbol: USDT perpetual 심볼
        stop_loss: 새 stop loss price (None이면 변경 없음)
        take_profit: 새 take profit price (None이면 변경 없음)
        position_idx: 0=one-way, 1=hedge buy, 2=hedge sell

    Returns: API 응답 dict. 실패 시 raise.
    """
    # endpoint: /v5/position/trading-stop
    # category: linear, tpslMode: Full
    ...
```

pybit SDK의 `HTTP.set_trading_stop()` 또는 raw HTTP call 사용. demo API 호환성 확인 필요.

### 4.7 Legacy 전역 ATR trailing의 BBKC 한정 비활성화

**검증 사실** (코드 조사):
- `_legacy/strategies/bb_kc_squeeze.py:191`: BBKC Signal에 `atr=atr_val`이 채워짐 (atr_val > 0)
- `_legacy/paper_engine/trading_engine.py:1297-1310`: 모든 전략의 포지션에 대해 `pos.atr > 0`이면 ATR trailing 활성화 (`trailing_activation_atr=2.5 × ATR`, `trailing_distance_atr=1.5 × ATR`)
- line 1305-1310의 `pos.stop_loss = self.risk_manager.update_trailing_stop(...)` → 로컬 stop 갱신
- line 1316-1327: 갱신된 로컬 stop을 매 15m close에서 체크 → STOP 트리거 시 line 1356-1359의 `rest_client.place_order(close_side, qty)`로 시장가 청산

**결론**: 이 트레일링은 dead code가 아님. 로컬 체크 경유로 BBKC `exit_mode="fixed"` 위에 추가 ATR 트레일링이 작동 중. 즉:
- src 평가의 `fixed baseline` = 순수 fixed (BacktestBroker는 line 1297-1310 같은 코드 없음)
- 라이브 운영의 `fixed` = fixed + 전역 ATR trailing
- → 평가 결과를 라이브에 그대로 옮기면 동작이 다름

**수정**: `_legacy/paper_engine/trading_engine.py:1297-1310`에 BBKC 한정 게이트 추가:

```python
# 트레일링 스톱 갱신 (활성화 조건: 수익이 trailing_activation_atr * ATR 이상)
# BBKCSqueeze는 자체 청산 정책(fixed/be_trail)을 따르므로 전역 ATR trailing 제외
if pos.strategy != "BBKCSqueeze" and pos.atr > 0:
    activation_dist = self.risk_manager.params.trailing_activation_atr * pos.atr
    ...
```

**효과**:
- src 평가의 `fixed` 의미 = 라이브 `fixed` 의미 일치 회복
- 다른 전략(IchimokuCloud OFF, RSIMACD)은 그대로 — 영향 없음
- 라이브에 BBKC be_trail를 적용할 때 src 평가의 `_manage_position` 로직만 라이브에 포팅하면 정확히 같은 청산이 됨

**테스트 영향**: legacy 단위 테스트 중 BBKC + trailing 관련 케이스가 있다면 갱신 필요.

**배포 정책 (writing-plans 단계에서 한 줄로 못박을 것)**:
- 라이브 영향: 현재 운영 중 BBKC 포지션의 경우 이 변경 적용 시 ATR trailing이 사라지고 오직 fixed SL만 남음
- **권장 정책 (구현 계획서에 명시)**: 운영 중 BBKC 포지션이 자연 종료(SL/TP/수동)된 후 배포. 또는 배포 직전 운영 포지션을 시장가로 정리. 둘 중 하나를 plan에 고정해서 배포 절차에 못박을 것.

### 4.8 BacktestBroker MFE 확장

**검증 사실**:
- `src/execution/backtest_broker.py:18-31`의 `TradeRecord`에는 `max_favorable` 없음
- `src/execution/position_tracker.py`의 `Position`에도 max_favorable 추적 없음 (확인 필요, 없으면 추가)
- legacy `_legacy/paper_engine/trading_engine.py:1294`는 `pos.max_favorable = max(...)` 추적 있음

**수정**:

1. `src/execution/broker.py`의 `Position` dataclass에 필드 추가:
   ```python
   max_favorable: float = 0.0   # 진입 후 최대 유리 가격 거리 (절대값)
   ```

2. **갱신 주 경로**: `src/execution/backtest_broker.py:process_bar()` 안에서 bar.high/low로 직접 갱신. PositionTracker에 별도 메서드는 두지 않음 (불필요한 간접화 방지).

3. `src/execution/backtest_broker.py:process_bar()` line 168 부근(`update_unrealized` 호출 직전 또는 직후)에 high/low 기반 갱신 추가:
   ```python
   if pos.side == "LONG":
       max_fav_this_bar = bar.high - pos.entry_price
   else:
       max_fav_this_bar = pos.entry_price - bar.low
   if max_fav_this_bar > pos.max_favorable:
       pos.max_favorable = max_fav_this_bar
   ```

4. `TradeRecord`에 필드 추가:
   ```python
   @dataclass
   class TradeRecord:
       ...
       max_favorable: float = 0.0
   ```

5. `_execute_close` (line 200-205) / `_execute_exit` (line 215-220)에서 `pos.max_favorable`을 TradeRecord에 전달.

**MFE retention 계산** (sweep 스크립트 측):
```python
# LONG: realized_R = (exit_price - entry_price) / R
# max_favorable_R = pos.max_favorable / R
# retention = realized_R / max_favorable_R (max_favorable_R > 0인 경우만, 음수/0이면 N/A)
```

**테스트 영향**: 기존 trade record 기반 테스트는 새 필드 추가만으로는 영향 없음. trade record 구조 검증 테스트가 있다면 max_favorable 필드 존재 확인 추가.

---

## 5. 그리드 sweep 설계

### 5.1 sweep 매트릭스

지표 파라미터 고정:
- `bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0, atr_period=14, rsi_period=14, rsi_filter=70.0, tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h"`

청산 axis:

| 셀 | exit_mode | trail_distance_r | time_stop_bars |
|---|---|---|---|
| F0 | fixed | — | 0 |
| F24 | fixed | — | 24 |
| F48 | fixed | — | 48 |
| F72 | fixed | — | 72 |
| T05_0 | be_trail | 0.5 | 0 |
| T05_24 | be_trail | 0.5 | 24 |
| T05_48 | be_trail | 0.5 | 48 |
| T05_72 | be_trail | 0.5 | 72 |
| T10_0 | be_trail | 1.0 | 0 |
| T10_24 | be_trail | 1.0 | 24 |
| T10_48 | be_trail | 1.0 | 48 |
| T10_72 | be_trail | 1.0 | 72 |

**12셀**. baseline = `F0`.

`trail_be_r=1.0`, `trail_start_r=2.0`은 첫 라운드 고정 (다음 라운드 후속 sweep 가능).

### 5.2 데이터 / 심볼 / 프로토콜

- 심볼: BIGTHREE (BTCUSDT, ETHUSDT, AVAXUSDT)
- 기간: 2024-03-01 ~ 2026-04-30 (약 26개월)
- TF: 1h (primary)
- 비용: taker_fee=0.00055, maker_fee=0.0002, slippage=0.0003 (변경 없음)
- WF 프로토콜: IS 6m / OOS 2m / step 2m, 9 윈도우 (2026-03-30과 동일)

총 평가량: **12 셀 × 3 심볼 × 9 WF 윈도우 = 324 backtest run**.

### 5.3 sweep 인프라

선택지:
- (A) `scripts/bbkc_universe_eval.py` 확장 — exit axis 추가 처리. 기존 결과 호환성 깨짐 가능
- (B) 새 스크립트 `scripts/bbkc_exit_eval.py` — sweep 매트릭스를 명시적으로 12셀 정의, BIGTHREE만 처리, 결과는 `logs/research/bbkc_squeeze/exit_round/` 신규 디렉토리에 분리

**(B) 추천**. 이유:
- 기존 결과를 건드리지 않음
- sweep 매트릭스가 작아(12셀) 단순 nested loop로 명시 가능
- 결과 디렉토리 분리로 비교 깔끔

### 5.4 결과 저장 형식

`logs/research/bbkc_squeeze/exit_round/`:
- `wf_results.jsonl` — 각 라인: `{symbol, cell, window_idx, is_metrics: {...}, oos_metrics: {...}}`
- `summary.json` — 셀별 평균: `{cell: {wf_oos_positive: 7, mean_oos_pnl: ..., mean_r_per_trade: ..., max_dd: ..., trade_count: ...}}`
- `auxiliary.json` — 보조 지표 (§6.2): `{cell: {symbol: {exit_reason_dist: {...}, mean_r_win: ..., mean_r_loss: ..., mfe_retention: ..., mean_holding_bars: ...}}}`
- `report.md` — 사람이 읽는 리포트, 셀별 PROMOTE/KILL 판정

---

## 6. 성공 판정 기준

### 6.1 메인 게이트 (심볼별 독립)

baseline = `F0` (fixed + time_stop=0)

| 조건 | 판정 |
|---|---|
| WF OOS 양수 ≥ 7/9 **AND** mean R/trade ≥ baseline R/trade | **PROMOTE** |
| 위 + Max DD ≤ baseline Max DD | **STRONG PROMOTE** |
| WF OOS 양수 < 7/9 **OR** mean R/trade < baseline R/trade | **KILL** |
| trade count < baseline × 0.5 | **WARNING** (표본 부족, 결론 보류) |

심볼별 독립 = BTC PROMOTE / ETH KILL / AVAX PROMOTE 가능. 라이브 적용 시 심볼별 다른 mode 운영 결정은 별도.

### 6.2 보조 지표 (셀별, 해석용)

각 셀 × 심볼에 대해:

1. **Exit reason 분포** — `{tp: %, sl: %, trail: %, time_stop: %, other: %}`
2. **Mean R per win** — 승리 거래의 R 분포 평균
3. **Mean R per loss** — 패배 거래의 R 분포 평균
4. **MFE retention** — `(realized_R) / (max_favorable_R)` 평균. 측정에는 `Position.max_favorable` 추적 + `TradeRecord.max_favorable` 필드가 필요 — §4.8에서 backtest_broker 확장으로 보장
5. **Mean holding bars** — 진입~청산 사이 1h 봉 수

WF 9 윈도우 평균 + 분산 같이 출력.

이 지표들은 **PROMOTE/KILL 판정에 직접 사용 안 함**, 결과 해석용:
- be_trail이 작동했다면 exit reason의 `trail` 비율 증가, mean R per win 증가, MFE retention 상승
- time_stop이 의미있다면 exit reason의 `time_stop` 비율 증가, mean holding bars 감소
- BE 효과가 있다면 mean R per loss가 0에 수렴

---

## 7. 파일 변경 목록

### 7.1 새 파일

- `scripts/bbkc_exit_eval.py` — 12셀 × BIGTHREE × WF 9윈도우 sweep 실행 스크립트
- `logs/research/bbkc_squeeze/exit_round/` (디렉토리) — 결과 저장
- `docs/superpowers/specs/experiments/2026-04-25_bbkc_exit_strategy_design.md` (이 문서)

### 7.2 수정 파일

- `src/strategies/bbkc_squeeze.py`
  - `__init__`에 `exit_mode`, `trail_be_r`, `trail_start_r`, `trail_distance_r`, `time_stop_bars` 파라미터 추가
  - `__init__`에 `_pos_meta: Dict[str, dict]` 인스턴스 변수 초기화
  - `on_bar_fast()` 도입부에 §4.5 lazy init/cleanup 패턴 추가 (broker 포지션 변화 감지)
  - `on_bar_fast()` — `pos is not None` 분기에서 `_manage_position()` 호출
  - 신규 `_manage_position()` 메서드 — be_trail 단계 트리거 (+1R BE, +2R trailing) + time_stop 체크 + bars_held 증가
  - `on_fill()` 미수정 (런타임 비호출이므로 사용 안 함)
  - `get_params()` / `set_params()`에 새 파라미터 반영

- `src/strategies/registry_builder.py`
  - 별도 `STRATEGY_CONFIGS["BBKCSqueeze"]["exit_round_grid"]` 키로 12셀 정의 (기존 coarse_grid 분리)

- `src/execution/broker.py`
  - `Position` dataclass에 `max_favorable: float = 0.0` 필드 추가

- `src/execution/backtest_broker.py`
  - `TradeRecord` dataclass에 `max_favorable: float = 0.0` 필드 추가
  - `process_bar()` — bar high/low 기반으로 `pos.max_favorable` 매 봉 직접 갱신 (§4.8 주 경로)
  - `_execute_close` / `_execute_exit` — `TradeRecord(... max_favorable=pos.max_favorable)` 전달

- `_legacy/api/rest_client.py`
  - `set_trading_stop(symbol, stop_loss, take_profit, position_idx=0)` 메서드 추가

- `_legacy/paper_engine/trading_engine.py`
  - line 1064-1071 직후: avgPrice 기반 SL/TP 재계산 → `set_trading_stop()` 호출 → 성공 시 `_PositionInfo` 갱신, 실패 시 로컬 미갱신 + `desired_*` 보관 + WARN 로그
  - line 1297-1310: BBKC 한정 게이트 추가 (`if pos.strategy != "BBKCSqueeze" and pos.atr > 0`)
  - `_PositionInfo` dataclass에 `desired_stop_loss`, `desired_take_profit`, `sl_tp_resync_failed` 필드 추가

### 7.3 미변경 (확인용)

- `src/execution/live_broker.py` — 이번 라운드는 평가 통과 후 별도 (라이브 트레일링 API 전파)
- `_legacy/strategies/bb_kc_squeeze.py` — 라이브 운영 전략. 이번 라운드는 평가 결과만 산출, 라이브 적용은 별도

---

## 8. 테스트 계획

### 8.1 단위 테스트

- `tests/test_strategies/test_bbkc_squeeze.py` 확장:
  - 기존 `exit_mode="fixed"` (default) 테스트 — 기존 동작 보존 확인
  - `_pos_meta` lazy init 테스트:
    - on_bar_fast 호출 시 broker 포지션이 새로 생기면 _pos_meta 자동 초기화 (R 정확도 검증)
    - 포지션 사라지면 _pos_meta에서 자동 제거
    - on_fill을 호출하지 않아도 정상 작동 확인 (테스트 시 broker 상태로만 트리거)
  - `exit_mode="be_trail"` 신규 테스트:
    - +1R 미달 → SL 변경 없음
    - +1R 도달 → SL = entry 확인 (`broker.update_stop` 호출 검증)
    - +2R 도달 후 close 변동 → SL 갱신 확인 (LONG: max(current, close - 0.5R))
    - 한 봉에 +1R/+2R 모두 넘는 갭 케이스 → BE+trail 모두 활성화
    - SHORT 대칭 동작
  - `time_stop_bars` 신규 테스트:
    - 0 → 발동 안 함
    - N > 0 + N봉 도달 → `broker.close(symbol, reason="time_stop")` 호출
    - 포지션이 없는 1h 봉(진입 전)은 카운트 안 됨 (bars_held는 진입 후부터)
    - SL/TP가 먼저 트리거 → 다음 봉에서 pos is None → time_stop 미발동
    - close 요청 시점은 N봉 도달 봉의 on_bar_fast, 실제 체결은 다음 봉 open (BacktestBroker 큐 동작 확인)
  - MFE retention 사전조건 — TradeRecord에 max_favorable 필드 존재, 거래 종료 시 양수 채워짐

### 8.2 통합 테스트

- `tests/integration_test.py` — sweep 매트릭스 12셀 중 1셀(예: T05_24)을 짧은 데이터로 실행, 결과 jsonl 형식 검증

### 8.3 Legacy F2 테스트

- `tests/_legacy/test_trading_engine_sl_resync.py` (신규):
  - mock rest_client로 place_order → set_trading_stop 호출 시퀀스 검증
  - 성공 케이스: 로컬 _PositionInfo.stop_loss/take_profit가 avgPrice 기준 값으로 갱신되는지 확인
  - 실패 케이스: WARN 로그 + 포지션 유지 + sl_tp_resync_failed=True + **로컬 stop_loss/take_profit는 미갱신 (signal_entry 기준 유지)** 확인
  - desired_stop_loss / desired_take_profit가 avgPrice 기준 값으로 따로 보관되는지 확인
  - signal close ≠ avgPrice일 때 SL/TP가 avgPrice 기준으로 재계산되어 set_trading_stop에 전달되는지 검증
  - signal close = avgPrice (slippage 0)인 케이스에서도 호출이 발생하되 동일 값임을 확인 (정책 §4.6: "항상 호출")

- `tests/_legacy/test_trading_engine_bbkc_trailing_gate.py` (신규):
  - BBKC 포지션에 대해 `_check_open_positions_for_symbol`의 line 1297-1310 ATR trailing이 활성화되지 않는지 확인 (수익 +5×ATR 시점에서도 stop_loss 미변경)
  - 다른 전략(예: RSIMACD) 포지션에 대해서는 기존대로 ATR trailing 작동 확인 (회귀 보장)

### 8.4 회귀 검증

- `tests/test_strategies/test_bbkc_squeeze.py`의 기존 케이스들이 default 파라미터 유지 시 동일 시그널 생성하는지 확인
- registry sweep grid가 default 모드(`fixed + time_stop=0`)일 때 2026-03-30 결과와 PnL/trade count 재현 (회귀 baseline)

---

## 9. 리스크 / 알려진 한계

1. **`set_trading_stop` API의 demo 환경 호환성** — pybit `HTTP.set_trading_stop()`이 `api-demo.bybit.com`에서 동작하는지 미확인. 실패 시 raw HTTP call로 우회 필요.
2. **체결~SL 재설정 사이 무보호 구간** — 수백ms. 첫 1차 SL/TP는 signal 기준으로 미리 설정되어 있으므로 실제로는 "잘못된 SL이지만 없는 것보단 나음" 상태. 무보호는 아님.
3. **Legacy 전역 ATR trailing의 BBKC 영향** — 코드 조사로 확정: BBKC Signal에 atr이 채워지므로 line 1297-1310 trailing이 BBKC에도 작동 중. §4.7에서 BBKC 한정 게이트로 비활성화 처리. 잔여 리스크: 게이트 적용 시점에 운영 중 BBKC 포지션은 기존 trailing이 빠지면서 SL이 원래 fixed 값으로 돌아감 — 배포 시 운영 포지션 종료를 기다리거나 즉시 적용 결정 필요.
4. **R 정의가 sl_pct에 종속** — sl_pct를 향후 sweep할 경우 R 단위가 셀마다 달라져 trail_distance_r 비교가 어려워짐. 이번 라운드는 sl_pct=0.07 고정이므로 문제 없음.
5. **WF 윈도우 단위 분산** — 9윈도우 평균만 보면 윈도우 간 분산을 놓칠 수 있음. 보조 지표는 평균 + std 같이 출력.

---

## 10. Out of Scope (반복 명시)

1. B7 (Donchian-style trailing exit) — be_trail 결과 보고 결정
2. B4 (ATR-adaptive TP/SL) — 우선순위 낮음, 결과 부족 시 검토
3. D4 (partial take-profit) — BacktestBroker에 partial close 구현 필요, 별도 라운드
4. 슬리피지 가드레일 (F1/F3) — F2가 부족할 때 추가
5. `src/execution/live_broker.py:update_stop()` Bybit API 전파 — 평가 PROMOTE 후 라이브 적용 라운드에서
6. 지표 파라미터 sweep — 청산 효과 측정의 cleanness 위해 고정
7. 13코인 일반화 — BIGTHREE에서 PROMOTE 셀 확정 후 2라운드
8. Legacy 전역 ATR trailing의 **다른 전략(IchimokuCloud OFF / RSIMACD)**에 대한 정리/검토 — 이번 라운드는 BBKC 한정 게이트만

---

## 11. 다음 단계

1. **본 문서 사용자 검토 + 승인**
2. **writing-plans 스킬로 전환** — 본 설계를 단계별 구현 plan으로 분해
3. 구현은 plan 승인 후 시작

---

## 12. Round 2 Results (2026-04-25 sweep)

**Run**: `logs/research/bbkc_squeeze/exit_round/2026-04-25_T2036/` + `latest/`
**Coverage**: 12 cells × 3 symbols × 9 WF windows = **324 backtests**, ~2분 소요

### 12.1 판정 결과

**모든 셀 KILL** (baseline F0 포함하여 PROMOTE 0건). 표면적으로는 어떤 청산 변경도 7/9 게이트를 못 넘었지만, 실제 데이터는 두 가지 별개의 메시지를 담고 있다.

### 12.2 핵심 발견 1: be_trail 침묵 (수학적 unreachable)

`be_trail` 셀(`T05_*`, `T10_*`)이 `fixed` 셀(`F0/F24/F48/F72`)과 **숫자 한 자릿수까지 동일**. 디버그 추적:
- `_manage_position` 호출: 204회 (정상)
- `broker.update_stop()` 호출: **0회** (BE/trailing 미발동)

**원인** — BBKC의 청산 스케일이 R-단위 thresholds와 충돌:

```
tp_pct=0.06, sl_pct=0.07, leverage=3
→ TP 거리 = 2.00%, SL 거리 = 2.33% (= 1R)

trail_be_r=1.0  → BE trigger at +2.33% favorable
trail_start_r=2.0 → trail trigger at +4.66% favorable
TP at +2.00% favorable

→ 가격이 TP(+2%)에 먼저 닿아 청산. BE(+2.33%) 도달 불가능. trail(+4.66%) 더더욱 불가.
```

**`sl_pct ≥ tp_pct` AND R-단위 thresholds AND TP 유지** 세 조건이 동시에 만족되면 trailing은 reachable space 외부로 밀려난다. 설계 단계 Q4에서 합의한 조합이 BBKC scale에서는 dead path.

**라운드 3 후보**:
- **(R3a)** `trail_be_r`를 TP 거리 비율로 재정의: 예 `trail_be_at_tp_frac=0.5` → BE at +1.00%, `trail_start_at_tp_frac=0.8` → trail at +1.60%
- **(R3b)** be_trail 모드에서 TP 제거 (`trail_drops_tp=True` 추가) — fat-tail 가설 직접 검증
- **(R3c)** sl_pct < tp_pct 전략에만 be_trail 적용 (BBKC는 제외, 다른 전략 도입 시)

### 12.3 핵심 발견 2: time_stop, 심볼별 효과 갈림

`time_stop` 자체는 정상 작동 (exit_reason `time_stop` 비율 확인됨, ETH F24 = 15.1%). 효과는 심볼별로 갈림:

| 심볼 | F0 (baseline) | F24 (time_stop=24) | 효과 |
|---|---|---|---|
| BTC | 3/9, R/trade -0.048, +169 PnL | 1/9, -0.096, -190 PnL | **악화** (긴 추세 자름) |
| ETH | 4/9, +0.024, +154 PnL, MFE retention -7.78 | **5/9, +0.118, +466 PnL, MFE -5.33** | **R/trade 5×, PnL 3× 개선** |
| AVAX | 3/9, -0.145, -95 PnL | 4/9, -0.146, -97 PnL | 거의 무변화 |

ETH는 명확히 짧게 컷이 유리한 시장 구조 (boom/bust 반복, 추세 짧음). BTC는 반대 (추세 길게 살릴 때 수익). AVAX는 중간.

**라운드 3 후보**:
- **(R3d)** ETH 한정 time_stop 정밀 sweep (`{0, 12, 18, 24, 36, 48}`)
- **(R3e)** 심볼별 다른 청산 운영 (BTC=fixed/long, ETH=fixed+time_stop=24)

### 12.4 핵심 발견 3: 7/9 게이트와 baseline의 미스매치

baseline F0 자체가 모든 심볼에서 3-4/9 OOS+. 2026-03-30 그리드의 "WF 7/9 = 78%"와 차이가 큼.

**원인 후보**:
- 평가 기간/윈도우 폭 차이: 2026-03-30은 다른 holdout/WF 셋업 (정확한 셋업 미기록)
- 2024-08~2026-02 전체 구간이 BBKC에 덜 우호적일 수 있음 (2024년 변동성 환경 차이)

→ 메인 게이트 7/9는 spec §6.1에서 직역한 임계치이지만, **이번 sweep WF 셋업에 맞춰 재보정해야 의미 있음**. 예: "F0 baseline 대비 OOS+ 카운트 ≥ baseline + 1" 같은 상대 게이트.

### 12.5 부수 검증

- F2 흐름 자체는 라이브에서 검증 안 됨 (sweep은 BacktestBroker 사용). 다음 라이브 BBKC 진입 시 set_trading_stop 호출 + idempotent 거동을 확인할 것.
- BBKC trailing gate 또한 다음 라이브 BBKC 진입에서 stop이 fixed 그대로 머무는지 확인 필요.
- MFE retention 수치가 모든 셀에서 음수(-4 ~ -8) — 손실 거래의 실현 R / max favorable R 비율이 음수라 절대값이 큼. 이건 수치 정의상 자연스러운 결과 (loss / positive max_fav < 0). 다음 라운드에서 wins-only retention만 따로 계산하는 것을 검토.

### 12.6 라운드 2 종료 액션

- ✅ 코드 변경 (Phase A-D) 모두 main 머지
- ✅ be_trail 로직 코드 유지 (default fixed라 운영 영향 없음, 라운드 3 자산)
- ⏭ 라운드 3 사전 결정: R3a/R3b/R3c 중 어느 trailing 재설계, R3d/R3e time_stop 정밀화 — 별도 라운드에서 brainstorming 진입
- ⏭ 7/9 게이트 재보정 또는 baseline-relative 게이트로 변경 — 라운드 3 spec 단계에서

### 12.7 한 줄 요약

**round 2는 PROMOTE 0건이지만 두 가지 학습을 산출**: be_trail thresholds는 BBKC scale에서 unreachable이고, time_stop은 ETH에는 유효하나 BTC에 해롭다. 라운드 3는 trailing 재정의 + 심볼별 청산 분기를 다뤄야 한다.
