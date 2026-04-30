"""Events subsystem (PR 6).

Phase 1:
- types.py: EventType, Event, IntentCreatedPayload, SnapshotReason
- serialize.py: serialize_event_payload (모든 도메인 타입을 JSON-친화 dict로)
- log.py: EVENT_SCHEMA_VERSION=1, EventLog (JSONL writer, context manager)

Phase 1.5: events/reader.py (EventLogReader), events/replay.py.
"""

from backtester.events.log import EVENT_SCHEMA_VERSION, EventLog
from backtester.events.serialize import serialize_event_payload
from backtester.events.types import Event, EventType, IntentCreatedPayload, SnapshotReason

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "Event",
    "EventLog",
    "EventType",
    "IntentCreatedPayload",
    "SnapshotReason",
    "serialize_event_payload",
]
