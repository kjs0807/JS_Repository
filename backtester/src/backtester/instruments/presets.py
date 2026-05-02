"""Bybit linear perpetual instrument presets (Phase 2.5 후속).

매번 ``Instrument(...)`` 를 손으로 채우지 않도록 Bybit USDT linear perpetual 상위 10여
개 심볼의 standard spec 을 고정해 둔다. preset 값 출처:

- price_tick / qty_step / min_qty: ``Crypto/Bybit_Trading/db/bybit_data.db`` 의
  ``products_master`` 테이블 (Bybit instruments-info 캐시, 갱신 ts: 2026-04 분기).
- min_notional: legacy ``ProductSpec`` default 5 USDT (DB 에 None 으로 저장돼 있음).
- max_leverage: legacy default 100 (DB 에 None). 실제 거래는 위험 한도 ``RiskLimits
  .max_leverage`` 로 제한 권장.
- fee: Bybit linear perp 표준 taker 0.055% / maker -0.005~0.02%. 보수적으로 taker
  0.055% (0.00055) / maker 0.02% (0.0002).
- maintenance_margin_rate: 일반 0.5%. 0.005.
- liquidation_fee_rate: ~0.06%. 0.0006.

**중요 (conservative preset)**: 실거래 / 정밀 리서치 전에는 Bybit instruments-info 로
다시 fetch 해서 갱신할 것. tier 별 maintenance margin / fee tier 등 세부는 후속 PR 에서
``BybitInstrumentSpecFetcher`` 로 자동 동기화 예정.

TONUSDT 는 DB 에 없어 LINKUSDT 와 동일 패턴 (min_qty/qty_step=0.1, tick=0.001) 으로
보수 추정. 사용자 본인 거래 전 실측 권장.
"""

from __future__ import annotations

from decimal import Decimal

from backtester.instruments.base import (
    ExchangeRule,
    FeeModel,
    Instrument,
    MarginModel,
)

# (symbol, base_coin, price_tick, qty_step, min_qty)
# DB ``products_master`` 출처. min_notional / max_leverage 는 legacy default.
_BYBIT_LINEAR_PERP_TABLE: dict[str, dict[str, str]] = {
    "BTCUSDT": {
        "base": "BTC",
        "price_tick": "0.1",
        "qty_step": "0.001",
        "min_qty": "0.001",
    },
    "ETHUSDT": {
        "base": "ETH",
        "price_tick": "0.01",
        "qty_step": "0.01",
        "min_qty": "0.01",
    },
    "SOLUSDT": {
        "base": "SOL",
        "price_tick": "0.01",
        "qty_step": "0.1",
        "min_qty": "0.1",
    },
    "XRPUSDT": {
        "base": "XRP",
        "price_tick": "0.0001",
        "qty_step": "0.1",
        "min_qty": "0.1",
    },
    "BNBUSDT": {
        "base": "BNB",
        "price_tick": "0.1",
        "qty_step": "0.01",
        "min_qty": "0.01",
    },
    "DOGEUSDT": {
        "base": "DOGE",
        "price_tick": "0.00001",
        "qty_step": "1",
        "min_qty": "1",
    },
    "ADAUSDT": {
        "base": "ADA",
        "price_tick": "0.0001",
        "qty_step": "1",
        "min_qty": "1",
    },
    "AVAXUSDT": {
        "base": "AVAX",
        "price_tick": "0.001",
        "qty_step": "0.1",
        "min_qty": "0.1",
    },
    "LINKUSDT": {
        "base": "LINK",
        "price_tick": "0.001",
        "qty_step": "0.1",
        "min_qty": "0.1",
    },
    "TONUSDT": {
        # DB 미수록 — LINKUSDT 패턴으로 보수 추정 (실거래 전 갱신 필요)
        "base": "TON",
        "price_tick": "0.001",
        "qty_step": "0.1",
        "min_qty": "0.1",
    },
}

# 심볼 공통 default — Bybit linear perp 표준 + legacy ProductSpec.
_DEFAULT_MIN_NOTIONAL = Decimal("5")
_DEFAULT_MAX_LEVERAGE = Decimal("100")
_DEFAULT_TAKER_FEE = Decimal("0.00055")  # Crypto/Bybit_Trading demo config
_DEFAULT_MAKER_FEE = Decimal("0.0002")  # Crypto/Bybit_Trading demo config
_DEFAULT_MMR = Decimal("0.005")
_DEFAULT_LIQ_FEE = Decimal("0.0006")


def available_bybit_linear_symbols() -> list[str]:
    """Preset 이 제공된 심볼 목록 (sorted). 후속 PR 에서 동적 fetch 가 추가될 수 있음."""
    return sorted(_BYBIT_LINEAR_PERP_TABLE.keys())


def bybit_linear_perp(symbol: str) -> Instrument:
    """Bybit USDT linear perpetual ``symbol`` 의 conservative preset Instrument.

    포함:
    - ``asset_class="crypto_perp"``, ``size_unit="base_asset"``, ``quote_currency="USDT"``.
    - ``FeeModel(taker=0.00055, maker=0.0002)`` — Bybit_Trading demo parity.
    - ``ExchangeRule(price_tick, qty_step, min_qty, min_notional=5, max_leverage=100)``.
    - ``MarginModel(maintenance_margin_rate=0.005, liquidation_fee_rate=0.0006)``.

    실거래 / 정밀 백테스트 전에는 Bybit instruments-info 로 다시 갱신할 것 — 모듈
    docstring 참조.

    Raises:
    - ``ValueError``: 등록되지 않은 symbol. ``available_bybit_linear_symbols()`` 참조.
    """
    spec = _BYBIT_LINEAR_PERP_TABLE.get(symbol)
    if spec is None:
        raise ValueError(
            f"unknown Bybit linear perp symbol: {symbol!r}. Available: "
            f"{available_bybit_linear_symbols()}"
        )
    price_tick = Decimal(spec["price_tick"])
    qty_step = Decimal(spec["qty_step"])
    min_qty = Decimal(spec["min_qty"])
    base = spec["base"]
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        # tick_size / tick_value 은 ledger 회계용 — price_tick 과 동일하게 둠.
        tick_size=price_tick,
        tick_value=price_tick,
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(
            type="flat",
            taker=_DEFAULT_TAKER_FEE,
            maker=_DEFAULT_MAKER_FEE,
        ),
        exchange_rule=ExchangeRule(
            symbol=symbol,
            price_tick=price_tick,
            qty_step=qty_step,
            min_qty=min_qty,
            min_notional=_DEFAULT_MIN_NOTIONAL,
            max_leverage=_DEFAULT_MAX_LEVERAGE,
        ),
        margin_model=MarginModel(
            maintenance_margin_rate=_DEFAULT_MMR,
            liquidation_fee_rate=_DEFAULT_LIQ_FEE,
        ),
    )


# ---------- 편의 함수 -------------------------------------------------------


def bybit_btcusdt_perp() -> Instrument:
    """BTCUSDT linear perp preset."""
    return bybit_linear_perp("BTCUSDT")


def bybit_ethusdt_perp() -> Instrument:
    """ETHUSDT linear perp preset."""
    return bybit_linear_perp("ETHUSDT")


def bybit_solusdt_perp() -> Instrument:
    """SOLUSDT linear perp preset."""
    return bybit_linear_perp("SOLUSDT")


def bybit_xrpusdt_perp() -> Instrument:
    """XRPUSDT linear perp preset."""
    return bybit_linear_perp("XRPUSDT")


def bybit_bnbusdt_perp() -> Instrument:
    """BNBUSDT linear perp preset."""
    return bybit_linear_perp("BNBUSDT")


def bybit_dogeusdt_perp() -> Instrument:
    """DOGEUSDT linear perp preset."""
    return bybit_linear_perp("DOGEUSDT")


def bybit_adausdt_perp() -> Instrument:
    """ADAUSDT linear perp preset."""
    return bybit_linear_perp("ADAUSDT")


def bybit_avaxusdt_perp() -> Instrument:
    """AVAXUSDT linear perp preset."""
    return bybit_linear_perp("AVAXUSDT")


def bybit_linkusdt_perp() -> Instrument:
    """LINKUSDT linear perp preset."""
    return bybit_linear_perp("LINKUSDT")


def bybit_tonusdt_perp() -> Instrument:
    """TONUSDT linear perp preset (conservative — DB 미수록, 실거래 전 갱신 권장)."""
    return bybit_linear_perp("TONUSDT")
