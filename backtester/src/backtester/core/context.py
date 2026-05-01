"""StrategyContext + BarsView + IndicatorsView (spec §3.6, §3.8).

전략의 `on_bar`에 전달되는 컨텍스트 — 현재 시각, primary symbol/timeframe, BarsView,
IndicatorsView.

BarsView / IndicatorsView 둘 다 last_closed 시점 이전의 봉들만 슬라이스해서 노출.
미래 누설 차단(spec §2.4).

성능: O(1) timestamp_index 우선, 정렬 데이터에 phantom timestamp(갭)인 경우만 bisect 폴백.
`df.filter()` 매 봉 호출 금지 (spec §3.6, §11).

IndicatorsView (PR 16 전 prep, FRAMA 등 recursive/stateful 지표 대비):
- Engine 의 IndicatorEngine.precompute() 결과 (timestamp + 지표 컬럼) 를 BarsView 와 같은
  방식으로 lookahead-clipped 슬라이스로 노출.
- 전략은 ``ctx.indicators[symbol][tf]`` 로 사전계산된 지표를 읽어 매 봉 재계산 비용을 절감.
- 캐시에 없는 (symbol, tf) 는 ``KeyError`` — ``required_indicators()`` 에 올린 지표만 사용 가능.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


class _IndicatorsTimeframeView:
    """`IndicatorsView[symbol]` 결과로 반환되는 helper. `[tf]`로 슬라이스 접근."""

    __slots__ = ("_parent", "_symbol")

    def __init__(self, parent: IndicatorsView, symbol: str) -> None:
        self._parent = parent
        self._symbol = symbol

    def __getitem__(self, timeframe: str) -> pl.DataFrame:
        return self._parent._slice(self._symbol, timeframe)


class IndicatorsView:
    """precomputed indicator view — last_closed 시점까지만 노출 (PR 16 전 prep, spec §3.8).

    `view[symbol][tf]` 로 접근하면 ``IndicatorEngine.precompute()`` 가 미리 계산해 둔
    (symbol, tf) 의 지표 DataFrame 을 lookahead-clipped 슬라이스로 반환한다. 입력
    bars 와 행 수가 같고 timestamp 컬럼 포함 — BarsView 와 동일한 시간축.

    빈 ``IndicatorsView`` (cache empty) 는 모든 (symbol, tf) 에서 ``KeyError`` 를
    낸다 — 테스트 fixture 가 BarsView 만 만들고 indicators 를 안 쓰는 경우 대비.
    """

    def __init__(
        self,
        cache: dict[tuple[str, str], pl.DataFrame],
        timestamp_index: dict[str, dict[str, dict[datetime, int]]],
        timestamps: dict[str, dict[str, list[datetime]]],
        clock_helper: ClockHelper,
        now: datetime,
    ) -> None:
        self._cache = cache
        self._timestamp_index = timestamp_index
        self._timestamps = timestamps
        self._clock_helper = clock_helper
        self._now = now

    def __getitem__(self, symbol: str) -> _IndicatorsTimeframeView:
        return _IndicatorsTimeframeView(self, symbol)

    def has(self, symbol: str, timeframe: str) -> bool:
        """``(symbol, tf)`` 조합에 precomputed 결과가 있는지."""
        return (symbol, timeframe) in self._cache

    def _slice(self, symbol: str, timeframe: str) -> pl.DataFrame:
        key = (symbol, timeframe)
        if key not in self._cache:
            raise KeyError(
                f"Indicators not precomputed for {symbol!r}/{timeframe!r}. "
                f"Available: {sorted(self._cache.keys())}. "
                f"Add the indicator to strategy.required_indicators()."
            )
        df = self._cache[key]
        ts_list = self._timestamps.get(symbol, {}).get(timeframe, [])
        idx_map = self._timestamp_index.get(symbol, {}).get(timeframe, {})
        last_closed = self._clock_helper.last_closed_time(timeframe, self._now)
        end_idx = idx_map.get(last_closed)
        if end_idx is None:
            end_idx = bisect_right(ts_list, last_closed) - 1
        if end_idx < 0:
            return df.slice(0, 0)
        return df.slice(0, end_idx + 1)


def _empty_indicators_view() -> IndicatorsView:
    """ctx.indicators 의 default factory — cache 비어 있는 view (테스트 fixture 호환)."""
    return IndicatorsView(
        cache={},
        timestamp_index={},
        timestamps={},
        clock_helper=ClockHelper(),
        now=datetime.fromtimestamp(0, tz=timezone.utc),
    )


@dataclass(frozen=True)
class StrategyContext:
    """전략의 on_bar 호출 시 전달되는 컨텍스트 (spec §3.5, §4.2, §3.8).

    필드:
    - `now`: ClockEvent.timestamp (= 봉 마감 시각, 의사결정 시점)
    - `primary_symbol`/`primary_timeframe`: 전략 기본 축
    - `bars`: BarsView, last_closed 이전만 노출
    - `indicators`: IndicatorsView, last_closed 이전만 노출 (PR 16 prep — FRAMA 등
      stateful/recursive 지표가 batch precompute 결과를 직접 읽도록 하기 위함). Engine 은
      항상 IndicatorEngine cache 를 연결한 view 를 주입; 직접 ``StrategyContext`` 를
      만드는 테스트 fixture 는 default factory (빈 cache) 가 사용된다.

    추후 Phase에서 추가 예정: position 조회, equity 등.
    """

    now: datetime
    primary_symbol: str
    primary_timeframe: str
    bars: BarsView
    indicators: IndicatorsView = field(default_factory=_empty_indicators_view)
