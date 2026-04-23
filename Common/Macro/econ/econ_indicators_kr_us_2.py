"""
한국 + 미국 경제지표 통합 수집기
================================
- 한국: KOSIS + ECOS API
- 미국: FRED API
- 카테고리별 시트 분리
- 기존 파일 병합 (Smart Update)
- 출력: econ_data_kr.xlsx/json, econ_data_us.xlsx/json

설치:
pip install PublicDataReader
pip install PublicDataReader pandas openpyxl requests
pip install fredapi
pip install fredapi pandas openpyxl

실행:
python econ_indicator_kr_us.py           # 전체 수집
python econ_indicator_kr_us.py --kr      # 한국만
python econ_indicator_kr_us.py --us      # 미국만
"""

import pandas as pd
import numpy as np
import requests
import json
import time
import sys
import os
import urllib3
from datetime import datetime
from dateutil.relativedelta import relativedelta

# SSL 인증서 검증 경고 비활성화 (회사 네트워크 등 자체 서명 인증서 환경용)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================
# API 키 설정
# ============================================
# 한국
KOSIS_API_KEY = "N2JkZjIyMTM5YmM4ZTQwNDEyZDFhMzY0Y2E2ZGViZTM="
ECOS_API_KEY = "8AGMFXBNTKDZWIP7OW5I"

# 미국
FRED_API_KEY = "85ba726fc3e5a33359bb58a21ddf1c9f"

# ============================================
# 수집 기간 설정 (자동: 최근 10년)
# ============================================
END_YEAR = datetime.now().year
START_YEAR = END_YEAR - 10

# ============================================
# 파일명 설정
# ============================================
KR_EXCEL_FILE = "econ_data_kr.xlsx"
KR_JSON_FILE = "econ_data_kr.json"
US_EXCEL_FILE = "econ_data_us.xlsx"
US_JSON_FILE = "econ_data_us.json"

# ============================================
# 재시도 및 타임아웃 설정
# ============================================
MAX_RETRIES = 3
RETRY_DELAY = 2
REQUEST_TIMEOUT = 60  # 30초 → 60초로 증가


# ============================================================
# ==================== 한국 경제지표 설정 ====================
# ============================================================

KOSIS_INDICATORS = {
    "CPI": {
        "name": "소비자물가지수",
        "org_id": "101",
        "tbl_id": "DT_1J22003",
        "frequency": "M",
        "tier": 1,
    },
    "EMPLOYMENT": {
        "name": "고용률/실업률",
        "org_id": "101",
        "tbl_id": "DT_1DA7001S",
        "frequency": "M",
        "tier": 1,
    },
    "LEADING_INDEX": {
        "name": "경기종합지수",
        "org_id": "101",
        "tbl_id": "DT_1C8015",
        "frequency": "M",
        "tier": 1,
    },
    "INDUSTRIAL_PROD": {
        "name": "전산업생산지수",
        "org_id": "101",
        "tbl_id": "DT_1JH20201",
        "frequency": "M",
        "tier": 2,
    },
    "CONSTRUCTION": {
        "name": "건설수주액",
        "org_id": "101",
        "tbl_id": "DT_1G1B045",
        "frequency": "M",
        "tier": 2,
    },
}

ECOS_INDICATORS = {
    # 월간(M) 지표 - Tier 1
    "M2": {
        "name": "M2(광의통화)",
        "stat_code": "101Y003",
        "item_code1": "BBHS00",
        "cycle": "M",
        "tier": 1,
    },
    "PPI": {
        "name": "생산자물가지수",
        "stat_code": "404Y014",
        "item_code1": "*AA",  # 총지수
        "cycle": "M",
        "tier": 1,
    },
    "BASE_RATE": {
        "name": "한국은행 기준금리",
        "stat_code": "722Y001",
        "item_code1": "0101000",
        "cycle": "M",
        "tier": 1,
    },
    "FX_RESERVES": {
        "name": "외환보유액",
        "stat_code": "732Y001",
        "item_code1": "99",
        "cycle": "M",
        "tier": 1,
    },
    "CURRENT_ACCOUNT": {
        "name": "경상수지",
        "stat_code": "301Y017",
        "item_code1": "SA000",  # 경상수지
        "cycle": "M",
        "tier": 1,
    },
    # 월간(M) 지표 - Tier 2
    "BSI_MANUFACTURING": {
        "name": "제조업BSI(업황전망)",
        "stat_code": "512Y014",
        "item_code1": "C0000",  # 제조업
        "item_code2": "BA",  # 업황전망BSI
        "cycle": "M",
        "tier": 2,
    },
    # 분기(Q) 지표
    "GDP": {
        "name": "GDP(실질, 계절조정)",
        "stat_code": "200Y108",
        "item_code1": "10601",
        "cycle": "Q",
        "tier": 1,
    },
    "HOUSEHOLD_CREDIT": {
        "name": "가계신용",
        "stat_code": "151Y001",
        "item_code1": "1000000",  # 가계신용 총계
        "cycle": "Q",
        "tier": 2,
    },
    "SHORT_TERM_DEBT": {
        "name": "대외채무",
        "stat_code": "311Y004",
        "item_code1": "A000000",  # 대외채무 총계
        "cycle": "Q",
        "tier": 2,
    },
}


# ============================================================
# ==================== 미국 경제지표 설정 ====================
# ============================================================

US_INDICATORS = {
    # 고용 (Employment) - Tier 1
    'Employment': {
        'NFP_Total': 'PAYEMS',
        'NFP_Private': 'USPRIV',
        'NFP_Manufacturing': 'MANEMP',
        'NFP_Services': 'SRVPRD',
        'Unemployment_Rate': 'UNRATE',
        'Participation_Rate': 'CIVPART',
        'Avg_Hourly_Earnings': 'CES0500000003',
        'Avg_Weekly_Hours': 'AWHAETP',
        'Initial_Claims': 'ICSA',
        'Continued_Claims': 'CCSA',
    },
    # 물가 (Inflation) - Tier 1
    'Inflation': {
        'CPI_Headline': 'CPIAUCSL',
        'CPI_Core': 'CPILFESL',
        'CPI_Shelter': 'CUSR0000SAH1',
        'CPI_Energy': 'CPIENGSL',
        'CPI_Food': 'CPIUFDSL',
        'CPI_Services': 'CUSR0000SAS',
        'PCE_Headline': 'PCEPI',
        'PCE_Core': 'PCEPILFE',
        'PPI_Final_Demand': 'PPIFIS',
        'Breakeven_5Y': 'T5YIFR',
        'Breakeven_10Y': 'T10YIE',
    },
    # 성장 (Growth) - Tier 1
    'Growth': {
        'Real_GDP': 'GDPC1',
        'Real_GDP_Growth': 'A191RL1Q225SBEA',
        'Retail_Sales_Total': 'RSAFS',
        'Retail_Sales_ExAuto': 'RSFSXMV',
        'Retail_Sales_Control': 'MARTSSM44W72USS',
        'Industrial_Production': 'INDPRO',
        'Capacity_Utilization': 'TCU',
        'Durable_Goods_Orders': 'DGORDER',
        'Durable_Goods_ExTransport': 'ADXTNO',
    },
    # 금리 (Rates) - Tier 1
    'Rates': {
        'Fed_Funds_Rate': 'FEDFUNDS',
        'Fed_Funds_Daily': 'DFF',
        'Treasury_3M': 'DTB3',
        'Treasury_2Y': 'DGS2',
        'Treasury_5Y': 'DGS5',
        'Treasury_10Y': 'DGS10',
        'Treasury_30Y': 'DGS30',
        'Spread_10Y_2Y': 'T10Y2Y',
        'Spread_10Y_3M': 'T10Y3M',
        'M2': 'M2SL',
        'HY_Spread': 'BAMLH0A0HYM2',
        'IG_Spread': 'BAMLC0A0CM',
    },
    # 무역 (Trade) - Tier 1
    'Trade': {
        'Trade_Balance': 'BOPGSTB',
        'Goods_Balance': 'BOPGTB',
        'Exports_Total': 'BOPTEXP',
        'Imports_Total': 'BOPTIMP',
        'Exports_Goods': 'BOPGEXP',
        'Imports_Goods': 'BOPGIMP',
    },
    # 주거 (Housing) - Tier 2
    'Housing': {
        'Housing_Starts': 'HOUST',
        'Housing_Starts_Single': 'HOUST1F',
        'Building_Permits': 'PERMIT',
        'Building_Permits_Single': 'PERMIT1',
        'Existing_Home_Sales': 'EXHOSLUSM495S',
        'New_Home_Sales': 'HSN1F',
        'Home_Price_Index': 'CSUSHPINSA',
        'Mortgage_Rate_30Y': 'MORTGAGE30US',
    },
    # 소비자 심리 (Sentiment) - Tier 2
    'Sentiment': {
        'Michigan_Sentiment': 'UMCSENT',
        'Michigan_Expectations': 'MICH',
        'Personal_Income': 'PI',
        'Personal_Spending': 'PCE',
        'Saving_Rate': 'PSAVERT',
    },
    # 금융시장 (Markets) - Tier 2
    'Markets': {
        'VIX': 'VIXCLS',
        'SP500': 'SP500',
        'Dollar_Index': 'DTWEXBGS',
        'EUR_USD': 'DEXUSEU',
        'JPY_USD': 'DEXJPUS',
        'WTI_Oil': 'DCOILWTICO',
    },
}


# ============================================================
# ==================== 공통 유틸리티 함수 ====================
# ============================================================

def is_connection_error(error_msg):
    """연결 오류인지 확인"""
    keywords = ['Remote', 'Connection', 'aborted', 'timeout', 'Timeout', 'timed out']
    return any(keyword in error_msg for keyword in keywords)


# ============================================================
# ==================== 한국 데이터 수집 함수 ====================
# ============================================================

def get_kr_value_column(df):
    for col in ['DATA_VALUE', 'DT', 'VALUE', '값']:
        if col in df.columns:
            return col
    return None


def get_kr_period_column(df):
    for col in ['TIME', 'PRD_DE', 'PERIOD', '시점']:
        if col in df.columns:
            return col
    return None


def get_kr_item_column(df):
    for col in ['ITEM_NAME1', 'ITM_NM', 'C1_NM', '항목명', 'KEYSTAT_NAME']:
        if col in df.columns:
            return col
    return None


def fetch_kr_gdp_data():
    """GDP 데이터 전용 수집 함수 (재시도 로직 포함)"""
    print(f"\n📊 [ECOS] GDP성장률 (전용 함수)")
    
    base_url = "https://ecos.bok.or.kr/api/StatisticSearch"
    stat_code = "200Y108"
    item_code = "10601"
    url = f"{base_url}/{ECOS_API_KEY}/json/kr/1/500/{stat_code}/Q/{START_YEAR}Q1/{END_YEAR}Q4/{item_code}/"
    
    for attempt in range(MAX_RETRIES):
        try:
            if attempt == 0:
                print(f"   🔗 {stat_code}/Q/{START_YEAR}Q1~{END_YEAR}Q4/{item_code}")
            else:
                print(f"   🔄 재시도 {attempt + 1}...", end=' ')
            
            response = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False)

            if response.status_code != 200:
                print(f"   ❌ HTTP 오류: {response.status_code}")
                return None

            data = response.json()
            rows = data.get("StatisticSearch", {}).get("row", [])
            
            if not rows:
                print(f"   ⚠️ 데이터 없음")
                return None
            
            if not isinstance(rows, list):
                rows = [rows]
            
            df = pd.DataFrame(rows)
            df['indicator_code'] = 'GDP'
            df['indicator_name'] = 'GDP(실질, 계절조정)'
            df['source'] = 'ECOS'
            df['tier'] = 1
            
            print(f"   ✅ {len(df)}개 레코드")
            return df
            
        except Exception as e:
            error_msg = str(e)
            if attempt < MAX_RETRIES - 1 and is_connection_error(error_msg):
                print(f"⚠️ 연결 오류, ", end='')
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                print(f"   ❌ 오류: {e}")
                return None
    
    return None


def fetch_kr_ecos_direct(code, config):
    """ECOS API 직접 호출 (재시도 로직 포함)"""
    print(f"\n📊 [ECOS] {config['name']} ({code})")

    cycle = config['cycle']
    base_url = "https://ecos.bok.or.kr/api/StatisticSearch"
    item_code1 = config['item_code1']
    item_code2 = config.get('item_code2', '')  # 선택적 item_code2

    if cycle == 'Q':
        attempts_list = [
            ('Q', f"{START_YEAR}Q1", f"{END_YEAR}Q4", item_code1, item_code2),
            ('Q', f"{START_YEAR}Q1", f"{END_YEAR}Q4", "", ""),
            ('QQ', f"{START_YEAR}0101", f"{END_YEAR}1231", item_code1, item_code2),
            ('QQ', f"{START_YEAR}0101", f"{END_YEAR}1231", "", ""),
        ]
    else:
        if cycle == 'M':
            start_date = f"{START_YEAR}01"
            end_date = f"{END_YEAR}12"
        else:
            start_date = str(START_YEAR)
            end_date = str(END_YEAR)
        attempts_list = [(cycle, start_date, end_date, item_code1, item_code2)]
        if item_code1:
            attempts_list.append((cycle, start_date, end_date, "", ""))

    for cyc, start_d, end_d, itm1, itm2 in attempts_list:
        # URL 구성: item_code1과 item_code2 모두 처리
        url = f"{base_url}/{ECOS_API_KEY}/json/kr/1/1000/{config['stat_code']}/{cyc}/{start_d}/{end_d}"
        if itm1:
            url += f"/{itm1}"
            if itm2:
                url += f"/{itm2}"

        log_suffix = ""
        if itm1:
            log_suffix = f"/{itm1}"
            if itm2:
                log_suffix += f"/{itm2}"
        
        # 재시도 로직
        for attempt in range(MAX_RETRIES):
            try:
                if attempt == 0:
                    print(f"   🔗 시도: {config['stat_code']}/{cyc}/{start_d}~{end_d}{log_suffix}")
                else:
                    print(f"   🔄 재시도 {attempt + 1}...", end=' ')

                response = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False)

                if response.status_code != 200:
                    break  # 다음 URL 시도
                
                data = response.json()
                
                if 'StatisticSearch' in data:
                    rows = data['StatisticSearch'].get('row', [])
                    if rows:
                        df = pd.DataFrame(rows)
                        df['indicator_code'] = code
                        df['indicator_name'] = config['name']
                        df['source'] = 'ECOS'
                        df['tier'] = config['tier']
                        print(f"   ✅ {len(df)}개 레코드")
                        return df
                
                if 'RESULT' in data:
                    msg = data['RESULT'].get('MESSAGE', '')
                    if '해당하는 데이터가 없습니다' not in msg:
                        print(f"   ⚠️ {msg}")
                break  # 다음 URL 시도
                
            except Exception as e:
                error_msg = str(e)
                if attempt < MAX_RETRIES - 1 and is_connection_error(error_msg):
                    print(f"⚠️ 연결 오류, ", end='')
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    # 마지막 재시도 실패 시 다음 URL 시도
                    break
    
    print(f"   ❌ 모든 형식 시도 실패")
    return None


def fetch_kr_kosis_data(api, code, config):
    """KOSIS 데이터 수집 (재시도 로직 포함)"""
    print(f"\n📊 [KOSIS] {config['name']} ({code})")

    for attempt in range(MAX_RETRIES):
        try:
            if attempt > 0:
                print(f"   🔄 재시도 {attempt + 1}...", end=' ')
            
            freq = config['frequency']
            if freq == 'M':
                start_prd, end_prd, prd_se = f"{START_YEAR}01", f"{END_YEAR}12", "M"
            elif freq == 'Q':
                start_prd, end_prd, prd_se = f"{START_YEAR}1", f"{END_YEAR}4", "Q"
            else:
                start_prd, end_prd, prd_se = str(START_YEAR), str(END_YEAR), "Y"

            df = api.get_data(
                "통계자료",
                orgId=config['org_id'],
                tblId=config['tbl_id'],
                prdSe=prd_se,
                startPrdDe=start_prd,
                endPrdDe=end_prd,
                itmId="ALL",
                objL1="ALL",
                objL2="",
                objL3="",
            )

            if df is not None and len(df) > 0:
                df['indicator_code'] = code
                df['indicator_name'] = config['name']
                df['source'] = 'KOSIS'
                df['tier'] = config['tier']
                print(f"   ✅ {len(df)}개 레코드")
                return df
            else:
                print(f"   ⚠️ 데이터 없음")
                return None

        except Exception as e:
            error_msg = str(e)
            if attempt < MAX_RETRIES - 1 and is_connection_error(error_msg):
                print(f"   ⚠️ 연결 오류, ", end='')
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                print(f"   ❌ 오류: {e}")
                return None

    return None


def save_kr_results(all_data):
    """한국 데이터 저장"""
    
    # JSON 처리
    existing_json = {}
    try:
        with open(KR_JSON_FILE, 'r', encoding='utf-8') as f:
            existing_json = json.load(f)
        print(f"\n📂 기존 JSON 파일 로드: {KR_JSON_FILE}")
    except FileNotFoundError:
        print(f"\n📂 새 JSON 파일 생성: {KR_JSON_FILE}")
    
    for code, df in all_data.items():
        if df is not None and len(df) > 0:
            df_clean = df.replace({np.nan: None, np.inf: None, -np.inf: None})
            existing_data = existing_json.get(code, {}).get('data', [])
            new_data = json.loads(df_clean.to_json(orient='records', date_format='iso'))
            
            prd_col = get_kr_period_column(df)
            
            if prd_col and existing_data:
                existing_dict = {str(item.get(prd_col, item.get('TIME', ''))): item for item in existing_data}
                for item in new_data:
                    key = str(item.get(prd_col, item.get('TIME', '')))
                    existing_dict[key] = item
                merged_data = list(existing_dict.values())
            else:
                merged_data = new_data
            
            existing_json[code] = {
                "meta": {
                    "name": df['indicator_name'].iloc[0] if 'indicator_name' in df.columns else code,
                    "source": df['source'].iloc[0] if 'source' in df.columns else '',
                    "tier": int(df['tier'].iloc[0]) if 'tier' in df.columns else 0,
                    "records": len(merged_data),
                    "last_updated": datetime.now().isoformat(),
                },
                "data": merged_data
            }
    
    with open(KR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(existing_json, f, ensure_ascii=False, indent=2, default=str)
    print(f"📁 JSON 저장: {KR_JSON_FILE}")
    
    # Excel 처리
    existing_excel = {}
    try:
        xlsx = pd.ExcelFile(KR_EXCEL_FILE)
        for sheet in xlsx.sheet_names:
            existing_excel[sheet] = pd.read_excel(xlsx, sheet_name=sheet)
        print(f"📂 기존 Excel 파일 로드: {KR_EXCEL_FILE}")
    except FileNotFoundError:
        print(f"📂 새 Excel 파일 생성: {KR_EXCEL_FILE}")
    
    with pd.ExcelWriter(KR_EXCEL_FILE, engine='openpyxl') as writer:
        for code, df in all_data.items():
            if df is not None and len(df) > 0:
                sheet_name = code[:31]
                prd_col = get_kr_period_column(df)
                
                if sheet_name in existing_excel and prd_col:
                    existing_df = existing_excel[sheet_name]
                    if prd_col in existing_df.columns:
                        combined = pd.concat([existing_df, df], ignore_index=True)
                        combined[prd_col] = combined[prd_col].astype(str)  # 타입 통일 (수정)
                        combined = combined.drop_duplicates(subset=[prd_col], keep='last')
                        combined = combined.sort_values(prd_col)
                        combined.to_excel(writer, sheet_name=sheet_name, index=False)
                    else:
                        df.to_excel(writer, sheet_name=sheet_name, index=False)
                else:
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        config_rows = []
        for code, cfg in {**KOSIS_INDICATORS, **ECOS_INDICATORS}.items():
            config_rows.append({
                'code': code,
                'name': cfg['name'],
                'source': 'KOSIS' if code in KOSIS_INDICATORS else 'ECOS',
                'tier': cfg['tier'],
            })
        pd.DataFrame(config_rows).to_excel(writer, sheet_name='Config', index=False)
    
    print(f"📁 Excel 저장: {KR_EXCEL_FILE}")


def collect_korea_data():
    """한국 데이터 수집 메인"""
    print("\n" + "=" * 60)
    print("🇰🇷 한국 경제지표 수집")
    print("=" * 60)
    print(f"기간: {START_YEAR} ~ {END_YEAR}")
    print(f"KOSIS: {len(KOSIS_INDICATORS)}개 | ECOS: {len(ECOS_INDICATORS)}개")
    print(f"타임아웃: {REQUEST_TIMEOUT}초 | 최대 재시도: {MAX_RETRIES}회")
    
    from PublicDataReader import Kosis
    
    all_data = {}
    success, fail = 0, 0
    
    # KOSIS 수집
    print("\n" + "=" * 40)
    print("📡 KOSIS (통계청) 데이터 수집")
    print("=" * 40)
    
    kosis_api = Kosis(KOSIS_API_KEY)
    
    for code, config in KOSIS_INDICATORS.items():
        df = fetch_kr_kosis_data(kosis_api, code, config)
        if df is not None:
            all_data[code] = df
            success += 1
        else:
            fail += 1
        time.sleep(0.5)
    
    # ECOS 수집
    print("\n" + "=" * 40)
    print("📡 ECOS (한국은행) 데이터 수집")
    print("=" * 40)
    
    for code, config in ECOS_INDICATORS.items():
        if code == 'GDP':
            df = fetch_kr_gdp_data()
        else:
            df = fetch_kr_ecos_direct(code, config)
        
        if df is not None:
            all_data[code] = df
            success += 1
        else:
            fail += 1
        time.sleep(0.5)
    
    # 결과 처리
    print("\n" + "=" * 60)
    print(f"📊 한국 수집 결과: 성공 {success}개 / 실패 {fail}개")
    print("=" * 60)
    
    save_kr_results(all_data)
    
    return all_data


# ============================================================
# ==================== 미국 데이터 수집 함수 ====================
# ============================================================

def get_us_start_date(years_back=10):
    """자동으로 최근 n년 전 날짜 계산"""
    return (datetime.now() - relativedelta(years=years_back)).strftime('%Y-%m-%d')


def fetch_us_category_data(fred, category_name, series_dict, start_date):
    """미국 카테고리별 데이터 수집 (재시도 로직 포함)"""
    print(f"\n{'='*50}")
    print(f"📂 {category_name} 데이터 수집 중...")
    print(f"{'='*50}")

    data_frames = {}
    success_count = 0
    fail_count = 0

    for name, series_id in series_dict.items():
        for attempt in range(MAX_RETRIES):
            try:
                if attempt == 0:
                    print(f"  📊 {name} ({series_id})...", end=' ')
                else:
                    print(f"재시도 {attempt + 1}...", end=' ')

                series = fred.get_series(series_id, observation_start=start_date)

                if series is not None and len(series) > 0:
                    data_frames[name] = series
                    print(f"✅ {len(series)}개 레코드")
                    success_count += 1
                    break
                else:
                    print("⚠️ 데이터 없음")
                    fail_count += 1
                    break

            except Exception as e:
                error_msg = str(e)
                if attempt < MAX_RETRIES - 1 and is_connection_error(error_msg):
                    print(f"⚠️ 연결 오류, ", end='')
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    print(f"❌ 오류: {error_msg[:50]}")
                    fail_count += 1
                    break

        time.sleep(0.3)  # API rate limit 방지

    print(f"  → 성공: {success_count}, 실패: {fail_count}")
    
    if data_frames:
        df = pd.DataFrame(data_frames)
        df.index.name = 'Date'
        return df
    return None


def merge_us_with_existing(df_new, sheet_name, existing_file):
    """미국 데이터 기존 파일과 병합"""
    if not os.path.exists(existing_file):
        return df_new
    
    try:
        df_old = pd.read_excel(existing_file, sheet_name=sheet_name, index_col='Date')
        df_old.index = pd.to_datetime(df_old.index)
        df_new.index = pd.to_datetime(df_new.index)
        
        df_merged = df_new.combine_first(df_old)
        
        for col in df_new.columns:
            if col not in df_merged.columns:
                df_merged[col] = df_new[col]
        
        return df_merged.sort_index()
        
    except Exception as e:
        print(f"  ⚠️ 기존 시트 '{sheet_name}' 로드 실패: {e}")
        return df_new


def save_us_results(all_data):
    """미국 데이터 저장"""
    
    print(f"\n{'='*50}")
    print(f"💾 미국 데이터 저장 중...")
    print(f"{'='*50}")
    
    # Excel 저장
    with pd.ExcelWriter(US_EXCEL_FILE, engine='openpyxl') as writer:
        for category, df in all_data.items():
            if df is not None and len(df) > 0:
                df.to_excel(writer, sheet_name=category)
                print(f"  ✅ {category} 시트 저장 ({len(df.columns)}개 지표)")
    
    print(f"\n📁 Excel 저장 완료: {US_EXCEL_FILE}")
    
    # JSON 저장
    json_data = {
        'metadata': {
            'updated_at': datetime.now().isoformat(),
            'source': 'FRED API',
            'period': f'{get_us_start_date(10)} ~ {datetime.now().strftime("%Y-%m-%d")}'
        },
        'data': {}
    }
    
    for category, df in all_data.items():
        if df is not None and len(df) > 0:
            df_copy = df.copy()
            df_copy.index = df_copy.index.strftime('%Y-%m-%d')
            json_data['data'][category] = df_copy.to_dict(orient='index')
    
    with open(US_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"📁 JSON 저장 완료: {US_JSON_FILE}")


def collect_us_data():
    """미국 데이터 수집 메인"""
    from fredapi import Fred
    
    print("\n" + "=" * 60)
    print("🇺🇸 미국 경제지표 수집")
    print("=" * 60)
    
    fred = Fred(api_key=FRED_API_KEY)
    start_date = get_us_start_date(10)
    
    print(f"수집 기간: {start_date} ~ 현재")
    print(f"총 카테고리: {len(US_INDICATORS)}개")
    
    total_indicators = sum(len(v) for v in US_INDICATORS.values())
    print(f"총 지표 수: {total_indicators}개")
    print(f"타임아웃: {REQUEST_TIMEOUT}초 | 최대 재시도: {MAX_RETRIES}회")
    
    all_data = {}
    
    for category, series_dict in US_INDICATORS.items():
        df = fetch_us_category_data(fred, category, series_dict, start_date)
        
        if df is not None:
            df = merge_us_with_existing(df, category, US_EXCEL_FILE)
            all_data[category] = df
    
    save_us_results(all_data)
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("📋 미국 수집 완료 요약")
    print("=" * 60)
    
    for category, df in all_data.items():
        if df is not None:
            date_range = f"{df.index.min().strftime('%Y-%m-%d')} ~ {df.index.max().strftime('%Y-%m-%d')}"
            print(f"  {category}: {len(df.columns)}개 지표, {len(df)}개 레코드 ({date_range})")
    
    return all_data


# ============================================================
# ==================== 메인 실행 ====================
# ============================================================

def main():
    print("\n" + "=" * 70)
    print("📊 한국 + 미국 경제지표 통합 수집기")
    print("=" * 70)
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"수집 기간: {START_YEAR} ~ {END_YEAR} (최근 10년)")
    
    # 명령줄 인자 확인
    collect_kr = True
    collect_us = True
    
    if len(sys.argv) > 1:
        if '--kr' in sys.argv:
            collect_us = False
        elif '--us' in sys.argv:
            collect_kr = False
    
    # 한국 데이터 수집
    if collect_kr:
        try:
            collect_korea_data()
        except Exception as e:
            print(f"\n❌ 한국 데이터 수집 오류: {e}")
    
    # 미국 데이터 수집
    if collect_us:
        try:
            collect_us_data()
        except Exception as e:
            print(f"\n❌ 미국 데이터 수집 오류: {e}")
    
    # 최종 결과
    print("\n" + "=" * 70)
    print("✅ 모든 작업 완료!")
    print("=" * 70)
    
    if collect_kr:
        print(f"🇰🇷 한국: {KR_EXCEL_FILE}, {KR_JSON_FILE}")
    if collect_us:
        print(f"🇺🇸 미국: {US_EXCEL_FILE}, {US_JSON_FILE}")
    
    print(f"종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == "__main__":
    main()