"""PR 7 on_run_exists 4가지 정책 테스트 (spec §20)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.errors import RunDirectoryError
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


def _btc() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _make_parquet(tmp_path: Path, n_bars: int = 5) -> Path:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0 + i for i in range(n_bars)],
            "high": [101.0 + i for i in range(n_bars)],
            "low": [99.0 + i for i in range(n_bars)],
            "close": [100.5 + i for i in range(n_bars)],
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.write_parquet(data_dir / "BTCUSDT_1h.parquet")
    return data_dir


def _config(tmp_path: Path, **overrides: Any) -> BacktestConfig:
    data_dir = _make_parquet(tmp_path)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs: dict[str, Any] = {
        "run_id": "test_run",
        "data_source": DataSourceConfig(base_dir=data_dir),
        "instruments": [_btc()],
        "timeframes_per_symbol": {"BTCUSDT": ["1h"]},
        "primary_symbol": "BTCUSDT",
        "primary_timeframe": "1h",
        "start": base,
        "end": base + timedelta(hours=10),
        "initial_equity": Decimal("100000"),
        "output_dir": tmp_path / "runs",
    }
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


class _NoOp(BaseStrategy):
    def on_bar(self, ctx):  # type: ignore[no-untyped-def]
        return []


# ---------- fail (기본) -----------------------------------------------------


def test_run_dir_fail_when_not_exists_creates(tmp_path: Path) -> None:
    """target이 없으면 정상 생성. on_run_exists 정책 무관."""
    config = _config(tmp_path)
    engine = BacktestEngine(config, _NoOp(), verbose=False)
    assert engine.run_dir.exists()
    assert engine.resolved_run_id == "test_run"
    assert engine.run_dir == tmp_path / "runs" / "test_run"


def test_run_dir_fail_when_exists_raises(tmp_path: Path) -> None:
    """기본 'fail' 정책: 이미 존재하면 RunDirectoryError."""
    target = tmp_path / "runs" / "test_run"
    target.mkdir(parents=True)
    config = _config(tmp_path)  # default on_run_exists='fail'

    with pytest.raises(RunDirectoryError, match="already exists"):
        BacktestEngine(config, _NoOp(), verbose=False)


# ---------- overwrite -------------------------------------------------------


def test_run_dir_overwrite_removes_and_recreates(tmp_path: Path) -> None:
    target = tmp_path / "runs" / "test_run"
    target.mkdir(parents=True)
    sentinel = target / "sentinel.txt"
    sentinel.write_text("old")

    config = _config(tmp_path, on_run_exists="overwrite")
    engine = BacktestEngine(config, _NoOp(), verbose=False)
    assert engine.run_dir == target
    assert engine.resolved_run_id == "test_run"
    assert not sentinel.exists()  # 기존 파일 삭제


# ---------- auto_suffix -----------------------------------------------------


def test_run_dir_auto_suffix_increments(tmp_path: Path) -> None:
    """기존 디렉토리 + 'auto_suffix' → run_id_2."""
    (tmp_path / "runs" / "test_run").mkdir(parents=True)
    config = _config(tmp_path, on_run_exists="auto_suffix")
    engine = BacktestEngine(config, _NoOp(), verbose=False)

    assert engine.resolved_run_id == "test_run_2"
    assert engine.run_dir == tmp_path / "runs" / "test_run_2"
    assert engine.run_dir.exists()


def test_run_dir_auto_suffix_skips_taken_indices(tmp_path: Path) -> None:
    """test_run, test_run_2가 이미 있으면 test_run_3을 부여."""
    (tmp_path / "runs" / "test_run").mkdir(parents=True)
    (tmp_path / "runs" / "test_run_2").mkdir(parents=True)
    config = _config(tmp_path, on_run_exists="auto_suffix")
    engine = BacktestEngine(config, _NoOp(), verbose=False)

    assert engine.resolved_run_id == "test_run_3"


# ---------- archive ---------------------------------------------------------


def test_run_dir_archive_moves_existing(tmp_path: Path) -> None:
    target = tmp_path / "runs" / "test_run"
    target.mkdir(parents=True)
    sentinel = target / "old.txt"
    sentinel.write_text("history")

    config = _config(tmp_path, on_run_exists="archive")
    engine = BacktestEngine(config, _NoOp(), verbose=False)

    assert engine.resolved_run_id == "test_run"
    assert engine.run_dir == target
    assert engine.run_dir.exists()
    # archive_*가 sibling으로 생성됨
    runs_dir = tmp_path / "runs"
    archives = list(runs_dir.glob("test_run_archive_*"))
    assert len(archives) == 1
    # 기존 sentinel은 archive로 이동
    assert (archives[0] / "old.txt").exists()


# ---------- resolved_run_id가 BacktestResult + config.json + 디렉토리 일치 -


def test_resolved_run_id_propagated_to_result_and_config(tmp_path: Path) -> None:
    """auto_suffix 시 resolved_run_id가 BacktestResult, config.json, dir name 모두 일치."""
    (tmp_path / "runs" / "test_run").mkdir(parents=True)
    config = _config(tmp_path, on_run_exists="auto_suffix")
    engine = BacktestEngine(config, _NoOp(), verbose=False)
    result = engine.run()

    # BacktestResult
    assert result.requested_run_id == "test_run"
    assert result.resolved_run_id == "test_run_2"
    assert result.run_dir.name == "test_run_2"

    # config.json
    import json

    config_data = json.loads(result.config_path.read_text(encoding="utf-8"))
    assert config_data["run_id"] == "test_run"
    assert config_data["requested_run_id"] == "test_run"  # 명시적 audit 필드
    assert config_data["resolved_run_id"] == "test_run_2"
    assert config_data["run_dir"].endswith("test_run_2")


# ---------- config.json은 persist_run_data와 독립 ---------------------------


def test_config_json_exists_even_with_persist_none(tmp_path: Path) -> None:
    """persist_run_data='none'이어도 config.json은 항상 생성 (audit 산출물)."""
    config = _config(tmp_path, persist_run_data="none")
    engine = BacktestEngine(config, _NoOp(), verbose=False)
    result = engine.run()

    assert result.config_path.exists()
    # bars/는 persist 안 됨
    bars_persisted = list((engine.run_dir / "bars").iterdir())
    assert bars_persisted == []


def test_config_json_includes_requested_run_id_explicit_field(tmp_path: Path) -> None:
    """spec §20 PR 7: requested_run_id 명시 필드 보장 (run_id와 구분되는 audit 키)."""
    config = _config(tmp_path)
    engine = BacktestEngine(config, _NoOp(), verbose=False)
    result = engine.run()

    import json

    cfg = json.loads(result.config_path.read_text(encoding="utf-8"))
    assert "requested_run_id" in cfg
    assert "resolved_run_id" in cfg
    assert "run_dir" in cfg
    assert cfg["requested_run_id"] == config.run_id


# ---------- archive 정책 충돌 처리 ------------------------------------------


def test_archive_path_uses_microsecond_precision(tmp_path: Path) -> None:
    """archive 디렉토리명에 microsecond(`_%f`)가 포함되어 같은 초 충돌을 거의 차단."""
    target = tmp_path / "runs" / "test_run"
    target.mkdir(parents=True)
    config = _config(tmp_path, on_run_exists="archive")
    BacktestEngine(config, _NoOp(), verbose=False)

    archives = list((tmp_path / "runs").glob("test_run_archive_*"))
    assert len(archives) == 1
    # 형식: test_run_archive_YYYYMMDD_HHMMSS_microseconds
    name = archives[0].name
    parts = name.removeprefix("test_run_archive_").split("_")
    # date(8) + time(6) + us(6) = 3 underscore-separated segments
    assert len(parts) == 3, f"Expected 3 segments, got {parts!r}"
    assert len(parts[0]) == 8  # YYYYMMDD
    assert len(parts[1]) == 6  # HHMMSS
    assert len(parts[2]) == 6  # microseconds


def test_archive_collision_appends_suffix(tmp_path: Path) -> None:
    """timestamp 주입형 헬퍼(`_archive_path_with_suffix`)로 _2/_3 분기를 결정적으로 검증."""
    parent = tmp_path / "runs"
    parent.mkdir()
    ts_str = "20260101_120000_000000"

    # 1차: 충돌 없음 → base 경로
    p1 = BacktestEngine._archive_path_with_suffix(parent, "test_run", ts_str)
    assert p1.name == f"test_run_archive_{ts_str}"
    p1.mkdir()

    # 2차: 동일 timestamp 충돌 → _2 부여
    p2 = BacktestEngine._archive_path_with_suffix(parent, "test_run", ts_str)
    assert p2.name == f"test_run_archive_{ts_str}_2"
    p2.mkdir()

    # 3차: 다시 → _3 부여
    p3 = BacktestEngine._archive_path_with_suffix(parent, "test_run", ts_str)
    assert p3.name == f"test_run_archive_{ts_str}_3"


def test_archive_two_runs_in_quick_succession_succeed(tmp_path: Path) -> None:
    """microsecond 정밀도 덕에 빠른 연속 archive 두 번 모두 성공."""
    (tmp_path / "runs" / "test_run").mkdir(parents=True)
    config = _config(tmp_path, on_run_exists="archive")
    # 1차 — 기존 target → archive_<ts1>, 새 target 자동 생성
    BacktestEngine(config, _NoOp(), verbose=False)
    # 2차 — 새 target → archive_<ts2> (microsecond 단위로 다른 path)
    BacktestEngine(config, _NoOp(), verbose=False)

    archives = list((tmp_path / "runs").glob("test_run_archive_*"))
    assert len(archives) == 2
    # 두 archive 경로가 서로 다름
    assert archives[0] != archives[1]
