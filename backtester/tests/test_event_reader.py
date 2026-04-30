"""PR 10 EventLogReader 테스트 (Phase 1.5, spec §3.15)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from backtester.events import EVENT_SCHEMA_VERSION, EventLog
from backtester.events.reader import EventLogReader, EventLogSchemaError
from backtester.events.types import Event, EventType

UTC = timezone.utc


def _write_jsonl(path: Path, lines: list[dict[str, object]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for obj in lines:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _snap(ts: datetime, reason: str, equity: str = "10000") -> dict[str, object]:
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "ts": ts.isoformat(),
        "type": "snapshot",
        "payload": {
            "equity": equity,
            "cash": equity,
            "realized_pnl": "0",
            "unrealized_pnl": "0",
            "positions": {},
            "snapshot_reason": reason,
        },
    }


def _fill(ts: datetime, side: str = "buy") -> dict[str, object]:
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "ts": ts.isoformat(),
        "type": "fill",
        "payload": {
            "symbol": "BTCUSDT",
            "side": side,
            "price": "100",
            "size": "1",
        },
    }


# ---------- 기본 동작 -------------------------------------------------------


def test_reader_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="events jsonl"):
        EventLogReader(tmp_path / "nope.jsonl")


def test_reader_empty_file_yields_zero_events(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    p.write_text("", encoding="utf-8")
    r = EventLogReader(p)
    assert len(r) == 0
    assert list(r.all_events()) == []
    assert r.counts_by_type() == {}


def test_reader_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_jsonl(p, [_snap(base, "periodic")])
    # append blank line
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n\n")
    r = EventLogReader(p)
    assert len(r) == 1


def test_reader_indexes_by_type(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_jsonl(
        p,
        [
            _snap(base, "periodic"),
            _fill(base + timedelta(hours=1), "buy"),
            _snap(base + timedelta(hours=1), "fill"),
            _fill(base + timedelta(hours=2), "sell"),
            _snap(base + timedelta(hours=2), "fill"),
            _snap(base + timedelta(hours=2), "periodic"),
        ],
    )
    r = EventLogReader(p)
    assert len(r) == 6
    counts = r.counts_by_type()
    assert counts[EventType.SNAPSHOT] == 4
    assert counts[EventType.FILL] == 2

    snaps = list(r.by_type(EventType.SNAPSHOT))
    fills = list(r.by_type(EventType.FILL))
    assert len(snaps) == 4
    assert len(fills) == 2
    # 순서 보존 (file order)
    assert [e.ts for e in fills] == [base + timedelta(hours=1), base + timedelta(hours=2)]


def test_reader_by_snapshot_reason(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_jsonl(
        p,
        [
            _snap(base, "periodic"),
            _snap(base + timedelta(hours=1), "fill"),
            _snap(base + timedelta(hours=2), "periodic"),
            _snap(base + timedelta(hours=3), "fill"),
        ],
    )
    r = EventLogReader(p)
    fills = list(r.by_snapshot_reason("fill"))
    periodics = list(r.by_snapshot_reason("periodic"))
    assert len(fills) == 2
    assert len(periodics) == 2
    # 다른 reason 은 0
    assert list(r.by_snapshot_reason("settlement")) == []


# ---------- schema_version 호환 -----------------------------------------


def test_reader_rejects_higher_schema_version(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    line = _snap(base, "periodic")
    line["schema_version"] = EVENT_SCHEMA_VERSION + 5
    _write_jsonl(p, [line])
    with pytest.raises(EventLogSchemaError, match="schema_version"):
        EventLogReader(p)


def test_reader_accepts_lower_schema_version(tmp_path: Path) -> None:
    """미래 reader 가 옛 events 를 읽는 케이스 — additive 변경은 동일 버전 유지지만,
    혹시 v0 (legacy seed) 가 있어도 거부하지 않는다."""
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    line = _snap(base, "periodic")
    line["schema_version"] = 0
    _write_jsonl(p, [line])
    r = EventLogReader(p)
    assert len(r) == 1


# ---------- malformed ------------------------------------------------------


def test_reader_malformed_json_raises_with_lineno(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    p.write_text(
        '{"schema_version": 1, "ts": "2026-03-01T00:00:00+00:00", '
        '"type": "snapshot", "payload": {}}\n'
        "this is not json\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed at line 2"):
        EventLogReader(p)


# ---------- to_dataframe ---------------------------------------------------


def test_to_dataframe_for_known_type(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_jsonl(
        p,
        [
            _fill(base + timedelta(hours=i), "buy" if i % 2 == 0 else "sell")
            for i in range(3)
        ],
    )
    r = EventLogReader(p)
    df = r.to_dataframe(EventType.FILL)
    assert df.height == 3
    assert df.schema["ts"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert df.schema["payload"] == pl.String
    # payload JSON 디코드 가능
    first = json.loads(df["payload"][0])
    assert first["side"] == "buy"


def test_to_dataframe_empty_for_missing_type(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_jsonl(p, [_snap(base, "periodic")])
    r = EventLogReader(p)
    df = r.to_dataframe(EventType.FILL)  # 없음
    assert df.height == 0
    assert df.schema["ts"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert df.schema["payload"] == pl.String


# ---------- EventLog 와의 round-trip ---------------------------------------


def test_eventlog_to_reader_round_trip(tmp_path: Path) -> None:
    """EventLog 로 쓴 events 를 reader 가 모두 읽는다."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    with EventLog(run_dir) as log:
        log.append(
            Event(
                ts=base,
                type=EventType.SNAPSHOT,
                payload={"equity": "10000", "snapshot_reason": "periodic"},
            )
        )
        log.append(
            Event(
                ts=base + timedelta(hours=1),
                type=EventType.SNAPSHOT,
                payload={"equity": "10100", "snapshot_reason": "fill"},
            )
        )

    r = EventLogReader(run_dir / "events.jsonl")
    assert len(r) == 2
    fills = list(r.by_snapshot_reason("fill"))
    assert len(fills) == 1
    assert fills[0].payload["equity"] == "10100"
