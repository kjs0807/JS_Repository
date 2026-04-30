"""PR 5 Ledger 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest

from backtester.core.snapshot import MarketSnapshot
from backtester.core.types import Fill, to_decimal
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.ledger import CashFlow, Ledger

UTC = timezone.utc
TS = datetime(2026, 1, 1, 14, tzinfo=UTC)


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
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _fill(
    side: str = "buy",
    price: str = "50000",
    size: str = "1",
    fee: str = "30",
    ts: datetime = TS,
    order_id: str = "ord_0",
) -> Fill:
    return Fill(
        timestamp=ts,
        symbol="BTCUSDT",
        price=Decimal(price),
        size=Decimal(size),
        side=side,  # type: ignore[arg-type]
        fee=Decimal(fee),
        fee_currency="USDT",
        order_id=order_id,
        intent_reason="entry" if side == "buy" else "exit",
    )


def _snap(close: str, ts: datetime = TS) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


# ---------- to_decimal 가드 -------------------------------------------------


def test_to_decimal_handles_all_supported_types() -> None:
    assert to_decimal(Decimal("1.5")) == Decimal("1.5")
    assert to_decimal(10) == Decimal("10")
    assert to_decimal("12.34") == Decimal("12.34")
    # float은 str() 경유로 정확성 유지
    assert to_decimal(0.1) == Decimal("0.1")


def test_to_decimal_rejects_unsupported() -> None:
    with pytest.raises(TypeError, match="Cannot convert"):
        to_decimal([1, 2])  # type: ignore[arg-type]


# ---------- 초기화 -----------------------------------------------------------


def test_ledger_init_with_int() -> None:
    ledger = Ledger(initial_equity=10000)
    assert ledger.cash == Decimal("10000")
    assert ledger.equity == Decimal("10000")
    assert ledger.realized_pnl == Decimal("0")
    assert ledger.unrealized_pnl == Decimal("0")
    assert ledger.positions == {}


def test_ledger_init_with_str_and_float() -> None:
    ledger = Ledger(initial_equity="50000.5")
    assert ledger.cash == Decimal("50000.5")

    ledger2 = Ledger(initial_equity=10000.0)
    assert ledger2.cash == Decimal("10000.0")


def test_ledger_init_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        Ledger(initial_equity=0)
    with pytest.raises(ValueError, match="must be > 0"):
        Ledger(initial_equity=-100)


# ---------- on_fill: buy ----------------------------------------------------


def test_ledger_on_fill_buy_updates_cash_and_position() -> None:
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(
        _fill(side="buy", price="50000", size="1", fee="30"),
        instrument=_btc(),
    )
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("1")
    assert p.avg_price == Decimal("50000")
    # cash = 100000 - (1*50000) - 30 = 49970
    assert ledger.cash == Decimal("49970")
    # equity = cash + size*avg + unrealized = 49970 + 50000 + 0 = 99970 (수수료만큼만 손실)
    assert ledger.equity == Decimal("99970")


def test_ledger_on_fill_buy_then_buy_weighted_avg() -> None:
    """두 번째 매수 시 avg_price 가중평균."""
    ledger = Ledger(initial_equity=200000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    ledger.on_fill(_fill(side="buy", price="60000", size="1", fee="0"), _btc())
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("2")
    # avg = (1*50000 + 1*60000) / 2 = 55000
    assert p.avg_price == Decimal("55000")


# ---------- on_fill: sell (long-only 청산) ----------------------------------


def test_ledger_on_fill_sell_partial_realizes_pnl() -> None:
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="2", fee="0"), _btc())
    ledger.on_fill(_fill(side="sell", price="51000", size="1", fee="0"), _btc())
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("1")
    assert p.avg_price == Decimal("50000")  # 부분 청산 시 avg 유지
    # realized = (51000 - 50000) * 1 = 1000
    assert p.realized_pnl == Decimal("1000")
    # cash = 100000 - 100000(buy) + 51000(sell) = 51000
    assert ledger.cash == Decimal("51000")


def test_ledger_on_fill_sell_full_resets_avg() -> None:
    """전량 매도 시 avg_price=0 + unrealized=0."""
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    ledger.on_fill(_fill(side="sell", price="55000", size="1", fee="0"), _btc())
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("0")
    assert p.avg_price == Decimal("0")
    assert p.unrealized_pnl == Decimal("0")
    assert p.realized_pnl == Decimal("5000")
    # cash = 100000 - 50000 + 55000 = 105000
    assert ledger.cash == Decimal("105000")
    # equity = cash + 0 + 0 = 105000
    assert ledger.equity == Decimal("105000")


def test_ledger_on_fill_sell_exceeds_position_raises() -> None:
    """Sizer 가드를 우회한 매도가 들어오면 Ledger가 invariant 위반으로 ValueError."""
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    with pytest.raises(ValueError, match="exceeds position size"):
        ledger.on_fill(_fill(side="sell", price="51000", size="2", fee="0"), _btc())


# ---------- on_fill: stale unrealized 방지 ----------------------------------


def test_ledger_on_fill_partial_sell_recomputes_unrealized() -> None:
    """부분 매도 후 unrealized_pnl은 fill.price 기준으로 재계산되어야 stale되지 않는다.

    시나리오: 2단위 매수 → on_market(60000)로 unrealized=20000 → 1단위 부분 매도(at 55000).
    fix 전: 남은 1단위에 대한 unrealized가 20000(2단위 기준)으로 stale.
    fix 후: fill.price(55000) 기준 (55000-50000)*1 = 5000.
    """
    ledger = Ledger(initial_equity=200000)
    ledger.on_fill(_fill(side="buy", price="50000", size="2", fee="0"), _btc())
    ledger.on_market({"BTCUSDT": _snap("60000")})
    p = ledger.positions["BTCUSDT"]
    assert p.unrealized_pnl == Decimal("20000")  # mark 60000 기준

    ledger.on_fill(_fill(side="sell", price="55000", size="1", fee="0"), _btc())
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("1")
    assert p.avg_price == Decimal("50000")
    # fill.price(55000) 기준으로 재계산: (55000-50000)*1 = 5000
    assert p.unrealized_pnl == Decimal("5000")
    # equity 정합성: cash=155000 + cost(50000) + unrealized(5000) = 210000
    # = 현실 환산: cash 155000 + 1 BTC at 55000 = 210000 ✓
    assert ledger.equity == Decimal("210000")


def test_ledger_on_fill_additional_buy_recomputes_unrealized() -> None:
    """기존 보유 + on_market 갱신 후 추가 매수해도 unrealized는 새 fill.price로 재계산."""
    ledger = Ledger(initial_equity=300000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    ledger.on_market({"BTCUSDT": _snap("60000")})
    assert ledger.positions["BTCUSDT"].unrealized_pnl == Decimal("10000")

    # 추가 매수 at 55000 — 새 avg = (1*50000 + 1*55000)/2 = 52500
    ledger.on_fill(_fill(side="buy", price="55000", size="1", fee="0"), _btc())
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("2")
    assert p.avg_price == Decimal("52500")
    # fill.price(55000) 기준: (55000-52500)*2 = 5000
    assert p.unrealized_pnl == Decimal("5000")


def test_ledger_on_fill_full_close_zeros_unrealized() -> None:
    """전량 매도 시 unrealized_pnl=0."""
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    ledger.on_market({"BTCUSDT": _snap("60000")})
    ledger.on_fill(_fill(side="sell", price="55000", size="1", fee="0"), _btc())
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("0")
    assert p.avg_price == Decimal("0")
    assert p.unrealized_pnl == Decimal("0")


# ---------- on_fill: to_decimal 가드 ---------------------------------------


def test_ledger_on_fill_converts_float_to_decimal() -> None:
    """fill.size/price/fee가 float이어도 to_decimal로 변환되어 처리.

    Fill dataclass의 타입힌트는 런타임 강제가 아니므로 외부에서 float이 들어오는
    상황(테스트·외부 라이브러리 연동 등)을 방어.
    """
    ledger = Ledger(initial_equity=100000)
    fill = Fill(
        timestamp=TS,
        symbol="BTCUSDT",
        price=50000.0,  # type: ignore[arg-type]
        size=1.0,  # type: ignore[arg-type]
        side="buy",
        fee=30.0,  # type: ignore[arg-type]
        fee_currency="USDT",
        order_id="ord_0",
        intent_reason="entry",
    )
    ledger.on_fill(fill, _btc())
    # 내부 상태는 모두 Decimal
    assert isinstance(ledger.cash, Decimal)
    assert ledger.cash == Decimal("49970")
    p = ledger.positions["BTCUSDT"]
    assert isinstance(p.size, Decimal)
    assert isinstance(p.avg_price, Decimal)
    # equity도 정확
    assert ledger.equity == Decimal("99970")


# ---------- on_market: mark-to-market ---------------------------------------


def test_ledger_on_market_updates_unrealized_pnl() -> None:
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    ledger.on_market({"BTCUSDT": _snap("51000")})
    p = ledger.positions["BTCUSDT"]
    # unrealized = (51000 - 50000) * 1 = 1000
    assert p.unrealized_pnl == Decimal("1000")
    # equity = cash(50000) + size*avg(50000) + unrealized(1000) = 101000
    assert ledger.equity == Decimal("101000")


def test_ledger_on_market_flat_position_zeros_unrealized() -> None:
    ledger = Ledger(initial_equity=100000)
    # flat 포지션 (Position 객체는 있지만 size=0 — buy/sell 사이클로 발생 가능)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    ledger.on_fill(_fill(side="sell", price="51000", size="1", fee="0"), _btc())
    ledger.on_market({"BTCUSDT": _snap("60000")})
    p = ledger.positions["BTCUSDT"]
    assert p.size == Decimal("0")
    assert p.unrealized_pnl == Decimal("0")  # flat이면 mark 무관


def test_ledger_on_market_appends_equity_history() -> None:
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())

    ts1 = TS
    ts2 = TS + timedelta(hours=1)
    ledger.on_market({"BTCUSDT": _snap("51000", ts=ts1)})
    ledger.on_market({"BTCUSDT": _snap("52000", ts=ts2)})

    curve = ledger.equity_curve()
    assert curve.height == 2
    assert curve["timestamp"][0] == ts1
    assert curve["timestamp"][1] == ts2


def test_ledger_on_market_skips_unknown_symbol() -> None:
    ledger = Ledger(initial_equity=100000)
    # ETHUSDT 포지션 없는데 snapshot이 들어와도 noop
    ledger.on_market({"ETHUSDT": _snap("3000")})
    assert "ETHUSDT" not in ledger.positions


def test_ledger_on_market_empty_dict_noop() -> None:
    ledger = Ledger(initial_equity=100000)
    ledger.on_market({})
    assert ledger.equity_curve().height == 0


# ---------- equity_curve ----------------------------------------------------


def test_ledger_equity_curve_empty_returns_typed_empty_df() -> None:
    ledger = Ledger(initial_equity=100000)
    df = ledger.equity_curve()
    assert df.height == 0
    assert df.schema["timestamp"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert df.schema["equity"] == pl.Float64()


# ---------- snapshot --------------------------------------------------------


def test_ledger_snapshot_str_serialization() -> None:
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="30"), _btc())
    ledger.on_market({"BTCUSDT": _snap("51000")})

    snap = ledger.snapshot()
    # 모든 Decimal 필드가 str
    assert isinstance(snap["equity"], str)
    assert isinstance(snap["cash"], str)
    assert isinstance(snap["realized_pnl"], str)
    assert isinstance(snap["unrealized_pnl"], str)
    assert "BTCUSDT" in snap["positions"]
    assert isinstance(snap["positions"]["BTCUSDT"]["size"], str)


def test_ledger_snapshot_excludes_flat_positions() -> None:
    """is_flat인 포지션은 positions dict에서 제외 (spec §3.13)."""
    ledger = Ledger(initial_equity=100000)
    ledger.on_fill(_fill(side="buy", price="50000", size="1", fee="0"), _btc())
    ledger.on_fill(_fill(side="sell", price="51000", size="1", fee="0"), _btc())
    snap = ledger.snapshot()
    # size=0 → positions에서 제외
    assert snap["positions"] == {}


# ---------- on_settle / on_expired ------------------------------------------


def test_ledger_on_settle_raises_phase_15() -> None:
    ledger = Ledger(initial_equity=100000)
    cf = CashFlow(timestamp=TS, symbol="BTCUSDT", amount=Decimal("100"), reason="funding")
    with pytest.raises(NotImplementedError, match="Phase 1.5"):
        ledger.on_settle(cf)


def test_ledger_on_expired_is_noop() -> None:
    """Phase 1: expire_pending이 항상 []이라 호출되어도 영향 없음."""
    ledger = Ledger(initial_equity=100000)
    cash_before = ledger.cash
    ledger.on_expired([])  # 빈 리스트 — Phase 1 정상 케이스
    ledger.on_expired(["dummy"])  # 더미 입력도 noop
    assert ledger.cash == cash_before
