# KIS_Trading 해외선물 전환 계획

## 개요
KIS_Trading을 NASDAQ 주식 → 해외선물 모의투자로 전환
- 모의투자 계좌: 60037542 (선물옵션, 5000만원)
- 대상: 해외선물 20개 (KTBF 제외)

---

## Phase 1: 데이터 수집 (futures_price_mornitor)

### 1-1. products.json 만기코드 업데이트
- 현재: ESM24, NQH26 등 혼재 → 모두 현재 활성 계약으로 갱신
- 2026년 3월 기준 활성 계약: H26(3월) 또는 M26(6월)
- KIS API로 실제 조회 가능한 코드 테스트 필요

### 1-2. 데이터 수집 스크립트 작성
- `scripts/collect_futures_data.py` 신규 생성
- 기존 `collector/daily_ohlcv.py`, `collector/intraday_ohlcv.py` 활용
- 수집 범위:
  - 일봉: 2023-01-03 ~ 2026-03-05 (기존 ~2025-02-13 이후 갱신)
  - 분봉: 60분봉 (모의매매 타임프레임용)
- .env의 KIS_REAL 키 사용 (시세는 실전서버만 가능)
- 20개 해외선물 순차 수집, 진행률 표시

### 1-3. 데이터 검증
- 수집 후 각 종목별 건수, 기간, 결측치 확인
- FGBL은 DATA_QUALITY_EXCLUSIONS로 이미 제외됨

---

## Phase 2: 최적화 실행 (futures_price_mornitor)

### 2-1. run_full_optimization.py 실행
- 해외선물 20개에 대해 일봉 기준 Walk-Forward 최적화
- WF 기간: 2023-01-03 ~ 2025-06-30 (확장)
- FWD 기간: 2025-07-01 ~ 2026-03-05
- 결과: `logs/optimization_report/summary.json` 갱신
- SYMBOL_CONFIG에 해외선물별 base_qty, initial_capital 추가

### 2-2. 포트폴리오 최적화
- `python main.py find-portfolio --budget 33800 --safety 1.5 --max-assets 6`
- 5000만원 / 환율1478 ≈ $33,800 예산
- 결과: 최적 종목 조합 + 수량 배분

---

## Phase 3: KIS_Trading 코드 전환

### 3-1. config.py 수정
- SYMBOLS: NASDAQ 10 주식 → 해외선물 (최적화 결과 기반)
- SymbolSpec: exchange를 "NAS" → 선물 거래소코드 (CME 등)
- point_value: 1.0 → 종목별 tick_value
- BAR_BOUNDARIES: 해외선물 거래시간에 맞게 조정
  - CME 선물: 거의 24시간 (18:00~17:00 ET)
  - 60분봉 유지
- DEFAULT_CONFIG: 최적화된 파라미터로 교체
- SUMMARY_JSON: futures summary.json 경로로 변경

### 3-2. kis_client.py 수정 (핵심)
**시세 API 변경:**
- 주식: `/uapi/overseas-price/v1/quotations/price` (TR: HHDFS00000300)
- 선물: `/uapi/overseas-futureoption/v1/quotations/inquire-price` (TR: HHDFC55010000)

**주문 API 변경:**
- endpoint: `/uapi/overseas-futureoption/v1/trading/order`
- TR_ID: 실전 OTFM3001U(추정), 모의투자는 동일 TR_ID + VTS서버 사용
- 파라미터: SRS_CD(종목코드), ORD_QTY, ORD_UNPR, SLL_BUY_DVSN_CD(매수1/매도2)
- 주의: 정확한 모의투자 TR_ID는 실행 시 테스트 필요

**잔고 API 변경:**
- endpoint: `/uapi/overseas-futureoption/v1/trading/inquire-unpd`
- TR_ID: OTFM1412R

**예수금 API 추가:**
- endpoint: `/uapi/overseas-futureoption/v1/trading/inquire-deposit`
- TR_ID: OTFM1411R

### 3-3. price_reader.py 수정
- get_price() → get_futures_current_price() 호출
- PriceQuote → FuturesQuote (필드명 변경 가능)

### 3-4. bar_builder.py 수정
- 해외선물은 거의 24시간 거래 → BAR_BOUNDARIES를 24시간 기준으로
- 또는 미국 정규장 시간(09:30~16:00 ET)만 사용 (전략 일관성)

### 3-5. order_executor.py 수정
- 주식 buy/sell → 선물 매수/매도 (SLL_BUY_DVSN_CD)
- exchange 파라미터 제거/변경

### 3-6. trade_manager.py 수정
- PnL 계산: point_value * tick 수 (주식과 다름)
- 종목별 tick_size, tick_value 적용

### 3-7. dashboard.py 수정
- PnL 표시 단위: USD (tick_value 기반)
- 종목명 표시 변경

---

## Phase 4: 테스트 및 검증

### 4-1. API 연결 테스트
- 선물 현재가 조회 테스트 (ES, NQ 등)
- 모의투자 주문 테스트 (1계약)
- 잔고/예수금 조회 테스트

### 4-2. Internal 모드 테스트
- --mode internal로 시그널 추적 정상 동작 확인

---

## 파일 변경 요약

### 신규 생성
- `futures_price_mornitor/scripts/collect_futures_data.py`

### 수정
- `futures_price_mornitor/config/products.json` (kis_code 갱신)
- `KIS_Trading/config.py` (선물 종목, 거래시간)
- `KIS_Trading/kis_client.py` (선물 API endpoint)
- `KIS_Trading/price_reader.py` (선물 시세)
- `KIS_Trading/bar_builder.py` (거래시간)
- `KIS_Trading/order_executor.py` (선물 주문)
- `KIS_Trading/trade_manager.py` (PnL 계산)
- `KIS_Trading/dashboard.py` (표시)
- `KIS_Trading/app.py` (선물 설정 로드)
