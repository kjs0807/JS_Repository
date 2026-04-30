"""PR 10 build_equity_series 테스트 (Phase 1.5, spec §10.3)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.events import EVENT_SCHEMA_VERSION
from backtester.events.reader import EventLogReader
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from backtester.viz.equity import build_equity_series

UTC = timezone.utc


def _write_snapshots(
    path: Path, snaps: list[tuple[datetime, str, dict[str, object]]]
) -> None:
    """snaps: [(ts, snapshot_reason, payload_overrides), ...]"""
    with open(path, "w", encoding="utf-8") as f:
        for ts, reason, overrides in snaps:
            payload: dict[str, object] = {
                "equity": "10000",
                "cash": "10000",
                "realized_pnl": "0",
                "unrealized_pnl": "0",
                "positions": {},
                "snapshot_reason": reason,
            }
            payload.update(overrides)
            line = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "ts": ts.isoformat(),
                "type": "snapshot",
                "payload": payload,
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _reader(path: Path) -> EventLogReader:
    return EventLogReader(path)


# ---------- 기본 동작 -------------------------------------------------------


def test_build_equity_series_empty(tmp_path: Path) -> None:
    """SNAPSHOT 0건 → 스키마만 있는 빈 DataFrame."""
    p = tmp_path / "events.jsonl"
    p.write_text("", encoding="utf-8")
    df = build_equity_series(_reader(p), initial_equity=Decimal("10000"))
    assert df.height == 0
    assert df.schema["timestamp"] == pl.Datetime(time_unit="us", time_zone="UTC")
    assert "equity" in df.columns
    assert "drawdown" in df.columns
    assert "drawdown_pct" in df.columns


def test_build_equity_series_single_snapshot(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_snapshots(p, [(base, "periodic", {"equity": "10000"})])
    df = build_equity_series(_reader(p), initial_equity=Decimal("10000"))
    assert df.height == 1
    assert df["equity"][0] == 10000.0
    assert df["drawdown"][0] == 0.0
    assert df["drawdown_pct"][0] == 0.0


def test_build_equity_series_multiple_snapshots_drawdown(tmp_path: Path) -> None:
    """equity 가 12000 정점 후 9000 으로 떨어지면 drawdown=-3000, drawdown_pct=-0.25."""
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_snapshots(
        p,
        [
            (base + timedelta(hours=0), "periodic", {"equity": "10000"}),
            (base + timedelta(hours=1), "periodic", {"equity": "12000"}),
            (base + timedelta(hours=2), "periodic", {"equity": "11000"}),
            (base + timedelta(hours=3), "periodic", {"equity": "9000"}),
            (base + timedelta(hours=4), "periodic", {"equity": "13000"}),
        ],
    )
    df = build_equity_series(_reader(p), initial_equity=Decimal("10000"))
    assert df.height == 5
    eq = df["equity"].to_list()
    assert eq == [10000.0, 12000.0, 11000.0, 9000.0, 13000.0]
    dd = df["drawdown"].to_list()
    # running_max: 10000, 12000, 12000, 12000, 13000
    assert dd == [0.0, 0.0, -1000.0, -3000.0, 0.0]
    pct = df["drawdown_pct"].to_list()
    assert pct[2] == pytest.approx(-1000.0 / 12000.0)
    assert pct[3] == pytest.approx(-3000.0 / 12000.0)
    assert pct[4] == 0.0


def test_build_equity_series_dedup_same_ts_keep_last(tmp_path: Path) -> None:
    """같은 ts 의 여러 SNAPSHOT 은 마지막 값만 유지 (FILL 직후 + 같은 봉 periodic 케이스)."""
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_snapshots(
        p,
        [
            (base, "fill", {"equity": "10500", "realized_pnl": "500"}),
            (base, "periodic", {"equity": "10500", "realized_pnl": "500"}),
            (base + timedelta(hours=1), "periodic", {"equity": "10800"}),
        ],
    )
    df = build_equity_series(_reader(p), initial_equity=Decimal("10000"))
    assert df.height == 2
    assert df["timestamp"][0] == base
    assert df["timestamp"][1] == base + timedelta(hours=1)


def test_build_equity_series_extracts_position_columns(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    _write_snapshots(
        p,
        [
            (
                base,
                "fill",
                {
                    "equity": "10500",
                    "positions": {
                        "BTCUSDT": {"size": "0.1", "avg_price": "50000", "unrealized_pnl": "500"}
                    },
                },
            ),
            (
                base + timedelta(hours=1),
                "periodic",
                {
                    "equity": "10500",
                    "positions": {
                        "BTCUSDT": {"size": "0.1", "avg_price": "50000", "unrealized_pnl": "500"}
                    },
                },
            ),
        ],
    )
    df = build_equity_series(_reader(p), initial_equity=Decimal("10000"))
    assert "position_size_BTCUSDT" in df.columns
    assert df["position_size_BTCUSDT"][0] == 0.1


def test_build_equity_series_handles_missing_optional_fields(tmp_path: Path) -> None:
    """payload 에 ``cash`` / ``realized_pnl`` / ``unrealized_pnl`` 가 없어도 0 으로 처리."""
    p = tmp_path / "events.jsonl"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    with open(p, "w", encoding="utf-8") as f:
        line = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "ts": base.isoformat(),
            "type": "snapshot",
            "payload": {"equity": "10000", "snapshot_reason": "periodic"},
        }
        f.write(json.dumps(line) + "\n")
    df = build_equity_series(_reader(p), initial_equity=Decimal("10000"))
    assert df["cash"][0] == 0.0
    assert df["realized_pnl"][0] == 0.0
    assert df["unrealized_pnl"][0] == 0.0


# ---------- BacktestEngine 와의 통합 ---------------------------------------


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


def test_build_equity_series_from_engine_run(tmp_path: Path) -> None:
    """BacktestEngine 실행 후 events.jsonl → reader → equity 시리즈."""
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
        run_id="equity_smoke",
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

    reader = EventLogReader(result.events_path)
    df = build_equity_series(reader, initial_equity=cfg.initial_equity)
    assert df.height > 0
    # equity 시작값은 initial_equity 근처 (warmup 직후 첫 SNAPSHOT)
    assert df["equity"][0] == pytest.approx(100000.0, abs=1.0)
    # drawdown 은 항상 ≤ 0
    assert all(d <= 0 for d in df["drawdown"].to_list())
