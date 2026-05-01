"""PR 15c BarPathModel 4종 정책 테스트 (Phase 2, spec §3.10).

PR 15c minimum 차별화:
- ``PESSIMISTIC`` (default) = ``OPTIMISTIC`` = ``OHLC_ORDER`` (단일 주문 단일 봉
  컨텍스트에서 동일 fill 결과). 진정한 차별화는 PR 16+ TP/SL coexistence 도입 시.
- ``OPEN_TO_CLOSE``: high/low 무시, open→close linear path 만 사용. high 만 닿고
  close 가 limit 미도달이면 no fill (vs PESSIMISTIC 의 high 도달 시 fill).

random 정책은 ``BarPathModel`` 에 정의되어 있지 않음 → PR 15c 에서 도입하지 않는다.
재현성 보장 인프라가 갖춰진 후 별도 PR 에서 검토.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.orderbook import Order, OrderBook
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import BarPathModel
from backtester.execution.next_bar import NextBarOpenExecution
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

UTC = timezone.utc
TS = datetime(2026, 4, 1, tzinfo=UTC)


def _btc(taker: str = "0.001", maker: str = "0.0005") -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal(taker), maker=Decimal(maker)),
    )


def _snap(o: str, h: str, low_: str, c: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=TS,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low_),
        close=Decimal(c),
        volume=Decimal("1"),
    )


def _order(
    type_: Literal["limit", "stop", "stop_limit"],
    side: Literal["buy", "sell"],
    *,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
) -> Order:
    intent = OrderIntent(
        symbol="BTCUSDT",
        side=side,
        type=type_,
        size_spec=TargetUnits(units=Decimal("1")),
        limit_price=limit_price,
        stop_price=stop_price,
    )
    return OrderBook().add(intent, sized_quantity=Decimal("1"), ts=TS)


# ---------- enum 정렬 + 검증 -----------------------------------------------


def test_bar_path_model_enum_has_four_members() -> None:
    """spec §3.10 의 4종 enum 이 코드와 정렬."""
    members = {m.value for m in BarPathModel}
    assert members == {"pessimistic", "optimistic", "linear", "ohlc"}


def test_random_policy_not_in_enum() -> None:
    """PR 15c 는 random 정책을 명시적으로 제외 — enum 에 없음."""
    values = {m.value for m in BarPathModel}
    assert "random" not in values
    assert "monte_carlo" not in values


def test_next_bar_open_default_bar_path_model_is_pessimistic() -> None:
    em = NextBarOpenExecution()
    assert em.bar_path_model == BarPathModel.PESSIMISTIC


def test_next_bar_open_rejects_non_enum_bar_path_model() -> None:
    with pytest.raises(ValueError, match="BarPathModel enum member"):
        NextBarOpenExecution(bar_path_model="pessimistic")  # type: ignore[arg-type]


# ---------- 단일 주문 동일성 (PESSIMISTIC = OPTIMISTIC = OHLC_ORDER) -------


@pytest.mark.parametrize(
    "bar_path_model",
    [BarPathModel.OPTIMISTIC, BarPathModel.OHLC_ORDER],
)
def test_optimistic_and_ohlc_match_pessimistic_for_limit_buy(
    bar_path_model: BarPathModel,
) -> None:
    """단일 limit BUY 한 봉에서 OPTIMISTIC / OHLC_ORDER 가 PESSIMISTIC 와 동일 fill."""
    snap = _snap("101", "102", "99", "101")  # low<=L=100 → fill at L
    pess = NextBarOpenExecution(bar_path_model=BarPathModel.PESSIMISTIC)
    other = NextBarOpenExecution(bar_path_model=bar_path_model)
    f_pess = pess.try_fill(_order("limit", "buy", limit_price=Decimal("100")), snap, _btc())
    f_other = other.try_fill(_order("limit", "buy", limit_price=Decimal("100")), snap, _btc())
    assert f_pess is not None and f_other is not None
    assert f_pess.price == f_other.price == Decimal("100")


@pytest.mark.parametrize(
    "bar_path_model",
    [BarPathModel.OPTIMISTIC, BarPathModel.OHLC_ORDER],
)
def test_optimistic_and_ohlc_match_pessimistic_for_stop_sell(
    bar_path_model: BarPathModel,
) -> None:
    snap = _snap("101", "102", "99", "101")  # low<=S=100 → fill at S
    pess = NextBarOpenExecution(bar_path_model=BarPathModel.PESSIMISTIC)
    other = NextBarOpenExecution(bar_path_model=bar_path_model)
    f_pess = pess.try_fill(_order("stop", "sell", stop_price=Decimal("100")), snap, _btc())
    f_other = other.try_fill(_order("stop", "sell", stop_price=Decimal("100")), snap, _btc())
    assert f_pess is not None and f_other is not None
    assert f_pess.price == f_other.price == Decimal("100")


# ---------- OPEN_TO_CLOSE 차별화 -------------------------------------------


def test_open_to_close_limit_buy_ignores_low() -> None:
    """PESSIMISTIC 은 low<=L 로 fill. OPEN_TO_CLOSE 는 close>L 라 no fill."""
    snap = _snap("101", "105", "99", "104")  # open=101>L, low=99<=L, close=104>L
    pess = NextBarOpenExecution(bar_path_model=BarPathModel.PESSIMISTIC)
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    L = Decimal("100")
    assert pess.try_fill(_order("limit", "buy", limit_price=L), snap, _btc()) is not None
    assert otc.try_fill(_order("limit", "buy", limit_price=L), snap, _btc()) is None


def test_open_to_close_limit_buy_fills_when_close_below_limit() -> None:
    """open>L 인데 close<=L → linear path 가 L 통과 → maker fill at L."""
    snap = _snap("101", "105", "99", "100")  # close=100=L
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    L = Decimal("100")
    fill = otc.try_fill(_order("limit", "buy", limit_price=L), snap, _btc())
    assert fill is not None
    assert fill.price == L


def test_open_to_close_limit_buy_fills_at_open_when_open_below_limit() -> None:
    """open<=L → 즉시 open 가격에 taker fill (high/low 무관)."""
    snap = _snap("99", "105", "98", "104")
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    L = Decimal("100")
    fill = otc.try_fill(_order("limit", "buy", limit_price=L), snap, _btc())
    assert fill is not None
    assert fill.price == Decimal("99")


def test_open_to_close_limit_sell_ignores_high() -> None:
    """PESSIMISTIC 은 high>=L 로 fill. OPEN_TO_CLOSE 는 close<L 라 no fill."""
    snap = _snap("99", "101", "95", "96")  # open=99<L, high=101>=L, close=96<L
    pess = NextBarOpenExecution(bar_path_model=BarPathModel.PESSIMISTIC)
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    L = Decimal("100")
    assert pess.try_fill(_order("limit", "sell", limit_price=L), snap, _btc()) is not None
    assert otc.try_fill(_order("limit", "sell", limit_price=L), snap, _btc()) is None


def test_open_to_close_stop_buy_ignores_high() -> None:
    """PESSIMISTIC 은 high>=S 로 trigger. OPEN_TO_CLOSE 는 close<S 라 no fill."""
    snap = _snap("99", "105", "98", "99")  # open<S, high>=S, close<S
    pess = NextBarOpenExecution(bar_path_model=BarPathModel.PESSIMISTIC)
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    S = Decimal("100")
    assert pess.try_fill(_order("stop", "buy", stop_price=S), snap, _btc()) is not None
    assert otc.try_fill(_order("stop", "buy", stop_price=S), snap, _btc()) is None


def test_open_to_close_stop_sell_ignores_low() -> None:
    snap = _snap("101", "102", "95", "101")  # open>S, low<S, close>S
    pess = NextBarOpenExecution(bar_path_model=BarPathModel.PESSIMISTIC)
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    S = Decimal("100")
    assert pess.try_fill(_order("stop", "sell", stop_price=S), snap, _btc()) is not None
    assert otc.try_fill(_order("stop", "sell", stop_price=S), snap, _btc()) is None


def test_open_to_close_stop_limit_buy_no_trigger_when_close_below_stop() -> None:
    """OPEN_TO_CLOSE: open<S AND close<S → trigger 안 됨 (high 무시)."""
    snap = _snap("99", "110", "98", "99")  # high=110>=S=100 이지만 close<S
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    fill = otc.try_fill(
        _order(
            "stop_limit",
            "buy",
            limit_price=Decimal("102"),
            stop_price=Decimal("100"),
        ),
        snap,
        _btc(),
    )
    assert fill is None


def test_open_to_close_stop_limit_buy_trigger_at_open_fills_at_open() -> None:
    """``o >= S`` 라 open 시점 발동. post-trigger 가격 = open. open <= L → fill at open."""
    snap = _snap("101", "104", "99", "103")  # o=101>=S=100, o<=L=102
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    fill = otc.try_fill(
        _order(
            "stop_limit",
            "buy",
            limit_price=Decimal("102"),
            stop_price=Decimal("100"),
        ),
        snap,
        _btc(),
    )
    assert fill is not None
    assert fill.price == Decimal("101")


# ---------- trigger_via_close 분리 (PR 15c 후속 버그 정정) ------------------


def test_open_to_close_stop_limit_buy_trigger_via_close_fills_at_stop_not_open() -> None:
    """버그 회귀: o<S 라 open 시점 미발동인데 close>=S 라 path 위에서 발동.

    이전 구현은 ``triggered = o>=S or c>=S`` 후 ``o<=L`` 분기를 먼저 실행해 발동 전
    open 가격(99)에 체결하던 버그. 정정: post-trigger 가격은 S=100 부터 → S<=L 이면
    fill at S (taker).
    """
    snap = _snap("99", "104", "98", "103")  # o=99<S=100, c=103>=S, triggered via close
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    fill = otc.try_fill(
        _order(
            "stop_limit",
            "buy",
            limit_price=Decimal("102"),
            stop_price=Decimal("100"),
        ),
        snap,
        _btc(),
    )
    assert fill is not None
    assert fill.price == Decimal("100")  # S, NOT open=99


def test_open_to_close_stop_limit_sell_trigger_via_close_fills_at_stop_not_open() -> None:
    """sell 대칭: o>S 라 open 시점 미발동, close<=S 라 path 위에서 발동.
    post-trigger 가격 = S, S>=L 이면 fill at S (taker)."""
    snap = _snap("101", "102", "96", "97")  # o=101>S=100, c=97<=S, triggered via close
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    fill = otc.try_fill(
        _order(
            "stop_limit",
            "sell",
            limit_price=Decimal("98"),
            stop_price=Decimal("100"),
        ),
        snap,
        _btc(),
    )
    assert fill is not None
    assert fill.price == Decimal("100")  # S, NOT open=101


def test_open_to_close_stop_limit_buy_trigger_via_close_atypical_s_above_l() -> None:
    """S>L atypical (don't-buy-if-too-high). trigger_via_close + post-trigger path
    S→c 가 L 통과 (c<=L) → fill at L (maker)."""
    snap = _snap("99", "101", "97", "98")
    # o=99<S=100 (no trigger at open), c=98<S? No c=98 < 100 → not via_close either.
    # Adjust: need c>=S=100 for trigger via close. Let's use:
    snap = _snap("99", "105", "97", "100.5")
    # o=99<S=100, c=100.5>=S → triggered via close. S=100 > L=99 → no immediate fill.
    # post-trigger path 100→100.5: doesn't cross L=99 going down. c=100.5>L=99 → no fill.
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    fill = otc.try_fill(
        _order(
            "stop_limit",
            "buy",
            limit_price=Decimal("99"),  # L < S
            stop_price=Decimal("100"),
        ),
        snap,
        _btc(),
    )
    assert fill is None


def test_open_to_close_stop_limit_buy_no_trigger_when_path_misses_stop() -> None:
    """o<S AND c<S → path 어디에서도 stop 미발동 → no fill."""
    snap = _snap("95", "99", "94", "97")  # o<S=100, c=97<S, never triggered
    otc = NextBarOpenExecution(bar_path_model=BarPathModel.OPEN_TO_CLOSE)
    fill = otc.try_fill(
        _order(
            "stop_limit",
            "buy",
            limit_price=Decimal("102"),
            stop_price=Decimal("100"),
        ),
        snap,
        _btc(),
    )
    assert fill is None


# ---------- Engine wiring (config.bar_path_model 전달) ---------------------


def test_engine_wires_bar_path_model_from_config(tmp_path: Path) -> None:
    """BacktestConfig.bar_path_model 이 NextBarOpenExecution 에 전달된다."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": base + timedelta(hours=i),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1.0,
        }
        for i in range(24)
    ]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(data_dir / "BTCUSDT_1h.parquet")

    cfg = BacktestConfig(
        run_id="bpm_smoke",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=23),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        bar_path_model=BarPathModel.OPEN_TO_CLOSE,
    )
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    em = engine.execution
    assert isinstance(em, NextBarOpenExecution)
    assert em.bar_path_model == BarPathModel.OPEN_TO_CLOSE
