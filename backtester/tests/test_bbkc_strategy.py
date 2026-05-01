"""PR 8 BBKCSqueezeStrategy 단위 테스트 (synthetic 데이터)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import polars as pl

from backtester.core.clock import ClockHelper
from backtester.core.context import BarsView, StrategyContext
from backtester.core.orders import ClosePosition, TargetUnits
from backtester.indicators.stateless.bb import BollingerBands
from backtester.indicators.stateless.kc import KeltnerChannel
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

UTC = timezone.utc


def _make_ohlcv(closes: list[float]) -> pl.DataFrame:
    n = len(closes)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(n)]
    opens = [c - 0.05 for c in closes]
    highs = [max(c + 0.1, o) for c, o in zip(closes, opens, strict=True)]
    lows = [min(c - 0.1, o) for c, o in zip(closes, opens, strict=True)]
    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * n,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))


def _make_ctx(
    df: pl.DataFrame,
    *,
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    has_position: bool = False,
) -> StrategyContext:
    """PR A: ``has_position`` 으로 ledger 시뮬. BBKC 가 ``ctx.has_position`` 을 읽어
    의사결정하므로, 진입/청산 흐름 테스트는 fill 직후 상태를 ``has_position=True`` 로
    재구성해 호출해야 한다.
    """
    from types import MappingProxyType

    from backtester.core.context import PortfolioView, PositionView

    timestamps = df["timestamp"].to_list()
    now = timestamps[-1] + timedelta(hours=1)  # 마지막 봉 마감 시각

    if has_position:
        portfolio = PortfolioView(
            equity=Decimal("100000"),
            cash=Decimal("50000"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            positions=MappingProxyType(
                {
                    symbol: PositionView(
                        symbol=symbol,
                        size=Decimal("1"),
                        avg_price=Decimal("100"),
                        realized_pnl=Decimal("0"),
                        unrealized_pnl=Decimal("0"),
                    ),
                }
            ),
        )
    else:
        portfolio = PortfolioView(
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            positions=MappingProxyType({}),
        )

    return StrategyContext(
        now=now,
        primary_symbol=symbol,
        primary_timeframe=tf,
        bars=BarsView(
            bars={symbol: {tf: df}},
            timestamp_index={symbol: {tf: {ts: i for i, ts in enumerate(timestamps)}}},
            timestamps={symbol: {tf: timestamps}},
            clock_helper=ClockHelper(),
            now=now,
        ),
        portfolio=portfolio,
    )


# ---------- 기본 동작 -------------------------------------------------------


def test_bbkc_required_indicators_returns_bb_and_kc() -> None:
    s = BBKCSqueezeStrategy(bb_period=20, kc_period=20, kc_use_ema=False)
    inds = s.required_indicators()
    assert len(inds) == 2
    assert isinstance(inds[0], BollingerBands)
    assert isinstance(inds[1], KeltnerChannel)


def test_bbkc_default_params_match_legacy() -> None:
    """기본 파라미터: bb_num_std=1.5, kc_multiplier=1.0, kc_atr_period=14, kc_use_ema=True."""
    s = BBKCSqueezeStrategy()
    assert s._bb.period == 20
    assert s._bb.num_std == 1.5
    assert s._kc.period == 20
    assert s._kc.multiplier == 1.0
    assert s._kc.atr_period == 14
    assert s._kc.use_ema is True


def test_bbkc_default_ema_smoke_does_not_crash() -> None:
    """legacy 호환 기본값(EMA 모드)으로 충분한 데이터에서 동작 보장."""
    closes = _generate_squeeze_then_breakout(n_squeeze=25, n_trend=15)
    df = _make_ohlcv(closes)
    s = BBKCSqueezeStrategy()  # legacy 기본값
    for n in range(2, df.height + 1):
        slice_df = df.slice(0, n)
        ctx = _make_ctx(slice_df)
        s.on_bar(ctx)  # raise 없이 통과면 OK


def test_bbkc_returns_empty_with_too_few_bars() -> None:
    s = BBKCSqueezeStrategy()
    df = _make_ohlcv([100.0])  # 1봉
    ctx = _make_ctx(df)
    assert s.on_bar(ctx) == []


def test_bbkc_returns_empty_during_warmup() -> None:
    """워밍업 미충족 (인덱스 < period)이면 indicator 값이 null이라 신호 없음."""
    s = BBKCSqueezeStrategy(bb_period=20, kc_period=20, kc_use_ema=False)
    df = _make_ohlcv([100.0 + i * 0.01 for i in range(15)])  # 15봉 — period 미달
    ctx = _make_ctx(df)
    assert s.on_bar(ctx) == []


# ---------- squeeze release 시나리오 ----------------------------------------


def _generate_squeeze_then_breakout(
    n_squeeze: int = 25, n_trend: int = 25
) -> list[float]:
    """초반 평탄(squeeze) → 후반 강한 상승추세(release with up momentum)."""
    closes: list[float] = []
    # 평탄 구간: 100 근방 작은 진폭
    for i in range(n_squeeze):
        closes.append(100.0 + ((i % 3) - 1) * 0.02)
    # 상승 구간: 큰 폭 +1.0/봉
    for _i in range(n_trend):
        closes.append(closes[-1] + 1.0)
    return closes


def test_bbkc_emits_buy_intent_on_release_with_up_momentum() -> None:
    """squeeze 후 상승 release 시 buy intent를 적어도 1번 발행."""
    s = BBKCSqueezeStrategy(bb_period=20, bb_num_std=2.0, kc_period=20, kc_multiplier=1.5)
    closes = _generate_squeeze_then_breakout(n_squeeze=25, n_trend=15)
    df = _make_ohlcv(closes)

    # 봉마다 strategy.on_bar를 단계적으로 호출 (slice 시뮬레이션)
    saw_buy = False
    for n in range(2, df.height + 1):
        slice_df = df.slice(0, n)
        ctx = _make_ctx(slice_df)
        intents = s.on_bar(ctx)
        for it in intents:
            if it.side == "buy" and isinstance(it.size_spec, TargetUnits):
                saw_buy = True
                assert it.reason == "bbkc_squeeze_release"
                break
        if saw_buy:
            break

    assert saw_buy, "BBKC strategy did not emit a buy intent on squeeze release"


def test_bbkc_emits_exit_after_close_below_mid() -> None:
    """진입 후 가격이 mid 하회 시 ClosePosition 매도 intent 발행.

    PR A: BBKC 가 ``ctx.has_position`` 을 읽으므로 buy intent 가 발행되면 다음
    호출부터 fill 시뮬레이션 (has_position=True). exit signal 후 다시 False.
    """
    s = BBKCSqueezeStrategy(bb_period=20, kc_period=20, kc_use_ema=False)
    closes = _generate_squeeze_then_breakout(n_squeeze=25, n_trend=15)
    # 후반에 급락 봉 추가 → close < mid 트리거
    closes.extend([closes[-1] - 5.0 for _ in range(10)])
    df = _make_ohlcv(closes)

    saw_buy = False
    saw_sell = False
    has_pos = False
    for n in range(2, df.height + 1):
        slice_df = df.slice(0, n)
        ctx = _make_ctx(slice_df, has_position=has_pos)
        intents = s.on_bar(ctx)
        for it in intents:
            if it.side == "buy":
                saw_buy = True
                has_pos = True  # 다음 봉부터 fill 시뮬
            elif it.side == "sell" and isinstance(it.size_spec, ClosePosition):
                saw_sell = True
                has_pos = False
                assert it.reason == "bbkc_close_below_mid"

    assert saw_buy and saw_sell


def test_bbkc_does_not_double_enter() -> None:
    """has_position True인 동안 추가 buy intent를 발행하지 않는다 (PR A: ctx.has_position 기반)."""
    s = BBKCSqueezeStrategy(bb_period=20, kc_period=20, kc_use_ema=False)
    closes = _generate_squeeze_then_breakout(n_squeeze=25, n_trend=20)
    df = _make_ohlcv(closes)

    buy_count = 0
    has_pos = False
    for n in range(2, df.height + 1):
        slice_df = df.slice(0, n)
        ctx = _make_ctx(slice_df, has_position=has_pos)
        intents = s.on_bar(ctx)
        for it in intents:
            if it.side == "buy":
                buy_count += 1
                has_pos = True  # fill 시뮬
    # exit 신호가 없으면 (가격이 mid 위 유지) 추가 buy 0
    assert buy_count == 1


# ---------- order_size 커스터마이즈 -----------------------------------------


def test_bbkc_uses_custom_order_size() -> None:
    s = BBKCSqueezeStrategy(
        bb_period=20, kc_period=20, order_size=Decimal("2.5")
    )
    closes = _generate_squeeze_then_breakout(n_squeeze=25, n_trend=10)
    df = _make_ohlcv(closes)

    found_size: Decimal | None = None
    for n in range(2, df.height + 1):
        slice_df = df.slice(0, n)
        ctx = _make_ctx(slice_df)
        intents = s.on_bar(ctx)
        for it in intents:
            if it.side == "buy" and isinstance(it.size_spec, TargetUnits):
                found_size = it.size_spec.units
                break
        if found_size is not None:
            break

    assert found_size == Decimal("2.5")


# ---------- 헬퍼 메서드 -----------------------------------------------------


def test_is_squeezed_returns_false_with_nulls() -> None:
    """워밍업 None 입력은 모두 False (안전 기본값)."""
    assert BBKCSqueezeStrategy._is_squeezed(None, None, None, None) is False
    assert BBKCSqueezeStrategy._is_squeezed(101.0, 99.0, None, 98.0) is False


def test_is_squeezed_true_when_bb_inside_kc() -> None:
    # bb_upper(100.5) < kc_upper(101) AND bb_lower(99.5) > kc_lower(99) → squeezed
    assert (
        BBKCSqueezeStrategy._is_squeezed(
            bb_upper=100.5, bb_lower=99.5, kc_upper=101.0, kc_lower=99.0
        )
        is True
    )


def test_is_squeezed_false_when_bb_breaches_kc() -> None:
    # bb_upper(102) > kc_upper(101) → 더 이상 squeeze 아님
    assert (
        BBKCSqueezeStrategy._is_squeezed(
            bb_upper=102.0, bb_lower=99.5, kc_upper=101.0, kc_lower=99.0
        )
        is False
    )
