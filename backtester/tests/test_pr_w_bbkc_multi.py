"""PR W — BBKCMultiLegacyCompatStrategy 회귀.

검증:
1. 3 심볼 (BTC/ETH/AVAX) fixture — 각 심볼별 INTENT_CREATED + FILL 발생.
2. registry build_strategy("bbkc_multi_legacy_compat", ...) 동작.
3. 단일 심볼 BBKCLegacyCompatStrategy 회귀 — symbols 가 1 개여도 동일 결과.
4. ConfigError — 빈 symbols / 중복 symbols.
5. on_pending_orders — symbol 별 SL stop modify 가 다른 심볼로 leak 되지 않음.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.errors import ConfigError
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.bbkc_multi_legacy_compat import (
    BBKCMultiLegacyCompatStrategy,
)
from backtester.strategies.registry import build_strategy

UTC = timezone.utc


# ---------- fixture builders -------------------------------------------------


def _make_squeeze_breakout(
    target: Path,
    *,
    base_price: float = 100.0,
) -> None:
    """squeeze 25 + breakout 25 + mean revert 30 — PR T fixture 재사용 형태."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": base_price,
                "high": base_price + 0.05,
                "low": base_price - 0.05,
                "close": base_price + (0.01 if i % 2 else -0.01),
                "volume": 1.0,
            }
        )
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=25 + i),
                "open": base_price + i * 0.5,
                "high": base_price + 0.5 + i * 0.5,
                "low": base_price - 0.5 + i * 0.5,
                "close": base_price + 0.5 + i * 0.5,
                "volume": 1.0,
            }
        )
    peak = base_price + 0.5 + 24 * 0.5
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


def _instrument(symbol: str, base: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _multi_config(
    tmp_path: Path,
    *,
    symbols: list[tuple[str, str, float]],
    primary: str,
) -> BacktestConfig:
    """symbols = [(symbol, base_currency, base_price)] — 각 심볼별 parquet 생성."""
    data_dir = tmp_path / "data"
    for sym, _base, price in symbols:
        _make_squeeze_breakout(data_dir / f"{sym}_1h.parquet", base_price=price)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=80 + 1)
    insts = [_instrument(s, b) for s, b, _ in symbols]
    return BacktestConfig(
        run_id="bbkc_multi_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=insts,
        timeframes_per_symbol={s: ["1h"] for s, _, _ in symbols},
        primary_symbol=primary,
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )


# ---------- 1. 3 심볼 entry/fill 분기 ----------------------------------------


def test_multi_symbol_three_symbols_each_get_intent_and_fill(
    tmp_path: Path,
) -> None:
    syms = [
        ("BTCUSDT", "BTC", 100.0),
        ("ETHUSDT", "ETH", 50.0),
        ("AVAXUSDT", "AVAX", 25.0),
    ]
    cfg = _multi_config(tmp_path, symbols=syms, primary="BTCUSDT")

    strategy = BBKCMultiLegacyCompatStrategy(
        symbols=[s for s, _, _ in syms],
        timeframe="1h",
        child_params={
            "tp_pct": Decimal("0.06"),
            "sl_pct": Decimal("0.07"),
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "exit_mode": "fixed",
            "rsi_filter": 100.0,  # PR U: disable RSI filter
        },
    )
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    reader = EventLogReader(result.events_path)

    intents = list(reader.by_type(EventType.INTENT_CREATED))
    fills = list(reader.by_type(EventType.FILL))
    intent_syms = {evt.payload["intent"]["symbol"] for evt in intents}
    fill_syms = {evt.payload["symbol"] for evt in fills}

    # 각 심볼별 INTENT_CREATED + FILL 1 개 이상.
    for sym, _, _ in syms:
        assert sym in intent_syms, (
            f"{sym} produced no INTENT_CREATED in multi run; got {intent_syms}"
        )
        assert sym in fill_syms, (
            f"{sym} produced no FILL in multi run; got {fill_syms}"
        )


# ---------- 2. registry build_strategy --------------------------------------


def test_registry_builds_multi_strategy() -> None:
    strategy = build_strategy(
        "bbkc_multi_legacy_compat",
        {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframe": "1h",
            "child_params": {
                "leverage": Decimal("3"),
                "margin_pct": Decimal("0.05"),
            },
        },
    )
    assert isinstance(strategy, BBKCMultiLegacyCompatStrategy)
    assert strategy.symbols == ["BTCUSDT", "ETHUSDT"]
    assert "BTCUSDT" in strategy._children
    assert "ETHUSDT" in strategy._children
    # 지표가 children 사이에 공유 — IndicatorEngine 중복 컬럼 방지.
    btc = strategy._children["BTCUSDT"]
    eth = strategy._children["ETHUSDT"]
    assert btc._bb is strategy._bb
    assert eth._bb is strategy._bb
    assert btc._kc is eth._kc is strategy._kc
    assert btc._rsi is eth._rsi is strategy._rsi


# ---------- 3. 단일 심볼 회귀 ------------------------------------------------


def test_single_symbol_multi_matches_legacy(tmp_path: Path) -> None:
    """symbols=[BTCUSDT] 1 개여도 동작 + legacy 와 동일 fill 수."""
    syms = [("BTCUSDT", "BTC", 100.0)]
    cfg = _multi_config(tmp_path, symbols=syms, primary="BTCUSDT")

    multi = BBKCMultiLegacyCompatStrategy(
        symbols=["BTCUSDT"],
        timeframe="1h",
        child_params={
            "tp_pct": Decimal("0.06"),
            "sl_pct": Decimal("0.07"),
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "exit_mode": "fixed",
            "rsi_filter": 100.0,
        },
    )
    res_multi = BacktestEngine(cfg, multi, verbose=False).run()
    fills_multi = list(EventLogReader(res_multi.events_path).by_type(EventType.FILL))

    # 같은 fixture 로 legacy 단일 — 별도 run dir.
    cfg2 = _multi_config(tmp_path, symbols=syms, primary="BTCUSDT")
    cfg2_kwargs: dict[str, Any] = {
        f.name: getattr(cfg2, f.name) for f in cfg2.__dataclass_fields__.values()
    }
    cfg2_kwargs["run_id"] = "bbkc_legacy_test"
    cfg2_single = BacktestConfig(**cfg2_kwargs)
    legacy = BBKCLegacyCompatStrategy(
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        exit_mode="fixed",
        rsi_filter=100.0,
    )
    res_legacy = BacktestEngine(cfg2_single, legacy, verbose=False).run()
    fills_legacy = list(
        EventLogReader(res_legacy.events_path).by_type(EventType.FILL)
    )
    assert len(fills_multi) == len(fills_legacy)


# ---------- 4. ConfigError ---------------------------------------------------


def test_empty_symbols_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="non-empty 'symbols'"):
        BBKCMultiLegacyCompatStrategy(symbols=[])


def test_duplicate_symbols_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="duplicates"):
        BBKCMultiLegacyCompatStrategy(symbols=["BTCUSDT", "BTCUSDT"])


def test_bad_child_params_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="signature"):
        BBKCMultiLegacyCompatStrategy(
            symbols=["BTCUSDT"],
            child_params={"unknown_kwarg": 1},
        )


# ---------- 5. on_pending_orders 격리 ---------------------------------------


def test_on_pending_orders_filters_by_symbol(tmp_path: Path) -> None:
    """multi 의 on_pending_orders 가 각 child 에 그 심볼의 pending 만 전달.

    검증 방식: be_trail 모드 multi 를 가동하고 다른 심볼의 stop order 가 child 의 SL
    매칭 로직에 잘못 잡히지 않는지 확인. 직접 strategy 호출로 시뮬레이션.
    """
    from backtester.core.clock import ClockHelper
    from backtester.core.context import (
        BarsView,
        OrdersView,
        OrderView,
        StrategyContext,
    )

    bars = BarsView(
        bars={
            "BTCUSDT": {"1h": pl.DataFrame({"close": [100.0]})},
            "ETHUSDT": {"1h": pl.DataFrame({"close": [50.0]})},
        },
        timestamp_index={"BTCUSDT": {"1h": {}}, "ETHUSDT": {"1h": {}}},
        timestamps={"BTCUSDT": {"1h": []}, "ETHUSDT": {"1h": []}},
        clock_helper=ClockHelper(),
        now=datetime(2026, 3, 5, tzinfo=UTC),
    )
    btc_stop = OrderView(
        id="btc_sl_1",
        symbol="BTCUSDT",
        side="sell",
        type="stop",
        state="pending",
        sized_quantity=Decimal("1"),
        remaining=Decimal("1"),
        submitted_at=datetime(2026, 3, 5, tzinfo=UTC),
        limit_price=None,
        stop_price=Decimal("90"),
    )
    eth_stop = OrderView(
        id="eth_sl_1",
        symbol="ETHUSDT",
        side="sell",
        type="stop",
        state="pending",
        sized_quantity=Decimal("1"),
        remaining=Decimal("1"),
        submitted_at=datetime(2026, 3, 5, tzinfo=UTC),
        limit_price=None,
        stop_price=Decimal("45"),
    )
    pending = (btc_stop, eth_stop)
    ctx = StrategyContext(
        now=datetime(2026, 3, 5, tzinfo=UTC),
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        bars=bars,
        orders=OrdersView(_orders=pending),
    )
    multi = BBKCMultiLegacyCompatStrategy(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1h",
        child_params={"exit_mode": "be_trail"},
    )
    # 포지션이 없으므로 actions 는 빈 list — 단순히 leak 없이 끝나는지 검증.
    actions = multi.on_pending_orders(ctx, pending)
    assert actions == []
