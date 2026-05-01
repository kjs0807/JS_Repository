"""BybitInstrumentSpecFetcher (Phase 2.5 후속) — Bybit instruments-info REST.

Bybit ``GET /v5/market/instruments-info?category=linear`` 응답을 backtester 의
``BybitInstrumentSpec`` (DataSource-friendly dataclass) 로 정규화하고, ``_BYBIT_LINEAR
_PERP_TABLE`` 과 diff 한다.

목적:
1. 정밀 리서치 / 실거래 전 preset table 을 최신 거래소 값으로 갱신.
2. 같은 backtest run 시점의 spec snapshot 을 ``run_dir`` 에 저장 → replay 재현성.

본 모듈은 ``backtester.data.bybit_source`` 와 동일한 stdlib ``urllib`` 기반 — 외부 SDK
의존성 추가 안 함. 테스트는 ``http_fetcher`` 인자로 mock 주입.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from backtester.core.errors import DataError

BYBIT_REST_BASE = "https://api.bybit.com"
BYBIT_INSTRUMENTS_PATH = "/v5/market/instruments-info"


@dataclass(frozen=True)
class BybitInstrumentSpec:
    """Bybit linear perp 한 심볼의 정규화 spec.

    REST 응답의 ``priceFilter`` / ``lotSizeFilter`` / ``leverageFilter`` 를
    ``Decimal`` 로 변환해 backtester 가 그대로 ``ExchangeRule`` 로 활용 가능하게 한다.
    """

    symbol: str
    base_coin: str
    quote_coin: str
    price_tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_leverage: Decimal
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True)
class PresetDiff:
    """``_BYBIT_LINEAR_PERP_TABLE`` 항목과 fetched 사양 차이."""

    symbol: str
    field_name: str
    preset_value: str
    fetched_value: str


HttpFetcher = Callable[[str], dict[str, Any]]


def _default_http_fetcher(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as e:
        raise DataError(f"Bybit instruments-info request failed: {url}: {e}") from e
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise DataError(f"Bybit returned non-JSON: {body[:200]!r}") from e
    return parsed  # type: ignore[no-any-return]


class BybitInstrumentSpecFetcher:
    """Bybit linear perp instruments-info 페치 + 정규화."""

    def __init__(
        self,
        *,
        base_url: str = BYBIT_REST_BASE,
        http_fetcher: HttpFetcher | None = None,
    ) -> None:
        self.base_url = base_url
        self._http_fetcher = http_fetcher or _default_http_fetcher

    def fetch_linear_perp(
        self,
        symbols: list[str] | None = None,
    ) -> dict[str, BybitInstrumentSpec]:
        """``category=linear`` instruments-info 페치. ``symbols`` 미지정 시 USDT 전체.

        반환: ``{symbol: BybitInstrumentSpec}``. retCode != 0 또는 누락 필드는
        ``DataError``.

        PR V 후속 최적화: ``symbols=[single]`` 일 때는 Bybit ``symbol=`` 파라미터
        직접 전송 → 단일 페이지 호출. 다중 symbol 또는 None 일 때는 cursor pagination
        으로 전체 리스트 받고 로컬 필터.
        """
        # Single-symbol fast path
        if symbols is not None and len(symbols) == 1:
            sym_only = symbols[0]
            if sym_only.endswith("USDT"):
                return self._fetch_single_symbol(sym_only)
            return {}
        return self._fetch_paginated(symbols)

    def _fetch_single_symbol(self, symbol: str) -> dict[str, BybitInstrumentSpec]:
        params = {"category": "linear", "symbol": symbol}
        url = f"{self.base_url}{BYBIT_INSTRUMENTS_PATH}?{urllib.parse.urlencode(params)}"
        payload = self._http_fetcher(url)
        if payload.get("retCode") != 0:
            raise DataError(
                f"Bybit instruments-info retCode={payload.get('retCode')}, "
                f"retMsg={payload.get('retMsg')!r}"
            )
        result = payload.get("result") or {}
        out: dict[str, BybitInstrumentSpec] = {}
        for entry in result.get("list") or []:
            sym = entry.get("symbol")
            if sym == symbol:
                out[sym] = self._normalize(entry)
        return out

    def _fetch_paginated(
        self, symbols: list[str] | None
    ) -> dict[str, BybitInstrumentSpec]:
        out: dict[str, BybitInstrumentSpec] = {}
        cursor: str | None = None
        max_pages = 50
        for _ in range(max_pages):
            params: dict[str, str] = {"category": "linear"}
            if cursor:
                params["cursor"] = cursor
            url = f"{self.base_url}{BYBIT_INSTRUMENTS_PATH}?{urllib.parse.urlencode(params)}"
            payload = self._http_fetcher(url)
            if payload.get("retCode") != 0:
                raise DataError(
                    f"Bybit instruments-info retCode={payload.get('retCode')}, "
                    f"retMsg={payload.get('retMsg')!r}"
                )
            result = payload.get("result") or {}
            for entry in result.get("list") or []:
                sym = entry.get("symbol")
                if not sym or not sym.endswith("USDT"):
                    continue
                if symbols is not None and sym not in symbols:
                    continue
                out[sym] = self._normalize(entry)
            cursor = result.get("nextPageCursor")
            if not cursor:
                break
        else:
            raise DataError(
                f"Bybit instruments-info exceeded max_pages={max_pages}"
            )
        return out

    @staticmethod
    def _normalize(entry: dict[str, Any]) -> BybitInstrumentSpec:
        sym = entry["symbol"]
        try:
            base = entry["baseCoin"]
            quote = entry.get("quoteCoin", "USDT")
            price = entry.get("priceFilter") or {}
            lot = entry.get("lotSizeFilter") or {}
            lev = entry.get("leverageFilter") or {}
            return BybitInstrumentSpec(
                symbol=sym,
                base_coin=base,
                quote_coin=quote,
                price_tick=Decimal(str(price.get("tickSize", "0"))),
                qty_step=Decimal(str(lot.get("qtyStep", "0"))),
                min_qty=Decimal(str(lot.get("minOrderQty", "0"))),
                min_notional=Decimal(
                    str(lot.get("minNotionalValue", lot.get("minOrderAmt", "0")))
                ),
                max_leverage=Decimal(str(lev.get("maxLeverage", "0"))),
            )
        except (KeyError, ValueError) as e:
            raise DataError(
                f"Bybit instruments-info: unexpected schema for {sym!r}: {e}"
            ) from e


# ---------- preset diff -----------------------------------------------------


def diff_against_preset(
    fetched: dict[str, BybitInstrumentSpec],
) -> list[PresetDiff]:
    """``_BYBIT_LINEAR_PERP_TABLE`` 와 fetched 의 mismatch 항목 리스트.

    검사 필드: ``price_tick`` / ``qty_step`` / ``min_qty``. ``min_notional`` /
    ``max_leverage`` 는 거래소가 None / 별도 tier 일 수 있어 본 함수에서 제외 (필요 시
    별도 검사).
    """
    from backtester.instruments.presets import _BYBIT_LINEAR_PERP_TABLE

    out: list[PresetDiff] = []
    for sym, table_entry in _BYBIT_LINEAR_PERP_TABLE.items():
        spec = fetched.get(sym)
        if spec is None:
            out.append(
                PresetDiff(
                    symbol=sym,
                    field_name="<missing>",
                    preset_value=str(table_entry),
                    fetched_value="",
                )
            )
            continue
        for field_name, attr in (
            ("price_tick", "price_tick"),
            ("qty_step", "qty_step"),
            ("min_qty", "min_qty"),
        ):
            preset_val = Decimal(table_entry[field_name])
            fetched_val = getattr(spec, attr)
            if preset_val != fetched_val:
                out.append(
                    PresetDiff(
                        symbol=sym,
                        field_name=field_name,
                        preset_value=str(preset_val),
                        fetched_value=str(fetched_val),
                    )
                )
    return out


# ---------- run-time spec snapshot -----------------------------------------


def write_spec_snapshot(run_dir: Path, specs: dict[str, BybitInstrumentSpec]) -> Path:
    """run 시점 사용된 spec 들을 ``run_dir/instruments_snapshot.yaml`` 로 보존.

    rebuild / replay 시 ``BacktestConfig.from_yaml`` + 이 snapshot 으로 정확한 거래소
    spec 재현 가능. preset 만으로는 거래소 갱신 시점 정합성이 깨질 수 있어 별도 보존.
    """
    import yaml

    target = run_dir / "instruments_snapshot.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        sym: {
            "symbol": spec.symbol,
            "base_coin": spec.base_coin,
            "quote_coin": spec.quote_coin,
            "price_tick": str(spec.price_tick),
            "qty_step": str(spec.qty_step),
            "min_qty": str(spec.min_qty),
            "min_notional": str(spec.min_notional),
            "max_leverage": str(spec.max_leverage),
            "fetched_at": spec.fetched_at.isoformat(),
        }
        for sym, spec in specs.items()
    }
    with open(target, "w", encoding="utf-8") as fp:
        yaml.safe_dump(data, fp, sort_keys=False, default_flow_style=False)
    return target
