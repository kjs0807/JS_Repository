"""``events.jsonl`` → ``events.parquet`` 변환 (Phase 1.5 PR 9, spec §6.2).

분석 편의용 산출물 — events.jsonl 이 1차 원본 (spec §6.3) 이고 parquet 은 cache.

스키마:
    schema_version: pl.Int64        # 라인별 EVENT_SCHEMA_VERSION
    ts:             pl.Datetime("us", time_zone="UTC")
    type:           pl.String       # EventType.value
    payload:        pl.String       # JSON-encoded payload blob (lossless)

payload 를 평면 컬럼으로 펼치지 않는 이유:
- event type 별로 payload 구조가 달라 단일 스키마로 만들기 어렵다.
- 분석 시 ``df.with_columns(pl.col("payload").str.json_decode(...))`` 로 필요한 필드만 추출.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl


def events_jsonl_to_parquet(jsonl_path: Path, parquet_path: Path) -> Path:
    """``jsonl_path`` 를 읽어 ``parquet_path`` 로 변환. parquet 절대 경로 반환."""
    if not jsonl_path.exists():
        raise FileNotFoundError(f"events jsonl not found: {jsonl_path}")

    schema_versions: list[int] = []
    timestamps: list[datetime] = []
    types: list[str] = []
    payloads: list[str] = []

    with open(jsonl_path, encoding="utf-8") as fp:
        for raw_line in fp:
            line = raw_line.strip()
            if not line:
                continue
            obj = json.loads(line)
            schema_versions.append(int(obj["schema_version"]))
            timestamps.append(datetime.fromisoformat(obj["ts"]))
            types.append(str(obj["type"]))
            # payload 는 dict — JSON 문자열로 보존 (lossless)
            payloads.append(json.dumps(obj.get("payload"), ensure_ascii=False))

    df = pl.DataFrame(
        {
            "schema_version": schema_versions,
            "ts": timestamps,
            "type": types,
            "payload": payloads,
        }
    ).with_columns(
        pl.col("schema_version").cast(pl.Int64),
        pl.col("ts").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("type").cast(pl.String),
        pl.col("payload").cast(pl.String),
    )

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(parquet_path)
    return parquet_path
