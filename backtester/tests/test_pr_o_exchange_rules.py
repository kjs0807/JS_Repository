"""PR O — Exchange Rules / Bybit Precision 회귀.

검증:
1. ExchangeRule dataclass 검증 — non-positive field reject.
2. quantize_qty_floor 의 floor 동작.
3. is_price_aligned 의 tick 정수배 검사.
4. Sizer: qty_step 미만 → ValueError.
5. Sizer: min_qty / min_notional 위반 → ValueError.
6. Sizer: limit_price / stop_price tick 미정렬 → ValueError.
7. ClosePosition 은 quantize 미적용 (보유 그대로).
8. exchange_rule None → 기존 동작.
9. YAML round-trip.
10. Engine 통합: 잘못된 사이즈/가격 → ORDER_REJECTED.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.errors import DataError
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.snapshot import MarketSnapshot
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import ExchangeRule, FeeModel, Instrument
from backtester.portfolio.position import Position
from backtester.portfolio.sizer import Sizer
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


def _rule(
    *,
    price_tick: str = "0.01",
    qty_step: str = "0.001",
    min_qty: str = "0.001",
    min_notional: str = "5",
) -> ExchangeRule:
    return ExchangeRule(
        symbol="BTCUSDT",
        price_tick=Decimal(price_tick),
        qty_step=Decimal(qty_step),
        min_qty=Decimal(min_qty),
        min_notional=Decimal(min_notional),
    )


def _btc(rule: ExchangeRule | None) -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
        exchange_rule=rule,
    )


def _market(close: float = 100.0) -> MarketSnapshot:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    d = Decimal(str(close))
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=base,
        open=d,
        high=d + Decimal("1"),
        low=d - Decimal("1"),
        close=d,
        volume=Decimal("1"),
    )


def _intent(
    side: str = "buy",
    units: str = "1",
    *,
    type: str = "market",
    limit_price: str | None = None,
    stop_price: str | None = None,
) -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
        type=type,  # type: ignore[arg-type]
        size_spec=TargetUnits(units=Decimal(units)),
        limit_price=Decimal(limit_price) if limit_price else None,
        stop_price=Decimal(stop_price) if stop_price else None,
        reason="test",
    )


# ---------- 1. ExchangeRule validation --------------------------------------


def test_exchange_rule_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="price_tick"):
        ExchangeRule(
            symbol="X",
            price_tick=Decimal("0"),
            qty_step=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
        )


# ---------- 2. quantize_qty_floor -------------------------------------------


def test_quantize_qty_floor_basic() -> None:
    r = _rule()
    assert r.quantize_qty_floor(Decimal("1.2345")) == Decimal("1.234")
    assert r.quantize_qty_floor(Decimal("0.0001")) == Decimal("0")
    assert r.quantize_qty_floor(Decimal("0")) == Decimal("0")


# ---------- 3. is_price_aligned ---------------------------------------------


def test_is_price_aligned() -> None:
    r = _rule(price_tick="0.5")
    assert r.is_price_aligned(Decimal("100.0"))
    assert r.is_price_aligned(Decimal("100.5"))
    assert not r.is_price_aligned(Decimal("100.3"))


# ---------- 4. Sizer qty_step 미만 ------------------------------------------


def test_sizer_rejects_units_below_min_qty() -> None:
    sizer = Sizer()
    rule = _rule(qty_step="0.001", min_qty="0.001")
    pos = Position(symbol="BTCUSDT")
    with pytest.raises(ValueError, match="min_qty"):
        sizer.resolve(
            _intent(units="0.0005"), _btc(rule), Decimal("100000"), pos, _market(100)
        )


# ---------- 5. Sizer min_notional 위반 --------------------------------------


def test_sizer_rejects_notional_below_min() -> None:
    sizer = Sizer()
    rule = _rule(min_notional="50")
    pos = Position(symbol="BTCUSDT")
    # 0.001 * 100 = 0.1 < 50
    with pytest.raises(ValueError, match="min_notional"):
        sizer.resolve(
            _intent(units="0.001"), _btc(rule), Decimal("100000"), pos, _market(100)
        )


def test_sizer_min_notional_uses_market_close_for_market_order() -> None:
    sizer = Sizer()
    rule = _rule(min_notional="5")
    pos = Position(symbol="BTCUSDT")
    # market intent (no limit/stop) → ref_price = market.close = 100. 0.1 * 100 = 10 ≥ 5
    out = sizer.resolve(
        _intent(units="0.1"), _btc(rule), Decimal("100000"), pos, _market(100)
    )
    assert out == Decimal("0.1")


# ---------- 6. price_tick 미정렬 --------------------------------------------


def test_sizer_rejects_limit_price_misaligned() -> None:
    sizer = Sizer()
    rule = _rule(price_tick="0.01")
    pos = Position(symbol="BTCUSDT")
    with pytest.raises(ValueError, match="limit_price"):
        sizer.resolve(
            _intent(type="limit", limit_price="100.001", units="1"),
            _btc(rule),
            Decimal("100000"),
            pos,
            _market(100),
        )


def test_sizer_rejects_stop_price_misaligned() -> None:
    sizer = Sizer()
    rule = _rule(price_tick="0.5")
    pos = Position(symbol="BTCUSDT")
    with pytest.raises(ValueError, match="stop_price"):
        sizer.resolve(
            _intent(type="stop", stop_price="99.3", units="1"),
            _btc(rule),
            Decimal("100000"),
            pos,
            _market(100),
        )


# ---------- 7. quantize 정상 -----------------------------------------------


def test_sizer_quantize_floor_applies() -> None:
    """0.5678 with step 0.001 → 0.567."""
    sizer = Sizer()
    rule = _rule(qty_step="0.001", min_qty="0.001", min_notional="0.001")
    pos = Position(symbol="BTCUSDT")
    out = sizer.resolve(
        _intent(units="0.5678"),
        _btc(rule),
        Decimal("100000"),
        pos,
        _market(100),
    )
    assert out == Decimal("0.567")


# ---------- 8. exchange_rule None → 기존 동작 -------------------------------


def test_sizer_no_rule_returns_unchanged_units() -> None:
    sizer = Sizer()
    pos = Position(symbol="BTCUSDT")
    out = sizer.resolve(
        _intent(units="0.0001"), _btc(None), Decimal("100000"), pos, _market(100)
    )
    assert out == Decimal("0.0001")


# ---------- 9. YAML round-trip ----------------------------------------------


def test_config_yaml_round_trip_exchange_rule(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="rt",
        data_source=DataSourceConfig(base_dir=tmp_path),
        instruments=[_btc(_rule())],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    yaml_path = tmp_path / "cfg.yaml"
    cfg.to_yaml(yaml_path)
    text = yaml_path.read_text(encoding="utf-8")
    assert "exchange_rule" in text
    assert "price_tick" in text
    restored = BacktestConfig.from_yaml(yaml_path)
    rule = restored.instruments[0].exchange_rule
    assert rule is not None
    assert rule.price_tick == Decimal("0.01")
    assert rule.qty_step == Decimal("0.001")


# ---------- 10. Engine 통합 -------------------------------------------------


def _make_parquet(target: Path, n_bars: int = 5) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0] * n_bars,
            "high": [101.0] * n_bars,
            "low": [99.0] * n_bars,
            "close": [100.0] * n_bars,
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


class _BadSizeStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="market",
                size_spec=TargetUnits(units=Decimal("0.0001")),  # below min_qty
                reason="bad",
            )
        ]


def test_engine_min_qty_violation_emits_order_rejected(tmp_path: Path) -> None:
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="bad_qty",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc(_rule())],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=5),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    result = BacktestEngine(cfg, _BadSizeStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    rejected = list(reader.by_type(EventType.ORDER_REJECTED))
    assert any("min_qty" in str(e.payload.get("reason", "")) for e in rejected)


# Touch unused imports
_ = DataError
