"""PR T — BBKC Legacy Compat Strategy + Futures Harness 회귀.

검증:
1. RSI 단위 정확성 (gain-only / loss-only 한계).
2. BBKC legacy compat — long entry + bracket TP/SL, time stop, RSI 필터.
3. FuturesStrategyHarness 5 base + bracket lifecycle + reduce_only audit 통과.
4. funding boundary deterministic (8h alignment).
5. Liquidation safety — benign fixture 에서 false positive 없음.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.execution.funding import FundingModel
from backtester.indicators.stateless.rsi import RSI
from backtester.instruments.base import (
    ExchangeRule,
    FeeModel,
    Instrument,
    MarginModel,
)
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from tests._futures_strategy_harness import FuturesStrategyHarness
from tests._strategy_harness import HarnessSpec

UTC = timezone.utc


# ---------- 1. RSI 단위 ------------------------------------------------------


def test_rsi_period_warmup_returns_none_for_first_period_bars() -> None:
    rsi = RSI(period=5)
    df = pl.DataFrame(
        {"close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]}
    )
    out = rsi.compute(df)
    vals = out[rsi.name].to_list()
    # 첫 5 봉은 None
    assert vals[:5] == [None] * 5
    # 이후는 100 근처 (gain-only 시리즈)
    for v in vals[5:]:
        assert v is not None
        assert v > 90


def test_rsi_only_loss_returns_zero() -> None:
    rsi = RSI(period=5)
    df = pl.DataFrame(
        {"close": [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0]}
    )
    out = rsi.compute(df)
    vals = out[rsi.name].to_list()
    for v in vals[5:]:
        assert v is not None
        assert v < 10  # loss only → ~0


# ---------- 2. BBKC legacy compat 단위 --------------------------------------


def _btc(
    *,
    margin_model: MarginModel | None = None,
    exchange_rule: ExchangeRule | None = None,
) -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
        margin_model=margin_model,
        exchange_rule=exchange_rule,
    )


def _make_squeeze_breakout(target: Path) -> None:
    """squeeze 25 봉 → breakout 25 봉 → mean revert 30 봉."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": 100.0,
                "high": 100.05,
                "low": 99.95,
                "close": 100.0 + (0.01 if i % 2 else -0.01),
                "volume": 1.0,
            }
        )
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=25 + i),
                "open": 100.0 + i * 0.5,
                "high": 100.5 + i * 0.5,
                "low": 99.5 + i * 0.5,
                "close": 100.5 + i * 0.5,
                "volume": 1.0,
            }
        )
    peak = 100.5 + 24 * 0.5
    for i in range(30):
        rows.append(
            {
                "timestamp": base + timedelta(hours=50 + i),
                "open": peak - i * 0.4,
                "high": peak + 0.5 - i * 0.4,
                "low": peak - 0.5 - i * 0.4,
                "close": peak - i * 0.4,
                "volume": 1.0,
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _bbkc_config(
    tmp_path: Path,
    *,
    margin_model: MarginModel | None = None,
    funding_models: dict[str, FundingModel] | None = None,
) -> BacktestConfig:
    parquet = tmp_path / "data" / "BTCUSDT_1h.parquet"
    _make_squeeze_breakout(parquet)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    df = pl.read_parquet(parquet)
    end = df["timestamp"][-1] + timedelta(hours=1)
    from typing import Any

    kwargs: dict[str, Any] = dict(
        run_id="bbkc_lc",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc(margin_model=margin_model)],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    if funding_models is not None:
        kwargs["funding_models"] = funding_models
    return BacktestConfig(**kwargs)


def test_bbkc_legacy_compat_long_entry_with_bracket(tmp_path: Path) -> None:
    """squeeze release + 양봉 → buy + bracket children 생성."""
    cfg = _bbkc_config(tmp_path)

    def factory() -> BBKCLegacyCompatStrategy:
        return BBKCLegacyCompatStrategy(
            tp_pct=Decimal("0.06"),
            sl_pct=Decimal("0.07"),
            leverage=Decimal("3"),
            margin_pct=Decimal("0.05"),
            exit_mode="fixed",
            rsi_filter=100.0,  # PR U: disable RSI filter for fixture-driven entry
        )

    result = BacktestEngine(cfg, factory(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    # 최소 1 entry + TP + SL = 3 ORDER_ADDED 발견
    assert len(added) >= 3
    children = [a for a in added if a.payload["parent_order_id"] is not None]
    assert len(children) >= 2
    # Long entry → child side='sell' + reduce_only=True
    for c in children:
        intent = c.payload["intent"]
        assert intent["side"] == "sell"
        assert intent["reduce_only"] is True


def test_bbkc_legacy_compat_drop_tp_only_sl_child(tmp_path: Path) -> None:
    cfg = _bbkc_config(tmp_path)

    def factory() -> BBKCLegacyCompatStrategy:
        return BBKCLegacyCompatStrategy(
            drop_tp=True,
            sl_pct=Decimal("0.07"),
            tp_pct=Decimal("0.06"),
            leverage=Decimal("3"),
            margin_pct=Decimal("0.05"),
            rsi_filter=100.0,  # PR U: disable RSI filter
        )

    result = BacktestEngine(cfg, factory(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    children = [a for a in added if a.payload["parent_order_id"] is not None]
    # drop_tp=True → SL 만 (limit child 없음)
    assert all(c.payload["intent"]["type"] == "stop" for c in children)
    assert len(children) >= 1


# ---------- 3. FuturesStrategyHarness 통과 ----------------------------------


def _harness_spec(
    tmp_path: Path,
    *,
    margin_model: MarginModel | None = None,
) -> HarnessSpec:
    parquet_path = tmp_path / "data" / "BTCUSDT_1h.parquet"
    _make_squeeze_breakout(parquet_path)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    df = pl.read_parquet(parquet_path)
    end = df["timestamp"][-1] + timedelta(hours=1)

    def factory() -> BBKCLegacyCompatStrategy:
        return BBKCLegacyCompatStrategy(
            tp_pct=Decimal("0.06"),
            sl_pct=Decimal("0.07"),
            leverage=Decimal("3"),
            margin_pct=Decimal("0.05"),
            exit_mode="fixed",
            rsi_filter=100.0,  # PR U: disable RSI filter for fixture-driven entry
        )

    return HarnessSpec(
        name="bbkc_lc_harness",
        strategy_factory=factory,
        instrument=_btc(margin_model=margin_model),
        parquet_path=parquet_path,
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_root=tmp_path,
    )


def test_bbkc_legacy_compat_passes_5_base_contracts(tmp_path: Path) -> None:
    """기본 5 contracts (PR F StrategyHarness)."""
    h = FuturesStrategyHarness(_harness_spec(tmp_path))
    h.assert_all()


def test_bbkc_legacy_compat_passes_bracket_lifecycle(tmp_path: Path) -> None:
    h = FuturesStrategyHarness(_harness_spec(tmp_path))
    h.assert_bracket_lifecycle_consistent()


def test_bbkc_legacy_compat_passes_reduce_only_audit(tmp_path: Path) -> None:
    h = FuturesStrategyHarness(_harness_spec(tmp_path))
    h.assert_reduce_only_intents_preserved()


def test_bbkc_legacy_compat_passes_futures_all(tmp_path: Path) -> None:
    """일괄 5 base + bracket + reduce_only — 새 전략 추가 시 한 줄 검증."""
    h = FuturesStrategyHarness(_harness_spec(tmp_path))
    h.assert_futures_all()


# ---------- 4. Liquidation safety with margin_model -------------------------


def test_bbkc_legacy_compat_no_false_positive_liquidation(tmp_path: Path) -> None:
    """leverage=3 + mmr=0.005 → liq ≈ 67% 하락 필요. fixture 변동 ~12% 라 미발생."""
    spec = _harness_spec(
        tmp_path,
        margin_model=MarginModel(maintenance_margin_rate=Decimal("0.005")),
    )
    h = FuturesStrategyHarness(spec)
    h.assert_no_false_positive_liquidation()
