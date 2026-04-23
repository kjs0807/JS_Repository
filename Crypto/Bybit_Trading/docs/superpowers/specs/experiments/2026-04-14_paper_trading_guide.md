# BBKC[BIGTHREE] Paper Trading — Setup & Operation Guide

**날짜**: 2026-04-14
**적용 대상**: `BBKCSqueeze[BIGTHREE]` (BTC + ETH + AVAX) staged
promote paper validation
**구현 파일**:
- `src/execution/paper_broker.py`
- `src/execution/paper_runner.py`
- `scripts/run_bbkc_paper.py`
- `tests/test_execution/test_paper_broker.py`

## 1. 목적

BBKCSqueeze[BIGTHREE]를 staged promote에서 정식 승격으로 보내기 위한
**paper trading 경로**를 확보한다. 실제 Bybit live 주문 API는
건드리지 않되, 같은 strategy/broker 흐름으로 운용 가능성과 재현성을
검증한다.

- **지금 가능**: replay-based paper trading (히스토리컬 데이터 재생)
- **다음 단계**: live websocket paper trading (이 가이드의 구조를 그대로 상속받는 `LivePaperRunner` subclass 추가)

## 2. 구성 요소 한눈 요약

```
scripts/run_bbkc_paper.py
         │
         ▼
   PaperRunner (src/execution/paper_runner.py)
         │   ─ multi-symbol bar merge
         │   ─ warmup gate
         │   ─ strategy.prepare + on_bar_fast
         │   ─ checkpoint every N bars
         ▼
   PaperBroker (src/execution/paper_broker.py)
         │   ─ subclasses BacktestBroker
         │   ─ universe guard (BIGTHREE only)
         │   ─ fills.jsonl / signals.jsonl / equity_curve.csv append
         │   ─ paper_state.json save/load/restore
         ▼
   HistoricalDataFeed  (replay data source)
```

**Live broker는 호출하지 않는다**. PaperBroker는 Bybit REST client를
import하지 않아서 네트워크 미구성으로 실거래 주문이 나갈 가능성이
구조적으로 봉쇄되어 있다.

## 3. 실행 명령

### 3.1 기본 실행 (자동 window, auto run-id)

```bash
python -m scripts.run_bbkc_paper
```

기본값:
- `--symbols BTCUSDT ETHUSDT AVAXUSDT`
- `--start` = 오늘 - 14일
- `--end` = 오늘
- `--warmup-days 14`
- `--initial-capital 10000`
- `--run-id` = `<start>_<end>_HHMMSS`
- `--checkpoint-every 200` 봉마다 state 저장

### 3.2 특정 기간, 특정 run-id

```bash
python -m scripts.run_bbkc_paper \
    --start 2026-03-25 --end 2026-04-10 \
    --run-id 2026_march_window
```

### 3.3 Dry-run (준비만 하고 바로 종료)

```bash
python -m scripts.run_bbkc_paper \
    --start 2026-04-08 --end 2026-04-10 \
    --run-id dryrun --dry-run
```

`--dry-run`은 broker + runner 생성까지만 확인. state 파일은 작성하지만
bar 루프는 돌리지 않음. Resume 테스트에도 유용.

### 3.4 Resume (같은 run-id 재실행)

```bash
python -m scripts.run_bbkc_paper --run-id 2026_march_window \
    --start 2026-03-25 --end 2026-04-10
```

실행 시 `run_dir/paper_state.json`이 있으면 자동 로드 +
`broker.restore_from_state`로 포지션/equity/last_bar_ts 복원. 그
이후 bar 루프가 이어진다. Pending order는 복원 대상이 아니다 (설계
참고: `paper_broker.py::save_state` docstring).

## 4. 산출물 경로

```
logs/paper/bbkc_bigthree/<run_id>/
├── paper_state.json      ─ 포트폴리오 스냅샷 (resume 용)
├── signals.jsonl         ─ 전략 signal 로그 (현재는 브로커 log_signal 호출 시 기록)
├── fills.jsonl           ─ 완료된 trade row (entry→exit 쌍)
└── equity_curve.csv      ─ bar당 equity/realized_pnl/n_open_positions
```

### 4.1 paper_state.json 구조

```json
{
  "run_id": "smoke_2026_march",
  "run_dir": "…",
  "symbols_allowed": ["AVAXUSDT","BTCUSDT","ETHUSDT"],
  "equity": 10907.11,
  "equity_incl_unrealized": 10882.00,
  "realized_pnl": 986.12,
  "n_open_positions": 1,
  "positions": [
    {"symbol": "BTCUSDT","side":"LONG","qty":0.1297,
     "entry_price":72139.94,"entry_time":1775750400000,
     "stop_loss":70435.54,"take_profit":73560.67,
     "unrealized_pnl":-25.11,"strategy_name":"STRATEGY"}
  ],
  "trades_total": 15,
  "last_bar_ts": {"BTCUSDT":1775779200000,…},
  "updated_at": "2026-04-14T21:46:42+00:00",
  "extra": {"bars_processed":2047,"final":true,…}
}
```

### 4.2 fills.jsonl 예시

```json
{"symbol":"AVAXUSDT","strategy_name":"STRATEGY","side":"LONG",
 "entry_time":1773349200000,"exit_time":1773360000000,
 "entry_price":9.608,"exit_price":9.798,"qty":892.3,
 "pnl":164.05,"fee":4.81,"exit_reason":"TP","source":"STRATEGY"}
```

### 4.3 equity_curve.csv 예시

```
ts_ms,equity,realized_pnl,n_open_positions
1773187200000,10000.0000,0.0000,0
...
1775779200000,10881.9964,986.1230,1
```

## 5. 운영 절차

### 5.1 시작 / 중단 / 재개

1. **시작**: `python -m scripts.run_bbkc_paper --start … --end … --run-id NAME`
2. **중단**: `Ctrl+C`. `PaperRunner._install_signal_handler`가 SIGINT를
   잡아서 다음 bar boundary에서 루프를 빠져나간 뒤 `save_state`를
   한 번 더 호출한다. 강제 `kill -9`는 중간 상태를 잃을 수 있으므로
   권장하지 않음.
3. **재개**: 같은 `--run-id`로 동일 명령 재실행. 로그 파일은
   append-only이므로 기존 fills.jsonl / equity_curve.csv에 이어
   쓰인다.

### 5.2 2주 paper run 프로토콜 (권장)

이것이 BIGTHREE 정식 승격의 최종 관문이다. Protocol §P8.

```bash
# 시작일 지정 (ex: 오늘)
python -m scripts.run_bbkc_paper \
    --start 2026-04-14 --end 2026-04-28 \
    --run-id bigthree_paper_2w_start2026-04-14 \
    --checkpoint-every 100
```

2주 이후 체크리스트:
- `fills.jsonl`에서 trade 수, win rate, 평균 pnl 확인
- `equity_curve.csv`로 drawdown 계산
- `paper_state.json`으로 final equity / realized_pnl 확인
- 2주 결과가 다음을 모두 만족해야 정식 승격:
  - realized_pnl > 0
  - drawdown ≤ 15% (holdout 결과와 동등 이하)
  - 3개 심볼 모두에서 최소 1건 이상 trade
  - 심볼 concentration ≤ 65% (verdict 기본값)

### 5.3 주의사항

- **Live 주문 발생 경로 없음**: PaperBroker는 Bybit REST client를
  import하지 않는다. 하지만 사용자가 `main_live.py`와 이 스크립트를
  혼동하지 않도록 **paper 전용 entrypoint를 명확히 구분**한다. live
  전환은 반드시 별도 commit + 별도 문서 근거와 함께.
- **BIGTHREE 외 universe 금지**: `--symbols`로 SOL/LINK를 추가하면
  PaperBroker 레벨에서 거부된다 (WARN 로그). 실수 방지.
- **Replay 윈도우 주의**: `--start` / `--end`가 DB 커버리지 밖이면
  HistoricalDataFeed가 빈 DataFrame을 반환해 bar 0개로 실행된다.
  데이터 수집은 `scripts/collect_daily_history.py` 또는 1h 데이터
  수집 스크립트로 선행.
- **Entry logic 변경 금지**: BBKCSqueeze는 P5에 따라 entry 불변.
  paper에서도 동일.

## 6. 금지 사항 (Protocol §P5 / §P7 / §P8 재확인)

1. `PaperBroker`가 Bybit REST 주문 API를 호출하지 않도록 유지.
2. BBKCSqueeze의 `on_bar_fast` entry 로직 수정 금지.
3. Paper 결과만으로 정식 승격 결정 금지 — paper는 holdout 2-window
   PROMOTE에 더해지는 **추가 증거**일 뿐.
4. Paper와 live를 같은 entrypoint에 플래그로 섞지 말 것. live 전환은
   반드시 `main_live.py`를 통해.
5. Paper run 중 signal loop에 RSI regime artifact 등 research 산출물
   연결 금지 (Protocol §P9).

## 7. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `plan=0 bars` | window가 DB 커버리지 밖. `--start/--end` 확인 |
| `blocked for SOLUSDT — not in allowed universe` | 의도한 동작. BIGTHREE 외 심볼은 거부됨 |
| resume 후 equity가 처음과 다름 | 정상 — 이전 run의 state를 이어받음 |
| `주문 거부 MDD 한도 초과` 로그 폭주 | RiskConfig max_drawdown_pct=0.15가 일부 combo에서 빠르게 hit. 현재 파라미터에서는 정상 발생 |
| pending order 사라짐 | save_state 시 pending은 discarded (설계). 손실 최소화하려면 `--checkpoint-every`를 줄이거나 루프 중 정지 타이밍 조절 |

## 8. Live paper mode (실제 2주 전진 paper)

2026-04-14 구현 완료. `scripts/run_bbkc_paper_live.py` + `src/data_manager/gap_filler.py`.

### 8.1 Gap filler

`src/data_manager/gap_filler.py`는 Bybit mainnet v5 kline에서 특정
interval/범위의 bar를 가져와 DB에 upsert한다. `_legacy/collector/historical.py`의 페이지네이션 패턴을 현재 `src/` 아키텍처에 맞춰 재작성한 모듈.

- `fill_gap(db, symbol, "60", since_ms, until_ms)` — 단일 심볼
- `fill_gap_for_universe(db, symbols, "60", since_ms)` — 여러 심볼
- `current_db_tail_ms(db, symbol, "60")` — DB 마지막 bar 시간

### 8.2 Live paper entry point

```bash
python -m scripts.run_bbkc_paper_live \
    --run-id bigthree_paper_2w_start<YYYY-MM-DD> \
    --stop-at <YYYY-MM-DD>
```

동작:
1. 시작 시 `(now - warmup_days) ~ now` 구간의 gap을 자동 fill
2. Bybit WebSocket `kline.60.<symbol>` 구독 (`src/api/ws_client.py`)
3. 매 bar confirm 시:
   - DB에 upsert
   - DB에서 full_series 재조회 → strategy.prepare 재실행
   - broker.process_bar + strategy.on_bar_fast
   - broker.save_state (per bar)
4. `--stop-at` 도달 또는 SIGINT 시 정리

### 8.3 Smoke: gap-fill-only

ws loop 없이 gap fill 경로만 확인:

```bash
python -m scripts.run_bbkc_paper_live \
    --run-id gapfill_smoke --gap-fill-only
```

### 8.4 Resume

같은 `--run-id`로 재실행 시:
1. `paper_state.json` 자동 load + 포지션 복원
2. `current_db_tail_ms`로 DB 꼬리 확인 → 중단 중 빠진 bar gap fill
3. ws 재구독 + 루프 재개

Append-only log이므로 fills.jsonl / equity_curve.csv는 이어 쓰인다.

### 8.5 14일 운영 절차

```bash
# Day 0
python -m scripts.run_bbkc_paper_live \
    --run-id bigthree_paper_2w_start2026-04-14 \
    --stop-at 2026-04-28

# (중단 발생 시 같은 커맨드 재실행)
# Day 14: 프로세스 자동 종료, 최종 summary 출력

# 결과 확인
cat logs/paper/bbkc_bigthree/bigthree_paper_2w_start2026-04-14/paper_state.json
wc -l logs/paper/bbkc_bigthree/bigthree_paper_2w_start2026-04-14/fills.jsonl
```

### 8.6 Historical replay vs live 차이

| 측면 | historical replay (`run_bbkc_paper.py`) | live (`run_bbkc_paper_live.py`) |
|---|---|---|
| 데이터 소스 | DB에 이미 있는 bar | ws 실시간 + gap fill로 DB 동기화 |
| 시간 | 즉시 완료 | 실시간 (14일 = 14일) |
| 용도 | 과거 구간 검증 | forward staged promote 검증 |
| 출력 | 동일 (paper_state.json, fills.jsonl, equity_curve.csv) | 동일 |
| 재현성 | deterministic | not deterministic (실제 ws 순서) |

### 8.7 2026-04-14 기준 historical replay 결과 (참고)

확장된 데이터로 재실행 (`run-id: bigthree_paper_2w_hist_extended`):
- window: `2026-03-31 ~ 2026-04-14` (14일)
- bars processed: 1903
- trades: **16**
- realized pnl: **+$1154.63**
- equity incl unrealized: **+$11,078.03**
- open positions at end: 0

이는 historical replay이므로 실제 forward paper 결과의 **기저선**이지
production decision criterion은 아니다. forward 14-day live paper가
최종 관문.

## 9. PaperBroker 자체는 live/replay 둘 다 지원

`PaperBroker` 자체는 bar source를 가리지 않는다. Replay (`PaperRunner`)
와 live (`BbkcLivePaperRunner`) 둘 다 동일 broker 인스턴스를 사용한다.
향후 다른 strategy class용 live runner를 추가할 때도 `PaperBroker`
는 수정 불필요.

### 8.2 RiskConfig 조정

현재 `RiskConfig()` 기본값은 `max_drawdown_pct=0.15`. Paper 용도로는
좀 더 느슨하게 (`0.25` 정도) 가져가도 된다. CLI 플래그 추가는 3줄
수준.

### 8.3 Telegram / Discord 알림

`PaperRunner.run`에서 `broker.save_state` 직후 optional alert manager
호출 훅을 추가하면 된다. `src/core/alert.py::AlertManager`가 이미
존재.

## 9. 재현성 요약

paper smoke (2026-04-14 검증):

```bash
python -m scripts.run_bbkc_paper \
    --start 2026-03-25 --end 2026-04-10 \
    --run-id smoke_2026_march
```

결과:
- bars processed: 2047 (2137 planned, warmup 90 skipped)
- trades total: 15
- realized_pnl: +$986.12
- equity_incl_unrealized: $10,882
- open positions at end: 1 (BTCUSDT LONG)
- 모든 artifact 파일 생성: `paper_state.json`, `fills.jsonl`, `equity_curve.csv`

## 10. 테스트

```bash
python -m pytest tests/test_execution/test_paper_broker.py -q
```

11 tests passing (universe guard, equity append, fills logging, signal
logging, state save/load, restore).
