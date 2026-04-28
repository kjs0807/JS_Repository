"""Bybit USDT 무기한 선물 상품 스펙 관리.

시작 시 Bybit instruments-info API에서 동적으로 조회하고,
실패 시 기본값(ProductSpec 디폴트)으로 fallback한다.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_PUBLIC_BASE_URL = "https://api.bybit.com"


@dataclass
class ProductSpec:
    """코인 선물 상품 스펙.

    Attributes:
        symbol: 심볼 (예: "BTCUSDT")
        base_coin: 기초 자산 코인 (예: "BTC")
        quote_coin: 견적 통화 (항상 "USDT")
        tick_size: 최소 가격 변동 단위
        min_qty: 최소 주문 수량
        qty_step: 수량 증감 단위
        min_notional: 최소 주문 명목가 (USDT)
        max_leverage: 최대 레버리지
        contract_type: 계약 유형 (항상 "LinearPerpetual")
    """
    symbol: str
    base_coin: str
    quote_coin: str = "USDT"
    tick_size: float = 0.01
    min_qty: float = 0.001
    qty_step: float = 0.001
    min_notional: float = 5.0
    max_leverage: int = 100
    contract_type: str = "LinearPerpetual"


# 런타임에 채워지는 상품 스펙 딕셔너리
PRODUCTS: Dict[str, ProductSpec] = {}


def fetch_products_from_api(symbols: Optional[List[str]] = None) -> int:
    """Bybit instruments-info API에서 상품 스펙을 조회하여 PRODUCTS에 저장한다.

    Args:
        symbols: 조회할 심볼 리스트. None이면 USDT 무기한 전체.

    Returns:
        등록된 심볼 수
    """
    try:
        count = 0
        cursor: Optional[str] = None

        while True:
            params: Dict[str, str] = {"category": "linear"}
            if cursor:
                params["cursor"] = cursor

            resp = requests.get(
                f"{_PUBLIC_BASE_URL}/v5/market/instruments-info",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                logger.warning("instruments-info API 오류: %s", data.get("retMsg"))
                break

            for info in data["result"]["list"]:
                sym = info["symbol"]
                if not sym.endswith("USDT"):
                    continue
                if info.get("status") != "Trading":
                    continue
                if symbols is not None and sym not in symbols:
                    continue

                lot = info.get("lotSizeFilter", {})
                price = info.get("priceFilter", {})
                lev = info.get("leverageFilter", {})

                PRODUCTS[sym] = ProductSpec(
                    symbol=sym,
                    base_coin=info.get("baseCoin", sym.replace("USDT", "")),
                    tick_size=float(price.get("tickSize", 0.01)),
                    min_qty=float(lot.get("minOrderQty", 0.001)),
                    qty_step=float(lot.get("qtyStep", 0.001)),
                    min_notional=float(lot.get("minNotionalValue", 5.0)),
                    max_leverage=int(float(lev.get("maxLeverage", 100))),
                )
                count += 1

            # 다음 페이지 cursor 확인
            next_cursor = data["result"].get("nextPageCursor", "")
            if not next_cursor:
                break
            cursor = next_cursor

        logger.info("상품 스펙 로드 완료: %d개 심볼", count)
        return count
    except Exception as exc:
        logger.error("상품 스펙 API 조회 실패: %s", exc)
        return 0


def get_product(symbol: str) -> ProductSpec:
    """심볼로 상품 스펙을 조회한다.

    PRODUCTS에 없으면 기본값 ProductSpec으로 생성하여 등록한다.

    Args:
        symbol: 심볼 (예: "BTCUSDT")

    Returns:
        ProductSpec 객체
    """
    if symbol not in PRODUCTS:
        logger.warning(
            "상품 스펙 미등록: %s — 기본값 사용 (tick=0.01, min_qty=0.001)",
            symbol,
        )
        PRODUCTS[symbol] = ProductSpec(
            symbol=symbol,
            base_coin=symbol.replace("USDT", ""),
        )
    return PRODUCTS[symbol]


def round_qty(symbol: str, qty: float) -> float:
    """수량을 qty_step에 맞게 내림 반올림한다.

    Args:
        symbol: 심볼
        qty: 원래 수량

    Returns:
        qty_step 배수로 내림한 수량
    """
    spec = get_product(symbol)
    step = spec.qty_step
    factor = round(1.0 / step)
    return int(qty * factor) / factor


def round_price(symbol: str, price: float) -> float:
    """가격을 tick_size에 맞게 반올림한다.

    Args:
        symbol: 심볼
        price: 원래 가격

    Returns:
        tick_size 배수로 반올림한 가격
    """
    spec = get_product(symbol)
    tick = spec.tick_size
    factor = round(1.0 / tick)
    return round(price * factor) / factor


__all__ = [
    "ProductSpec", "PRODUCTS", "fetch_products_from_api",
    "get_product", "round_qty", "round_price",
]
