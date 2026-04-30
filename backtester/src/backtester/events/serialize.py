"""serialize_event_payload — 도메인 타입 → JSON-친화 형태 변환 (spec §3.15).

처리 순서 (중요 — 타입 hierarchy 때문):
1. None / bool 명시 (bool이 int 서브클래스라 int 검사보다 먼저)
2. Enum (str-Enum이 str 서브클래스라 str 검사보다 먼저)
3. int / str (직접 패스스루)
4. Decimal → str (정확성 보존)
5. float → 그대로 (JSON이 float 지원)
6. datetime / date → isoformat()
7. dataclass instance → asdict + 재귀
8. dict → {k: serialize(v)}
9. list / tuple / set / frozenset → [serialize(x) ...]
10. 그 외 → TypeError

asdict가 nested dataclass를 자동 변환하므로 `serialize(asdict(obj))` 한 번으로 깊은
변환이 끝난다.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any


def serialize_event_payload(obj: Any) -> Any:
    """도메인 객체를 JSON.dumps에 안전한 구조로 재귀 변환."""
    # None / bool 먼저 (bool은 int 서브클래스)
    if obj is None or isinstance(obj, bool):
        return obj
    # Enum 먼저 (str-Enum은 str 서브클래스)
    if isinstance(obj, Enum):
        return obj.value
    # 기본 스칼라
    if isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, float):
        return obj
    # 시간
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    # Path → str (config.json / run_dir 직렬화에 활용)
    if isinstance(obj, PurePath):
        return str(obj)
    # dataclass instance (class object 자체는 제외)
    if is_dataclass(obj) and not isinstance(obj, type):
        return serialize_event_payload(asdict(obj))
    # 컨테이너
    if isinstance(obj, dict):
        return {k: serialize_event_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [serialize_event_payload(x) for x in obj]
    raise TypeError(
        f"Cannot serialize {type(obj).__name__} for EventLog payload: {obj!r}"
    )
