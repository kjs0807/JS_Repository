"""Equity 시리즈 빌더 (Phase 1.5 PR 10, spec §10.3).

``EventLogReader`` 의 SNAPSHOT 이벤트들을 polars DataFrame 으로 펼친다.

출력 컬럼 (정렬: ``timestamp`` 오름차순):
- ``timestamp`` (Datetime UTC)
- ``equity`` (Float64)
- ``cash`` (Float64)
- ``realized_pnl`` (Float64)
- ``unrealized_pnl`` (Float64)
- ``position_size_{symbol}`` (Float64) — 보유 심볼별 size. 다른 시점에서 0인 심볼은 null.
- ``drawdown`` (Float64) — ``equity - running_max`` (≤ 0)
- ``drawdown_pct`` (Float64) — ``drawdown / running_max`` (≤ 0). running_max=0 인 경계는 null.

같은 ``ts`` 에 여러 SNAPSHOT (예: FILL 직후 + 같은 봉 마감 periodic) 이 찍히는 케이스는
spec §3.15 가 명시 허용 — 본 함수는 ``group_by(ts, maintain_order=True).last()`` 로 마지막
값만 사용 (의미상 같은 ts 안의 가장 최신 상태가 그 시점의 정확한 ledger).

`initial_equity` 는 향후 첫 봉 이전 baseline 보강에 쓸 수 있으나 Phase 1.5 PR 10 에서는
SNAPSHOT 자체에 equity 가 이미 들어 있어 baseline 추가는 PR 11 (run_chart) 또는 후속 PR
에서 필요해질 때 도입한다.
"""

from __future__ import annotations

from decimal import Decimal

import polars as pl

from backtester.events.reader import EventLogReader
from backtester.events.types import EventType


def _empty_schema() -> dict[str, pl.DataType]:
    return {
        "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
        "equity": pl.Float64(),
        "cash": pl.Float64(),
        "realized_pnl": pl.Float64(),
        "unrealized_pnl": pl.Float64(),
        "drawdown": pl.Float64(),
        "drawdown_pct": pl.Float64(),
    }


def build_equity_series(
    reader: EventLogReader,
    initial_equity: Decimal,  # noqa: ARG001 — 후속 PR baseline 보강 시 활용
) -> pl.DataFrame:
    """SNAPSHOT 이벤트 시퀀스를 equity 시리즈 DataFrame 으로 변환."""
    rows: list[dict[str, object]] = []
    for snap in reader.by_type(EventType.SNAPSHOT):
        payload = snap.payload
        row: dict[str, object] = {
            "timestamp": snap.ts,
            "equity": float(payload["equity"]),
            "cash": float(payload.get("cash", 0)),
            "realized_pnl": float(payload.get("realized_pnl", 0)),
            "unrealized_pnl": float(payload.get("unrealized_pnl", 0)),
        }
        for sym, p in payload.get("positions", {}).items():
            row[f"position_size_{sym}"] = float(p["size"])
        rows.append(row)

    if not rows:
        return pl.DataFrame(schema=_empty_schema())

    df = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).sort("timestamp")

    # 같은 ts 중복 제거 — 마지막 값만 유지 (spec §10.3, §11.5)
    df = df.group_by("timestamp", maintain_order=True).last().sort("timestamp")

    # Drawdown 계산
    df = df.with_columns(pl.col("equity").cum_max().alias("_running_max"))
    df = df.with_columns(
        (pl.col("equity") - pl.col("_running_max")).alias("drawdown"),
        pl.when(pl.col("_running_max") != 0)
        .then(
            (pl.col("equity") - pl.col("_running_max")) / pl.col("_running_max")
        )
        .otherwise(None)
        .alias("drawdown_pct"),
    ).drop("_running_max")

    return df
