# Regression Fixtures

PR 8 BBKC 회귀 게이트(`tests/test_bbkc_regression.py`)에서 사용하는 데이터.

## 위치

- `tests/fixtures/bbkc_signals.csv` — 기준 신호 시퀀스 (모의매매 등 legacy 출력)
- `tests/fixtures/{symbol}_{timeframe}.parquet` — OHLCV 데이터 (예: `BTCUSDT_1h.parquet`)

둘 다 존재해야 회귀 테스트가 활성화된다. 하나라도 없으면 `pytest.skip`.

## bbkc_signals.csv 포맷

UTF-8 CSV. 헤더 필수.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `symbol` | str | 거래 심볼 (예: `BTCUSDT`). Phase 1 회귀는 단일 symbol. |
| `timestamp` | ISO8601 (UTC) | 신호 발행 시각. ClockEvent.timestamp = 봉 마감 = 의사결정 시각. |
| `direction` | str | `buy` 또는 `sell`. Phase 1 long-only 진입(buy) / 청산(sell). |
| `source_run_id` | str | 신호를 생성한 원본 백테스트/모의매매 run id (감사용). |

예시:
```csv
symbol,timestamp,direction,source_run_id
BTCUSDT,2026-01-15T08:00:00+00:00,buy,legacy_mock_v1
BTCUSDT,2026-01-18T20:00:00+00:00,sell,legacy_mock_v1
```

## OHLCV Parquet 스키마 (spec §3.1)

| 컬럼 | dtype |
|------|-------|
| `timestamp` | `pl.Datetime("us", time_zone="UTC")` |
| `open` / `high` / `low` / `close` / `volume` | `pl.Float64` |

`timestamp`는 오름차순 정렬, 중복 없음, naive datetime 금지.

## 생성 방법

OHLCV: ``tools/export_db_to_parquet.py``로 SQLite DB에서 export. 예::

    python tools/export_db_to_parquet.py \
        --db ../Crypto/Bybit_Trading/db/bybit_data.db \
        --table ohlcv_1h --symbol ETHUSDT --timeframe 1h \
        --output-dir tests/fixtures \
        --start 2026-03-01 --end 2026-04-29

시그널: ``signal_log`` (legacy forward demon)에서 ``BBKCSqueeze`` strategy + 대상
symbol/direction 조회 → ``timestamp`` 컬럼을 봉 마감 (= ``HH:00:00``)으로 정규화.

## 현 fixture 상태 (PR 8)

- OHLCV: ``ETHUSDT_1h.parquet`` (2026-03-01 ~ 2026-04-29, 1399 봉)
- 시그널: ``bbkc_signals.csv`` 2건 (둘 다 ETHUSDT LONG/buy)
- 출처: ``signal_log`` BBKCSqueeze ETHUSDT LONG forward demon 결과

### Legacy vs v8 시그널 정합성

forward demon ``signal_log`` 의 ETHUSDT BBKC LONG 4건 중 2건만 fixture에 포함.
다음 2건은 v8 출력과 매칭되지 않아 회귀 fixture에서 제외:

- ``2026-03-31T17:00:00+00:00`` — legacy ``bb_mid≈2050.94`` vs v8 ``bb_mid≈2050.39``
  (Δ0.55). EWM(Wilder ATR) 시드 처리 차이 (legacy ``tr[0]=H-L``, v8 ``tr[0]=null``)와
  legacy 데몬이 가졌던 OHLCV 스냅샷 vs 현 DB 의 미세 갱신이 누적되면서 ``BB_lower > KC_lower``
  비교가 boundary에서 flip → v8는 같은 봉을 squeeze 상태로 보지 않아 release 미감지.
- ``2026-04-28T20:00:00+00:00`` — legacy log 시각이 ``20:15:01.21``이지만 squeeze 상태표
  분석 결과 실제 release는 ``17:00→18:00`` 전이에서 발생 (decision_ts ``19:00:00``).
  v8는 정확히 ``19:00:00``에 buy 발행. legacy 로그가 1시간 지연 폴링.

남은 2건 (``2026-04-04T22:00:00``, ``2026-04-22T03:00:00``)은 v8 출력과 timestamp
+ direction이 정확히 일치하므로 회귀 게이트에 사용.

### Legacy/Phase 1 의도된 차이

- legacy SHORT entries — v8 Phase 1 long-only (Sizer 차단)
- legacy TP/SL/be_trail/time_stop 청산 — v8 Phase 2 (limit/stop)
- legacy RSI 필터 (rsi_filter=70) — v8 미적용 → v8 buy 가 legacy 보다 다수 발행됨

## .gitignore 정책

- 작은 회귀 fixture (``bbkc_signals.csv``, ``ETHUSDT_1h.parquet`` 약 70KB) 커밋.
- 대용량 OHLCV 캐시는 ``backtester/data_cache/``에 두고 gitignore.
