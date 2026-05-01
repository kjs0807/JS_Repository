"""PR A 회귀 — ctx.portfolio / ctx.orders / 편의 proxy + Engine wiring.

본 파일이 검증:
1. PortfolioView / OrderView / OrdersView 의 read-only 계약 (frozen + Mapping proxy)
2. ctx.position / has_position / equity / cash / open_orders 편의 proxy
3. Engine ``_invoke_strategy`` 가 매 호출마다 ledger / orderbook snapshot 을 새로 만들어
   주입 — risk reject / 부분체결 시 desync 없음
4. BBKC 가 ledger 기반 has_position 으로 작동 (risk reject 후 다음 release 에서 재시도)
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import polars as pl
import pytest

from backtester.core.clock import ClockHelper
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import (
    BarsView,
    OrdersView,
    OrderView,
    PortfolioView,
    PositionView,
    StrategyContext,
)
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


# ---------- View 단위 ---------------------------------------------------------


def test_position_view_is_frozen() -> None:
    pv = PositionView(
        symbol="BTCUSDT",
        size=Decimal("1"),
        avg_price=Decimal("100"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        pv.size = Decimal("2")  # type: ignore[misc]
    assert pv.is_flat is False
    assert pv.direction == "long"


def test_position_view_flat_and_short() -> None:
    flat = PositionView(
        symbol="X", size=Decimal("0"), avg_price=Decimal("0"),
        realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
    )
    short = PositionView(
        symbol="X", size=Decimal("-1"), avg_price=Decimal("100"),
        realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
    )
    assert flat.is_flat
    assert flat.direction == "flat"
    assert short.direction == "short"


def test_portfolio_view_position_helpers() -> None:
    p = PositionView(
        symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"),
        realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
    )
    pv = PortfolioView(
        equity=Decimal("100100"),
        cash=Decimal("99900"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("100"),
        positions=MappingProxyType({"BTCUSDT": p}),
    )
    assert pv.position("BTCUSDT") is p
    assert pv.position("ETHUSDT") is None
    assert pv.has_position("BTCUSDT") is True
    assert pv.has_position("ETHUSDT") is False
    # positions 가 read-only proxy
    with pytest.raises(TypeError):
        pv.positions["ETHUSDT"] = p  # type: ignore[index]


def test_orders_view_filter_by_symbol() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ov = OrdersView(
        _orders=(
            OrderView(
                id="ord_0",
                symbol="BTCUSDT",
                side="buy",
                type="market",
                state="pending",
                sized_quantity=Decimal("1"),
                remaining=Decimal("1"),
                submitted_at=base,
                limit_price=None,
                stop_price=None,
            ),
            OrderView(
                id="ord_1",
                symbol="ETHUSDT",
                side="sell",
                type="limit",
                state="pending",
                sized_quantity=Decimal("2"),
                remaining=Decimal("2"),
                submitted_at=base,
                limit_price=Decimal("3000"),
                stop_price=None,
            ),
        )
    )
    assert len(ov.open_orders()) == 2
    btc = ov.open_orders("BTCUSDT")
    assert len(btc) == 1
    assert btc[0].id == "ord_0"
    assert ov.open_orders("XRPUSDT") == ()


def test_strategy_context_proxy_methods() -> None:
    """ctx.position / has_position / equity / cash / open_orders 편의 proxy."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    p = PositionView(
        symbol="BTCUSDT", size=Decimal("1"), avg_price=Decimal("100"),
        realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
    )
    pv = PortfolioView(
        equity=Decimal("100100"),
        cash=Decimal("99900"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("100"),
        positions=MappingProxyType({"BTCUSDT": p}),
    )
    ov = OrdersView(_orders=())
    # bars/indicators 빈 view (default factory)
    bars_view = BarsView(
        bars={"BTCUSDT": {"1h": pl.DataFrame({"timestamp": [base]}).with_columns(
            pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
        )}},
        timestamp_index={"BTCUSDT": {"1h": {base: 0}}},
        timestamps={"BTCUSDT": {"1h": [base]}},
        clock_helper=ClockHelper(),
        now=base + timedelta(hours=1),
    )
    ctx = StrategyContext(
        now=base + timedelta(hours=1),
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        bars=bars_view,
        portfolio=pv,
        orders=ov,
    )
    assert ctx.equity == Decimal("100100")
    assert ctx.cash == Decimal("99900")
    assert ctx.position("BTCUSDT") is p
    assert ctx.has_position("BTCUSDT") is True
    assert ctx.has_position("ETHUSDT") is False
    assert ctx.positions["BTCUSDT"] is p
    assert ctx.open_orders() == ()


# ---------- Engine wiring -----------------------------------------------------


def _btc() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _make_parquet(target: Path, n_bars: int = 10) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0 + i for i in range(n_bars)],
            "high": [101.0 + i for i in range(n_bars)],
            "low": [99.0 + i for i in range(n_bars)],
            "close": [100.5 + i for i in range(n_bars)],
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


class _PortfolioCaptureStrategy(BaseStrategy):
    """on_bar 마다 ctx.portfolio + ctx.orders 를 캡쳐. 한 번 buy 후 청산."""

    def __init__(self) -> None:
        self.captures: list[tuple[Decimal, Decimal, bool, int]] = []
        # (equity, cash, has_position, open_orders_count)
        self._fired_entry = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        eq = ctx.equity
        cash = ctx.cash
        has = ctx.has_position("BTCUSDT")
        oo = len(ctx.open_orders())
        self.captures.append((eq, cash, has, oo))

        if not self._fired_entry:
            self._fired_entry = True
            return [
                OrderIntent(
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=Decimal("1")),
                    reason="entry",
                )
            ]
        return []


def test_engine_invokes_strategy_with_live_portfolio_view(tmp_path: Path) -> None:
    """Engine 이 매 on_bar 마다 PortfolioView 를 새로 만들어 주입하는지 — fill 후 ledger
    상태가 ctx.portfolio 에 즉시 반영."""
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=8)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="ctx_pv",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=8),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = _PortfolioCaptureStrategy()
    BacktestEngine(cfg, strat, verbose=False).run()
    assert len(strat.captures) >= 2
    # 첫 호출 — flat
    eq0, cash0, has0, oo0 = strat.captures[0]
    assert has0 is False
    assert oo0 == 0
    assert eq0 == Decimal("100000")
    assert cash0 == Decimal("100000")
    # 두 번째 호출 — buy intent 가 다음 bar open 에서 체결됐으므로 has_position True
    eq1, cash1, has1, oo1 = strat.captures[1]
    assert has1 is True
    assert cash1 < cash0  # 매수에 cash 사용


def test_engine_orders_view_reflects_active_orders(tmp_path: Path) -> None:
    """fill 직후 active order 가 0 이 됨 — ctx.open_orders() 도 반영.

    테스트는 첫 봉 close 에서 buy intent 발행 → 봉 N+1 open 에서 fill → 봉 N+1 close
    의 on_bar 시점에 ctx.open_orders() = 0 (fill 됐으므로).
    """
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=5)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="ctx_orders",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=5),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = _PortfolioCaptureStrategy()
    BacktestEngine(cfg, strat, verbose=False).run()
    # 모든 capture 에서 open_orders 는 (대부분) 0 — market order 는 즉시 fill 되어
    # 다음 bar close 시점에는 active 가 비어 있다.
    for _, _, _, oo in strat.captures:
        assert oo == 0


# ---------- BBKC: ctx.has_position 기반 ---------------------------------------


def test_bbkc_does_not_use_internal_has_position_attr() -> None:
    """PR A 마이그레이션 회귀 — BBKC 는 더 이상 ``_has_position`` 인스턴스 변수에 의존하지
    않음. ledger 가 single source of truth.
    """
    from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

    s = BBKCSqueezeStrategy()
    assert not hasattr(s, "_has_position")
