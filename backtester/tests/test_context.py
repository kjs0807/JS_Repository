"""PR 4 BarsView + StrategyContext 테스트 (spec §20 PR 4 acceptance).

Lookahead 차단: now 시점에 진행 중인 봉은 view에 보이지 않는다 (spec §2.4).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from backtester.core.clock import ClockHelper
from backtester.core.context import BarsView, IndicatorsView, StrategyContext

UTC = timezone.utc


def _make_ohlcv_df(timestamps: list[datetime]) -> pl.DataFrame:
    n = len(timestamps)
    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": [50000.0 + i for i in range(n)],
            "high": [50100.0 + i for i in range(n)],
            "low": [49900.0 + i for i in range(n)],
            "close": [50050.0 + i for i in range(n)],
            "volume": [1.0] * n,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))


def _build_view(
    timestamps: list[datetime],
    *,
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    now: datetime,
) -> BarsView:
    df = _make_ohlcv_df(timestamps)
    idx_map = {ts: i for i, ts in enumerate(timestamps)}
    return BarsView(
        bars={symbol: {tf: df}},
        timestamp_index={symbol: {tf: idx_map}},
        timestamps={symbol: {tf: timestamps}},
        clock_helper=ClockHelper(),
        now=now,
    )


# ---------- BarsView lookahead 차단 -----------------------------------------


def test_barsview_includes_just_closed_bar() -> None:
    """now가 봉 마감과 정확히 일치하면 그 봉은 포함된다 (spec §2.3)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    # now=04:00 (=03:00 봉 마감 시각) → 03:00 봉까지 포함
    view = _build_view(timestamps, now=base + timedelta(hours=4))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 4  # 00:00, 01:00, 02:00, 03:00
    assert df["timestamp"].to_list()[-1] == base + timedelta(hours=3)


def test_barsview_excludes_in_progress_bar() -> None:
    """진행 중인 봉(아직 마감 안 됨)은 노출되지 않는다 (lookahead 차단)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    # now=04:30 — 03:00 봉은 04:00에 마감, 04:00 봉은 05:00에 마감 예정 (제외)
    view = _build_view(timestamps, now=base + timedelta(hours=4, minutes=30))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 4  # 04:00 봉 미포함
    last_ts = df["timestamp"].to_list()[-1]
    assert last_ts == base + timedelta(hours=3)


def test_barsview_now_before_any_bar_returns_empty() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    # now가 첫 봉 시작보다 이전 → 마감된 봉 없음 → 빈 df
    view = _build_view(timestamps, now=base - timedelta(hours=1))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 0


def test_barsview_now_at_first_close_includes_only_first() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(3)]
    view = _build_view(timestamps, now=base + timedelta(hours=1))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 1
    assert df["timestamp"][0] == base


def test_barsview_now_far_future_includes_all() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    view = _build_view(timestamps, now=base + timedelta(days=10))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 5  # 모두 포함


# ---------- O(1) idx_map vs bisect 폴백 -------------------------------------


def test_barsview_uses_idx_map_for_exact_match() -> None:
    """정확 매칭이면 dict.get 즉시 결과 (O(1)). bisect 호출 안 됨."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    # now=03:00 boundary → last_closed=02:00 → idx_map에 있음
    view = _build_view(timestamps, now=base + timedelta(hours=3))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 3  # 00:00, 01:00, 02:00


def test_barsview_bisect_fallback_for_missing_bar() -> None:
    """갭으로 인해 last_closed가 idx_map에 없으면 bisect로 직전 봉 찾기.

    데이터: 13:00, 14:00, 16:00 (15:00 갭)
    now=16:30 → 이론적 last_closed=15:00 (정렬 grid). idx_map에 없음.
    bisect_right([13,14,16], 15) = 2 → end_idx=1 → slice [13, 14].
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [
        base + timedelta(hours=13),
        base + timedelta(hours=14),
        base + timedelta(hours=16),
    ]
    view = _build_view(timestamps, now=base + timedelta(hours=16, minutes=30))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 2
    assert df["timestamp"].to_list() == [
        base + timedelta(hours=13),
        base + timedelta(hours=14),
    ]


def test_barsview_bisect_before_all_returns_empty() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [
        base + timedelta(hours=13),
        base + timedelta(hours=14),
    ]
    # now=10:00 → last_closed=09:00 (이론) → idx_map 없음, bisect=0, end_idx=-1 → empty
    view = _build_view(timestamps, now=base + timedelta(hours=10))
    df = view["BTCUSDT"]["1h"]
    assert df.height == 0


# ---------- KeyError 처리 ---------------------------------------------------


def test_barsview_unknown_symbol_raises() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    view = _build_view([base], now=base + timedelta(hours=1))
    with pytest.raises(KeyError, match="Unknown symbol"):
        view["ETHUSDT"]


def test_barsview_unknown_timeframe_raises() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    view = _build_view([base], now=base + timedelta(hours=1))
    with pytest.raises(KeyError, match="Unknown timeframe"):
        view["BTCUSDT"]["4h"]


# ---------- StrategyContext --------------------------------------------------


def test_strategy_context_is_frozen() -> None:
    import dataclasses

    base = datetime(2026, 1, 1, tzinfo=UTC)
    view = _build_view([base], now=base + timedelta(hours=1))
    ctx = StrategyContext(
        now=base + timedelta(hours=1),
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        bars=view,
    )
    assert ctx.now == base + timedelta(hours=1)
    assert ctx.primary_symbol == "BTCUSDT"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.now = base  # type: ignore[misc]


def test_strategy_context_default_indicators_is_empty_view() -> None:
    """직접 생성 fixture 호환: indicators 기본값은 빈 IndicatorsView."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    view = _build_view([base], now=base + timedelta(hours=1))
    ctx = StrategyContext(
        now=base + timedelta(hours=1),
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        bars=view,
    )
    assert isinstance(ctx.indicators, IndicatorsView)
    assert ctx.indicators.has("BTCUSDT", "1h") is False


# ---------- IndicatorsView ---------------------------------------------------


def _build_indicators_view(
    timestamps: list[datetime],
    *,
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    now: datetime,
) -> IndicatorsView:
    n = len(timestamps)
    ind_df = pl.DataFrame(
        {
            "timestamp": timestamps,
            "sma_5": [float(i) + 0.5 for i in range(n)],
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )
    idx_map = {ts: i for i, ts in enumerate(timestamps)}
    return IndicatorsView(
        cache={(symbol, tf): ind_df},
        timestamp_index={symbol: {tf: idx_map}},
        timestamps={symbol: {tf: timestamps}},
        clock_helper=ClockHelper(),
        now=now,
    )


def test_indicators_view_returns_clipped_at_last_closed() -> None:
    """now 와 마감 시각이 정확히 일치하면 그 봉까지 포함."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]  # 00..04
    # now = 03:00 → last_closed = 02:00 (봉 시작 02:00, 03:00 마감) → 인덱스 2 까지.
    view = _build_indicators_view(timestamps, now=base + timedelta(hours=3))
    out = view["BTCUSDT"]["1h"]
    assert out.height == 3  # 인덱스 0/1/2
    assert "sma_5" in out.columns
    assert out["timestamp"][-1] == base + timedelta(hours=2)


def test_indicators_view_clips_lookahead() -> None:
    """now 시점에 진행 중인 봉은 노출 안 됨."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    # now = 02:30 → last_closed = 02:00 (= 02:00 봉이 03:00 마감 직전, 즉 last_closed = 01:00)
    view = _build_indicators_view(
        timestamps, now=base + timedelta(hours=2, minutes=30)
    )
    out = view["BTCUSDT"]["1h"]
    # 1h 봉 기준 last_closed_time(02:30) = 02:00 (open 01:00 봉이 02:00 마감) → idx 1
    assert out.height == 2
    assert out["timestamp"][-1] == base + timedelta(hours=1)


def test_indicators_view_unknown_pair_raises() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    view = _build_indicators_view([base], now=base + timedelta(hours=1))
    with pytest.raises(KeyError, match="not precomputed"):
        view["ETHUSDT"]["1h"]


def test_indicators_view_has_helper() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    view = _build_indicators_view([base], now=base + timedelta(hours=1))
    assert view.has("BTCUSDT", "1h") is True
    assert view.has("ETHUSDT", "1h") is False
    assert view.has("BTCUSDT", "4h") is False


def test_indicators_view_empty_returns_empty_slice() -> None:
    """now 가 첫 봉 시작 직전이면 빈 슬라이스."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    view = _build_indicators_view(timestamps, now=base - timedelta(hours=1))
    out = view["BTCUSDT"]["1h"]
    assert out.height == 0
