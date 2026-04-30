"""PR 6 EventLog 테스트 (spec §20 PR 6 acceptance)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backtester.events.log import EVENT_SCHEMA_VERSION, EventLog
from backtester.events.types import Event, EventType

UTC = timezone.utc
TS = datetime(2026, 1, 1, 14, tzinfo=UTC)


# ---------- context manager 필수 -------------------------------------------


def test_event_log_outside_context_raises(tmp_path: Path) -> None:
    """spec §3.15 + §20 PR 6: context manager 외부 사용 시 RuntimeError."""
    log = EventLog(tmp_path)
    with pytest.raises(RuntimeError, match="outside context manager"):
        log.append(Event(ts=TS, type=EventType.FILL, payload={}))


def test_event_log_after_exit_raises(tmp_path: Path) -> None:
    """with 블록 종료 후 append → RuntimeError."""
    log = EventLog(tmp_path)
    with log:
        log.append(Event(ts=TS, type=EventType.SNAPSHOT, payload={}))
    with pytest.raises(RuntimeError, match="outside context manager"):
        log.append(Event(ts=TS, type=EventType.SNAPSHOT, payload={}))


# ---------- 라인 형식 -------------------------------------------------------


def test_event_log_line_includes_schema_version(tmp_path: Path) -> None:
    """spec §20 PR 6: 모든 라인에 schema_version 포함."""
    with EventLog(tmp_path) as log:
        log.append(Event(ts=TS, type=EventType.FILL, payload={"foo": "bar"}))

    content = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    line = json.loads(content.strip())
    assert line["schema_version"] == EVENT_SCHEMA_VERSION
    assert line["schema_version"] == 1  # Phase 1


def test_event_log_line_has_required_keys(tmp_path: Path) -> None:
    with EventLog(tmp_path) as log:
        log.append(
            Event(ts=TS, type=EventType.SNAPSHOT, payload={"equity": "10000"})
        )

    line = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").strip())
    assert set(line.keys()) == {"schema_version", "ts", "type", "payload"}
    assert line["ts"] == TS.isoformat()
    assert line["type"] == "snapshot"  # EventType.SNAPSHOT.value
    assert line["payload"] == {"equity": "10000"}


def test_event_log_serializes_decimal_payload(tmp_path: Path) -> None:
    """payload의 Decimal은 serialize_event_payload를 통해 str로 직렬화."""
    with EventLog(tmp_path) as log:
        log.append(
            Event(
                ts=TS,
                type=EventType.FILL,
                payload={"price": Decimal("50000"), "size": Decimal("1.5")},
            )
        )
    line = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").strip())
    assert line["payload"] == {"price": "50000", "size": "1.5"}


# ---------- 다중 이벤트 -----------------------------------------------------


def test_event_log_multiple_events_one_per_line(tmp_path: Path) -> None:
    with EventLog(tmp_path) as log:
        log.append(Event(ts=TS, type=EventType.BAR_CLOSE, payload={"i": 0}))
        log.append(Event(ts=TS, type=EventType.SNAPSHOT, payload={"i": 1}))
        log.append(Event(ts=TS, type=EventType.FILL, payload={"i": 2}))

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [p["payload"]["i"] for p in parsed] == [0, 1, 2]
    assert [p["type"] for p in parsed] == ["bar_close", "snapshot", "fill"]


def test_event_log_appends_across_context_blocks(tmp_path: Path) -> None:
    """동일 run_dir에 두 번 with 블록을 열어도 이전 내용을 보존하며 append."""
    with EventLog(tmp_path) as log:
        log.append(Event(ts=TS, type=EventType.BAR_CLOSE, payload={"first": True}))
    with EventLog(tmp_path) as log:
        log.append(Event(ts=TS, type=EventType.BAR_CLOSE, payload={"second": True}))

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# ---------- 파일 경로 / 디렉토리 자동 생성 -----------------------------------


def test_event_log_creates_run_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "run"
    assert not nested.exists()
    log = EventLog(nested)
    assert nested.exists()  # __init__에서 mkdir(parents=True)
    assert log.path == nested / "events.jsonl"


# ---------- 모든 EventType이 .value를 가지고 직렬화됨 -----------------------


def test_event_log_handles_all_event_types(tmp_path: Path) -> None:
    """모든 EventType 멤버가 직렬화 가능 (회귀 게이트)."""
    with EventLog(tmp_path) as log:
        for et in EventType:
            log.append(Event(ts=TS, type=et, payload={}))
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(EventType)
    types_in_file = [json.loads(line)["type"] for line in lines]
    assert types_in_file == [et.value for et in EventType]
