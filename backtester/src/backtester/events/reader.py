"""EventLogReader — events.jsonl 파싱 + type별 인덱스 (Phase 1.5 PR 10, spec §3.15).

events.jsonl 은 1차 원본 (spec §6.3). 이 reader 는 줄 단위로 파싱한 ``Event`` 를 메모리에
적재하고 type 별로 인덱싱해 ``by_type`` / ``by_snapshot_reason`` 같은 분석 헬퍼를 제공한다.

스키마 호환성 (spec §3.15):
- 라인별 ``schema_version`` 가 ``EVENT_SCHEMA_VERSION`` 보다 크면 ``EventLogSchemaError``.
- 작거나 같으면 read 통과 (필드 추가만 있는 변경은 동일 버전 유지).
- 추후 의미가 바뀌는 변경이 도입되면 마이그레이션 또는 명시 거부 로직을 본 클래스에서 분기.

읽기 전용 — events.jsonl 변경하지 않는다. payload 는 dict (Decimal 은 str 그대로 보존).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from backtester.events.log import EVENT_SCHEMA_VERSION
from backtester.events.types import Event, EventType, SnapshotReason


class EventLogSchemaError(Exception):
    """events.jsonl 의 ``schema_version`` 이 reader 가 지원하는 범위를 넘어설 때."""


class EventLogReader:
    """events.jsonl 분석용 read-only 리더.

    사용 패턴::

        reader = EventLogReader(run_dir / "events.jsonl")
        for fill in reader.by_type(EventType.FILL):
            ...
        for snap in reader.by_snapshot_reason("fill"):
            ...
        df_intents = reader.to_dataframe(EventType.INTENT_CREATED)
    """

    def __init__(
        self,
        events_path: Path,
        *,
        max_schema_version: int = EVENT_SCHEMA_VERSION,
    ) -> None:
        self._path: Path = events_path
        self._events: list[Event] = []
        self._by_type: dict[EventType, list[int]] = defaultdict(list)

        if not events_path.exists():
            raise FileNotFoundError(f"events jsonl not found: {events_path}")

        with open(events_path, encoding="utf-8") as fp:
            for lineno, raw_line in enumerate(fp, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"events.jsonl malformed at line {lineno}: {e}"
                    ) from e
                version = int(obj.get("schema_version", 0))
                if version > max_schema_version:
                    raise EventLogSchemaError(
                        f"events.jsonl line {lineno}: schema_version={version} "
                        f"exceeds reader max={max_schema_version}. "
                        f"Update backtester or use a newer reader."
                    )
                ts = datetime.fromisoformat(obj["ts"])
                event_type = EventType(obj["type"])
                payload: dict[str, Any] = obj.get("payload") or {}
                evt = Event(ts=ts, type=event_type, payload=payload)
                self._events.append(evt)
                self._by_type[event_type].append(len(self._events) - 1)

    # ---------- 메타 -------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._events)

    def all_events(self) -> Iterator[Event]:
        return iter(self._events)

    def counts_by_type(self) -> dict[EventType, int]:
        return {t: len(idxs) for t, idxs in self._by_type.items()}

    # ---------- 필터 -------------------------------------------------------

    def by_type(self, t: EventType) -> Iterator[Event]:
        for i in self._by_type.get(t, []):
            yield self._events[i]

    def by_snapshot_reason(self, reason: SnapshotReason) -> Iterator[Event]:
        """SNAPSHOT 이벤트 중 ``reason`` 이 일치하는 것만 (spec §3.15)."""
        for evt in self.by_type(EventType.SNAPSHOT):
            if evt.payload.get("snapshot_reason") == reason:
                yield evt

    # ---------- 분석용 DataFrame -------------------------------------------

    def to_dataframe(self, t: EventType) -> pl.DataFrame:
        """특정 type 의 이벤트들을 ``ts`` + ``payload`` (JSON 문자열) DataFrame 으로.

        spec §3.15 가 명시한 스키마는 의도적으로 단순한 long-form: 분석자는 polars
        ``json_decode`` 로 필요한 컬럼을 펼친다 (``events.parquet`` 와 동일 정책,
        spec §6.2). 평면 컬럼화는 type 별로 다른 helper 가 책임.
        """
        rows = list(self.by_type(t))
        if not rows:
            return pl.DataFrame(
                schema={
                    "ts": pl.Datetime(time_unit="us", time_zone="UTC"),
                    "payload": pl.String,
                }
            )
        return pl.DataFrame(
            {
                "ts": [evt.ts for evt in rows],
                "payload": [json.dumps(evt.payload, ensure_ascii=False) for evt in rows],
            }
        ).with_columns(
            pl.col("ts").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
            pl.col("payload").cast(pl.String),
        )
