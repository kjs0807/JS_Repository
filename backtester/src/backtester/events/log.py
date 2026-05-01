"""EventLog (spec §3.15, §13.3) — JSONL writer, context manager.

매 라인에 `schema_version` 필드를 포함해 호환성을 깨는 변경 시 Reader가 거부할 수
있게 한다 (필드 추가만 있는 변경은 동일 버전 유지 — spec §3.15).

context manager 외부 사용 시 RuntimeError로 차단해 lifecycle 누락을 방지.

Canonical JSON (PR 16 prep 2차, spec §13.3):
- ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` 로 deterministic 출력.
- 같은 (config, random_seed) 두 번 실행 시 events.jsonl 이 byte-identical 하도록 게이트
  활성. ``serialize_event_payload`` 가 Decimal → str / datetime → ISO8601 / Path → str 등
  payload 결정성을 이미 보장하므로, log 라인 자체의 결정성만 추가.
- ``json.loads`` 는 어떤 포맷이든 읽어들이므로 EventLogReader / rebuild-results / 분석
  도구는 영향 없음.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import IO

from backtester.events.serialize import serialize_event_payload
from backtester.events.types import Event

EVENT_SCHEMA_VERSION = 1
"""호환성을 깨는 스키마 변경(필드 제거/타입 변경/의미 변경) 시에만 증가.
필드 추가만 있는 변경은 동일 버전 유지 (spec §3.15)."""


class EventLog:
    """events.jsonl 라인 단위 추가 writer.

    사용 패턴:
        with EventLog(run_dir) as log:
            log.append(event)
    """

    def __init__(self, run_dir: Path) -> None:
        self._path: Path = run_dir / "events.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self) -> EventLog:
        # buffering=1: line buffered (line이 끝날 때마다 flush)
        self._file = open(self._path, "a", buffering=1, encoding="utf-8")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None

    def append(self, event: Event) -> None:
        if self._file is None:
            raise RuntimeError(
                "EventLog used outside context manager. "
                "Use `with EventLog(run_dir) as log: log.append(...)`."
            )
        # Canonical JSON (spec §13.3): sort_keys + 고정 separators 로 byte-identical 출력.
        # ``ensure_ascii=True`` (default) 유지 — non-ASCII 가 \uXXXX 로 escape 되어 시스템
        # 인코딩 차이에도 동일 byte stream.
        line = json.dumps(
            {
                "schema_version": EVENT_SCHEMA_VERSION,
                "ts": event.ts.isoformat(),
                "type": event.type.value,
                "payload": serialize_event_payload(event.payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self._file.write(line + "\n")
