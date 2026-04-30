"""StrategyContext + BarsView (spec §3.6).

전략의 `on_bar`에 전달되는 컨텍스트 — 현재 시각, primary symbol/timeframe, BarsView.

BarsView는 last_closed 시점 이전의 봉들만 슬라이스해서 노출. 미래 누설 차단(spec §2.4).

성능: O(1) timestamp_index 우선, 정렬 데이터에 phantom timestamp(갭)인 경우만 bisect 폴백.
`df.filter()` 매 봉 호출 금지 (spec §3.6, §11).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from backtester.core.clock import ClockHelper


class _TimeframeView:
    """`BarsView[symbol]` 결과로 반환되는 helper. `[tf]`로 슬라이스 접근."""

    __slots__ = ("_parent", "_symbol")

    def __init__(self, parent: BarsView, symbol: str) -> None:
        self._parent = parent
        self._symbol = symbol

    def __getitem__(self, timeframe: str) -> pl.DataFrame:
        return self._parent._slice(self._symbol, timeframe)


class BarsView:
    """봉 데이터 view — last_closed 시점까지만 노출.

    `view[symbol][tf]`로 접근하면 `now` 시점에 마감된 가장 최근 봉까지 포함하는
    `pl.DataFrame` 슬라이스를 반환한다. now 시점에 진행 중인 봉은 노출되지 않는다
    (lookahead 차단).
    """

    def __init__(
        self,
        bars: dict[str, dict[str, pl.DataFrame]],
        timestamp_index: dict[str, dict[str, dict[datetime, int]]],
        timestamps: dict[str, dict[str, list[datetime]]],
        clock_helper: ClockHelper,
        now: datetime,
    ) -> None:
        self._bars = bars
        self._timestamp_index = timestamp_index
        self._timestamps = timestamps
        self._clock_helper = clock_helper
        self._now = now

    def __getitem__(self, symbol: str) -> _TimeframeView:
        if symbol not in self._bars:
            raise KeyError(f"Unknown symbol: {symbol!r}")
        return _TimeframeView(self, symbol)

    def _slice(self, symbol: str, timeframe: str) -> pl.DataFrame:
        try:
            symbol_index = self._timestamp_index[symbol]
            symbol_ts = self._timestamps[symbol]
            symbol_bars = self._bars[symbol]
        except KeyError as e:  # pragma: no cover — __getitem__가 먼저 차단
            raise KeyError(f"Unknown symbol: {symbol!r}") from e
        if timeframe not in symbol_bars:
            raise KeyError(f"Unknown timeframe for {symbol!r}: {timeframe!r}")
        idx_map = symbol_index[timeframe]
        ts_list = symbol_ts[timeframe]
        df = symbol_bars[timeframe]

        last_closed = self._clock_helper.last_closed_time(timeframe, self._now)

        # 정확 매칭 (O(1))
        end_idx = idx_map.get(last_closed)
        if end_idx is None:
            # 갭 등으로 정확 매칭 실패 → bisect (O(log n))
            end_idx = bisect_right(ts_list, last_closed) - 1
        if end_idx < 0:
            return df.slice(0, 0)
        return df.slice(0, end_idx + 1)


@dataclass(frozen=True)
class StrategyContext:
    """전략의 on_bar 호출 시 전달되는 컨텍스트 (spec §3.5, §4.2).

    Phase 1 필드:
    - `now`: ClockEvent.timestamp (= 봉 마감 시각, 의사결정 시점)
    - `primary_symbol`/`primary_timeframe`: 전략 기본 축
    - `bars`: BarsView, last_closed 이전만 노출

    추후 Phase에서 추가 예정: indicators view, position 조회, equity 등.
    """

    now: datetime
    primary_symbol: str
    primary_timeframe: str
    bars: BarsView
