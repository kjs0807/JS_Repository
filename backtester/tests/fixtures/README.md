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

## 생성 방법 (집 환경)

`tools/export_db_to_parquet.py`(Phase 1 PR 8 skeleton)를 채워 SQLite DB의 OHLCV
+ 모의매매 시그널을 export. 자세한 사양은 해당 스크립트 docstring 참조.

## .gitignore 정책

- 작은 회귀 fixture(`bbkc_signals.csv`, 작은 OHLCV)는 커밋 가능.
- 대용량 OHLCV 캐시는 `backtester/data_cache/`에 두고 gitignore 처리.
