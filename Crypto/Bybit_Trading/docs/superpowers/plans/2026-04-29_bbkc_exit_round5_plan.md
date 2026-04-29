# BBKC Exit Strategy Round 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Round 4에서 선정한 `be25_st60_di30`을 실제 forward/paper-live (Bybit Demo) 환경에서 검증 가능한 상태로 만든다 — `src/execution/live_broker.py`의 `update_stop`이 Bybit 실거래소 SL을 실제로 이동시키도록 API 연동, 운영 정책을 config로 표현, `--stop-at` 사용 금지를 코드/runbook 양쪽에 강제.

**Architecture:** Round 4까지의 모든 코드(BBKCSqueeze TP-fraction + integrate_label + judge baseline-relative + 28-cell grid + set_params invariant fix)가 main에 있음. Round 5는 인프라 라운드 — 새 전략 탐색 아님. src 경로의 `LiveBroker`/`rest_client`/`Broker` Protocol/`BacktestBroker`/`config`/`run_bbkc_live_trade.py`에 빠진 연결만 보완.

**Tech Stack:** Python 3.11+, pybit (Bybit unified_trading SDK, 이미 사용 중), pytest, PyYAML.

**Spec:** `Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round5_design.md` (commit `fcc0061`)

**Working directory for all bash commands:** `C:/Users/ceoji/Desktop/python_ibks/Crypto/Bybit_Trading/`

---

## File Structure

### New Files

| Path | Purpose |
|---|---|
| `scripts/check_15m_to_1h_parity.py` | 15m × 4 합성 1h vs Bybit direct 1h 비교 도구 (운영자 수동 실행, 자동 fallback 없음) |
| `docs/operations/bbkc_exit_round5_runbook.md` | Round 5 운영 체크리스트 (kill switch 절차, 1개월 mid-review, 기존 포지션 처리 방침, `--stop-at` 사용 금지 명시) |
| `docs/operations/src_vs_legacy_audit.md` | Phase A audit 산출물 (비교표 + A/B/C 결정) |
| `tests/test_api/test_set_trading_stop.py` | `BybitRestClient.set_trading_stop` 단위 테스트 |

### Modified Files

| Path | Change |
|---|---|
| `src/api/rest_client.py` | `set_trading_stop` 메서드 추가 (pybit `self._session.set_trading_stop` 경유) |
| `src/execution/broker.py` | `Broker` Protocol에 `update_tp(symbol, new_tp: Optional[float])` 추가 |
| `src/execution/position_tracker.py` | 기존 `update_tp(symbol, new_tp: float)` 시그니처를 `update_tp(symbol, new_tp: Optional[float])`로 변경 (None 허용 — drop_tp 후속 라운드 또는 LiveBroker.update_tp 일관성) |
| `src/execution/backtest_broker.py` | `update_tp` 메서드 추가 (`_positions.update_tp` 위임, `Optional[float]` 받음) |
| `src/execution/live_broker.py` | `_position_idx_for_side` 헬퍼, `update_stop` API 연동, `update_tp` 신규 + `manual_update_tp` API 경유, `sync_positions` SL/TP 파싱 |
| `src/core/config.py` | `BBKCExitConfig` dataclass + `load_config`에서 `bbkc_exit:` 섹션 + `BBKC_EXIT_MODE` env var override |
| `config.yaml` | `bbkc_exit:` 섹션 추가 (be25_st60_di30 default) |
| `scripts/run_bbkc_live_trade.py` | `BBKC_ROUND5_MODE=true` 가드 + config-derived BBKCSqueeze 파라미터 인스턴스화 |
| `tests/test_strategies/test_bbkc_squeeze.py` | `MockBroker`에 `update_tp` 메서드 추가 |
| `tests/test_strategies/test_bbkc_squeeze_exit_modes.py` | `_MockBroker`에 `update_tp` 메서드 추가 |
| 기타 broker mock | `update_tp` 메서드 추가 |

---

## Phase A — Audit (read-only)

### Task 1: src vs legacy 라이브 경로 audit + A/B/C 결정

**Files:**
- Create: `docs/operations/src_vs_legacy_audit.md`

이 task는 코드 수정 없음. 비교표 작성 + 결정. 결과에 따라 Phase B 이후 task scope가 바뀔 수 있음 (B/C 채택 시 Phase B-D 재정의 필요).

- [ ] **Step 1: src 경로 파일 읽기**

```bash
cat scripts/run_bbkc_live_trade.py | head -200
cat src/execution/bbkc_demo_broker.py
cat src/execution/live_broker.py
cat src/execution/paper_runner.py 2>/dev/null || echo "(missing)"
cat src/execution/paper_broker.py 2>/dev/null || echo "(missing)"
```

- [ ] **Step 2: legacy 경로 파일 읽기 (reference)**

```bash
cat _legacy/run_bbkc_trade.py | head -100
grep -nE "^def |^class " _legacy/paper_engine/trading_engine.py | head -40
```

- [ ] **Step 3: 비교표 작성 (`docs/operations/src_vs_legacy_audit.md`)**

다음 11 항목 각각 ✓/✗/부분 + 한 줄 코멘트:

| 항목 | src | legacy | 격차 |
|---|---|---|---|
| DB persistence (trade_log/signal_log/fill_log) | | | |
| signal/trade/fill 상세 로깅 | | | |
| WebSocket 15m confirmed feed | | | |
| 시작 시 15m/1h/4h gap fill | | | |
| 15m → 1h 정각 경계 리샘플링 | | | |
| confirmed 1h에서 전략 실행 | | | |
| API 포지션 vs 로컬 포지션 reconcile | | | |
| demo 재시작 후 진행 중 포지션 복구 | | | |
| manual close / update_stop 운영 도구 | | | |
| telegram/log 알림 | | | |
| heartbeat (1분 간격 equity/positions/daily PnL) | | | |

각 행에 `run_bbkc_live_trade.py:LINE` 또는 `_legacy/run_bbkc_trade.py:LINE` 형태로 인용.

- [ ] **Step 4: 결정 게이트**

비교표 끝에 결정 섹션:

```markdown
## Decision

Based on the comparison:
- Missing critical features in src: <list>
- Verdict:
  - [ ] A: src 충분 → Round 5 Phase B-D 그대로
  - [ ] A + 보완: src에 작은 누락 → Phase B에 1-2개 task 추가
  - [ ] B: legacy에 be_trail 포팅 → Phase B-D 재정의 필요 (이번 plan 무효, 새 plan 작성)
  - [ ] C: 병렬 demo → 보류 (주문/포지션 충돌 위험)
```

- [ ] **Step 5: 사용자 review checkpoint**

A 또는 A+보완: Phase B로 진행. B/C: **STOP**, 사용자에게 알리고 plan 재작성 협의.

- [ ] **Step 6: Commit audit document**

```bash
git add docs/operations/src_vs_legacy_audit.md
git commit -m "docs(ops): src vs legacy live path audit (Round 5 Phase A)"
```

---

## Phase B — API + Broker plumbing (assumes A 결정)

### Task 2: `BybitRestClient.set_trading_stop` 메서드

**Files:**
- Modify: `src/api/rest_client.py` (메서드 추가, `place_order` 다음에 배치)
- Create: `tests/test_api/test_set_trading_stop.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_api/test_set_trading_stop.py`:

```python
"""BybitRestClient.set_trading_stop 단위 테스트 (round 5)."""
from unittest.mock import MagicMock

from src.api.rest_client import BybitRestClient


def _make_client() -> BybitRestClient:
    """Bypass __init__ to avoid pybit HTTP construction with real keys."""
    rest = BybitRestClient.__new__(BybitRestClient)
    rest.api_key = "k"
    rest.api_secret = "s"
    rest.base_url = "https://api-demo.bybit.com"
    rest._session = MagicMock()
    rest._session.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    return rest


def test_set_trading_stop_passes_both_sl_and_tp():
    rest = _make_client()
    rest.set_trading_stop(symbol="BTCUSDT", stop_loss=99.5, take_profit=101.5,
                          position_idx=1)
    assert rest._session.set_trading_stop.call_count == 1
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert kwargs["category"] == "linear"
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["tpslMode"] == "Full"
    assert kwargs["positionIdx"] == 1
    assert kwargs["stopLoss"] == "99.5"
    assert kwargs["takeProfit"] == "101.5"


def test_set_trading_stop_omits_sl_when_none():
    rest = _make_client()
    rest.set_trading_stop(symbol="ETHUSDT", stop_loss=None, take_profit=2500.0,
                          position_idx=2)
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert "stopLoss" not in kwargs
    assert kwargs["takeProfit"] == "2500.0"
    assert kwargs["positionIdx"] == 2


def test_set_trading_stop_omits_tp_when_none():
    rest = _make_client()
    rest.set_trading_stop(symbol="ETHUSDT", stop_loss=2400.0, take_profit=None,
                          position_idx=1)
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert kwargs["stopLoss"] == "2400.0"
    assert "takeProfit" not in kwargs


def test_set_trading_stop_default_position_idx_zero():
    rest = _make_client()
    rest.set_trading_stop(symbol="BTCUSDT", stop_loss=99.0)
    kwargs = rest._session.set_trading_stop.call_args.kwargs
    assert kwargs["positionIdx"] == 0


def test_set_trading_stop_returns_session_response():
    rest = _make_client()
    rest._session.set_trading_stop.return_value = {"retCode": 0, "result": {"x": 1}}
    out = rest.set_trading_stop(symbol="BTCUSDT", stop_loss=99.0, position_idx=1)
    assert out == {"retCode": 0, "result": {"x": 1}}
```

If `tests/test_api/__init__.py` does not exist, create it as an empty file.

- [ ] **Step 2: Run failing test**

```bash
python -m pytest tests/test_api/test_set_trading_stop.py -v
```
Expected: FAIL — `BybitRestClient` has no `set_trading_stop` method.

- [ ] **Step 3: Add method to `src/api/rest_client.py`**

Insert this method right after `place_order` (and before any close/cancel utilities):

```python
    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        position_idx: int = 0,
    ) -> Dict[str, Any]:
        """Bybit /v5/position/trading-stop via pybit.

        체결 후 또는 BE/trail 트리거 시 SL/TP를 갱신할 때 사용.
        positionIdx: 0=OneWay, 1=Hedge Buy, 2=Hedge Sell.
        Round 5 §5.1 참조. category="linear", tpslMode="Full" 고정.

        Raises: pybit/HTTP 예외 — caller가 잡아 WARN 로그로 처리할 것.
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

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/test_api/test_set_trading_stop.py -v
```
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/rest_client.py tests/test_api/__init__.py tests/test_api/test_set_trading_stop.py
git commit -m "feat(api): BybitRestClient.set_trading_stop wrapper (round 5 §5.1)"
```

---

### Task 3: `LiveBroker._position_idx_for_side` helper

**Files:**
- Modify: `src/execution/live_broker.py` (helper 메서드 추가)
- Create: `tests/test_execution/test_live_broker_helpers.py`

**테스트 파일 분리 정책**: `tests/test_execution/test_live_broker.py`는 이미 존재. 기존 smoke/basic 테스트(생성자, get_position, get_portfolio 등)는 그 파일에 그대로 유지. **Round 5 helper + API 동기화 테스트(set_trading_stop 호출, 성공/실패 분기, sync_positions SL/TP 파싱 등)는 새 파일 `test_live_broker_helpers.py`로 분리**. 이유:
- 기존 파일을 200+ 줄 unrelated 테스트로 부풀리지 않음
- Round 5 변경의 회귀를 한 파일에서 일관되게 추적
- Phase B 끝나고 Round 6에서 helper 추가될 때 같은 패턴 유지 가능

- [ ] **Step 1: Write failing test**

Create `tests/test_execution/test_live_broker_helpers.py`:

```python
"""LiveBroker helper 메서드 단위 테스트 (round 5)."""
from unittest.mock import MagicMock

from src.execution.live_broker import LiveBroker


def _make_broker() -> LiveBroker:
    """Bypass __init__ to avoid wallet sync."""
    broker = LiveBroker.__new__(LiveBroker)
    broker._rest = MagicMock()
    broker._alert = None
    broker._risk = MagicMock()
    broker._leverage = 3
    broker._initial_capital = 50000.0
    broker._positions = {}
    broker._equity = 50000.0
    return broker


def test_position_idx_for_long_returns_1():
    broker = _make_broker()
    assert broker._position_idx_for_side("LONG") == 1


def test_position_idx_for_short_returns_2():
    broker = _make_broker()
    assert broker._position_idx_for_side("SHORT") == 2
```

- [ ] **Step 2: Run failing test**

```bash
python -m pytest tests/test_execution/test_live_broker_helpers.py -v
```
Expected: FAIL — `_position_idx_for_side` not defined.

- [ ] **Step 3: Add helper to `src/execution/live_broker.py`**

Insert after `__init__` (before `buy`):

```python
    def _position_idx_for_side(self, side: str) -> int:
        """Hedge mode 가정: LONG=1, SHORT=2.

        NOTE: 계정이 one-way mode이면 0을 반환해야 함. 현재 src 경로는
        rest_client.place_order의 자동 도출 로직(side='Buy' → 1)과 동일하게
        hedge를 가정. one-way 전환 시 이 헬퍼 + place_order 모두 수정 필요.
        Round 5 §5.2 참조.
        """
        return 1 if side == "LONG" else 2
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest tests/test_execution/test_live_broker_helpers.py -v
```
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/execution/live_broker.py tests/test_execution/test_live_broker_helpers.py
git commit -m "feat(live_broker): _position_idx_for_side hedge-mode helper (round 5 §5.2)"
```

---

### Task 4: `LiveBroker.update_stop` API 연동

**Files:**
- Modify: `src/execution/live_broker.py` (`update_stop` 본체 교체)
- Modify: `tests/test_execution/test_live_broker_helpers.py` (성공/실패 케이스 추가)

- [ ] **Step 1: Write failing test**

Append to `tests/test_execution/test_live_broker_helpers.py`:

```python
import logging

from src.execution.broker import Position


def _make_broker_with_long_pos() -> LiveBroker:
    broker = _make_broker()
    broker._positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="LONG", qty=0.1, entry_price=100.0,
        entry_time=0, stop_loss=95.0, take_profit=110.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    return broker


def test_update_stop_calls_set_trading_stop_with_position_idx_1_for_long():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_stop("BTCUSDT", 96.5)
    assert broker._rest.set_trading_stop.call_count == 1
    kwargs = broker._rest.set_trading_stop.call_args.kwargs
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["stop_loss"] == 96.5
    assert kwargs["position_idx"] == 1


def test_update_stop_updates_local_only_on_success():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_stop("BTCUSDT", 96.5)
    assert broker._positions["BTCUSDT"].stop_loss == 96.5


def test_update_stop_does_not_update_local_on_api_failure(caplog):
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.side_effect = RuntimeError("boom")
    with caplog.at_level(logging.WARNING, logger="src.execution.live_broker"):
        broker.update_stop("BTCUSDT", 96.5)
    # local 미갱신 (서버 값과 일치 유지)
    assert broker._positions["BTCUSDT"].stop_loss == 95.0
    # WARN 로그 확인
    assert any("set_trading_stop" in rec.message and "BTCUSDT" in rec.message
               for rec in caplog.records)


def test_update_stop_no_op_for_unknown_symbol():
    broker = _make_broker()
    broker.update_stop("BTCUSDT", 96.5)
    assert broker._rest.set_trading_stop.call_count == 0


def test_update_stop_short_uses_position_idx_2():
    broker = _make_broker()
    broker._positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="SHORT", qty=0.1, entry_price=100.0,
        entry_time=0, stop_loss=105.0, take_profit=90.0,
        unrealized_pnl=0.0, strategy_name="BBKCSqueeze",
    )
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_stop("BTCUSDT", 103.5)
    kwargs = broker._rest.set_trading_stop.call_args.kwargs
    assert kwargs["position_idx"] == 2
```

- [ ] **Step 2: Run failing tests**

```bash
python -m pytest tests/test_execution/test_live_broker_helpers.py -v
```
Expected: new tests FAIL — current `update_stop` only updates local, no API call.

- [ ] **Step 3: Replace `update_stop` body in `src/execution/live_broker.py`**

Replace the existing 3-line body (`pos = self._positions.get(symbol); if pos: pos.stop_loss = new_stop`) with:

```python
    def update_stop(self, symbol: str, new_stop: float) -> None:
        """API 경유 SL 갱신 (round 5 §5.2). 성공 시에만 로컬 갱신."""
        pos = self._positions.get(symbol)
        if pos is None:
            return
        pos_idx = self._position_idx_for_side(pos.side)
        try:
            self._rest.set_trading_stop(
                symbol=symbol, stop_loss=new_stop, position_idx=pos_idx,
            )
            pos.stop_loss = new_stop   # 성공 시에만 (서버 ↔ 로컬 일치)
        except Exception as exc:
            logger.warning(
                "set_trading_stop(stop_loss) failed for %s: %s "
                "— local stop_loss not updated to keep server/local consistent",
                symbol, exc,
            )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/test_execution/test_live_broker_helpers.py -v
```
Expected: ALL pass (helper + 5 update_stop tests = 7 total).

- [ ] **Step 5: Commit**

```bash
git add src/execution/live_broker.py tests/test_execution/test_live_broker_helpers.py
git commit -m "feat(live_broker): update_stop calls Bybit set_trading_stop, local updated only on success (round 5 §5.2)"
```

---

### Task 5: `update_tp` Protocol + BacktestBroker + LiveBroker

**Files:**
- Modify: `src/execution/broker.py` (Protocol)
- Modify: `src/execution/backtest_broker.py` (구현)
- Modify: `src/execution/live_broker.py` (구현 + manual_update_tp 경유)
- Modify: 영향 받는 mock — `tests/test_strategies/test_bbkc_squeeze.py`, `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`
- Modify: `tests/test_execution/test_live_broker_helpers.py` (update_tp 테스트 추가)

- [ ] **Step 1: Add Protocol method**

In `src/execution/broker.py`, find the `Broker` Protocol and add (between `update_stop` and `manual_buy`):

```python
    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None: ...
```

- [ ] **Step 2a: Update PositionTracker.update_tp signature to accept None**

In `src/execution/position_tracker.py:44`, change:

```python
    def update_tp(self, symbol: str, new_tp: float) -> None:
        pos = self._positions.get(symbol)
        if pos:
            pos.take_profit = new_tp
```

to:

```python
    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None:
        """Optional[float] 허용 — None은 TP 제거 의미 (drop_tp / 운영자 manual close 등)."""
        pos = self._positions.get(symbol)
        if pos:
            pos.take_profit = new_tp
```

Add `from typing import Optional` if not already imported (file already imports `Optional` from `typing` per `Position` field types).

- [ ] **Step 2b: Add BacktestBroker.update_tp**

In `src/execution/backtest_broker.py`, find `update_stop` method and add immediately after:

```python
    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None:
        self._positions.update_tp(symbol, new_tp)
```

(Verifies `PositionTracker.update_tp` accepts `Optional[float]` per Step 2a.)

- [ ] **Step 3: Add LiveBroker.update_tp + rewire manual_update_tp**

In `src/execution/live_broker.py`, replace the existing `manual_update_tp` and add `update_tp`:

```python
    def update_tp(self, symbol: str, new_tp: Optional[float]) -> None:
        """API 경유 TP 갱신 (round 5 §5.3). 성공 시에만 로컬 갱신."""
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

- [ ] **Step 4: Add update_tp tests for LiveBroker**

Append to `tests/test_execution/test_live_broker_helpers.py`:

```python
def test_update_tp_calls_set_trading_stop_with_take_profit():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.update_tp("BTCUSDT", 112.0)
    kwargs = broker._rest.set_trading_stop.call_args.kwargs
    assert kwargs["take_profit"] == 112.0
    assert kwargs["position_idx"] == 1
    assert broker._positions["BTCUSDT"].take_profit == 112.0


def test_update_tp_does_not_update_local_on_api_failure():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.side_effect = RuntimeError("boom")
    broker.update_tp("BTCUSDT", 112.0)
    # 로컬 미갱신 (110.0 그대로)
    assert broker._positions["BTCUSDT"].take_profit == 110.0


def test_manual_update_tp_routes_through_update_tp():
    broker = _make_broker_with_long_pos()
    broker._rest.set_trading_stop.return_value = {"retCode": 0, "result": {}}
    broker.manual_update_tp("BTCUSDT", 113.0)
    assert broker._rest.set_trading_stop.call_count == 1
    assert broker._positions["BTCUSDT"].take_profit == 113.0
```

- [ ] **Step 5: Update broker mocks in strategy tests**

In `tests/test_strategies/test_bbkc_squeeze.py`, find `class MockBroker` (at top of file). Add this method after `update_stop` (or after `close` if no `update_stop` exists):

```python
    def update_tp(self, symbol, new_tp):
        pass
```

In `tests/test_strategies/test_bbkc_squeeze_exit_modes.py`, find `class _MockBroker` and add similarly:

```python
    def update_tp(self, symbol, new_tp):
        self.tp_updates.append((symbol, new_tp))
```

Also add to `_MockBroker.__init__`:

```python
        self.tp_updates = []
```

- [ ] **Step 6: BacktestBroker test for update_tp**

Append to `tests/test_execution/test_backtest_broker_mfe.py` (or create new test file):

```python
def test_backtest_broker_update_tp(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.buy("BTCUSDT", qty=0.1, stop_loss=90.0, take_profit=110.0)
    bar1 = _bar("BTCUSDT", 2, 100, 105, 99, 102)
    broker.process_bar(bar1)
    broker.update_tp("BTCUSDT", 115.0)
    pos = broker.get_position("BTCUSDT")
    assert pos.take_profit == 115.0


def test_backtest_broker_update_tp_to_none(broker):
    bar0 = _bar("BTCUSDT", 1, 100, 100, 100, 100)
    broker.process_bar(bar0)
    broker.buy("BTCUSDT", qty=0.1, stop_loss=90.0, take_profit=110.0)
    bar1 = _bar("BTCUSDT", 2, 100, 105, 99, 102)
    broker.process_bar(bar1)
    broker.update_tp("BTCUSDT", None)
    pos = broker.get_position("BTCUSDT")
    assert pos.take_profit is None
```

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/test_execution/ tests/test_strategies/ tests/test_api/ -q --no-header
```
Expected: ALL pass. If any mock breaks, add `update_tp` no-op stub.

- [ ] **Step 8: Commit**

```bash
git add src/execution/broker.py src/execution/backtest_broker.py src/execution/live_broker.py tests/test_execution/test_live_broker_helpers.py tests/test_execution/test_backtest_broker_mfe.py tests/test_strategies/test_bbkc_squeeze.py tests/test_strategies/test_bbkc_squeeze_exit_modes.py
git commit -m "feat(broker): update_tp Protocol + BacktestBroker + LiveBroker (round 5 §5.3, §5.5, §5.6)"
```

---

### Task 6: `LiveBroker.sync_positions` SL/TP 파싱

**Files:**
- Modify: `src/execution/live_broker.py:sync_positions` (line 85-97)
- Modify: `tests/test_execution/test_live_broker_helpers.py` (sync_positions 테스트 추가)

- [ ] **Step 1: Write failing test**

Append to `tests/test_execution/test_live_broker_helpers.py`:

```python
def test_sync_positions_parses_stop_loss_take_profit():
    broker = _make_broker()
    broker._rest.get_positions.return_value = [
        {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "0.1",
            "avgPrice": "100.0",
            "stopLoss": "95.5",
            "takeProfit": "110.5",
            "unrealisedPnl": "0.0",
        },
    ]
    broker.sync_positions()
    pos = broker._positions["BTCUSDT"]
    assert pos.stop_loss == 95.5
    assert pos.take_profit == 110.5
    assert pos.entry_price == 100.0
    assert pos.qty == 0.1


def test_sync_positions_handles_empty_stop_loss_string():
    broker = _make_broker()
    broker._rest.get_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "100.0",
         "stopLoss": "", "takeProfit": "", "unrealisedPnl": "0.0"},
    ]
    broker.sync_positions()
    pos = broker._positions["BTCUSDT"]
    assert pos.stop_loss == 0.0
    assert pos.take_profit is None


def test_sync_positions_handles_zero_stop_loss():
    broker = _make_broker()
    broker._rest.get_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Sell", "size": "0.1", "avgPrice": "100.0",
         "stopLoss": "0", "takeProfit": "0", "unrealisedPnl": "0.0"},
    ]
    broker.sync_positions()
    pos = broker._positions["BTCUSDT"]
    assert pos.stop_loss == 0.0
    assert pos.take_profit is None
    assert pos.side == "SHORT"
```

- [ ] **Step 2: Run failing tests**

```bash
python -m pytest tests/test_execution/test_live_broker_helpers.py -v -k sync_positions
```
Expected: FAIL — current `sync_positions` hardcodes `stop_loss=0.0, take_profit=None`.

- [ ] **Step 3: Replace sync_positions body**

In `src/execution/live_broker.py`, replace the body of `sync_positions`:

```python
    def sync_positions(self) -> None:
        """Bybit get_positions 응답에서 SL/TP 파싱 (round 5 §5.4).

        재시작 후 BE/trail로 이동된 stop_loss/take_profit을 잃지 않도록 함.
        빈 문자열 / "0" / None은 해당 필드 미설정으로 취급.
        """
        raw_positions = self._rest.get_positions()
        new_positions: Dict[str, Position] = {}
        for raw in raw_positions:
            size = float(raw.get("size", 0))
            if size <= 0:
                continue
            symbol = raw["symbol"]
            side = "LONG" if raw.get("side") == "Buy" else "SHORT"

            sl_raw = raw.get("stopLoss")
            tp_raw = raw.get("takeProfit")
            sl_value = float(sl_raw) if sl_raw not in (None, "", "0") else 0.0
            tp_value = float(tp_raw) if tp_raw not in (None, "", "0") else None

            new_positions[symbol] = Position(
                symbol=symbol, side=side, qty=size,
                entry_price=float(raw.get("avgPrice", 0)),
                entry_time=0,
                stop_loss=sl_value,
                take_profit=tp_value,
                unrealized_pnl=float(raw.get("unrealisedPnl", 0)),
                strategy_name="SYNCED",
            )
        self._positions = new_positions
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_execution/test_live_broker_helpers.py -v
```
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add src/execution/live_broker.py tests/test_execution/test_live_broker_helpers.py
git commit -m "feat(live_broker): sync_positions parses Bybit stopLoss/takeProfit (round 5 §5.4)"
```

---

## Phase C — Config + Entrypoint

### Task 7: `bbkc_exit:` config block + `BBKC_EXIT_MODE` env var override

**Files:**
- Modify: `config.yaml`
- Modify: `src/core/config.py`
- Modify: `tests/test_core/test_config.py` (or create if missing)

- [ ] **Step 1: Add bbkc_exit block to config.yaml**

Append to `config.yaml`:

```yaml
# Round 5: BBKC be_trail 청산 운영 정책 (round 4 ROBUST_PROMOTE 1순위 후보)
bbkc_exit:
  mode: be_trail        # be_trail | fixed (env BBKC_EXIT_MODE로 override 가능)
  trail_be_at_tp_frac: 0.25
  trail_start_at_tp_frac: 0.60
  trail_distance_tp_frac: 0.30
  drop_tp: false
  time_stop_bars: 0
```

- [ ] **Step 2: Write failing test**

Create or append to `tests/test_core/test_config.py`:

```python
"""bbkc_exit config + BBKC_EXIT_MODE env override (round 5 §7.1)."""
import os
from pathlib import Path

import pytest

from src.core.config import load_config, BBKCExitConfig


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_bbkc_exit_config_loaded_from_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("BBKC_EXIT_MODE", raising=False)
    cfg_path = _write_config(tmp_path, """
app:
  mode: demo
bbkc_exit:
  mode: be_trail
  trail_be_at_tp_frac: 0.25
  trail_start_at_tp_frac: 0.60
  trail_distance_tp_frac: 0.30
  drop_tp: false
  time_stop_bars: 0
""")
    cfg = load_config(str(cfg_path))
    assert isinstance(cfg.bbkc_exit, BBKCExitConfig)
    assert cfg.bbkc_exit.mode == "be_trail"
    assert cfg.bbkc_exit.trail_be_at_tp_frac == 0.25
    assert cfg.bbkc_exit.trail_start_at_tp_frac == 0.60
    assert cfg.bbkc_exit.trail_distance_tp_frac == 0.30
    assert cfg.bbkc_exit.drop_tp is False
    assert cfg.bbkc_exit.time_stop_bars == 0


def test_bbkc_exit_mode_env_override_to_fixed(tmp_path, monkeypatch):
    monkeypatch.setenv("BBKC_EXIT_MODE", "fixed")
    cfg_path = _write_config(tmp_path, """
app:
  mode: demo
bbkc_exit:
  mode: be_trail
  trail_be_at_tp_frac: 0.25
  trail_start_at_tp_frac: 0.60
  trail_distance_tp_frac: 0.30
  drop_tp: false
  time_stop_bars: 0
""")
    cfg = load_config(str(cfg_path))
    assert cfg.bbkc_exit.mode == "fixed"   # env override


def test_bbkc_exit_defaults_when_yaml_missing_block(tmp_path, monkeypatch):
    monkeypatch.delenv("BBKC_EXIT_MODE", raising=False)
    cfg_path = _write_config(tmp_path, "app:\n  mode: demo\n")
    cfg = load_config(str(cfg_path))
    # Defaults: round 4 winner be25_st60_di30
    assert cfg.bbkc_exit.mode == "be_trail"
    assert cfg.bbkc_exit.trail_be_at_tp_frac == 0.25
    assert cfg.bbkc_exit.trail_start_at_tp_frac == 0.60
    assert cfg.bbkc_exit.trail_distance_tp_frac == 0.30
```

If `tests/test_core/__init__.py` does not exist, create it as empty.

- [ ] **Step 3: Run failing tests**

```bash
python -m pytest tests/test_core/test_config.py -v -k bbkc_exit
```
Expected: FAIL — `BBKCExitConfig` does not exist, `cfg.bbkc_exit` not present.

- [ ] **Step 4: Add `BBKCExitConfig` dataclass + load logic**

In `src/core/config.py`, near the other `@dataclass` definitions (after `RiskConfig` etc., before `AppConfig`):

```python
@dataclass
class BBKCExitConfig:
    """Round 5 BBKC 청산 운영 정책. config.yaml의 ``bbkc_exit`` 섹션 + env var.

    env BBKC_EXIT_MODE 설정 시 yaml의 mode를 override (kill switch).
    """
    mode: str = "be_trail"
    trail_be_at_tp_frac: float = 0.25
    trail_start_at_tp_frac: float = 0.60
    trail_distance_tp_frac: float = 0.30
    drop_tp: bool = False
    time_stop_bars: int = 0
```

In `AppConfig` dataclass, add field:

```python
@dataclass
class AppConfig:
    # ... existing fields ...
    bbkc_exit: BBKCExitConfig = field(default_factory=BBKCExitConfig)
```

In `load_config`, after the existing yaml parsing, add:

```python
    # Round 5: bbkc_exit + env override
    raw_bbkc = (raw or {}).get("bbkc_exit", {}) or {}
    bbkc_exit = BBKCExitConfig(
        mode=raw_bbkc.get("mode", BBKCExitConfig.mode),
        trail_be_at_tp_frac=float(raw_bbkc.get(
            "trail_be_at_tp_frac", BBKCExitConfig.trail_be_at_tp_frac)),
        trail_start_at_tp_frac=float(raw_bbkc.get(
            "trail_start_at_tp_frac", BBKCExitConfig.trail_start_at_tp_frac)),
        trail_distance_tp_frac=float(raw_bbkc.get(
            "trail_distance_tp_frac", BBKCExitConfig.trail_distance_tp_frac)),
        drop_tp=bool(raw_bbkc.get("drop_tp", BBKCExitConfig.drop_tp)),
        time_stop_bars=int(raw_bbkc.get("time_stop_bars", BBKCExitConfig.time_stop_bars)),
    )
    # env BBKC_EXIT_MODE override (kill switch)
    env_mode = os.getenv("BBKC_EXIT_MODE")
    if env_mode:
        import logging
        logging.getLogger(__name__).warning(
            "BBKC_EXIT_MODE env override active: mode=%s "
            "(kill-switch path; check rollback procedure in runbook §7.2)",
            env_mode,
        )
        bbkc_exit.mode = env_mode

    # 기존 AppConfig 인스턴스화 시 bbkc_exit=bbkc_exit 추가
```

Adjust `AppConfig(...)` instantiation in `load_config` to pass `bbkc_exit=bbkc_exit`.

The exact existing `load_config` shape may differ; the engineer must adapt — but the additions above are the new logic.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_core/test_config.py -v
```
Expected: ALL pass (3 new + existing config tests).

- [ ] **Step 6: Commit**

```bash
git add config.yaml src/core/config.py tests/test_core/__init__.py tests/test_core/test_config.py
git commit -m "feat(config): bbkc_exit block + BBKC_EXIT_MODE env override (round 5 §7.1)"
```

---

### Task 8: `run_bbkc_live_trade.py` BBKC_ROUND5_MODE 가드 + config 인스턴스화

**Files:**
- Modify: `scripts/run_bbkc_live_trade.py`
- Create or modify: `tests/test_cli/test_run_bbkc_live_trade.py`

This task implements §3 IN #8 (BBKC_ROUND5_MODE guard) + §9 file changes (config-derived BBKCSqueeze) — the work bundle the user explicitly requested.

- [ ] **Step 1: Locate argparse setup**

Find the argument parser in `scripts/run_bbkc_live_trade.py` (around line 41-45). The `--stop-at` and `--stop-in-minutes` options exist there.

- [ ] **Step 2: Write failing test for guard**

Create `tests/test_cli/test_run_bbkc_live_trade.py`:

```python
"""run_bbkc_live_trade BBKC_ROUND5_MODE guard (round 5 §2.3, §3 IN #8)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _run_script(args, env_extra=None):
    env = {**__import__("os").environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "scripts.run_bbkc_live_trade", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True, text=True, timeout=15,
    )


def test_round5_mode_rejects_stop_at(tmp_path):
    res = _run_script(
        ["--run-id", "test_guard", "--stop-at", "2026-12-31"],
        env_extra={"BBKC_ROUND5_MODE": "true"},
    )
    assert res.returncode != 0
    out = (res.stderr + res.stdout).lower()
    assert "stop-at" in out or "round5" in out or "bbkc_round5_mode" in out


def test_round5_mode_rejects_stop_in_minutes():
    res = _run_script(
        ["--run-id", "test_guard", "--stop-in-minutes", "5"],
        env_extra={"BBKC_ROUND5_MODE": "true"},
    )
    assert res.returncode != 0
    out = (res.stderr + res.stdout).lower()
    assert "stop-in-minutes" in out or "round5" in out or "bbkc_round5_mode" in out


def test_round5_mode_off_allows_stop_in_minutes_smoke():
    """Smoke: 가드 미설정 시 --stop-in-minutes 통과 (단순 인자 파싱 단계만 확인).

    실제 sweep 실행은 timeout으로 정지될 수 있어서, 매우 짧은 stop으로 빠르게 종료.
    """
    res = _run_script(
        ["--run-id", "smoke_test", "--stop-in-minutes", "0"],
        env_extra={"BBKC_ROUND5_MODE": "false"},
    )
    # parsing OK이면 returncode==0 또는 기능적 실패(API key 없음 등)는 OK.
    # ValueError로 인한 가드 실패만 거부.
    out = (res.stderr + res.stdout).lower()
    assert "bbkc_round5_mode" not in out, "guard should not fire when off"
```

If `tests/test_cli/__init__.py` does not exist, create it as empty.

- [ ] **Step 3: Run failing test**

```bash
python -m pytest tests/test_cli/test_run_bbkc_live_trade.py -v -k round5_mode_rejects
```
Expected: FAIL — guard not implemented.

- [ ] **Step 4: Add guard to `scripts/run_bbkc_live_trade.py`**

Find the section right after argparse `args = parser.parse_args()` and add:

```python
    # Round 5 §2.3: 자동 종료 금지 강제 가드 (BBKC_ROUND5_MODE=true).
    # forward 운영 시 운영자가 BBKC_ROUND5_MODE=true로 시작하면
    # --stop-at/--stop-in-minutes는 시작 거부됨. smoke 테스트는 가드 미설정으로.
    if os.getenv("BBKC_ROUND5_MODE", "").lower() == "true":
        if args.stop_at or getattr(args, "stop_in_minutes", None):
            parser.error(
                "BBKC_ROUND5_MODE=true: --stop-at/--stop-in-minutes are forbidden "
                "in Round 5 forward operations (per round 5 design §2.3). "
                "Unset BBKC_ROUND5_MODE for smoke tests."
            )
```

- [ ] **Step 5: Wire config-derived BBKCSqueeze instantiation**

**Location**: `scripts/run_bbkc_live_trade.py:213` 의 `_dispatch_bar()` 메서드 안에 다음 코드가 있음:

```python
        feed = HistoricalDataFeed(
            db=self._db, symbols=[symbol], timeframe="1h",
        )
        full = feed.get_full_series(symbol)
        strat = BBKCSqueeze()                                # ← 이 라인
        cache = strat.prepare(full)
        i = len(full.bars) - 1
        try:
            strat.on_bar_fast(bar, i, cache, self._broker)
```

`_dispatch_bar()`가 1h confirmed bar마다 호출되므로 매 봉마다 새 strategy 인스턴스 생성. config는 매번 load할 필요 없으니 `__init__`에서 한 번 로드 후 인스턴스 변수로 저장하는 패턴이 깔끔.

**(a)** Class `__init__`에서 config 로드 (대상 클래스는 `_dispatch_bar`가 속한 runner 클래스 — 파일 상단 grep으로 확인. 일반적으로 `BbkcLiveTradeRunner` 또는 유사):

```python
class BbkcLiveTradeRunner:   # 또는 실제 클래스 이름
    def __init__(self, ...):
        # ... existing init ...
        # Round 5 §7.1: config 한 번만 로드
        cfg = load_config()
        self._exit_cfg = cfg.bbkc_exit
        logger.info(
            "Round 5 BBKC exit profile loaded: mode=%s be=%.2f start=%.2f dist=%.2f drop_tp=%s",
            self._exit_cfg.mode, self._exit_cfg.trail_be_at_tp_frac,
            self._exit_cfg.trail_start_at_tp_frac, self._exit_cfg.trail_distance_tp_frac,
            self._exit_cfg.drop_tp,
        )
```

**(b)** `_dispatch_bar()`의 `strat = BBKCSqueeze()` (line 213) 교체:

```python
        strat = BBKCSqueeze(
            bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
            atr_period=14, rsi_period=14, rsi_filter=70.0,
            tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h",
            exit_mode=self._exit_cfg.mode,
            trail_be_at_tp_frac=self._exit_cfg.trail_be_at_tp_frac,
            trail_start_at_tp_frac=self._exit_cfg.trail_start_at_tp_frac,
            trail_distance_tp_frac=self._exit_cfg.trail_distance_tp_frac,
            drop_tp=self._exit_cfg.drop_tp,
            time_stop_bars=self._exit_cfg.time_stop_bars,
        )
```

**중요**: 매 dispatch마다 strat이 새로 생기므로 `_pos_meta`(strategy 인스턴스 변수)는 매 봉마다 비어있는 상태로 시작. lazy init 로직(`on_bar_fast` 도입부에서 broker.get_position 보고 자동 init)이 이를 처리하도록 Round 3에서 설계됨 — 추가 변경 불필요. 이 plan의 Round 4까지 검증된 동작.

**Import 확인**: 파일 상단 import에 `from src.core.config import RiskConfig, load_config`가 이미 있음 (line 71). 추가 import 불필요.

- [ ] **Step 6: Run guard tests**

```bash
python -m pytest tests/test_cli/test_run_bbkc_live_trade.py -v
```
Expected: ALL pass (2 reject tests + 1 smoke).

- [ ] **Step 7: Manual smoke (optional but recommended)**

```bash
# 가드 켰을 때 거부 확인
BBKC_ROUND5_MODE=true python -m scripts.run_bbkc_live_trade --run-id test --stop-at 2026-12-31
# Expected: "BBKC_ROUND5_MODE=true: --stop-at/--stop-in-minutes are forbidden ..."

# 가드 끄면 통과 (스크립트가 다른 이유로 실패해도 가드 메시지는 안 뜸)
unset BBKC_ROUND5_MODE
python -m scripts.run_bbkc_live_trade --run-id test --stop-in-minutes 0
```

- [ ] **Step 8: Commit**

```bash
git add scripts/run_bbkc_live_trade.py tests/test_cli/__init__.py tests/test_cli/test_run_bbkc_live_trade.py
git commit -m "feat(scripts): BBKC_ROUND5_MODE guard + config-derived BBKCSqueeze (round 5 §2.3, §7)"
```

---

### Task 9: `scripts/check_15m_to_1h_parity.py` (parity 도구)

**Files:**
- Create: `scripts/check_15m_to_1h_parity.py`

운영 도구. 단위 테스트는 합성 함수(synth_1h_from_15m)에 한정. 실제 Bybit API 호출은 옵션 e2e (이번 라운드 OUT).

- [ ] **Step 1: Create script**

```python
"""15m × 4 합성 1h vs Bybit direct 1h parity check (round 5 §6).

운영자가 1주 1회 수동 실행. 자동 fallback 안 함 — parity drift 발견 시
운영자가 수동으로 결정.

Usage:
    python -m scripts.check_15m_to_1h_parity --symbol BTCUSDT --bars 24
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.rest_client import BybitRestClient
from src.core.config import load_config


def synth_1h_from_15m(bars_15m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """4 consecutive 15m bars → 1 synthesized 1h bar (정각 경계 정렬).

    Input 봉들은 open_time 오름차순. 각 1h 윈도우의 open_time은 4*15m=3600000ms 배수.
    윈도우 미완성(4개 미만) 시 출력에서 제외.
    """
    out: List[Dict[str, Any]] = []
    bucket: List[Dict[str, Any]] = []
    for bar in bars_15m:
        ot = int(bar["open_time"])
        # 1h window 시작은 ot % 3600000 == 0
        if not bucket and ot % 3_600_000 != 0:
            continue   # 첫 봉이 정각 시작 아니면 skip
        bucket.append(bar)
        if len(bucket) == 4:
            window_start = int(bucket[0]["open_time"])
            out.append({
                "open_time": window_start,
                "open": bucket[0]["open"],
                "high": max(b["high"] for b in bucket),
                "low": min(b["low"] for b in bucket),
                "close": bucket[-1]["close"],
                "volume": sum(b["volume"] for b in bucket),
            })
            bucket = []
    return out


def _compare_bars(synth: List[Dict[str, Any]], direct: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """합성과 직접 봉을 open_time 기준 매칭, 차이 검출."""
    direct_by_ot = {int(b["open_time"]): b for b in direct}
    diffs: List[Dict[str, Any]] = []
    for s in synth:
        d = direct_by_ot.get(int(s["open_time"]))
        if d is None:
            diffs.append({"open_time": s["open_time"], "issue": "missing in direct"})
            continue
        for field in ("open", "high", "low", "close", "volume"):
            sv, dv = float(s[field]), float(d[field])
            if abs(sv - dv) > max(1e-6, abs(dv) * 1e-6):   # 6자리 정밀 허용
                diffs.append({
                    "open_time": s["open_time"], "field": field,
                    "synth": sv, "direct": dv, "delta": sv - dv,
                })
    return diffs


def main() -> None:
    parser = argparse.ArgumentParser(description="15m→1h parity check vs Bybit direct 1h")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--bars", type=int, default=24, help="비교할 1h 봉 수")
    args = parser.parse_args()

    cfg = load_config()
    rest = BybitRestClient(cfg.app.api_key, cfg.app.api_secret, cfg.app.base_url)

    n_15m = args.bars * 4 + 4   # 여유분
    bars_15m = rest.get_klines(symbol=args.symbol, interval="15", limit=n_15m)
    bars_15m.sort(key=lambda b: int(b["open_time"]))

    bars_1h_direct = rest.get_klines(symbol=args.symbol, interval="60", limit=args.bars + 2)
    bars_1h_direct.sort(key=lambda b: int(b["open_time"]))

    synth = synth_1h_from_15m(bars_15m)
    diffs = _compare_bars(synth, bars_1h_direct)

    print(f"Synthesized 1h bars: {len(synth)}")
    print(f"Direct 1h bars:      {len(bars_1h_direct)}")
    print(f"Differences:         {len(diffs)}")
    if diffs:
        print("\nFirst 10 diffs:")
        for d in diffs[:10]:
            print(f"  {d}")
        sys.exit(1)
    print("\nParity OK — no significant differences.")
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Unit test for synth_1h_from_15m**

Append to `tests/test_scripts/test_15m_to_1h_parity.py` (new file):

```python
"""Test synth_1h_from_15m (round 5 §6)."""
from scripts.check_15m_to_1h_parity import synth_1h_from_15m


def test_synth_one_window_4_bars():
    bars_15m = [
        {"open_time": 0,        "open": 100, "high": 105, "low":  99, "close": 102, "volume": 10},
        {"open_time":  900_000, "open": 102, "high": 108, "low": 101, "close": 107, "volume": 12},
        {"open_time": 1_800_000, "open": 107, "high": 110, "low": 105, "close": 109, "volume":  8},
        {"open_time": 2_700_000, "open": 109, "high": 112, "low": 106, "close": 111, "volume": 15},
    ]
    out = synth_1h_from_15m(bars_15m)
    assert len(out) == 1
    o = out[0]
    assert o["open_time"] == 0
    assert o["open"] == 100
    assert o["high"] == 112
    assert o["low"] == 99
    assert o["close"] == 111
    assert o["volume"] == 45


def test_synth_skips_partial_first_bar():
    """첫 15m 봉이 정각 경계 아니면 skip."""
    bars_15m = [
        {"open_time":  900_000, "open": 102, "high": 108, "low": 101, "close": 107, "volume": 12},
        {"open_time": 1_800_000, "open": 107, "high": 110, "low": 105, "close": 109, "volume":  8},
        {"open_time": 2_700_000, "open": 109, "high": 112, "low": 106, "close": 111, "volume": 15},
        # 다음 정각 봉은 3_600_000
        {"open_time": 3_600_000, "open": 111, "high": 113, "low": 110, "close": 112, "volume":  9},
    ]
    out = synth_1h_from_15m(bars_15m)
    assert len(out) == 0   # 정각 시작 봉이 1개뿐이라 4개 채우지 못함


def test_synth_two_windows():
    bars_15m = [
        # window 1
        {"open_time":         0, "open": 100, "high": 105, "low":  99, "close": 102, "volume": 10},
        {"open_time":   900_000, "open": 102, "high": 108, "low": 101, "close": 107, "volume": 12},
        {"open_time": 1_800_000, "open": 107, "high": 110, "low": 105, "close": 109, "volume":  8},
        {"open_time": 2_700_000, "open": 109, "high": 112, "low": 106, "close": 111, "volume": 15},
        # window 2
        {"open_time": 3_600_000, "open": 111, "high": 113, "low": 110, "close": 112, "volume":  9},
        {"open_time": 4_500_000, "open": 112, "high": 115, "low": 111, "close": 114, "volume": 11},
        {"open_time": 5_400_000, "open": 114, "high": 116, "low": 113, "close": 115, "volume": 13},
        {"open_time": 6_300_000, "open": 115, "high": 117, "low": 113, "close": 116, "volume":  7},
    ]
    out = synth_1h_from_15m(bars_15m)
    assert len(out) == 2
    assert out[0]["open_time"] == 0
    assert out[1]["open_time"] == 3_600_000
    assert out[1]["close"] == 116
    assert out[1]["high"] == 117
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_scripts/test_15m_to_1h_parity.py -v
```
Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/check_15m_to_1h_parity.py tests/test_scripts/test_15m_to_1h_parity.py
git commit -m "feat(scripts): check_15m_to_1h_parity tool (round 5 §6, manual operator use)"
```

---

## Phase D — Documentation

### Task 10: Round 5 운영 runbook

**Files:**
- Create: `docs/operations/bbkc_exit_round5_runbook.md`

이 task는 §4.1 + §7.2 + §9의 작업 묶음 중 **운영자 절차** 부분.

- [ ] **Step 1: Create runbook**

```markdown
# BBKC Exit Round 5 — Operations Runbook

이 문서는 Round 5 forward 운영자를 위한 체크리스트입니다. 설계 문서는
`docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round5_design.md` 참조.

## 1. Forward 시작 절차

### 1.1 사전 점검

- [ ] config.yaml 의 `bbkc_exit:` 섹션 값 확인 (default = be25_st60_di30):
  ```yaml
  bbkc_exit:
    mode: be_trail
    trail_be_at_tp_frac: 0.25
    trail_start_at_tp_frac: 0.60
    trail_distance_tp_frac: 0.30
    drop_tp: false
    time_stop_bars: 0
  ```
- [ ] `BYBIT_API_KEY` / `BYBIT_API_SECRET` env vars 설정됨
- [ ] `config.app.mode == "demo"` 확인
- [ ] Bybit 계정이 **Hedge mode** 설정됨 (one-way 전환 시 코드 수정 필요)
- [ ] 기존 BBKC 포지션 없음 (또는 사용자가 처리 방침 결정)

### 1.2 Forward 시작

```bash
export BBKC_ROUND5_MODE=true
python -m scripts.run_bbkc_live_trade --run-id bbkc_round5_forward_<YYYYMMDD>
```

⚠️ **금지 옵션**: `--stop-at`, `--stop-in-minutes`. `BBKC_ROUND5_MODE=true` 가
켜져 있으면 위 옵션은 시작 거부 (parser.error). 자동 종료 금지 원칙 (설계 §2.3).

종료는 SIGINT (Ctrl+C) 또는 kill switch만.

## 2. Kill switch 절차

### 2.1 신규 진입 fixed 롤백

새 진입을 fixed로 되돌릴 때:

```bash
export BBKC_EXIT_MODE=fixed
python -m scripts.run_bbkc_live_trade --run-id <id>   # 또는 데모 재시작
```

다음 BBKC 신규 진입부터 fixed SL/TP로 들어감. config.yaml은 그대로 둠.

### 2.2 이미 열린 포지션 처리

**자동 rollback 안 함** (설계 §7.2). 운영자 선택:

| 선택지 | 명령 |
|---|---|
| 자연 종료 대기 | (아무 것도 안 함) — 현 SL/TP 트리거할 때까지 |
| 수동 close | python REPL에서 `broker.manual_close("BTCUSDT", reason="rollback")` |
| 수동 SL 되돌리기 | `broker.manual_update_stop("BTCUSDT", original_fixed_sl)` |

이미 BE/trail로 이동된 SL이 거래소에 등록돼 있을 수 있음 — 자동 되돌리면
의도하지 않은 SL 변경 발생.

### 2.3 긴급 신규 진입 차단

`BBKC_DISABLE_NEW_ENTRY=true` 같은 강제 차단은 Round 6 후보 (현재 미구현).
긴급 시 SIGINT로 데모 종료.

## 3. 1개월 mid-review (기술 검증)

forward 시작 후 약 1개월 시점, 운영자가 수동으로 점검:

- [ ] BE/trail trigger 시 `set_trading_stop` 호출 로그 발생 (`grep -E "set_trading_stop|update_stop" logs/...`)
- [ ] Bybit 측 stopLoss가 BE/trail 따라 이동 — `python -m scripts.check_account` 로 현재 포지션 SL 확인
- [ ] 데모 재시작 후 SL/TP 상태 복구 — `sync_positions` 후 stop_loss/take_profit이 0 또는 None이 아닌 실제 값
- [ ] ETH 진입 ≥ 1건 (있으면 좋음, 없어도 FAIL 아님 — 시그널 환경 문제일 수 있음)

판정:
- 위 4 항목 모두 ✓: PASS, forward 계속
- 일부 ✗: FAIL, 원인 분석 + 코드 수정 후 forward 재시작
- ETH 진입 0건이지만 다른 항목 ✓: WATCH, 추가 1개월 관찰

## 4. 3개월 final 평가

자세한 PASS/STRONG/WATCH/FAIL 기준은 설계 §8.3 참조.

핵심:
- 주 기준 = ETH **R/trade**
- 보조 = mean PnL (단 L2 [같은 캘린더 fixed 백테스트 재계산]와만 비교)
- L1 (Round 4 F0 backtest) mean PnL +154는 참고용

L2 재계산:
```bash
# forward 기간을 캘린더로 명시. 예: 2026-04-29 ~ 2026-07-29
# F0 cell 한정으로 같은 OOS 윈도우로 백테스트 → mean PnL, R/trade 산출
python -m scripts.bbkc_exit_eval --cell F0 --symbol ETHUSDT
```

## 5. 15m → 1h parity check (1주 1회)

```bash
python -m scripts.check_15m_to_1h_parity --symbol BTCUSDT --bars 24
python -m scripts.check_15m_to_1h_parity --symbol ETHUSDT --bars 24
python -m scripts.check_15m_to_1h_parity --symbol AVAXUSDT --bars 24
```

차이 발견 시:
- **미세한 차이** (floating point, 마지막 봉 timing): 무시
- **반복적·구조적 차이**: 사용자에게 보고 + Round 6에서 "전략 신호는
  Bybit confirmed 1h 직접봉" 대안 검토 (이번 라운드는 자동 fallback 안 함)

## 6. 모니터링 명령

```bash
# 현재 계정 상태
python -m scripts.check_account

# 최근 거래 로그
sqlite3 db/bybit_data.db "SELECT * FROM trade_log ORDER BY id DESC LIMIT 10;"

# WARN 로그 (set_trading_stop 실패 시)
grep -i "set_trading_stop" logs/live_demo/*/run.log | tail -20
```

## 7. Round 5 종료 → Round 6 입력

3개월 final 평가 완료 시점에 Round 6 brainstorming 시작:

- forward 결과 (PASS/STRONG/WATCH/FAIL)
- 발견된 운영 이슈
- 라이브 적용 결정 (PASS이면 라이브 운영 채택 검토)
- 13코인 일반화, ETH time_stop 정밀화, BBKC_DISABLE_NEW_ENTRY 등 차후 후보
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/bbkc_exit_round5_runbook.md
git commit -m "docs(ops): bbkc exit round 5 runbook (kill switch + reviews + parity check)"
```

---

## Phase E — Final regression + push

### Task 11: 회귀 검증 + push + Round 5 §14 round-up template

**Files:**
- Modify: `Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round5_design.md` (§14)

- [ ] **Step 1: Run full regression**

```bash
python -m pytest tests/test_strategies/ tests/test_execution/ tests/test_scripts/ tests/test_api/ tests/test_core/ tests/test_cli/ tests/_legacy/ -q --no-header
```
Expected: ALL pass.

- [ ] **Step 2: Confirm audit decision recorded**

`docs/operations/src_vs_legacy_audit.md` 의 Decision 섹션에 A/A+보완 체크 + 오늘 날짜 + 운영자 서명. B/C 결정이었으면 이 plan 자체가 무효이므로 step 1까지 도달 못 함.

- [ ] **Step 3: Update spec §14 with execution summary**

In `2026-04-29_bbkc_exit_round5_design.md`, locate `## 14. Round 5 Results`. Replace placeholder content with:

```markdown
## 14. Round 5 Results (코드 머지 + 운영 정책 확정 시점)

**Status**: 코드/정책 완료. forward 모니터링 별도 관찰 기간으로 분리.

### 14.1 Audit 결과 (A/B/C 결정)

(Phase A 산출물 `docs/operations/src_vs_legacy_audit.md` 결정 섹션 요약. 채택안: A 또는 A+보완)

### 14.2 코드 변경 완료 사항

- `src/api/rest_client.py`: `set_trading_stop` wrapper 추가
- `src/execution/live_broker.py`: `_position_idx_for_side` 헬퍼, `update_stop` API 연동, `update_tp` 신규, `manual_update_tp` API 경유, `sync_positions` SL/TP 파싱
- `src/execution/broker.py`: Protocol에 `update_tp`
- `src/execution/backtest_broker.py`: `update_tp` 구현
- `src/core/config.py`: `BBKCExitConfig` + env override
- `config.yaml`: `bbkc_exit:` 섹션
- `scripts/run_bbkc_live_trade.py`: `BBKC_ROUND5_MODE` 가드 + config-derived BBKCSqueeze
- `scripts/check_15m_to_1h_parity.py`: parity 도구 신규
- 테스트 다수 + mock에 `update_tp` 추가

머지 커밋: <commit hash range, 예 fcc0061..XXXXXXX>

### 14.3 운영 정책 확정 사항

config.yaml: be25_st60_di30 (be=0.25, start=0.60, dist=0.30, drop_tp=false, time_stop_bars=0)
Kill switch: BBKC_EXIT_MODE=fixed (신규 진입만)
기존 포지션: 자동 rollback 없음 (수동 처리, runbook §2.2)
자동 종료 금지: BBKC_ROUND5_MODE=true 가드로 코드 강제

### 14.4 Forward 시작 신호

(forward 시작 일시, 첫 진입 트랜잭션 — 운영자 기록)

### 14.5 Round 6 후보

- forward 1개월 mid-review 결과 → 기술 검증 / 시그널 환경
- forward 3개월 final-review 결과 → PASS/STRONG/WATCH/FAIL 분기
- BBKC_DISABLE_NEW_ENTRY 가드
- One-way mode 전환 지원
- 15m → 1h parity 자동 fallback (drift 발견 시)
- 13코인 일반화

### 14.6 한 줄 요약

라운드 5는 be25_st60_di30 운영을 위한 코드/정책 인프라를 완료. forward는 라운드 외부 관찰 기간 시작.
```

- [ ] **Step 4: Commit + push**

```bash
git add Crypto/Bybit_Trading/docs/superpowers/specs/experiments/2026-04-29_bbkc_exit_round5_design.md
git commit -m "docs(bbkc_exit): round 5 §14 results — code/policy complete, forward begins"

# Optional: feature branch 사용 시 main 머지
# git checkout main && git merge --no-ff feature/bbkc-exit-round5 -m "Merge feature/bbkc-exit-round5"

git push origin main   # 또는 push origin <feature-branch>
```

- [ ] **Step 5: Tag round 5 (optional)**

```bash
git tag -a bbkc-exit-round5-code-complete -m "Round 5: code + policy complete; forward begins"
git push origin bbkc-exit-round5-code-complete
```

---

## Self-Review Checklist (run after writing all tasks)

- [ ] **Spec coverage**: Every spec §3 IN bullet has a task
  - §3 IN #1 audit → Task 1
  - §3 IN #2 set_trading_stop → Task 2
  - §3 IN #3a-d (LiveBroker mods) → Tasks 3, 4, 5, 6
  - §3 IN #4 Broker Protocol update_tp → Task 5
  - §3 IN #5 BacktestBroker update_tp → Task 5
  - §3 IN #6 config.yaml + load_config → Task 7
  - §3 IN #7 parity tool → Task 9
  - §3 IN #8 BBKC_ROUND5_MODE guard → Task 8
  - §3 IN #9 ops policy doc → Task 10
  - §3 IN #10 forward procedure → Task 10 (runbook)
  - §4 audit gate → Task 1
  - §5.1-5.7 모든 코드 변경 → Task 2-6
  - §6 parity check → Task 9
  - §7.1 config + env override → Task 7
  - §7.2 kill switch 절차 → Task 10 runbook
  - §8 forward test 절차 → Task 10 runbook
  - §11 리스크 → 명시되지 않은 별도 task 불필요 (인지 사항)
- [ ] **No placeholders**: §14 round-up template inside Task 11 step 3 is intentional fill-after; the template lines are concrete instructions.
- [ ] **Type consistency**: 
  - `BBKCExitConfig` field names consistent across Tasks 7, 8 (config.yaml + load_config + run_bbkc_live_trade)
  - `set_trading_stop` signature consistent (Task 2 def vs Tasks 4, 5 calls)
  - `_position_idx_for_side` consistent (Task 3 def vs Task 4, 5 calls)
  - `update_tp(symbol, new_tp)` signature consistent (Tasks 5 Protocol, BacktestBroker, LiveBroker)
- [ ] **Path consistency**: all scripts under `scripts/`, all src under `src/`, all tests under `tests/`. New `tests/test_api/`, `tests/test_core/`, `tests/test_cli/` directories with `__init__.py`.

## Deferred Items (out-of-scope for this plan)

1. forward 1-3개월 monitoring + 평가 → Round 6 input (운영 외부 관찰 기간)
2. `BBKC_DISABLE_NEW_ENTRY=true` 신규 진입 차단 (Round 6 후보)
3. One-way mode 지원 — hedge 가정만 명시
4. 15m → 1h parity 자동 fallback — drift 발견 시 운영자 수동 결정
5. 13코인 일반화
6. legacy `_legacy/` 추가 변경 — Round 2 F2 + BBKC trailing gate 그대로 유지
7. 다른 청산 primitive (partial TP 등)
