# OverseasFutures - 해외선물 통합 트레이딩 시스템 설계 문서

> 작성일: 2026-03-18
> 상태: 설계 완료, 구현 대기 (Step 1부터 시작)
> 이 문서만으로 새 세션에서 전체 맥락 파악 + 구현 가능하도록 작성됨

---

## 1. 프로젝트 개요

### 1.1 목표
KIS(한국투자증권) API를 활용한 **해외선물 멀티 거래소 Paper Trading 시스템** 구축.
기존 `futures_price_mornitor`(데이터/전략)와 `KIS_Trading`(UI/상태관리) 코드를 통합하여
`Trading/OverseasFutures/`에 새 프로젝트로 구축한다.

### 1.2 핵심 제약 조건

| 제약 | 내용 | 대응 |
|------|------|------|
| **모의투자 미제공** | KIS 해외선물 모의투자 계좌 없음 | 자체 Paper Trading 엔진 구현 |
| **분봉 REST 조회 불가** | 해외선물 분봉 API가 장중에만 동작하거나 미제공 | 현재가 폴링 → 틱 DB → 봉 리샘플링 |
| **일봉 제한** | 월물당 15~40일만 (계약 상장 이후) | 이전 월물 수집 + yfinance 보충 |
| **상시 가동 불가** | 사용자 PC 환경, 항상 켜놓을 수 없음 | 데이터 누적 전략 + 완전한 상태 복원 |
| **토큰 rate limit** | 1분 1회 발급 제한 | 23시간 캐시, 재시작 시 복원 |

### 1.3 계좌 정보

| 항목 | 값 |
|------|-----|
| 해외선물 계좌 | `44255007-08` (CANO=`44255007`, ACNT_PRDT_CD=`08`) |
| 실전 서버 | `https://openapi.koreainvestment.com:9443` (시세+주문) |
| 모의 서버 | `https://openapivts.koreainvestment.com:29443` (해외선물 미지원) |
| API 키 위치 | `etc/env_backup/futures_price_mornitor.env` (KIS_APP_KEY_REAL, KIS_APP_SECRET_REAL) |
| Rate Limit | 실전 20/sec, 모의 5/sec |

---

## 2. 확인된 상품 (14개)

### 2.1 API 테스트로 검증된 KIS 종목코드

**중요: products.json의 기존 KIS 코드(FESXH26, FGBLH26 등)는 틀림. 아래가 실제 확인된 코드.**

| KIS코드 | 거래소 | 상품 | Tick Size | Tick Value | Contract Size | 증거금 | 통화 | 일봉일수 | 만기 |
|---------|--------|------|-----------|------------|---------------|--------|------|---------|------|
| **VGM26** | EUREX | Euro Stoxx 50 | 1 | EUR 10 | 10 | EUR 4,610 | EUR | 40 | 20260619 |
| **BONM26** | EUREX | Euro-Bund 10Y | 0.01 | EUR 10 | 100,000 | EUR 3,283 | EUR | 15 | 20260609 |
| **OATM26** | EUREX | Euro-OAT (프랑스국채) | 0.01 | EUR 10 | 100,000 | EUR 2,154 | EUR | 28 | 20260609 |
| **GXM26** | EUREX | DAX | 1 | EUR 25 | 25 | EUR 49,079 | EUR | 40 | 20260619 |
| **JGBM26** | OSE | JGB 10Y (일본국채) | 0.01 | JPY 10,000 | 1,000,000 | JPY 1,448,084 | JPY | 25 | 20260616 |
| **TPXM26** | OSE | TOPIX | 0.5 | JPY 5,000 | 10,000 | JPY 1,399,362 | JPY | 38 | 20260612 |
| **HSIM26** | HKEx | Hang Seng Index | 1 | HKD 50 | 50 | HKD 117,705 | HKD | 40 | 20260629 |
| **MHIM26** | HKEx | Mini Hang Seng | 1 | HKD 10 | 10 | HKD 23,541 | HKD | 40 | 20260629 |
| **HHIM26** | HKEx | H-Shares Index | 1 | HKD 50 | 50 | HKD 45,885 | HKD | 40 | 20260629 |
| **YTM26** | ASX | 3Y Australian Bond | 0.001 | AUD 2.93 | 100,000 | AUD 1,075 | AUD | 18 | 20260615 |
| **XTM26** | ASX | 10Y Australian Bond | 0.001 | AUD 8.952 | 100,000 | AUD 2,625 | AUD | 21 | 20260615 |
| **SPIM26** | ASX | SPI 200 | 1 | AUD 25 | 25 | AUD 15,154 | AUD | 30 | 20260618 |
| **TXM26** | FTX | TAIEX | 1 | TWD 200 | 200 | TWD 339,000 | TWD | 40 | 20260617 |
| **MTXM26** | FTX | Mini TAIEX | 1 | TWD 50 | 50 | TWD 84,750 | TWD | 40 | 20260617 |

### 2.2 종목코드 명명 규칙
```
[상품루트코드][월코드][연도2자리]
월코드: F=1, G=2, H=3, J=4, K=5, M=6, N=7, Q=8, U=9, V=10, X=11, Z=12
```
예: `VGM26` = Euro Stoxx 50, 6월물, 2026년

### 2.3 못 찾은 종목 (KIS에서 코드 매핑 안됨)
- EUREX: Bobl(5Y), Schatz(2Y), Mini-DAX
- OSE: Nikkei 225, Mini Nikkei

### 2.4 추가 발견된 종목코드
- `BONU26` = Euro-Bund 9월물 (EUREX) — 다음 분기 월물
- `VGU26` = Euro Stoxx 50 9월물 (EUREX)
- `GXU26` 가능 추정
- `DXM26` = US Dollar Index (ICE) — 거래소 미신청이라 사용 불가할 수 있음

---

## 3. KIS API 스펙 (해외선물)

### 3.1 시세 API (구현됨 / 수정 필요)

| API | TR_ID | 엔드포인트 | 상태 | 비고 |
|-----|-------|-----------|------|------|
| 현재가 | `HHDFC55010000` | `/uapi/overseas-futureoption/v1/quotations/inquire-price` | **구현됨** | bid/ask 1단계 포함 |
| 종목상세 | `HHDFC55010100` | `/uapi/overseas-futureoption/v1/quotations/stock-detail` | 미구현 | tick_size, margin, expiry 등 |
| 일봉 OHLCV | `HHDFC55020100` | `/uapi/overseas-futureoption/v1/quotations/daily-ccnl` | **구현됨 (버그)** | 파라미터 수정 필요 |
| 분봉 차트 | `HHDFC55020400` | `/uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice` | **구현됨** | 장중에만 데이터 반환 |
| 틱 체결 | `HHDFC55020200` | 미확인 | 미구현 | VWAP 정밀 계산용 |
| 계약 검색 | `HHDFC55200000` | `/uapi/overseas-futureoption/v1/quotations/search-contract-detail` | 미구현 | 종목코드 일괄 확인 (최대 32개) |
| 거래소 운영시간 | `OTFM2229R` | 미확인 | 미구현 | 장시간 자동 감지 |

### 3.2 주문 API (전체 신규 구현)

| API | TR_ID (실전) | TR_ID (모의 추정) | 엔드포인트 |
|-----|-------------|------------------|-----------|
| 신규 주문 | `OTFM3001U` | `VTFM3001U` | `POST .../trading/order` |
| 정정 | `OTFM3002U` | `VTFM3002U` | `POST .../trading/order-rvsecncl` |
| 취소 | `OTFM3003U` | `VTFM3003U` | `POST .../trading/order-rvsecncl` |
| 체결조회 | `OTFM3116R` | `VTFM3116R` | `GET .../trading/inquire-ccld` |
| 예수금 | `OTFM1411R` | `VTFM1411R` | `GET .../trading/inquire-deposit` |

### 3.3 WebSocket 실시간 (Phase 3)

| API | TR_ID | 내용 |
|-----|-------|------|
| 실시간 호가 5단계 | `HDFFF010` | bid/ask 5레벨 가격+잔량 |
| 실시간 체결가 | `HDFFF020` | tick-by-tick 체결 |

5개 거래소(EUREX/OSE/HKEx/ASX/FTX) 모두 **무료 시세** — 추가 비용 없음.

### 3.4 일봉 API 파라미터 수정 사항 (핵심 버그)

**기존 rest_client.py의 `get_futures_daily_ohlcv()`는 파라미터가 틀림.**

기존 (동작 안함):
```python
params = {
    "SRS_CD": symbol,
    "EXCH_CD": "",           # ← 빈값이면 일부만 동작
    "START_DATE_TIME": start_date,
    "CLOSE_DATE_TIME": end_date,
    "QRY_TP": "0",
    "QRY_CNT": "100",
    "CTX_AREA_FK200": ctx_fk, # ← 구버전 파라미터
    "CTX_AREA_NK200": ctx_nk, # ← 구버전 파라미터
}
```

수정 (동작 확인됨):
```python
params = {
    "SRS_CD": symbol,
    "EXCH_CD": exchange,      # ← 필수! "EUREX", "OSE", "HKEx", "ASX", "FTX"
    "START_DATE_TIME": start_date,
    "CLOSE_DATE_TIME": end_date,
    "QRY_TP": "Q",            # ← "Q"=첫페이지, "P"=다음페이지
    "QRY_CNT": "40",
    "QRY_GAP": "",            # ← 새 필수 파라미터 (빈값)
    "INDEX_KEY": index_key,   # ← 페이지네이션 키 (CTX_AREA 대체)
}
```

**응답 필드 (output2 배열):**
```json
{
    "data_date": "20260318",
    "data_time": "",
    "open_price": "5916",
    "high_price": "5931",
    "low_price": "5905",
    "last_price": "5905",
    "last_qntt": "",
    "vol": "100",
    "prev_diff_flag": "5",
    "prev_diff_price": "..."
}
```

---

## 4. 기존 코드 분석 (재사용 맵)

### 4.1 futures_price_mornitor (전략/데이터 인프라)

경로: `Trading/futures_price_mornitor/`

#### 재사용 가능 (수정 후)

| 파일 | 역할 | 수정 사항 |
|------|------|----------|
| `api/auth.py` | TokenManager (OAuth2, 23h 캐시, thread-safe) | 그대로 사용. `.env` 경로만 조정 |
| `api/rest_client.py` | KISRestClient (rate limit, 시세 조회) | **일봉 파라미터 수정 (3.4절 참조)**, 현재가에 EXCH_CD 추가 |
| `strategy/dual_bollinger/config.py` | DualBBConfig (14개 파라미터) | 그대로 재사용 |
| `strategy/dual_bollinger/events.py` | State(7개), EventType(9개), Position, TradeRecord | 그대로 재사용 |
| `strategy/dual_bollinger/state_machine.py` | DualBBStateMachine (7-state FSM) | 그대로 재사용 |
| `strategy/dual_bollinger/bands.py` | 볼린저밴드 계산 (MA, std, ATR, RSI) | 그대로 재사용 |
| `strategy/dual_bollinger/engine.py` | 백테스트 엔진 | 그대로 재사용 |
| `strategy/dual_bollinger/optimizer.py` | 파라미터 최적화 | 그대로 재사용 |
| `optimizer/` | 포트폴리오 옵티마이저 전체 | 그대로 재사용 |
| `db/schema.sql` | DB 스키마 (5 테이블) | realtime_ticks 호가 5단계로 확장 |
| `collector/daily_ohlcv.py` | DailyCollector | 파라미터 수정, exchange 인자 추가 |

#### 참고만 (구조 재활용)
| 파일 | 참고 포인트 |
|------|-----------|
| `config/settings.py` | KISConfig 구조 (env별 키 로딩) |
| `config/products.json` | 상품 마스터 구조 (**KIS코드는 전부 틀림**, 2.1절 코드 사용) |
| `collector/intraday_ohlcv.py` | 분봉 수집 구조 |
| `collector/scheduler.py` | 스케줄러 골격 |

### 4.2 KIS_Trading (UI/실행 인프라)

경로: `Trading/KIS_Trading/`

#### 재사용 가능 (수정 후)

| 파일 | 역할 | 수정 사항 |
|------|------|----------|
| `state_store.py` | JSON 상태 저장/복원 (FSM, Position, PnL, 봉) | 선물 포지션 구조로 적응 (증거금, PnL 계산) |
| `bar_builder.py` | 틱→60m OHLCV 봉 변환 (시간대 처리) | **US Market 고정 → 멀티 거래소 장시간으로 일반화** |
| `dashboard.py` | tkinter 다크테마 대시보드 (17KB) | 멀티 상품 UI로 확장 |
| `trade_manager.py` | 심볼별 PnL 추적, 전략 연결 | 선물 PnL (point_value) 적용 |
| `config.py` | SymbolSpec, Colors, Fonts, BAR_BOUNDARIES | 선물 상품 스펙으로 교체, 거래소별 장시간 |

#### 참고만 (해외주식 전용)
| 파일 | 비고 |
|------|------|
| `kis_client.py` | 해외 **주식** 클라이언트 (API 경로가 다름: overseas-stock vs overseas-futureoption) |
| `order_executor.py` | 주식 주문 실행 (선물 TR_ID와 다름) |
| `price_reader.py` | 폴링 구조 참고 |

### 4.3 전략 엔진 상세 (DualBBStateMachine)

```
7-State FSM: FLAT → LONG_1ST → LONG_2ND → LONG_PARTIAL → FLAT
                  → SHORT_1ST → SHORT_2ND → SHORT_PARTIAL → FLAT

진입: Inner Band Crossover (close가 inner_upper/lower 돌파)
증거: Scale-in (pullback to inner band)
청산:
  - ATR 기반 동적 스탑로스
  - Trailing Stop (ATR 1배 수익 후 활성, ATR 1.5배 거리 추적)
  - Outer Band + RSI 조기 익절
  - Band Exit (가격이 band 안으로 복귀)
  - Partial Exit (추세 약화 시 일부 청산)

필요 입력: OHLCV + 밴드 값 (inner/outer upper/lower, ATR, RSI, bandwidth)
→ 일봉/시간봉 모두 가능
```

**DualBBConfig 주요 파라미터:**
```python
candle_minutes=60, ma_period=20, sigma_inner=1.5, sigma_outer=3.0,
breakout_pct=0.0, rsi_period=14, rsi_overbought=70, rsi_oversold=30,
atr_stop_multiplier=2.0, use_trailing_stop=True,
trailing_activation_atr=1.0, trailing_distance_atr=1.5,
vol_filter_enabled=True, max_bandwidth_pct=8.0,
base_qty=1, scale_qty=1
```

---

## 5. 프로젝트 구조

### 5.1 신규 디렉토리 구조

```
Trading/
├── OverseasFutures/              ← 신규 통합 프로젝트
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py           ← KISConfig (env별 키, 서버 URL)
│   │   └── products.py           ← 14개 상품 마스터 + 월물 롤오버 로직
│   ├── api/
│   │   ├── __init__.py
│   │   ├── auth.py               ← TokenManager (재사용)
│   │   ├── rest_client.py        ← KISRestClient (수정: 파라미터 교정)
│   │   └── ws_client.py          ← WebSocket 클라이언트 (Phase 3)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.sql            ← 확장 스키마 (호가 5단계)
│   │   └── init_db.py            ← DB 초기화
│   ├── collector/
│   │   ├── __init__.py
│   │   ├── daily_ohlcv.py        ← 일봉 수집 (수정: exchange 필수)
│   │   ├── realtime_poller.py    ← 현재가 REST 폴링 → 틱 DB
│   │   └── bar_resampler.py      ← 틱 → 분봉 (1m/5m/15m/60m) 리샘플링
│   ├── strategy/
│   │   └── dual_bollinger/       ← 전체 재사용 (심링크 또는 복사)
│   │       ├── config.py
│   │       ├── events.py
│   │       ├── state_machine.py
│   │       ├── bands.py
│   │       ├── engine.py
│   │       ├── optimizer.py
│   │       └── report.py
│   ├── optimizer/                ← 포트폴리오 옵티마이저 (재사용)
│   ├── paper_engine/             ← 자체 모의매매 엔진 (신규)
│   │   ├── __init__.py
│   │   ├── virtual_account.py    ← 가상 계좌 (잔고, 증거금, 예수금)
│   │   ├── order_manager.py      ← 주문 접수/체결 시뮬레이션
│   │   ├── fill_simulator.py     ← 체결 로직 (시장가=현재가, 지정가=조건충족시)
│   │   ├── position_tracker.py   ← 포지션/PnL 실시간 추적
│   │   └── state_persistence.py  ← JSON 저장/복원 (재시작 대비)
│   ├── scheduler/                ← 멀티 거래소 스케줄러 (신규)
│   │   ├── __init__.py
│   │   ├── exchange_hours.py     ← 거래소별 장시간 (KST)
│   │   └── poll_scheduler.py     ← 열린 거래소만 폴링
│   ├── dashboard/                ← tkinter 대시보드 (재활용+확장)
│   │   ├── __init__.py
│   │   ├── app.py                ← 메인 GUI 앱
│   │   ├── widgets.py            ← 공통 위젯
│   │   └── themes.py             ← Colors, Fonts (재사용)
│   ├── scripts/
│   │   ├── collect_daily.py      ← 일봉 일괄 수집
│   │   ├── test_api.py           ← API 테스트 유틸
│   │   └── migrate_data.py       ← 기존 데이터 이관
│   ├── logs/
│   ├── main.py                   ← CLI 진입점
│   ├── requirements.txt
│   └── .env                      ← API 키 (gitignore)
│
├── _Archive/                     ← 기존 코드 백업
│   ├── futures_price_mornitor/
│   └── KIS_Trading/
```

### 5.2 .env 파일 형식

```env
# KIS API - 실전 서버 (시세 + 해외선물 계좌)
KIS_APP_KEY_REAL=PSU5Oud...
KIS_APP_SECRET_REAL=eDD5Jua...
KIS_ACCOUNT_REAL=44255007-08

# KIS API - 모의 서버 (해외선물 미지원이지만 형식 유지)
KIS_APP_KEY_MOCK=
KIS_APP_SECRET_MOCK=
KIS_ACCOUNT_MOCK=
```

---

## 6. 데이터 전략

### 6.1 Phase 1: 초기 데이터 확보

```
KIS 일봉 API → 14개 상품 × 근월물 15~40일 수집
     ├── EXCH_CD 필수 지정 ("EUREX", "OSE", "HKEx", "ASX", "FTX")
     ├── 이전 월물도 수집 (BONH26, VGH26 등) → 연속 데이터 구성
     └── 페이지네이션: QRY_TP="Q"→"P", INDEX_KEY 사용

yfinance / 외부소스 → 백테스트용 장기 데이터 보충
     ├── Euro Stoxx 50: ^STOXX50E
     ├── DAX: ^GDAXI
     ├── HSI: ^HSI
     ├── Nikkei: ^N225
     ├── TOPIX: 1306.T
     └── 채권선물은 외부 데이터 제한적 → KIS 일봉 누적에 의존
```

### 6.2 Phase 2: 실시간 데이터 누적 (PC 가동 시)

```
현재가 REST 폴링 (30초 간격)
     ├── HHDFC55010000: last_price, bid_price, ask_price, vol
     ├── 장이 열린 거래소만 폴링 (스케줄러 연동)
     └── 틱 DB 저장 (realtime_ticks 테이블)

틱 → 분봉 리샘플링
     ├── 30초 폴링 데이터 → 1m/5m/15m/60m OHLCV 생성
     ├── ohlcv_intraday 테이블에 저장
     └── 시간 지나면 충분한 히스토리 확보

일봉 보충
     ├── 매일 장마감 후 KIS 일봉 API로 정확한 OHLCV 확보
     └── ohlcv_daily 테이블 UPSERT
```

### 6.3 Phase 3: WebSocket 실시간 (선택, 추후)

```
HDFFF020 실시간 체결가 → 정밀 틱 데이터
HDFFF010 실시간 호가 5단계 → VWAP 계산, 시장 깊이 분석
     ├── websockets 라이브러리 사용 (requirements.txt에 이미 있음)
     └── asyncio 기반 구현
```

### 6.4 일봉 수집 실행 결과 (2026-03-19)

#### 수집 내역

4개 분기 월물을 역순으로 수집하여 연속 데이터 구성:

| 월물 | 코드 예시 | 기간 | 수집건수 | 비고 |
|------|-----------|------|---------|------|
| **U25** (9월물) | VGU25, BONU25... | 2025.07~09 | 552건 | 14/14 성공 |
| **Z25** (12월물) | VGZ25, BONZ25... | 2025.10~12 | 560건 | 14/14 성공 |
| **H26** (3월물) | VGH26, BONH26... | 2025.12~03 | 560건 | 14/14 성공 |
| **M26** (6월물, 근월) | VGM26, BONM26... | 2026.01~03 | 462건 | 14/14 성공 |
| **합계** | | **~8개월** | **1,728건** | |

#### 상품별 커버리지

| 상품 | 거래소 | 건수 | 시작일 | 종료일 | 기간 |
|------|--------|------|--------|--------|------|
| TPX | OSE | 133 | 20250718 | 20260320 | 8.0M |
| OAT | EUREX | 132 | 20250714 | 20260319 | 8.1M |
| BON | EUREX | 130 | 20250714 | 20260319 | 8.1M |
| GX | EUREX | 126 | 20250728 | 20260319 | 7.7M |
| JGB | OSE | 126 | 20250718 | 20260320 | 8.0M |
| MTX | FTX | 121 | 20250724 | 20260320 | 7.9M |
| SPI | ASX | 121 | 20250227 | 20260320 | 12.7M |
| TX | FTX | 121 | 20250724 | 20260320 | 7.9M |
| HHI | HKEx | 120 | 20250805 | 20260320 | 7.5M |
| HSI | HKEx | 120 | 20250805 | 20260320 | 7.5M |
| MHI | HKEx | 120 | 20250805 | 20260320 | 7.5M |
| VG | EUREX | 120 | 20250728 | 20260319 | 7.7M |
| XT | ASX | 119 | 20250728 | 20260320 | 7.7M |
| YT | ASX | 119 | 20250728 | 20260320 | 7.7M |

#### 날짜 갭 분석

모든 상품에서 2~4개의 갭이 발견됨. 갭 유형은 두 가지:

**1. 월물전환 갭 (~35일)**
- 원인: KIS API가 월물당 최대 40건만 반환. 이전 월물 만기 → 다음 월물 데이터 시작까지 공백.
- 예: VG `20250919 → 20251027` (38일), JGB `20250911 → 20251020` (39일)
- 성격: 실제로는 다른 월물이 거래 중이었으나, API 응답 범위 밖이라 수집 불가.

**2. 연말휴장+월물전환 갭 (~25~37일)**
- 원인: 12월 월물 만기 + 크리스마스/신년 휴장이 겹침.
- 예: VG `20251219 → 20260123` (35일), BON `20251205 → 20260109` (35일)
- 성격: 실제 휴장일 포함이므로 일부는 자연 갭, 일부는 API 한계.

**갭 상세 (대표 상품)**
```
VG:  20250919→20251027 (38일, 월물전환)  /  20251219→20260123 (35일, 연말)
BON: 20250905→20251013 (38일, 월물전환)  /  20251205→20260109 (35일, 연말)
HSI: 20250929→20251103 (35일, 월물전환)  /  20251230→20260121 (22일, 연말)
JGB: 20250911→20251020 (39일, 월물전환)  /  20251212→20260116 (35일, 연말)
```

#### 갭 보완 방법

갭이 전략 운용에 영향을 줄 경우 아래 방법으로 보충 가능:

**방법 1: yfinance 기초자산 지수 데이터** (지수 선물만 가능)
```python
# pip install yfinance
import yfinance as yf

YFINANCE_MAP = {
    "VG": "^STOXX50E",   # Euro Stoxx 50
    "GX": "^GDAXI",      # DAX
    "HSI": "^HSI",        # Hang Seng
    "HHI": "^HSCE",       # H-Shares
    "TPX": "1306.T",      # TOPIX ETF
    "SPI": "^AXJO",       # ASX 200
    "TX": "^TWII",        # TAIEX
}
# 채권선물(BON, OAT, JGB, YT, XT)은 yfinance 대응 없음
# → KIS 일봉 누적에 의존
```

**방법 2: 실시간 폴링 누적** (현재 시스템으로 자동 해결)
- 30초 폴링 → 틱 → 60분봉 리샘플링 → 일봉 보충
- PC 가동 시간이 늘수록 갭 자연 해소

**방법 3: 더 오래된 월물 추가 수집**
- M25(6월물), H25(3월물) 등 추가 수집 시도
- 단, API 제공 기간 만료로 데이터 없을 가능성 높음

#### 결론

- **전략 워밍업(MA20 + ATR14)에 필요한 최소 데이터**: 34봉 → 전 상품 충족 (80~133봉)
- 현재 데이터로 DualBB 전략 즉시 운용 가능
- 갭 구간에서의 지표 불연속은 FSM이 `prev_close=None` 처리로 안전하게 스킵
- 장기적으로 실시간 폴링 누적이 가장 안정적인 해결책

---

## 7. Paper Trading 엔진 설계

KIS 해외선물 모의투자가 없으므로 내부 시뮬레이션 엔진 구축.

### 7.1 virtual_account.py

```python
class VirtualAccount:
    """가상 계좌 관리."""
    initial_balance: Dict[str, float]   # 통화별 초기 잔고 {"USD": 100000, "EUR": 50000, ...}
    cash: Dict[str, float]              # 통화별 가용 현금
    margin_used: Dict[str, float]       # 통화별 사용 증거금
    positions: Dict[str, Position]      # 심볼별 포지션

    def check_margin(symbol, qty) -> bool       # 증거금 여유 확인
    def reserve_margin(symbol, qty) -> None     # 증거금 예약
    def release_margin(symbol, qty) -> None     # 증거금 해제
    def update_equity() -> Dict[str, float]     # 평가 금액 계산
```

### 7.2 order_manager.py

```python
class OrderManager:
    """주문 접수/관리."""
    pending_orders: List[Order]   # 미체결 주문

    def submit_market_order(symbol, side, qty) -> Order     # 시장가 → 즉시 체결
    def submit_limit_order(symbol, side, qty, price) -> Order  # 지정가 → 조건 충족 시
    def cancel_order(order_id) -> bool
    def check_fills(current_prices) -> List[Fill]           # 체결 조건 확인
```

### 7.3 fill_simulator.py

```python
class FillSimulator:
    """체결 시뮬레이션."""
    def fill_market(order, current_price) -> Fill      # 시장가 = 현재가로 체결
    def check_limit(order, current_price) -> Optional[Fill]  # 지정가 = 가격 도달 시
    def apply_slippage(price, side) -> float           # 슬리피지 (선택)
```

### 7.4 position_tracker.py

```python
class PositionTracker:
    """포지션/PnL 추적."""
    def update_unrealized_pnl(positions, current_prices) -> None
    def realize_pnl(symbol, exit_price, qty) -> float
    # PnL 계산: (exit - entry) * qty * point_value * direction
    # point_value = tick_value / tick_size (상품별 상이)
```

### 7.5 state_persistence.py

```python
class StatePersistence:
    """앱 재시작 시 상태 완전 복원."""
    # JSON으로 저장:
    # - 모든 포지션 (심볼, 방향, 수량, 평균가, 스탑 레벨)
    # - 가상 계좌 (잔고, 증거금)
    # - FSM 상태 (각 심볼별 State)
    # - 진행 중 봉 (bar_builder 상태)
    # - 미체결 주문
    # - PnL 히스토리
```

---

## 8. 멀티 거래소 스케줄러

### 8.1 거래소별 장시간 (KST)

```python
EXCHANGE_HOURS = {
    "EUREX": [
        {"open": "16:00", "close": "06:00+1"},   # 다음날 새벽까지
    ],
    "OSE": [
        {"open": "08:45", "close": "15:30"},      # 전장
        {"open": "16:30", "close": "06:00+1"},    # 야간
    ],
    "HKEx": [
        {"open": "10:15", "close": "12:00"},      # 전장
        {"open": "13:00", "close": "16:15"},      # 후장
        {"open": "17:00", "close": "01:00+1"},    # 야간
    ],
    "ASX": [
        {"open": "07:10", "close": "18:30"},      # 전장
        {"open": "18:40", "close": "06:00+1"},    # 야간
    ],
    "FTX": [
        {"open": "08:45", "close": "13:45"},      # 전장
        {"open": "15:00", "close": "05:00+1"},    # 야간
    ],
}
```

### 8.2 스케줄러 동작

```
poll_scheduler.py:
  1. 현재 시각(KST) 확인
  2. 각 거래소별 장이 열려있는지 판단
  3. 열린 거래소의 상품만 폴링 리스트에 추가
  4. 30초 간격 순회 폴링 (rate limit 준수)
  5. 결과 → bar_resampler → DB 저장
  6. 전략 엔진에 봉 완성 이벤트 전달
```

---

## 9. DB 스키마 확장

기존 `futures_price_mornitor/db/schema.sql`을 기반으로 확장.

### 9.1 변경 사항

```sql
-- products_master: 기존 유지 + 컬럼 추가
ALTER TABLE products_master ADD COLUMN exch_cd TEXT;         -- KIS EXCH_CD ("EUREX", "OSE" 등)
ALTER TABLE products_master ADD COLUMN kis_code_current TEXT; -- 현재 근월물 KIS코드
ALTER TABLE products_master ADD COLUMN expiry_date TEXT;      -- 만기일 YYYYMMDD
ALTER TABLE products_master ADD COLUMN margin REAL;           -- 증거금
ALTER TABLE products_master ADD COLUMN point_value REAL;      -- tick_value / tick_size

-- realtime_ticks: 호가 5단계로 확장
DROP TABLE IF EXISTS realtime_ticks;
CREATE TABLE IF NOT EXISTS realtime_ticks (
    symbol      TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    price       REAL,
    volume      INTEGER,
    bid1        REAL, bid1_qty INTEGER,
    bid2        REAL, bid2_qty INTEGER,
    bid3        REAL, bid3_qty INTEGER,
    bid4        REAL, bid4_qty INTEGER,
    bid5        REAL, bid5_qty INTEGER,
    ask1        REAL, ask1_qty INTEGER,
    ask2        REAL, ask2_qty INTEGER,
    ask3        REAL, ask3_qty INTEGER,
    ask4        REAL, ask4_qty INTEGER,
    ask5        REAL, ask5_qty INTEGER,
    PRIMARY KEY (symbol, timestamp)
);

-- paper_trades: Paper Trading 기록
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    datetime    TEXT NOT NULL,
    side        TEXT NOT NULL,         -- "BUY" / "SELL"
    qty         INTEGER NOT NULL,
    price       REAL NOT NULL,
    order_type  TEXT,                  -- "MARKET" / "LIMIT"
    strategy    TEXT,
    event_type  TEXT,
    pnl         REAL,
    pnl_currency TEXT,
    commission  REAL DEFAULT 0,
    note        TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- paper_positions: Paper 포지션 스냅샷 (상태 복원용)
CREATE TABLE IF NOT EXISTS paper_positions (
    symbol      TEXT PRIMARY KEY,
    side        TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    avg_price   REAL NOT NULL,
    margin_used REAL,
    unrealized_pnl REAL,
    fsm_state   TEXT,
    updated_at  TEXT DEFAULT (datetime('now'))
);
```

---

## 10. 구현 TODO 체크리스트

> 각 항목 완료 시 `[ ]` → `[x]`로 변경

### Step 1: 프로젝트 구조 생성 + 기존 코드 이관
- [x] `Trading/OverseasFutures/` 디렉토리 구조 생성 (config/, api/, db/, collector/, strategy/, paper_engine/, scheduler/, dashboard/, scripts/, logs/)
- [x] `futures_price_mornitor`에서 재사용 모듈 복사 (api/auth.py, api/rest_client.py)
- [x] `futures_price_mornitor`에서 전략 엔진 복사 (strategy/dual_bollinger/ 전체)
- [x] `futures_price_mornitor`에서 옵티마이저 복사 (optimizer/ 전체)
- [x] `futures_price_mornitor`에서 DB 관련 복사 (db/schema.sql, db/init_db.py)
- [x] `futures_price_mornitor`에서 수집기 골격 복사 (collector/daily_ohlcv.py)
- [x] `.env` 파일 생성 (해외선물 계좌 44255007-08, 실전 API 키)
- [x] `main.py` 진입점 생성
- [x] `requirements.txt` 생성
- [x] 기존 `futures_price_mornitor/`, `KIS_Trading/` → `Trading/_Archive/`로 이동 (2026-03-20 완료)
- **의존성:** 없음

### Step 2: config/products 확장
- [x] `config/products.py` — 14개 상품 마스터 등록 (2.1절 데이터: KIS코드, 거래소, tick, margin 등)
- [x] 월물 코드 생성 함수 (`get_front_month_code(root, date)`)
- [x] 월물 롤오버 로직 (만기일 기준 자동 전환)
- [x] `config/settings.py` — 해외선물 전용 KISConfig (실전 서버 고정)
- [x] `config/__init__.py` — 경로/상수 정의
- **의존성:** Step 1

### Step 3: DB 초기화 + 일봉 데이터 수집
- [x] `db/schema.sql` 확장 (9절: products_master 컬럼 추가, realtime_ticks 호가 5단계, paper_trades, paper_positions)
- [x] `db/init_db.py` 수정 (products_master에 14개 상품 자동 INSERT)
- [x] `api/rest_client.py` 수정 — 일봉 파라미터 버그 수정 (EXCH_CD 필수, QRY_GAP/INDEX_KEY 추가, 3.4절)
- [x] `api/rest_client.py` 수정 — 현재가에 exchange 인자 추가
- [x] `api/rest_client.py` 추가 — 종목상세 API (HHDFC55010100) 메서드
- [x] `collector/daily_ohlcv.py` 수정 — exchange 파라미터 추가, 응답 필드명 매핑 (data_date, open_price 등)
- [x] 14개 상품 × 근월물 일봉 수집 실행 + DB 저장 (462건, 2026-03-19 완료)
- [x] 이전 월물(U25+Z25+H26) 일봉 수집 완료 → 1,728건/14상품/~8개월 (월물전환 갭은 API 40건 제한으로 구조적 한계)
- [ ] (선택) yfinance로 장기 일봉 보충 (^STOXX50E, ^HSI, ^GDAXI 등)
- **의존성:** Step 2

### Step 4: 현재가 폴링 + 틱→봉 변환 + DB 저장
- [x] `collector/realtime_poller.py` 신규 — 30초 간격 REST 폴링 (HHDFC55010000)
- [x] 폴링 결과 → realtime_ticks 테이블 저장
- [x] `collector/bar_resampler.py` 신규 — 틱 → 1m/5m/15m/60m OHLCV 리샘플링
- [x] 리샘플링 결과 → ohlcv_intraday 테이블 저장
- [x] BarBuilder 멀티 거래소 일반화 (KIS_Trading/bar_builder.py 참고, 거래소별 장시간 경계)
- [x] 봉 완성 이벤트 콜백 구조
- **의존성:** Step 3

### Step 5: Paper Trading 엔진
- [x] `paper_engine/virtual_account.py` — 통화별 가상 잔고, 증거금 예약/해제, 평가금액
- [x] `paper_engine/order_manager.py` — 시장가/지정가 주문 접수, 취소, 미체결 관리
- [x] `paper_engine/fill_simulator.py` — 체결 로직 (시장가=현재가, 지정가=조건충족시)
- [x] `paper_engine/position_tracker.py` — 포지션/PnL 추적 (point_value 적용: `(exit-entry)*qty*point_value*direction`)
- [x] `paper_engine/state_persistence.py` — JSON 저장/복원 (포지션, 계좌, FSM, 봉, 미체결주문, PnL 히스토리)
- [x] paper_trades 테이블에 거래 기록 저장
- **의존성:** Step 4

### Step 6: DBB 전략 연결 (FSM → Paper 주문)
- [x] 심볼별 독립 DualBBStateMachine 인스턴스 생성/관리
- [x] 봉 완성 이벤트 → bands 계산 → `fsm.on_bar()` 호출
- [x] StrategyEvent → Paper Engine 주문 연결 (ENTRY→BUY/SELL, STOP/EXIT→청산)
- [x] 심볼별 DualBBConfig 로딩 (기본값 or 최적화 결과)
- **의존성:** Step 4 + Step 5

### Step 7: 멀티 거래소 스케줄러
- [x] `scheduler/exchange_hours.py` — 5개 거래소 장시간 정의 (KST, 8.1절)
- [x] `scheduler/poll_scheduler.py` — 현재 시각 기준 열린 거래소 판단
- [x] 14개 상품을 거래소별 그룹화하여 효율적 순차 폴링
- [x] 장 시작/종료 이벤트 (일봉 수집 트리거, 봉 강제 완성 등)
- **의존성:** Step 4

### Step 8: Dashboard (tkinter)
- [x] 메인 앱 프레임 (다크 테마, KIS_Trading/dashboard.py 참고)
- [x] 거래소별 탭 또는 스크롤 그리드 (14개 상품 실시간 가격)
- [x] 포지션 패널 (심볼, 방향, 수량, 평균가, 미실현PnL)
- [x] PnL 요약 패널 (통화별 실현/미실현, 총손익)
- [x] FSM 상태 표시 (각 심볼별 현재 State)
- [x] 수동 주문 버튼 (Paper Engine 연동)
- [x] 거래소 장 상태 표시 (열림/닫힘)
- **의존성:** Step 5 + Step 6

### Step 9: 상태 저장/복원 + 재시작 안정성
- [x] 상태 저장 (KIS_Trading/state_store.py 패턴 확장): FSM, 포지션, 가상계좌, 진행중 봉, 미체결주문
- [x] 앱 시작 시 자동 복원 + 날짜 변경 감지 (당일 PnL 리셋)
- [x] 토큰 캐시 파일 저장/복원 (23시간 이내면 재사용) — auth.py에 _save_to_cache/_load_from_cache 구현 완료
- [x] graceful shutdown (종료 시그널 → 자동 저장)
- [x] 비정상 종료 복구 (손상된 state.json 감지 → 백업 사용)
- **의존성:** Step 5 + Step 6

### Phase 3: WebSocket 실시간 (2026-03-20 구현 완료)
- [x] `api/ws_client.py` — websockets + asyncio 기반 클라이언트 (자동 재연결, PINGPONG, 데몬 스레드)
- [x] HDFFF020 실시간 체결가 구독 (25개 필드 파싱, TradeData 콜백)
- [x] HDFFF010 실시간 호가 5단계 구독 (35개 필드 파싱, OrderbookData 콜백)
- [x] 체결가 → 정밀 봉 생성 / VWAP 계산 (bar_resampler.py Bar에 vwap/_cum_pv/_cum_vol 추가)
- [x] 호가 → realtime_ticks DB 저장 (5단계, save_ticks_to_db 옵션)
- [x] `main.py ws` CLI 명령 추가 (`python main.py ws [--symbol VG]`)

### Phase 3 추가 (2026-03-20): 데이터 수집 강화
- [x] `trade_ticks` 테이블 신규 — 체결 틱 원본 저장 (price, qty, cum_vol, direction/quotsign, OHLC)
- [x] 체결 틱 DB 자동 저장 (ws_client.py `_save_trade_to_db`)
- [x] BarResampler 1분봉으로 변경 (60m → 1m, 큰 봉은 1분봉에서 GROUP BY 집계)
- [x] `collector/vwap_calculator.py` 신규 — IntradayVWAPCalculator
  - 일중 VWAP: TP(H+L+C)/3 × volume 누적, 일간 리셋
  - TWAP: volume=0 fallback (expanding mean of close)
  - VWAP SD Band: volume-weighted 표준편차 (rolling 20봉)
  - 1SD/2SD 상하한 밴드
  - 상태 직렬화/복원 (to_dict/restore)
- [x] `main.py ws` 명령에서 1분봉 완성 시 VWAP/SD 자동 갱신 + 로깅

### (향후) 추가 과제
- [ ] VWAP 전략 엔진 (KTB_VWAP 패턴: Trend + MeanRev 듀얼모드)
- [ ] 포트폴리오 옵티마이저 연결 (해외선물 상품으로)
- [ ] 실전 주문 API 연동 (Paper → Live 전환)
- [ ] 못 찾은 종목 추가 탐색 (Bobl, Schatz, Nikkei 등)
- [ ] PyInstaller .exe 빌드

---

## 11. 의존성 다이어그램

```
Step 1 (구조 생성)
  └── Step 2 (products 설정)
       └── Step 3 (DB + 일봉)
            └── Step 4 (실시간 폴링)
                 ├── Step 5 (Paper Engine)
                 │    └── Step 6 (전략 연결)
                 │         ├── Step 8 (Dashboard)
                 │         └── Step 9 (상태 관리)
                 └── Step 7 (스케줄러)
```

---

## 12. 기술 스택

| 구분 | 선택 |
|------|------|
| Python | 3.11+ |
| HTTP | requests (동기) |
| WebSocket | websockets + asyncio (Phase 3) |
| DB | SQLite3 (sqlite3 내장) |
| GUI | tkinter (다크 테마) |
| 데이터 | pandas (분석/리샘플링) |
| 스케줄링 | threading.Timer / schedule |
| 환경 | python-dotenv |
| 빌드 | PyInstaller --onefile (최종) |

---

## 13. 참고: 기존 코드 주요 파일 절대 경로

```
# futures_price_mornitor (전략/데이터)
Trading/futures_price_mornitor/api/auth.py              ← TokenManager
Trading/futures_price_mornitor/api/rest_client.py       ← KISRestClient (버그 수정 필요)
Trading/futures_price_mornitor/config/settings.py       ← KISConfig
Trading/futures_price_mornitor/config/products.json     ← 상품 마스터 (KIS코드 틀림)
Trading/futures_price_mornitor/db/schema.sql            ← DB 스키마
Trading/futures_price_mornitor/db/init_db.py            ← DB 초기화
Trading/futures_price_mornitor/collector/daily_ohlcv.py ← DailyCollector
Trading/futures_price_mornitor/strategy/dual_bollinger/ ← 전략 엔진 전체
Trading/futures_price_mornitor/optimizer/               ← 포트폴리오 최적화

# KIS_Trading (UI/실행)
Trading/KIS_Trading/kis_client.py      ← 해외주식 REST 클라이언트 (참고)
Trading/KIS_Trading/config.py          ← SymbolSpec, Colors, Fonts, BAR_BOUNDARIES
Trading/KIS_Trading/bar_builder.py     ← BarBuilder (틱→봉)
Trading/KIS_Trading/state_store.py     ← save_state/load_state (JSON)
Trading/KIS_Trading/dashboard.py       ← tkinter 대시보드 (17KB)
Trading/KIS_Trading/trade_manager.py   ← TradeManager (전략 연결)
Trading/KIS_Trading/app.py             ← 메인 앱 (30초 폴링 루프)
```

---

## 14. 빠른 시작 가이드 (새 세션용)

```
1. 이 문서(DESIGN.md) 읽기
2. 현재 Step 확인 (어디까지 구현됐는지)
3. 기존 코드 참고 시 13절 경로 활용
4. API 호출 시 반드시:
   - EXCH_CD 지정 (3.4절)
   - 실전 서버 사용 (모의투자 미지원)
   - Rate limit 준수 (20/sec)
5. 종목코드는 2.1절 테이블 참조 (products.json은 틀림)
6. Paper Trading이므로 실제 주문 API 호출하지 않음
```
