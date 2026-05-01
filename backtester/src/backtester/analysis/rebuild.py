"""rebuild — events.jsonl 원본만으로 ``run_dir/results/`` 캐시 재생성 (Phase 2 PR 19).

spec §6.3 — events.jsonl 이 1차 원본, ``results/`` 는 캐시. 둘이 어긋나면 EventLog 기준.
``backtester rebuild-results runs/{run_id}/`` 로 캐시를 폐기하고 events.jsonl 만으로
모든 results 산출물을 재생성한다.

PR 19 산출물:
- ``rebuild_equity_curve(run_dir)`` — SNAPSHOT 이벤트 → ``results/equity_curve.parquet``.
- ``rebuild_results(run_dir)`` — 위 함수 호출 + 향후 PR 에서 추가될 results 캐시들.

Engine 런타임 ``Ledger.equity_curve`` 와의 차이:
- **시간 컨벤션**: Engine 은 ``MarketSnapshot.timestamp`` (= 봉 시작 시각) 을 적재.
  rebuild 는 ``SNAPSHOT`` 이벤트의 ``ts`` (= ``ClockEvent.timestamp`` = 봉 마감 시각)
  를 사용 → rebuild 의 timestamp 가 한 봉 길이만큼 미래로 이동. equity 값 시퀀스는
  동일.
- **멀티 TF**: Engine 은 매 ``ClockEvent`` (모든 TF close) 마다 on_market 호출.
  rebuild 는 ``SNAPSHOT`` 이벤트만 보므로 primary TF close 시점만. secondary TF
  단독 close 시점은 빠진다.

이 차이는 spec §6.3 acceptance ("EventLog 기준") 와 일관 — rebuild 의 봉-마감 timestamp
가 의미상 더 정확 (equity 가 봉 마감의 close 가격으로 mark-to-market 된 결과). Engine 의
봉-시작 timestamp 는 ``Ledger.on_market(snapshot)`` 시그니처 historical 잔재.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.events.reader import EventLogReader
from backtester.events.types import EventType


def _equity_curve_schema() -> dict[str, pl.DataType]:
    return {
        "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
        "equity": pl.Float64(),
    }


def rebuild_equity_curve(run_dir: Path) -> Path:
    """events.jsonl 의 SNAPSHOT 이벤트를 모아 ``results/equity_curve.parquet`` 재생성.

    같은 ``ts`` 의 다중 SNAPSHOT (예: FILL 직후 + periodic) 은 ``group_by + last`` 로
    하나의 row 로 dedup. 빈 events 는 빈 DataFrame (스키마만) 으로.
    """
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        raise FileNotFoundError(f"events.jsonl missing: {events_path}")

    reader = EventLogReader(events_path)
    rows: list[dict[str, object]] = []
    for snap in reader.by_type(EventType.SNAPSHOT):
        equity_str = snap.payload.get("equity")
        if equity_str is None:
            continue
        rows.append(
            {
                "timestamp": snap.ts,
                "equity": float(Decimal(str(equity_str))),
            }
        )

    if not rows:
        df = pl.DataFrame(schema=_equity_curve_schema())
    else:
        df = (
            pl.DataFrame(rows)
            .with_columns(
                pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
                pl.col("equity").cast(pl.Float64),
            )
            .sort("timestamp")
            .group_by("timestamp", maintain_order=True)
            .last()
            .sort("timestamp")
        )

    output = run_dir / "results" / "equity_curve.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output)
    return output


def rebuild_results(run_dir: Path) -> dict[str, Path]:
    """모든 ``results/`` 캐시 산출물을 events.jsonl 로부터 재생성.

    반환: ``{name: output_path}`` 매핑 — 호출자가 어떤 파일이 생성됐는지 확인 가능.
    PR 19 에서는 ``equity_curve`` 만. 후속 PR (PR 20 trades / metrics 캐시 등) 에서 추가.

    **계약**: events.jsonl 만 있으면 동작 — config.{yaml,json} 미존재여도 OK.
    후속 산출물 중 config 가 실제로 필요한 것이 추가되면 그 helper 가 ``_load_run_config``
    를 직접 호출하도록 한다 (현재는 equity_curve 만이라 config 미사용).
    """
    if not (run_dir.exists() and run_dir.is_dir()):
        raise FileNotFoundError(f"run dir not found or not a directory: {run_dir}")
    return {
        "equity_curve": rebuild_equity_curve(run_dir),
    }
