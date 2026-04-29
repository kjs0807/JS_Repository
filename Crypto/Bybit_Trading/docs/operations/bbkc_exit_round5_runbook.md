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
log에 다음 WARN이 보이면 override 적중:

```
WARNING ... BBKC_EXIT_MODE env override active: mode=fixed
(kill-switch path; check rollback procedure in runbook §7.2)
```

### 2.2 이미 열린 포지션 처리 — 즉시 운영 명령

**자동 rollback 안 함** (설계 §7.2). 운영자 선택. CLI는 없으므로 Python REPL로 실행.

#### A. REPL 부팅 (한 번만)

forward 데모를 다른 셸에서 돌리고 있는 동안, **별도 셸**에서:

```bash
cd C:\Users\ceoji\Desktop\python_ibks\Crypto\Bybit_Trading
python
```

```python
>>> import os
>>> from src.api.rest_client import BybitRestClient
>>> from src.core.config import load_config, RiskConfig
>>> from src.execution.bbkc_demo_broker import BbkcDemoBroker
>>> from pathlib import Path
>>>
>>> cfg = load_config("config.yaml")
>>> rest = BybitRestClient(
...     os.getenv("BYBIT_API_KEY"),
...     os.getenv("BYBIT_API_SECRET"),
...     cfg.app.base_url,
... )
>>> broker = BbkcDemoBroker(
...     rest_client=rest,
...     run_dir=Path("logs/manual_ops"),
...     symbols_allowed=["BTCUSDT", "ETHUSDT", "AVAXUSDT"],
...     risk_config=RiskConfig(),
...     leverage=cfg.app.leverage,
... )
>>> broker.sync()                      # API에서 현재 포지션/잔고 가져옴
>>> for p in broker.get_positions(): print(p.symbol, p.side, p.qty, p.stop_loss, p.take_profit)
```

#### B. 자주 쓰는 명령

| 의도 | 명령 |
|---|---|
| 자연 종료 대기 | (REPL 닫기 — 아무 것도 안 함) |
| 포지션 1개 즉시 청산 | `broker.manual_close("BTCUSDT", reason="rollback")` |
| 모든 BBKC 포지션 즉시 청산 | `broker.manual_close_all(reason="kill_switch")` |
| BE/trail 으로 이동된 SL을 fixed로 되돌리기 | `broker.manual_update_stop("BTCUSDT", original_fixed_sl)` |
| TP 제거 (drop_tp 효과를 라이브 적용) | `broker.manual_update_tp("BTCUSDT", None)` |
| TP를 다른 가격으로 갱신 | `broker.manual_update_tp("BTCUSDT", 70000.0)` |

⚠️ `manual_update_stop` / `manual_update_tp`는 Round 5에서 API 경유로 변경됐으므로
**Bybit 거래소 측 SL/TP가 즉시 변경됨**. local-only 아님. 잘못 입력하면 즉시 영향.

⚠️ 이미 BE/trail로 이동된 SL은 거래소에 등록돼 있음 — `manual_update_stop`으로
"되돌리기" 시 의도치 않은 SL 후퇴(loosen) 가능. 신중히.

#### C. forward 데모 재시작은 보통 불필요

`broker.sync()` 후 `BbkcDemoBroker._positions`가 거래소 측 상태로 동기화되므로,
forward 데모와 운영 REPL이 별도 broker 인스턴스여도 둘 다 거래소 = single source of truth.
다만 동시에 같은 포지션을 건드리지 말 것 (예: 운영자가 close 한 직후 forward 데모가
같은 심볼에 신규 진입하면 충돌).

### 2.3 긴급 신규 진입 차단

`BBKC_DISABLE_NEW_ENTRY=true` 같은 강제 차단은 Round 6 후보 (현재 미구현).
긴급 시 SIGINT로 forward 데모 종료 → 새 진입 차단 효과 (이미 열린 포지션은 §2.2 참조).

## 3. 1개월 mid-review (기술 검증)

forward 시작 후 약 1개월 시점, 운영자가 수동으로 점검:

- [ ] BE/trail trigger 시 `set_trading_stop` 호출 로그 발생
  ```bash
  grep -E "set_trading_stop|update_stop" logs/live_demo/bbkc_bigthree/<run_id>/*.log | tail -50
  ```
- [ ] Bybit 측 stopLoss가 BE/trail 따라 이동
  ```bash
  python -m scripts.check_account
  ```
- [ ] 데모 재시작 후 SL/TP 상태 복구 — 재시작 직후 log에서 `[startup] equity=... positions=N`,
  REPL `broker.sync()` 후 `pos.stop_loss`/`pos.take_profit`이 0/None이 아닌 실제 값
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

# orders.jsonl audit (Round 5 src 경로의 trade_log 대체)
tail -f logs/live_demo/bbkc_bigthree/<run_id>/orders.jsonl

# WARN 로그 (set_trading_stop 실패 시)
grep -i "set_trading_stop" logs/live_demo/bbkc_bigthree/<run_id>/*.log | tail -20

# heartbeat (60s 간격)
grep -i "heartbeat\|equity\|daily" logs/live_demo/bbkc_bigthree/<run_id>/*.log | tail -20
```

## 7. Round 5 종료 → Round 6 입력

3개월 final 평가 완료 시점에 Round 6 brainstorming 시작:

- forward 결과 (PASS/STRONG/WATCH/FAIL)
- 발견된 운영 이슈
- 라이브 적용 결정 (PASS이면 라이브 운영 채택 검토)
- 13코인 일반화, ETH time_stop 정밀화, BBKC_DISABLE_NEW_ENTRY, manual_close CLI 등 차후 후보
