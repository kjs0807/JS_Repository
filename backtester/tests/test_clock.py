"""PR 3 시간 모델 테스트 (spec §20 PR 3 acceptance)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backtester.core.clock import ClockEvent, ClockHelper, SimpleClock

UTC = timezone.utc


# ---------- ClockEvent ------------------------------------------------------


def test_clock_event_settlements_default_empty() -> None:
    """Phase 1: settlements는 항상 빈 리스트."""
    e = ClockEvent(
        timestamp=datetime(2026, 1, 1, 14, tzinfo=UTC),
        bar_closes={"BTCUSDT": ["1h"]},
    )
    assert e.settlements == []


def test_clock_event_is_frozen() -> None:
    import dataclasses

    e = ClockEvent(
        timestamp=datetime(2026, 1, 1, 14, tzinfo=UTC),
        bar_closes={"BTCUSDT": ["1h"]},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.timestamp = datetime(2026, 1, 1, 15, tzinfo=UTC)  # type: ignore[misc]


# ---------- SimpleClock -----------------------------------------------------


def test_simple_clock_yields_close_times_for_hourly() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)  # bar_start 00:00, 01:00, 02:00
    bar_starts = [base + timedelta(hours=i) for i in range(3)]
    clock = SimpleClock(symbols=["BTCUSDT"], timeframe="1h", bar_timestamps=bar_starts)
    events = list(clock)
    assert len(events) == 3
    # ClockEvent.timestamp = 봉 마감 시각 = bar_start + 1h
    assert events[0].timestamp == base + timedelta(hours=1)
    assert events[1].timestamp == base + timedelta(hours=2)
    assert events[2].timestamp == base + timedelta(hours=3)


def test_simple_clock_bar_closes_payload() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    clock = SimpleClock(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1h",
        bar_timestamps=[base],
    )
    [event] = list(clock)
    assert event.bar_closes == {"BTCUSDT": ["1h"], "ETHUSDT": ["1h"]}
    assert event.settlements == []  # Phase 1 invariant


def test_simple_clock_empty_bars_yields_nothing() -> None:
    clock = SimpleClock(symbols=["BTCUSDT"], timeframe="1h", bar_timestamps=[])
    assert list(clock) == []
    assert len(clock) == 0


def test_simple_clock_requires_symbols() -> None:
    with pytest.raises(ValueError, match="at least one symbol"):
        SimpleClock(symbols=[], timeframe="1h", bar_timestamps=[])


def test_simple_clock_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SimpleClock(
            symbols=["BTCUSDT"],
            timeframe="1h",
            bar_timestamps=[datetime(2026, 1, 1)],  # naive
        )


def test_simple_clock_rejects_non_utc_timestamp() -> None:
    kst = timezone(timedelta(hours=9))
    with pytest.raises(ValueError, match="must be UTC"):
        SimpleClock(
            symbols=["BTCUSDT"],
            timeframe="1h",
            bar_timestamps=[datetime(2026, 1, 1, 9, tzinfo=kst)],
        )


def test_simple_clock_rejects_duplicate_timestamps() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="strictly increasing"):
        SimpleClock(
            symbols=["BTCUSDT"],
            timeframe="1h",
            bar_timestamps=[base, base + timedelta(hours=1), base + timedelta(hours=1)],
        )


def test_simple_clock_rejects_descending_timestamps() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="strictly increasing"):
        SimpleClock(
            symbols=["BTCUSDT"],
            timeframe="1h",
            bar_timestamps=[base + timedelta(hours=2), base + timedelta(hours=1), base],
        )


# ---------- ClockHelper.last_closed_time ------------------------------------


@pytest.mark.parametrize(
    ("now", "tf", "expected"),
    [
        # 1h 경계에서 정확히 일치 → 직전 봉이 last_closed (spec §2.3)
        (
            datetime(2026, 1, 1, 14, 0, 0, tzinfo=UTC),
            "1h",
            datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC),
        ),
        # 1h 봉 중간 → 직전에 마감된 봉
        (
            datetime(2026, 1, 1, 14, 30, 0, tzinfo=UTC),
            "1h",
            datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC),
        ),
        # 다음 경계 → 14:00 봉이 마감됨
        (
            datetime(2026, 1, 1, 15, 0, 0, tzinfo=UTC),
            "1h",
            datetime(2026, 1, 1, 14, 0, 0, tzinfo=UTC),
        ),
        # 1m 케이스
        (
            datetime(2026, 1, 1, 14, 30, 0, tzinfo=UTC),
            "1m",
            datetime(2026, 1, 1, 14, 29, 0, tzinfo=UTC),
        ),
        # 4h 케이스: now=08:00 → 04:00 봉 마감 (00:00,04:00,08:00 boundary)
        (
            datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC),
            "4h",
            datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC),
        ),
        # 1d 케이스: now=2026-01-02 00:00 → 2026-01-01 00:00 봉 마감
        (
            datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC),
            "1d",
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ),
    ],
)
def test_clock_helper_last_closed_time(now: datetime, tf: str, expected: datetime) -> None:
    helper = ClockHelper()
    assert helper.last_closed_time(tf, now) == expected


def test_clock_helper_rejects_naive_now() -> None:
    helper = ClockHelper()
    with pytest.raises(ValueError, match="timezone-aware"):
        helper.last_closed_time("1h", datetime(2026, 1, 1, 14))


def test_clock_helper_rejects_non_utc_now() -> None:
    """KST(UTC+9) 등 비-UTC tz는 거부 (DataSource와 동일 정책)."""
    helper = ClockHelper()
    kst = timezone(timedelta(hours=9))
    with pytest.raises(ValueError, match="must be UTC"):
        helper.last_closed_time("1h", datetime(2026, 1, 1, 14, tzinfo=kst))


def test_clock_helper_microsecond_precision() -> None:
    """now가 마감 시각보다 1 microsecond 전이면 그 봉은 아직 마감 안 됨."""
    helper = ClockHelper()
    boundary = datetime(2026, 1, 1, 14, 0, 0, tzinfo=UTC)
    just_before = boundary - timedelta(microseconds=1)
    # boundary - 1us → 13:00 봉도 아직 마감 X (마감 시각이 14:00이고 now < 14:00)
    # last_closed = 12:00 봉 (마감 13:00, 13:00 <= 13:59:59.999999 ✓)
    assert helper.last_closed_time("1h", just_before) == datetime(
        2026, 1, 1, 12, 0, 0, tzinfo=UTC
    )
