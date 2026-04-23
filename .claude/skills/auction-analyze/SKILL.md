# auction-analyze — 국고채 입찰 전 수급/대차 분석 스킬

## 개요

국고채 입찰 전 투자자 순매수 비율 + 대차잔고를 분석하여 유사 과거 입찰을 찾고,
사용자가 제공한 인포맥스 5분봉 데이터로 유사 시점의 금융시장 환경을 분석하는 2단계 대화형 파이프라인.

## 코드 위치

모든 분석 코드: `KTB/Auction/Documents/`

| 파일 | 용도 |
|------|------|
| `investor_flow_ratio.py` | 투자자 순매수 비율 분석 + 유사 시점 |
| `lending_ratio_analysis.py` | 대차잔고 분석 (단독+합산) + 유사 시점 |
| `auction_market_analysis.py` | 인트라데이 금융시장 분석 (방향성+커브) |
| `crawl_ktbinfo.py` (data/ 폴더) | KTBinfo 투자자 순매수 크롤링 |

참고 문서:
| 파일 | 내용 |
|------|------|
| `investor_flow_analysis.md` | 투자자 분석 방법론 + 컬럼 매핑 |
| `lending_balance_analysis.md` | 대차 분석 방법론 + 벤치마크 체인 |
| `similar_auction_analysis.md` | 유사 시점 선정 방법 + 금융시장 분석 방법 |

## 파이프라인

### Stage 0: 파라미터 확인

사용자 입력에서 아래 3개를 확인. 없으면 질문:
- `auction_date`: 입찰일 (YYYY-MM-DD)
- `tenor`: 2, 3, 5, 10, 20, 30
- `bond_name`: 입찰 종목 전체명 (예: "국고03500-5603(26-2)")

추가로 확인:
- 투자자 순매수 데이터가 입찰 전일까지 있는지 (`KTB_Investor_Flow.xlsx` 최신 날짜 확인)
- 부족하면 `crawl_ktbinfo.py`로 보충 크롤링 (자동 실행)
- 대차잔고 파일(`KTB_Lending_Balance.xlsx`)이 SAFER DRM 형식인지 확인 → DRM이면 Stage 0.5 실행

### Stage 0.5: 대차잔고 DRM 해제 (조건부)

인포맥스에서 다운로드한 `KTB_Lending_Balance.xlsx`는 DOCUMENT SAFER DRM으로 암호화되어 있어
pandas가 직접 읽을 수 없다. 파일 매직 바이트가 `PK`(정상 xlsx)가 아니면 DRM 파일로 판단.

**판별 방법:**
```python
with open(path, 'rb') as f:
    is_drm = not f.read(4).startswith(b'PK')
```

**DRM 해제 절차:**
1. 사용자에게 `KTB_Lending_Balance.xlsx`를 **Excel에서 열어달라고** 요청
2. 사용자가 열었다고 확인하면 Excel COM으로 데이터를 추출하여 `_clean.xlsx`로 저장:

```python
import win32com.client, openpyxl, os

excel = win32com.client.GetActiveObject('Excel.Application')
src_wb = None
for i in range(1, excel.Workbooks.Count + 1):
    if excel.Workbooks(i).Name == "KTB_Lending_Balance.xlsx":
        src_wb = excel.Workbooks(i)
        break

sheet = src_wb.Sheets(1)
data = sheet.UsedRange.Value
# 단일 셀/행/열 정규화 후 openpyxl로 저장
out_wb = openpyxl.Workbook()
out_ws = out_wb.active
for r_idx, row in enumerate(data, start=1):
    for c_idx, val in enumerate(row, start=1):
        if val is not None:
            # datetime tz 제거
            if hasattr(val, 'tzinfo') and val.tzinfo:
                val = val.replace(tzinfo=None)
            out_ws.cell(r_idx, c_idx, val)
out_wb.save("data/KTB_Lending_Balance_clean.xlsx")
```

3. 분석 스크립트는 `_clean.xlsx`가 있으면 우선 사용, 없으면 원본 사용 (코드에 반영됨)
4. `_clean.xlsx` 저장 후 사용자에게 Excel에서 원본을 닫아도 된다고 안내

### Stage 1: 데이터 분석 (자동)

**Step 1-1. 투자자 순매수 분석**

```bash
cd KTB/Auction/Documents/
PYTHONIOENCODING=utf-8 python investor_flow_ratio.py \
  --tenor {tenor} \
  --target-date {auction_date} \
  --target-bond "{bond_name}" \
  --top-n 5
```

출력에서 추출할 정보:
- 타겟 프로파일 (외국인/보험/은행/금투 비율 + 백분위)
- 유사 시점 Top 5 리스트

**Step 1-2. 대차잔고 분석**

```bash
PYTHONIOENCODING=utf-8 python lending_ratio_analysis.py \
  --tenor {tenor} \
  --target-date {auction_date} \
  --target-bond "{bond_name}" \
  --top-n 5
```

출력에서 추출할 정보:
- 타겟 프로파일 (단독/합산 D-1 수준, 변화, 패턴, 백분위)
- 유사 시점 Top 5 리스트
- 숏 이전 패턴 해당 여부

**Step 1-3. 교차 비교 및 사용자 보고**

투자자 Top 5와 대차 Top 5를 교차:
- 겹치는 시점이 있으면 → **겹치는 시점 전부** 사용자에게 알림 (유사도 순위 명시)
- 겹치지 않으면 → **각각 Top 3**만 사용자에게 알림

보고 형식:
```
## 투자자 순매수 프로파일
- 외국인: +XX.X% (YY%ile)
- 보험: +XX.X% (YY%ile)
- 금투: -XX.X% (YY%ile)

## 대차잔고 프로파일
- 단독 D-1: XX.X% (YY%ile), 변화 +XX.X%p (가속/감속)
- 합산 D-1: XX.X% (YY%ile), 변화 +XX.X%p
- [숏 이전 패턴 해당/미해당]

## 유사 시점 (겹치는 시점)
1. YYYY-MM-DD (종목) — 투자자 N위 + 대차 M위 | 응찰 XXX.X
2. ...

## 유사 시점 (투자자 Top 3 / 대차 Top 3)
...
```

**여기서 반드시 멈추고 사용자에게 안내:**
> "유사 시점의 인포맥스 5분봉 데이터(9개 CSV)를 data/infomax_data_{날짜}/ 폴더에 넣어주세요.
> 입찰 5영업일 전 ~ 2영업일 후 기간으로 다운로드해주세요."

### Stage 2: 금융시장 분석 (사용자 데이터 제공 후)

사용자가 "데이터 넣었어", "준비됐어" 등으로 알리면 진행.

**Step 2-1. 데이터 폴더 확인**

사용자에게 데이터 폴더 위치를 확인하거나, 기본 경로(data/infomax_data/) 확인.

**Step 2-2. 금융시장 분석 실행**

```bash
PYTHONIOENCODING=utf-8 python auction_market_analysis.py \
  --auction-date {유사시점_날짜} \
  --tenor {tenor} \
  --awarded-rate {낙찰금리} \
  --data-dir {데이터폴더}
```

낙찰금리는 Auction_Result.xlsx에서 조회하거나 사용자에게 확인.

**Step 2-3. 결과 보고**

코드 출력의 Section 5(교차 비교 종합)를 중심으로 사용자에게 보고. 반드시 아래 항목을 모두 포함:

1. **일별 전일 대비 변동 (5-1)**: 장내국채 bp + IRS bp + 선물 틱을 한 줄에 보여줌
   - 어떤 상품이 더 크게 움직였는지 비교 (선물 vs IRS vs 장내)
2. **입찰 전 누적 변동 (5-2)**: 입찰 전 첫날→전일, 첫날→입찰일 누적
   - 단기 vs 장기 어느 쪽이 더 움직였는지
3. **입찰 당일 주요 시점 (5-3)**: 09:05, 11:00, 11:30, 15:30
   - 11시 장내금리 vs 낙찰금리 차이
   - IRS가 장내국채보다 더 움직였는지, 선물이 더 움직였는지
4. **입찰 후 변동 (5-4)**: D+1, D+2 입찰일 종가 대비
   - 입찰 테너가 다른 테너 대비 상대적으로 어떤지
5. **커브 교차 비교 (5-5)**: 국채/IRS/선물 커브 비교
   - 같은 3-10 커브라도 국채 vs IRS vs 선물에서 다르게 움직일 수 있음

여러 유사 시점을 분석하는 경우, 각각 실행 후 비교 요약도 제공.

## 주의사항

### 데이터 관련
- `KTB_Investor_Flow.xlsx` 컬럼 매핑: [3]=금융투자, [4]=보험, ..., [12]=외국인 (코드에 반영됨)
- 단위: 투자자 순매수=백만원, 발행잔액=억원, 변환계수=÷100
- 인포맥스 CSV: col0=날짜(만), col1=시간, 역순 정렬 (코드에서 처리)

### 벤치마크 체인
대차 분석의 벤치마크 체인은 입찰 결과(Auction_Result.xlsx)에서 **자동 구성**된다.
종목별 첫 입찰일 기준 시간순 정렬 → 직전 종목 자동 매핑.
새 종목이 Auction_Result.xlsx에 추가되면 코드 수정 없이 자동 반영.

### 에러 대응

| 상황 | 대응 |
|------|------|
| investor_flow 데이터 부족 | crawl_ktbinfo.py로 보충 크롤링 제안 |
| lending_balance 데이터 부족 | 사용자에게 최신 xlsx 업데이트 요청 |
| 인포맥스 CSV 파일 누락 | 누락 파일 목록 알려주고 다운로드 요청 |
| 새 벤치마크 종목 미등록 | Auction_Result.xlsx에 입찰 결과 추가하면 자동 반영 |
