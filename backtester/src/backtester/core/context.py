"""StrategyContext + BarsView + IndicatorsView + PortfolioView + OrdersView
(spec §3.5/§3.6/§3.8, PR A 추가).

전략의 `on_bar`에 전달되는 컨텍스트 — 현재 시각, primary symbol/timeframe, BarsView,
IndicatorsView, PortfolioView (read-only ledger snapshot), OrdersView (read-only open
orders snapshot).

BarsView / IndicatorsView 둘 다 last_closed 시점 이전의 봉들만 슬라이스해서 노출.
미래 누설 차단(spec §2.4).

성능: O(1) timestamp_index 우선, 정렬 데이터에 phantom timestamp(갭)인 경우만 bisect 폴백.
`df.filter()` 매 봉 호출 금지 (spec §3.6, §11).

IndicatorsView (PR 16 전 prep, FRAMA 등 recursive/stateful 지표 대비):
- Engine 의 IndicatorEngine.precompute() 결과 (timestamp + 지표 컬럼) 를 BarsView 와 같은
  방식으로 lookahead-clipped 슬라이스로 노출.
- 전략은 ``ctx.indicators[symbol][tf]`` 로 사전계산된 지표를 읽어 매 봉 재계산 비용을 절감.
- 캐시에 없는 (symbol, tf) 는 ``KeyError`` — ``required_indicators()`` 에 올린 지표만 사용 가능.

PortfolioView / OrdersView (PR A — 전략이 ledger / orderbook 을 직접 읽도록):
- ``ctx.position(symbol)`` / ``ctx.has_position(symbol)`` / ``ctx.equity`` / ``ctx.cash``
  / ``ctx.open_orders(symbol=None)`` 같은 read-only API 를 제공한다.
- 전략 내부 ``_has_position`` 같은 plagiarized state 대신 ledger 가 single source of
  truth. risk reject / 부분체결 등으로 desync 가 일어나지 않는다.
- frozen dataclass + tuple/MappingProxyType 으로 mutate 시도 시 ``FrozenInstanceError`` /
  ``TypeError``.
- Engine 이 ``_invoke_strategy`` 시점에 snapshot 을 만들어 주입; 직접 ``StrategyContext``
  를 만드는 테스트 fixture 는 default factory (빈 portfolio / orders) 가 사용된다.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

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

    **읽기 전용 계약** (PR 16 prep 2차): 반환되는 ``pl.DataFrame`` 은 IndicatorEngine
    cache 와 같은 객체를 공유한다. 전략 코드는 ``df.with_columns(...)`` 로 새 DataFrame 을
    파생해 사용해야 하며, in-place mutate (``df[col] = ...`` 같은 시도) 또는 cache 의
    DataFrame 을 직접 변경해서는 안 된다. polars 가 immutable lazy/eager 모델이라 실수로
    mutate 하기는 어렵지만, 명시 계약으로 못박아 두는 편이 안전하다. cache dict 자체는
    ``MappingProxyType`` 으로 보호 (``IndicatorEngine.snapshot()``).
    """

    def __init__(
        self,
        cache: Mapping[tuple[str, str], pl.DataFrame],
        timestamp_index: dict[str, dict[str, dict[datetime, int]]],
        timestamps: dict[str, dict[str, list[datetime]]],
        clock_helper: ClockHelper,
        now: datetime,
    ) -> None:
        # ``Mapping`` 으로 받아 ``IndicatorEngine.snapshot()`` 의 read-only proxy 와도 호환.
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


# ---------- PortfolioView (PR A) --------------------------------------------


@dataclass(frozen=True)
class PositionView:
    """Read-only ``Position`` snapshot (PR A + PR N opened_at).

    Engine 이 ``Ledger.positions[symbol]`` 의 mutable Position 으로부터 매 ``on_bar``
    호출 시점에 새로 만들어 주입한다. ``StrategyContext`` 가 frozen 이라 위치를
    바꾸려는 시도는 ``FrozenInstanceError``.

    Phase 1 long-only 가정 (size >= 0) 이지만 Phase 2+ short 활성 시 size < 0 도 그대로
    노출된다 — ``direction`` 으로 long/short/flat 구분.

    PR N: ``opened_at`` 은 현재 활성 포지션의 entry timestamp (fill ts). 같은 방향
    추가는 유지, flat→새로 / flip 은 갱신. flat 일 때는 None 이 아닐 수 있지만
    의미상 무시해야 한다 (``ctx.bars_held`` 가 flat 가드).
    """

    symbol: str
    size: Decimal
    avg_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    opened_at: datetime | None = None

    @property
    def is_flat(self) -> bool:
        return self.size == 0

    @property
    def direction(self) -> Literal["long", "short", "flat"]:
        if self.size > 0:
            return "long"
        if self.size < 0:
            return "short"
        return "flat"


@dataclass(frozen=True)
class PortfolioView:
    """Read-only ledger snapshot (PR A) — equity / cash / positions.

    ``positions`` 는 ``MappingProxyType`` 로 dict 보호. flat position 은 제외 (전략이
    "내가 가지고 있는 심볼" 만 iterate 할 수 있도록). flat 도 보고 싶으면
    ``has_position(symbol)`` 가 False 를 반환.
    """

    equity: Decimal
    cash: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    positions: Mapping[str, PositionView]

    def position(self, symbol: str) -> PositionView | None:
        return self.positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        p = self.positions.get(symbol)
        return p is not None and not p.is_flat


def _empty_portfolio_view() -> PortfolioView:
    """ctx.portfolio 의 default factory — 모든 값 0 (테스트 fixture 호환)."""
    return PortfolioView(
        equity=Decimal("0"),
        cash=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        positions=MappingProxyType({}),
    )


# ---------- OrdersView (PR A) -----------------------------------------------


OrderState = Literal[
    "pending", "partially_filled", "filled", "cancelled", "expired", "rejected"
]
OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]


@dataclass(frozen=True)
class OrderView:
    """Read-only ``Order`` snapshot (PR A + Phase 4 bracket extension).

    Engine 이 ``OrderBook.get_active()`` 결과로부터 매 ``on_bar`` 호출 시점에 새로
    만들어 주입. 전략이 mutate 시도하면 ``FrozenInstanceError``. 가격 필드는 ``intent``
    에서 가져오므로 Decimal | None.

    Phase 4: bracket-aware fields (``bracket_group_id`` / ``bracket_role`` /
    ``tp_leg_index``) are propagated from :class:`Order` so strategies can
    locate their bracket children (SL by role, TP legs by index) without
    parsing ``intent.reason`` strings. Single-bracket / non-bracket orders
    leave these as ``None`` — same default as the ``Order`` dataclass.
    """

    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    state: OrderState
    sized_quantity: Decimal
    remaining: Decimal
    submitted_at: datetime
    limit_price: Decimal | None
    stop_price: Decimal | None
    bracket_group_id: str | None = None
    bracket_role: str | None = None  # "tp_leg" | "protector_sl" | None
    tp_leg_index: int | None = None


@dataclass(frozen=True)
class OrdersView:
    """Read-only open order snapshot (PR A).

    ``open_orders(symbol=None)`` 로 전체 또는 심볼 필터. 빈 tuple 가능. 활성 (pending /
    partially_filled) 만 — terminal (filled/cancelled/expired/rejected) 은 제외.
    """

    _orders: tuple[OrderView, ...]

    def open_orders(self, symbol: str | None = None) -> tuple[OrderView, ...]:
        if symbol is None:
            return self._orders
        return tuple(o for o in self._orders if o.symbol == symbol)


def _empty_orders_view() -> OrdersView:
    """ctx.orders 의 default factory — 빈 tuple."""
    return OrdersView(_orders=())


# ---------- StrategyContext --------------------------------------------------


@dataclass(frozen=True)
class StrategyContext:
    """전략의 on_bar 호출 시 전달되는 컨텍스트 (spec §3.5, §4.2, §3.8, PR A).

    필드:
    - `now`: ClockEvent.timestamp (= 봉 마감 시각, 의사결정 시점)
    - `primary_symbol`/`primary_timeframe`: 전략 기본 축
    - `bars`: BarsView, last_closed 이전만 노출
    - `indicators`: IndicatorsView, last_closed 이전만 노출 (PR 16 prep — FRAMA 등
      stateful/recursive 지표가 batch precompute 결과를 직접 읽도록 하기 위함).
    - `portfolio`: PortfolioView (PR A) — read-only ledger snapshot.
    - `orders`: OrdersView (PR A) — read-only open order snapshot.

    Engine 은 항상 IndicatorEngine cache + Ledger / OrderBook snapshot 을 주입한다.
    직접 ``StrategyContext`` 를 만드는 테스트 fixture 는 default factory (빈 view) 가
    사용된다.

    편의 proxy: ``ctx.position(symbol)`` / ``ctx.has_position(symbol)`` / ``ctx.equity``
    / ``ctx.cash`` / ``ctx.open_orders(symbol=None)`` — ``portfolio`` / ``orders`` 의
    short-cut.
    """

    now: datetime
    primary_symbol: str
    primary_timeframe: str
    bars: BarsView
    indicators: IndicatorsView = field(default_factory=_empty_indicators_view)
    portfolio: PortfolioView = field(default_factory=_empty_portfolio_view)
    orders: OrdersView = field(default_factory=_empty_orders_view)

    # ---------- portfolio proxy --------------------------------------------

    def position(self, symbol: str) -> PositionView | None:
        return self.portfolio.position(symbol)

    def has_position(self, symbol: str) -> bool:
        return self.portfolio.has_position(symbol)

    @property
    def positions(self) -> Mapping[str, PositionView]:
        return self.portfolio.positions

    @property
    def equity(self) -> Decimal:
        return self.portfolio.equity

    @property
    def cash(self) -> Decimal:
        return self.portfolio.cash

    # ---------- orders proxy ------------------------------------------------

    def open_orders(self, symbol: str | None = None) -> tuple[OrderView, ...]:
        return self.orders.open_orders(symbol)

    # ---------- PR N: time stop helper -------------------------------------

    def bars_held(self, symbol: str) -> int | None:
        """현재 활성 포지션이 ``primary_timeframe`` 기준 몇 봉 동안 보유 중인지.

        반환:
        - flat → None
        - opened_at 미설정 → None
        - 그 외 → ``(now - opened_at) / primary_interval`` 정수.

        주의: ``primary_timeframe`` 기준이라 다른 TF 의 봉 수와 다를 수 있다.
        보통 strategy 가 단일 TF 만 쓰므로 충분.
        """
        from backtester.data.base import parse_timeframe

        pos = self.position(symbol)
        if pos is None or pos.is_flat or pos.opened_at is None:
            return None
        interval = parse_timeframe(self.primary_timeframe)
        elapsed = self.now - pos.opened_at
        if interval.total_seconds() <= 0:
            return None
        return int(elapsed.total_seconds() // interval.total_seconds())
