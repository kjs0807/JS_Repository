"""Clock 컴포넌트 (spec §3.4, §2).

핵심 시간 모델:
- OHLCV timestamp = 봉 시작 시각
- ClockEvent.timestamp = 봉 마감 시각 = 의사결정 가능 시각
- now가 마감 시각과 정확히 일치하면 그 봉은 '이미 마감' (last_closed_time §2.3)

Phase 1: SimpleClock 단일 타임프레임. settlements는 항상 빈 리스트.
GlobalClock(세션 기반)은 Phase 3.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backtester.data.base import parse_timeframe

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ClockEvent:
    """봉 마감 이벤트 (spec §3.4).

    `timestamp`는 봉 마감 시각 (= 의사결정 가능 시각).
    `bar_closes`는 이 시점에 마감된 (symbol, [timeframe...]) 매핑.
    `settlements`는 (symbol, kind) 튜플 리스트 — Phase 1에서는 항상 빈 리스트.
    """

    timestamp: datetime
    bar_closes: dict[str, list[str]]
    settlements: list[tuple[str, str]] = field(default_factory=list)


class SimpleClock:
    """단일 타임프레임 단일 그룹 Clock (Phase 1).

    `bar_timestamps`(봉 시작들)을 순회하며 각 bar_start + interval을 마감 시각으로
    하는 ClockEvent를 yield한다. `symbols`는 동일 timeframe을 공유하는 그룹.

    입력 검증 (시간 모델 핵심 컴포넌트):
    - 모든 timestamp는 UTC tz-aware (naive 또는 non-UTC 거부)
    - strictly increasing (중복·역순 거부)
    """

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        bar_timestamps: list[datetime],
    ) -> None:
        if not symbols:
            raise ValueError("SimpleClock requires at least one symbol")
        # UTC-aware 검증
        for i, ts in enumerate(bar_timestamps):
            if ts.tzinfo is None:
                raise ValueError(
                    f"bar_timestamps[{i}] must be timezone-aware (UTC), got naive: {ts!r}"
                )
            offset = ts.utcoffset()
            if offset != timedelta(0):
                raise ValueError(
                    f"bar_timestamps[{i}] must be UTC (offset 0), "
                    f"got tzinfo={ts.tzinfo!r} offset={offset}"
                )
        # strictly increasing 검증
        for prev, curr in zip(bar_timestamps[:-1], bar_timestamps[1:], strict=True):
            if curr <= prev:
                raise ValueError(
                    f"bar_timestamps must be strictly increasing; "
                    f"found {prev!r} followed by {curr!r}"
                )
        self._symbols = list(symbols)
        self._timeframe = timeframe
        self._bar_timestamps = list(bar_timestamps)
        self._interval = parse_timeframe(timeframe)

    def __iter__(self) -> Iterator[ClockEvent]:
        for bar_start in self._bar_timestamps:
            yield ClockEvent(
                timestamp=bar_start + self._interval,
                bar_closes={sym: [self._timeframe] for sym in self._symbols},
            )

    def __len__(self) -> int:
        return len(self._bar_timestamps)


class ClockHelper:
    """시간 그리드 계산 헬퍼 (spec §2.3, §3.6).

    Phase 1 가정: 봉 그리드는 UTC epoch에 정렬 (예: 1h봉은 매시 정각 시작).
    실데이터에 갭이 있어 last_closed_time이 데이터에 없는 경우 BarsView가
    `bisect_right`로 보정한다 (spec §3.6).
    """

    def last_closed_time(self, timeframe: str, now: datetime) -> datetime:
        """`now` 기준 가장 최근에 마감된 봉의 시작 시각.

        - 1h, now=14:00 → 13:00 (now가 마감 경계와 일치 → 그 봉은 이미 마감)
        - 1h, now=14:30 → 13:00 (현재 봉은 15:00에 마감 예정)
        - 1h, now=15:00 → 14:00 (다음 경계, 14:00 봉이 마감됨)
        """
        if now.tzinfo is None:
            raise ValueError(f"now must be timezone-aware (UTC), got naive: {now!r}")
        offset = now.utcoffset()
        if offset != timedelta(0):
            raise ValueError(
                f"now must be UTC (offset 0), got tzinfo={now.tzinfo!r} offset={offset}"
            )
        interval = parse_timeframe(timeframe)
        now_us = int((now - _EPOCH) / timedelta(microseconds=1))
        interval_us = int(interval / timedelta(microseconds=1))
        # 가장 큰 bar_start_us such that bar_start_us + interval_us <= now_us
        # i.e. bar_start_us <= now_us - interval_us
        last_closed_us = ((now_us - interval_us) // interval_us) * interval_us
        return _EPOCH + timedelta(microseconds=last_closed_us)
