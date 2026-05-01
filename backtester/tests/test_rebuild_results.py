"""PR 19 analysis/rebuild + CLI rebuild-results 테스트 (Phase 2, spec §6.3)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.analysis.rebuild import rebuild_equity_curve, rebuild_results
from backtester.cli.main import _build_parser, cmd_rebuild_results, main
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.events import EVENT_SCHEMA_VERSION
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

UTC = timezone.utc


def _instrument() -> Instrument:
    return Instrument(
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


def _make_synthetic_parquet(target: Path, n_bars: int = 60) -> None:
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
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _run_engine(tmp_path: Path, run_id: str = "rebuild_smoke") -> Path:
    data_dir = tmp_path / "data"
    _make_synthetic_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=48)
    cfg = BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 3, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    return BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False).run().run_dir


def _write_synthetic_events(
    run_dir: Path,
    *,
    snapshots: list[tuple[datetime, str]],
    config_initial_equity: str = "100000",
) -> None:
    """events.jsonl + config.yaml 만 가진 fake run_dir 구성 (Engine 미실행)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ts, equity in snapshots:
            line = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "ts": ts.isoformat(),
                "type": "snapshot",
                "payload": {
                    "equity": equity,
                    "snapshot_reason": "periodic",
                },
            }
            f.write(json.dumps(line) + "\n")
    # 최소 config.yaml — _load_run_config 가 통과만 하면 됨
    (run_dir / "config.yaml").write_text(
        f"run_id: fake\nprimary_symbol: BTCUSDT\nprimary_timeframe: 1h\n"
        f"initial_equity: '{config_initial_equity}'\n",
        encoding="utf-8",
    )


# ---------- rebuild_equity_curve 단위 ---------------------------------------


def test_rebuild_equity_curve_writes_parquet_with_correct_schema(tmp_path: Path) -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    _write_synthetic_events(
        tmp_path,
        snapshots=[
            (base + timedelta(hours=i), str(10000 + i * 10))
            for i in range(5)
        ],
    )
    out = rebuild_equity_curve(tmp_path)
    assert out == tmp_path / "results" / "equity_curve.parquet"
    assert out.exists()

    df = pl.read_parquet(out)
    assert df.schema == {
        "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
        "equity": pl.Float64,
    }
    assert df.height == 5
    assert df["equity"].to_list() == [10000.0, 10010.0, 10020.0, 10030.0, 10040.0]


def test_rebuild_equity_curve_dedupes_same_timestamp_keeps_last(tmp_path: Path) -> None:
    """같은 ts 의 SNAPSHOT 다중 (FILL 직후 + periodic) 은 last 만 유지."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    _write_synthetic_events(
        tmp_path,
        snapshots=[
            (base, "10000"),  # fill
            (base, "10500"),  # periodic 같은 ts
            (base + timedelta(hours=1), "10600"),
        ],
    )
    out = rebuild_equity_curve(tmp_path)
    df = pl.read_parquet(out)
    assert df.height == 2
    # 첫 ts 는 마지막 값 10500
    assert df["equity"][0] == 10500.0


def test_rebuild_equity_curve_empty_events_yields_empty_parquet(tmp_path: Path) -> None:
    _write_synthetic_events(tmp_path, snapshots=[])
    out = rebuild_equity_curve(tmp_path)
    df = pl.read_parquet(out)
    assert df.height == 0
    assert "timestamp" in df.columns
    assert "equity" in df.columns


def test_rebuild_equity_curve_missing_events_jsonl_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="events.jsonl missing"):
        rebuild_equity_curve(tmp_path)


def test_rebuild_equity_curve_skips_snapshot_without_equity(tmp_path: Path) -> None:
    """``payload.equity`` 가 없는 SNAPSHOT (이상 데이터) 는 무시."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    run_dir = tmp_path
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "events.jsonl", "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "ts": base.isoformat(),
                    "type": "snapshot",
                    "payload": {"snapshot_reason": "periodic"},  # equity 없음
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "ts": (base + timedelta(hours=1)).isoformat(),
                    "type": "snapshot",
                    "payload": {"equity": "10000", "snapshot_reason": "periodic"},
                }
            )
            + "\n"
        )
    (run_dir / "config.yaml").write_text(
        "run_id: fake\nprimary_symbol: BTCUSDT\nprimary_timeframe: 1h\n"
        "initial_equity: '10000'\n",
        encoding="utf-8",
    )
    out = rebuild_equity_curve(run_dir)
    df = pl.read_parquet(out)
    assert df.height == 1
    assert df["equity"][0] == 10000.0


# ---------- rebuild_results 통합 + Engine 출력과의 회귀 ---------------------


def test_rebuild_results_returns_mapping(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path)
    outputs = rebuild_results(run_dir)
    assert "equity_curve" in outputs
    assert outputs["equity_curve"].exists()


def test_rebuild_results_after_purging_results_dir(tmp_path: Path) -> None:
    """results/ 를 통째로 지운 뒤 rebuild → 다시 생성."""
    import shutil

    run_dir = _run_engine(tmp_path)
    shutil.rmtree(run_dir / "results")
    assert not (run_dir / "results" / "equity_curve.parquet").exists()
    outputs = rebuild_results(run_dir)
    assert outputs["equity_curve"].exists()


def test_rebuild_equity_curve_equity_sequence_matches_engine_in_single_tf(
    tmp_path: Path,
) -> None:
    """단일 TF + snapshot_every_bars=1 default 에서 rebuild 와 Engine runtime 의
    equity 값 시퀀스가 동일.

    timestamp 컨벤션은 다르다 — Engine 은 봉 시작 (MarketSnapshot.timestamp), rebuild
    는 봉 마감 (SNAPSHOT.ts). 두 시퀀스는 한 봉 만큼 시간 이동되어 있을 뿐 길이와
    equity 값은 동일. (rebuild docstring 의 시간 컨벤션 차이 회귀.)
    """
    run_dir = _run_engine(tmp_path)
    engine_path = run_dir / "results" / "equity_curve.parquet"
    engine_df = pl.read_parquet(engine_path).sort("timestamp")

    # results 비우고 rebuild
    engine_path.unlink()
    rebuild_equity_curve(run_dir)
    rebuild_df = pl.read_parquet(engine_path).sort("timestamp")

    assert rebuild_df.height > 0
    assert rebuild_df.schema == engine_df.schema
    # 봉 단위 emit 시점이 같으므로 길이 일치
    assert rebuild_df.height == engine_df.height
    # equity 시퀀스 (정렬 후 위치별) 일치
    eng_eq = engine_df["equity"].to_list()
    reb_eq = rebuild_df["equity"].to_list()
    for e, r in zip(eng_eq, reb_eq, strict=True):
        assert r == pytest.approx(e)


# ---------- CLI rebuild-results --------------------------------------------


def test_parser_rebuild_results_requires_run_dir() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["rebuild-results"])


def test_parser_rebuild_results_parses_quiet() -> None:
    parser = _build_parser()
    args = parser.parse_args(["rebuild-results", "runs/x", "--quiet"])
    assert args.cmd == "rebuild-results"
    assert args.run_dir == Path("runs/x")
    assert args.quiet is True


def test_cmd_rebuild_results_returns_2_when_run_dir_missing(tmp_path: Path) -> None:
    rc = cmd_rebuild_results(tmp_path / "nope", quiet=True)
    assert rc == 2


def test_cmd_rebuild_results_returns_2_when_events_jsonl_missing(tmp_path: Path) -> None:
    rd = tmp_path / "no_events"
    rd.mkdir()
    rc = cmd_rebuild_results(rd, quiet=True)
    assert rc == 2


def test_cmd_rebuild_results_succeeds_after_engine_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _run_engine(tmp_path)
    capsys.readouterr()  # flush
    rc = cmd_rebuild_results(run_dir, quiet=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Rebuilt equity_curve" in out
    assert (run_dir / "results" / "equity_curve.parquet").exists()


def test_main_dispatch_rebuild_results(tmp_path: Path) -> None:
    run_dir = _run_engine(tmp_path)
    rc = main(["rebuild-results", str(run_dir), "--quiet"])
    assert rc == 0
