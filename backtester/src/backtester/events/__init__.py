"""Events subsystem (PR 6 + Phase 1.5 PR 9).

Phase 1:
- types.py: EventType, Event, IntentCreatedPayload, SnapshotReason
- serialize.py: serialize_event_payload (모든 도메인 타입을 JSON-친화 dict로)
- log.py: EVENT_SCHEMA_VERSION=1, EventLog (JSONL writer, context manager)

Phase 1.5:
- parquet_export.py: events.jsonl → events.parquet 변환 (cache 산출물)
- (후속) reader.py (EventLogReader, by_snapshot_reason)
"""

from backtester.events.log import EVENT_SCHEMA_VERSION, EventLog
from backtester.events.parquet_export import events_jsonl_to_parquet
from backtester.events.serialize import serialize_event_payload
from backtester.events.types import Event, EventType, IntentCreatedPayload, SnapshotReason

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "Event",
    "EventLog",
    "EventType",
    "IntentCreatedPayload",
    "SnapshotReason",
    "events_jsonl_to_parquet",
    "serialize_event_payload",
]
