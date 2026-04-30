"""PR 9 events.jsonl → events.parquet 변환 테스트 (Phase 1.5 spec §6.2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.events import EVENT_SCHEMA_VERSION, EventLog, events_jsonl_to_parquet
from backtester.events.types import Event, EventType
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

UTC = timezone.utc


# ---------- 단위: events_jsonl_to_parquet -----------------------------------


def _write_synthetic_jsonl(path: Path, n: int) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            line = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "ts": (base + timedelta(hours=i)).isoformat(),
                "type": "snapshot",
                "payload": {"equity": "1000", "snapshot_reason": "periodic", "i": i},
            }
            f.write(json.dumps(line) + "\n")


def test_events_jsonl_to_parquet_basic(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "events.jsonl"
    _write_synthetic_jsonl(jsonl_path, n=5)

    parquet_path = tmp_path / "events.parquet"
    out = events_jsonl_to_parquet(jsonl_path, parquet_path)
    assert out == parquet_path
    assert parquet_path.exists()

    df = pl.read_parquet(parquet_path)
    assert df.height == 5
    assert df.schema["schema_version"] == pl.Int64
    assert df.schema["ts"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert df.schema["type"] == pl.String
    assert df.schema["payload"] == pl.String


def test_events_jsonl_to_parquet_payload_round_trip_via_json(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "events.jsonl"
    _write_synthetic_jsonl(jsonl_path, n=3)
    parquet_path = tmp_path / "events.parquet"
    events_jsonl_to_parquet(jsonl_path, parquet_path)
    df = pl.read_parquet(parquet_path)
    # payload 컬럼이 JSON 문자열인지 + 디코드 가능
    payload_0 = json.loads(df["payload"][0])
    assert payload_0["snapshot_reason"] == "periodic"
    assert payload_0["i"] == 0


def test_events_jsonl_to_parquet_skips_blank_lines(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for i in range(2):
            f.write(
                json.dumps(
                    {
                        "schema_version": EVENT_SCHEMA_VERSION,
                        "ts": (base + timedelta(hours=i)).isoformat(),
                        "type": "snapshot",
                        "payload": {},
                    }
                )
                + "\n"
            )
            f.write("\n")  # 빈 라인
    parquet_path = tmp_path / "events.parquet"
    events_jsonl_to_parquet(jsonl_path, parquet_path)
    df = pl.read_parquet(parquet_path)
    assert df.height == 2  # 빈 라인 무시


def test_events_jsonl_to_parquet_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="events jsonl"):
        events_jsonl_to_parquet(tmp_path / "nope.jsonl", tmp_path / "out.parquet")


def test_eventlog_followed_by_export(tmp_path: Path) -> None:
    """EventLog 로 쓴 후 변환 함수가 라인수 보존."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    with EventLog(run_dir) as log:
        for i in range(4):
            log.append(
                Event(
                    ts=base + timedelta(hours=i),
                    type=EventType.SNAPSHOT,
                    payload={"equity": "1", "snapshot_reason": "periodic"},
                )
            )

    parquet_path = run_dir / "events.parquet"
    events_jsonl_to_parquet(run_dir / "events.jsonl", parquet_path)
    df = pl.read_parquet(parquet_path)
    assert df.height == 4
    assert all(t == "snapshot" for t in df["type"].to_list())


# ---------- Engine wiring: run() 끝에 events.parquet 자동 생성 ---------------


def _make_btc_synthetic_parquet(target: Path, n_bars: int = 60) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(n_bars):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 1.0,
            }
        )
    df = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def test_engine_run_writes_events_parquet_alongside_jsonl(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_btc_synthetic_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=60)
    instrument = Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )
    cfg = BacktestConfig(
        run_id="event_parquet_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[instrument],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 4, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    result = engine.run()

    assert (result.run_dir / "events.jsonl").exists()
    parquet_path = result.run_dir / "events.parquet"
    assert parquet_path.exists()

    # 두 파일 라인/행 수 일치
    jsonl_lines = sum(
        1
        for line in (result.run_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    df = pl.read_parquet(parquet_path)
    assert df.height == jsonl_lines
