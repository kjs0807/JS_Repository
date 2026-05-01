"""PR V — BybitInstrumentSpecFetcher + YAML preset loader + spec snapshot 회귀.

검증:
1. BybitInstrumentSpecFetcher 가 mock 응답을 정규화 (price_tick / qty_step / 등 Decimal).
2. fetch_linear_perp 가 cursor pagination + USDT 필터 + retCode 검증.
3. diff_against_preset 가 mismatch 항목 정확히 보고.
4. write_spec_snapshot 이 ``run_dir/instruments_snapshot.yaml`` 생성.
5. ``load_preset_yaml`` ``preset: crypto_perp`` short YAML → BacktestConfig.
6. ``load_preset_yaml`` preset 미지정 → full schema fallback.
7. ``load_preset_yaml`` unknown preset → ConfigError.
8. ``load_preset_yaml`` 누락 키 → ConfigError.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from backtester.core import BacktestConfig, load_preset_yaml
from backtester.core.errors import ConfigError, DataError
from backtester.instruments import (
    BybitInstrumentSpec,
    BybitInstrumentSpecFetcher,
    bybit_btcusdt_perp,
    diff_against_preset,
    write_spec_snapshot,
)

UTC = timezone.utc


# ---------- 1. 정규화 -------------------------------------------------------


def _mock_response_btc_only() -> dict[str, Any]:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "priceFilter": {"tickSize": "0.1"},
                    "lotSizeFilter": {
                        "qtyStep": "0.001",
                        "minOrderQty": "0.001",
                        "minNotionalValue": "5",
                    },
                    "leverageFilter": {"maxLeverage": "100"},
                }
            ],
            "nextPageCursor": "",
        },
    }


def test_fetcher_normalizes_btc_response() -> None:
    fetcher = BybitInstrumentSpecFetcher(
        http_fetcher=lambda url: _mock_response_btc_only()
    )
    out = fetcher.fetch_linear_perp(symbols=["BTCUSDT"])
    assert "BTCUSDT" in out
    spec = out["BTCUSDT"]
    assert spec.symbol == "BTCUSDT"
    assert spec.base_coin == "BTC"
    assert spec.quote_coin == "USDT"
    assert spec.price_tick == Decimal("0.1")
    assert spec.qty_step == Decimal("0.001")
    assert spec.min_qty == Decimal("0.001")
    assert spec.min_notional == Decimal("5")
    assert spec.max_leverage == Decimal("100")


# ---------- 2. retCode != 0 ------------------------------------------------


def test_fetcher_raises_on_bad_ret_code() -> None:
    fetcher = BybitInstrumentSpecFetcher(
        http_fetcher=lambda url: {"retCode": 10001, "retMsg": "fail"}
    )
    with pytest.raises(DataError, match="retCode"):
        fetcher.fetch_linear_perp(symbols=["BTCUSDT"])


def test_fetcher_filters_non_usdt() -> None:
    response: dict[str, Any] = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSD",  # inverse
                    "baseCoin": "BTC",
                    "priceFilter": {"tickSize": "0.5"},
                    "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1"},
                    "leverageFilter": {"maxLeverage": "100"},
                },
                {
                    "symbol": "BTCUSDT",
                    "baseCoin": "BTC",
                    "priceFilter": {"tickSize": "0.1"},
                    "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
                    "leverageFilter": {"maxLeverage": "100"},
                },
            ],
            "nextPageCursor": "",
        },
    }
    fetcher = BybitInstrumentSpecFetcher(http_fetcher=lambda url: response)
    out = fetcher.fetch_linear_perp()
    assert "BTCUSDT" in out
    assert "BTCUSD" not in out


# ---------- 3. cursor pagination -------------------------------------------


def test_fetcher_iterates_cursor_pages() -> None:
    pages = iter(
        [
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "baseCoin": "BTC",
                            "priceFilter": {"tickSize": "0.1"},
                            "lotSizeFilter": {
                                "qtyStep": "0.001",
                                "minOrderQty": "0.001",
                            },
                            "leverageFilter": {"maxLeverage": "100"},
                        }
                    ],
                    "nextPageCursor": "abc",
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "ETHUSDT",
                            "baseCoin": "ETH",
                            "priceFilter": {"tickSize": "0.01"},
                            "lotSizeFilter": {
                                "qtyStep": "0.01",
                                "minOrderQty": "0.01",
                            },
                            "leverageFilter": {"maxLeverage": "100"},
                        }
                    ],
                    "nextPageCursor": "",
                },
            },
        ]
    )
    fetcher = BybitInstrumentSpecFetcher(http_fetcher=lambda url: next(pages))
    out = fetcher.fetch_linear_perp()
    assert set(out.keys()) == {"BTCUSDT", "ETHUSDT"}


# ---------- 4. diff_against_preset ----------------------------------------


def test_diff_no_mismatch_when_preset_matches() -> None:
    """preset table 과 동일 spec 들 → diff 없음."""
    from backtester.instruments.presets import _BYBIT_LINEAR_PERP_TABLE

    fetched: dict[str, BybitInstrumentSpec] = {}
    for sym, entry in _BYBIT_LINEAR_PERP_TABLE.items():
        fetched[sym] = BybitInstrumentSpec(
            symbol=sym,
            base_coin=entry["base"],
            quote_coin="USDT",
            price_tick=Decimal(entry["price_tick"]),
            qty_step=Decimal(entry["qty_step"]),
            min_qty=Decimal(entry["min_qty"]),
            min_notional=Decimal("5"),
            max_leverage=Decimal("100"),
        )
    diffs = diff_against_preset(fetched)
    assert diffs == []


def test_diff_reports_price_tick_mismatch() -> None:
    """fetched price_tick 가 preset 과 다르면 PresetDiff 발견."""
    from backtester.instruments.presets import _BYBIT_LINEAR_PERP_TABLE

    fetched: dict[str, BybitInstrumentSpec] = {}
    for sym, entry in _BYBIT_LINEAR_PERP_TABLE.items():
        # BTCUSDT 만 일부러 tick 변경
        tick = (
            Decimal("0.5") if sym == "BTCUSDT" else Decimal(entry["price_tick"])
        )
        fetched[sym] = BybitInstrumentSpec(
            symbol=sym,
            base_coin=entry["base"],
            quote_coin="USDT",
            price_tick=tick,
            qty_step=Decimal(entry["qty_step"]),
            min_qty=Decimal(entry["min_qty"]),
            min_notional=Decimal("5"),
            max_leverage=Decimal("100"),
        )
    diffs = diff_against_preset(fetched)
    assert len(diffs) == 1
    assert diffs[0].symbol == "BTCUSDT"
    assert diffs[0].field_name == "price_tick"
    assert diffs[0].fetched_value == "0.5"


def test_diff_reports_missing_symbol() -> None:
    fetched: dict[str, BybitInstrumentSpec] = {}  # 빈 — 모든 preset symbol 누락
    diffs = diff_against_preset(fetched)
    assert all(d.field_name == "<missing>" for d in diffs)
    assert len(diffs) == 10  # 10 preset symbols


# ---------- 5. write_spec_snapshot ----------------------------------------


def test_write_spec_snapshot_creates_yaml(tmp_path: Path) -> None:
    specs = {
        "BTCUSDT": BybitInstrumentSpec(
            symbol="BTCUSDT",
            base_coin="BTC",
            quote_coin="USDT",
            price_tick=Decimal("0.1"),
            qty_step=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
            max_leverage=Decimal("100"),
            fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
        ),
    }
    out = write_spec_snapshot(tmp_path, specs)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "BTCUSDT" in text
    assert "tickSize" not in text  # 정규화된 키 사용
    assert "price_tick" in text
    assert "fetched_at" in text


# ---------- 6. load_preset_yaml — crypto_perp short -----------------------


def _write_short_yaml(path: Path, overrides: dict[str, Any] | None = None) -> Path:
    data: dict[str, Any] = {
        "preset": "crypto_perp",
        "run_id": "smoke",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "data_dir": str(path / "data"),
        "output_dir": str(path / "runs"),
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-05-01T00:00:00+00:00",
        "strategy_name": "bbkc_legacy_compat",
        "strategy_params": {"leverage": "3", "tp_pct": "0.06"},
    }
    if overrides:
        data.update(overrides)
    yaml_path = path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return yaml_path


def test_load_preset_yaml_crypto_perp(tmp_path: Path) -> None:
    yaml_path = _write_short_yaml(tmp_path)
    cfg = load_preset_yaml(yaml_path)
    assert isinstance(cfg, BacktestConfig)
    assert cfg.run_id == "smoke"
    assert cfg.primary_symbol == "BTCUSDT"
    assert cfg.primary_timeframe == "1h"
    assert cfg.initial_equity == Decimal("50000")
    assert cfg.allow_short is True
    assert cfg.strategy_name == "bbkc_legacy_compat"
    assert cfg.strategy_params == {"leverage": "3", "tp_pct": "0.06"}
    # preset instrument 자동
    assert cfg.instruments[0].symbol == "BTCUSDT"
    assert cfg.instruments[0].exchange_rule is not None
    assert cfg.instruments[0].margin_model is not None


def test_load_preset_yaml_with_funding(tmp_path: Path) -> None:
    yaml_path = _write_short_yaml(
        tmp_path,
        {
            "funding": {
                "interval_hours": 8,
                "rate_source": "constant",
                "constant_rate": "0.0001",
            }
        },
    )
    cfg = load_preset_yaml(yaml_path)
    assert "BTCUSDT" in cfg.funding_models
    assert cfg.funding_models["BTCUSDT"].interval_hours == 8


def test_load_preset_yaml_initial_equity_override(tmp_path: Path) -> None:
    yaml_path = _write_short_yaml(tmp_path, {"initial_equity": "100000"})
    cfg = load_preset_yaml(yaml_path)
    assert cfg.initial_equity == Decimal("100000")


# ---------- 7. load_preset_yaml fallback / errors -------------------------


def test_load_preset_yaml_full_schema_fallback(tmp_path: Path) -> None:
    """preset 키 미지정 → BacktestConfig.from_dict 경로."""
    cfg_orig = BacktestConfig(
        run_id="full_schema",
        data_source=__import__(
            "backtester.core.config", fromlist=["DataSourceConfig"]
        ).DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[bybit_btcusdt_perp()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    yaml_path = tmp_path / "full.yaml"
    cfg_orig.to_yaml(yaml_path)
    restored = load_preset_yaml(yaml_path)
    assert restored.run_id == "full_schema"
    assert restored.initial_equity == Decimal("100000")


def test_load_preset_yaml_unknown_preset_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("preset: bogus_preset\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown preset"):
        load_preset_yaml(yaml_path)


def test_load_preset_yaml_missing_required_keys(tmp_path: Path) -> None:
    yaml_path = tmp_path / "missing.yaml"
    yaml_path.write_text(
        "preset: crypto_perp\nrun_id: only_run_id\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="missing required keys"):
        load_preset_yaml(yaml_path)


def test_load_preset_yaml_root_not_mapping(tmp_path: Path) -> None:
    yaml_path = tmp_path / "list.yaml"
    yaml_path.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_preset_yaml(yaml_path)
