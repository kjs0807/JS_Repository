"""해외선물 상품 마스터 + 월물 코드 관리.

DESIGN.md 2.1절의 API 테스트로 검증된 14개 상품 정보.
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, List, Optional


@dataclass
class FuturesProduct:
    """선물 상품 스펙."""
    symbol: str             # 루트 심볼 (예: "VG")
    kis_code: str           # 현재 KIS 종목코드 (예: "VGM26")
    exchange: str           # 거래소 (예: "EUREX")
    exch_cd: str            # KIS EXCH_CD (예: "EUREX")
    name_en: str
    name_kr: str
    asset_class: str        # "Index", "Rates"
    tick_size: float
    tick_value: float       # 1틱당 가치 (해당 통화)
    contract_size: float    # 계약 승수
    margin: float           # 증거금
    currency: str           # "EUR", "JPY", "HKD", "AUD", "TWD"
    point_value: float      # tick_value / tick_size
    expiry_date: str        # 만기일 YYYYMMDD
    daily_days: int         # 일봉 제공 일수


# 월코드 매핑
MONTH_CODES = {
    1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
    7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z',
}
MONTH_CODE_REVERSE = {v: k for k, v in MONTH_CODES.items()}

# 분기 월물 패턴 (대부분 선물은 3/6/9/12)
QUARTERLY_MONTHS = [3, 6, 9, 12]


def get_month_code(month: int) -> str:
    """월 → 월코드 변환."""
    return MONTH_CODES[month]


def get_front_month_code(root: str, ref_date: Optional[date] = None) -> str:
    """루트 심볼 + 기준일 → 근월물 KIS 종목코드 생성.

    Args:
        root: 상품 루트코드 (예: "VG", "BON")
        ref_date: 기준일 (기본: 오늘)

    Returns:
        KIS 종목코드 (예: "VGM26")
    """
    if ref_date is None:
        ref_date = date.today()

    year = ref_date.year
    month = ref_date.month

    # 다음 분기월 찾기
    for qm in QUARTERLY_MONTHS:
        if qm >= month:
            front_month = qm
            front_year = year
            break
    else:
        front_month = QUARTERLY_MONTHS[0]  # 다음해 3월
        front_year = year + 1

    month_code = MONTH_CODES[front_month]
    year_suffix = str(front_year)[-2:]
    return f"{root}{month_code}{year_suffix}"


def get_next_quarter_code(root: str, current_code: str) -> str:
    """현재 월물 → 다음 분기 월물 코드."""
    # 현재 코드에서 월코드와 연도 추출
    month_char = current_code[len(root)]
    year_suffix = current_code[len(root) + 1:]
    current_month = MONTH_CODE_REVERSE[month_char]
    current_year = 2000 + int(year_suffix)

    # 다음 분기월
    idx = QUARTERLY_MONTHS.index(current_month)
    if idx < len(QUARTERLY_MONTHS) - 1:
        next_month = QUARTERLY_MONTHS[idx + 1]
        next_year = current_year
    else:
        next_month = QUARTERLY_MONTHS[0]
        next_year = current_year + 1

    return f"{root}{MONTH_CODES[next_month]}{str(next_year)[-2:]}"


def should_rollover(expiry_date: str, ref_date: Optional[date] = None,
                    days_before: int = 3) -> bool:
    """만기일 N일 전 롤오버 필요 여부."""
    if ref_date is None:
        ref_date = date.today()
    expiry = datetime.strptime(expiry_date, "%Y%m%d").date()
    return (expiry - ref_date).days <= days_before


# ── 14개 확인된 상품 마스터 ───────────────────────────────────────

PRODUCTS: Dict[str, FuturesProduct] = {
    "VG": FuturesProduct(
        symbol="VG", kis_code="VGM26", exchange="EUREX", exch_cd="EUREX",
        name_en="Euro Stoxx 50", name_kr="유로스톡스50",
        asset_class="Index", tick_size=1.0, tick_value=10.0,
        contract_size=10, margin=4610.0, currency="EUR",
        point_value=10.0, expiry_date="20260619", daily_days=40,
    ),
    "BON": FuturesProduct(
        symbol="BON", kis_code="BONM26", exchange="EUREX", exch_cd="EUREX",
        name_en="Euro-Bund 10Y", name_kr="유로분트10년",
        asset_class="Rates", tick_size=0.01, tick_value=10.0,
        contract_size=100000, margin=3283.0, currency="EUR",
        point_value=1000.0, expiry_date="20260609", daily_days=15,
    ),
    "OAT": FuturesProduct(
        symbol="OAT", kis_code="OATM26", exchange="EUREX", exch_cd="EUREX",
        name_en="Euro-OAT (France)", name_kr="프랑스국채OAT",
        asset_class="Rates", tick_size=0.01, tick_value=10.0,
        contract_size=100000, margin=2154.0, currency="EUR",
        point_value=1000.0, expiry_date="20260609", daily_days=28,
    ),
    "GX": FuturesProduct(
        symbol="GX", kis_code="GXM26", exchange="EUREX", exch_cd="EUREX",
        name_en="DAX", name_kr="DAX",
        asset_class="Index", tick_size=1.0, tick_value=25.0,
        contract_size=25, margin=49079.0, currency="EUR",
        point_value=25.0, expiry_date="20260619", daily_days=40,
    ),
    "JGB": FuturesProduct(
        symbol="JGB", kis_code="JGBM26", exchange="OSE", exch_cd="OSE",
        name_en="JGB 10Y", name_kr="일본국채10년",
        asset_class="Rates", tick_size=0.01, tick_value=10000.0,
        contract_size=1000000, margin=1448084.0, currency="JPY",
        point_value=1000000.0, expiry_date="20260616", daily_days=25,
    ),
    "TPX": FuturesProduct(
        symbol="TPX", kis_code="TPXM26", exchange="OSE", exch_cd="OSE",
        name_en="TOPIX", name_kr="토픽스",
        asset_class="Index", tick_size=0.5, tick_value=5000.0,
        contract_size=10000, margin=1399362.0, currency="JPY",
        point_value=10000.0, expiry_date="20260612", daily_days=38,
    ),
    "HSI": FuturesProduct(
        symbol="HSI", kis_code="HSIM26", exchange="HKEx", exch_cd="HKEx",
        name_en="Hang Seng Index", name_kr="항셍지수",
        asset_class="Index", tick_size=1.0, tick_value=50.0,
        contract_size=50, margin=117705.0, currency="HKD",
        point_value=50.0, expiry_date="20260629", daily_days=40,
    ),
    "MHI": FuturesProduct(
        symbol="MHI", kis_code="MHIM26", exchange="HKEx", exch_cd="HKEx",
        name_en="Mini Hang Seng", name_kr="미니항셍",
        asset_class="Index", tick_size=1.0, tick_value=10.0,
        contract_size=10, margin=23541.0, currency="HKD",
        point_value=10.0, expiry_date="20260629", daily_days=40,
    ),
    "HHI": FuturesProduct(
        symbol="HHI", kis_code="HHIM26", exchange="HKEx", exch_cd="HKEx",
        name_en="H-Shares Index", name_kr="H주지수",
        asset_class="Index", tick_size=1.0, tick_value=50.0,
        contract_size=50, margin=45885.0, currency="HKD",
        point_value=50.0, expiry_date="20260629", daily_days=40,
    ),
    "YT": FuturesProduct(
        symbol="YT", kis_code="YTM26", exchange="ASX", exch_cd="ASX",
        name_en="3Y Australian Bond", name_kr="호주3년국채",
        asset_class="Rates", tick_size=0.001, tick_value=2.93,
        contract_size=100000, margin=1075.0, currency="AUD",
        point_value=2930.0, expiry_date="20260615", daily_days=18,
    ),
    "XT": FuturesProduct(
        symbol="XT", kis_code="XTM26", exchange="ASX", exch_cd="ASX",
        name_en="10Y Australian Bond", name_kr="호주10년국채",
        asset_class="Rates", tick_size=0.001, tick_value=8.952,
        contract_size=100000, margin=2625.0, currency="AUD",
        point_value=8952.0, expiry_date="20260615", daily_days=21,
    ),
    "SPI": FuturesProduct(
        symbol="SPI", kis_code="SPIM26", exchange="ASX", exch_cd="ASX",
        name_en="SPI 200", name_kr="SPI200",
        asset_class="Index", tick_size=1.0, tick_value=25.0,
        contract_size=25, margin=15154.0, currency="AUD",
        point_value=25.0, expiry_date="20260618", daily_days=30,
    ),
    "TX": FuturesProduct(
        symbol="TX", kis_code="TXM26", exchange="FTX", exch_cd="FTX",
        name_en="TAIEX", name_kr="대만가권지수",
        asset_class="Index", tick_size=1.0, tick_value=200.0,
        contract_size=200, margin=339000.0, currency="TWD",
        point_value=200.0, expiry_date="20260617", daily_days=40,
    ),
    "MTX": FuturesProduct(
        symbol="MTX", kis_code="MTXM26", exchange="FTX", exch_cd="FTX",
        name_en="Mini TAIEX", name_kr="미니대만가권",
        asset_class="Index", tick_size=1.0, tick_value=50.0,
        contract_size=50, margin=84750.0, currency="TWD",
        point_value=50.0, expiry_date="20260617", daily_days=40,
    ),
}


def get_products_by_exchange(exchange: str) -> List[FuturesProduct]:
    """거래소별 상품 필터."""
    return [p for p in PRODUCTS.values() if p.exch_cd == exchange]


def get_all_exchanges() -> List[str]:
    """사용 가능한 거래소 목록."""
    return sorted(set(p.exch_cd for p in PRODUCTS.values()))


def get_product_by_kis_code(kis_code: str) -> Optional[FuturesProduct]:
    """KIS 종목코드로 상품 검색."""
    for p in PRODUCTS.values():
        if p.kis_code == kis_code:
            return p
    return None
