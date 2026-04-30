"""PR 9 CLI ``backtester run`` 테스트 (Phase 1.5)."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backtester.cli.main import _build_parser, cmd_report, cmd_run, main
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.instruments.base import FeeModel, Instrument

UTC = timezone.utc

FIXTURE_DIR = Path(__file__).parent / "fixtures"
ETHUSDT_PARQUET = FIXTURE_DIR / "ETHUSDT_1h.parquet"


def _write_minimal_yaml(
    yaml_path: Path,
    *,
    data_dir: Path,
    output_dir: Path,
    run_id: str = "cli_test",
    strategy_name: str = "bbkc_squeeze",
) -> None:
    """ETHUSDT_1h.parquet fixture 가 ``data_dir`` 에 이미 있다고 가정."""
    cfg = BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir, type="parquet"),
        instruments=[
            Instrument(
                symbol="ETHUSDT",
                asset_class="crypto_perp",
                tick_size=Decimal("0.01"),
                tick_value=Decimal("0.01"),
                contract_multiplier=Decimal("1"),
                quote_currency="USDT",
                base_currency="ETH",
                size_unit="base_asset",
                fee_model=FeeModel(type="flat", taker=Decimal("0")),
            )
        ],
        timeframes_per_symbol={"ETHUSDT": ["1h"]},
        primary_symbol="ETHUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 4, 29, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=output_dir,
        strategy_name=strategy_name,
        strategy_params={},
    )
    cfg.to_yaml(yaml_path)


def _setup_data(tmp_path: Path) -> Path:
    if not ETHUSDT_PARQUET.exists():
        pytest.skip(
            f"ETHUSDT fixture parquet missing: {ETHUSDT_PARQUET}. Generate via "
            f"tools/export_db_to_parquet.py."
        )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(ETHUSDT_PARQUET, data_dir / "ETHUSDT_1h.parquet")
    return data_dir


# ---------- argparse 골격 ---------------------------------------------------


def test_parser_requires_subcommand() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_run_requires_config_path() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


def test_parser_run_parses_quiet_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(["run", "config.yaml", "--quiet"])
    assert args.cmd == "run"
    assert args.config_path == Path("config.yaml")
    assert args.quiet is True


# ---------- cmd_run end-to-end ----------------------------------------------


def test_cmd_run_returns_2_when_config_missing(tmp_path: Path) -> None:
    rc = cmd_run(tmp_path / "nope.yaml", quiet=True)
    assert rc == 2


def test_cmd_run_returns_2_when_strategy_unknown(tmp_path: Path) -> None:
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "bad.yaml"
    _write_minimal_yaml(
        yaml_path,
        data_dir=data_dir,
        output_dir=tmp_path / "runs",
        strategy_name="unknown_strategy",
    )
    rc = cmd_run(yaml_path, quiet=True)
    assert rc == 2


def test_cmd_run_returns_2_when_strategy_name_empty(tmp_path: Path) -> None:
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "empty_strategy.yaml"
    _write_minimal_yaml(
        yaml_path,
        data_dir=data_dir,
        output_dir=tmp_path / "runs",
        strategy_name="",
    )
    rc = cmd_run(yaml_path, quiet=True)
    assert rc == 2


def test_cmd_run_quiet_succeeds_and_creates_run_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "ok.yaml"
    output_dir = tmp_path / "runs"
    _write_minimal_yaml(
        yaml_path,
        data_dir=data_dir,
        output_dir=output_dir,
    )

    rc = cmd_run(yaml_path, quiet=True)
    assert rc == 0
    captured = capsys.readouterr()
    # quiet → INFO 출력 0
    assert "[INFO]" not in captured.out

    run_dir = output_dir / "cli_test"
    assert run_dir.exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "config.json").exists()


def test_cmd_run_verbose_prints_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "ok.yaml"
    _write_minimal_yaml(
        yaml_path,
        data_dir=data_dir,
        output_dir=tmp_path / "runs",
    )
    rc = cmd_run(yaml_path, quiet=False)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Final equity" in captured.out
    assert "Run directory" in captured.out


def test_cmd_run_returns_2_on_run_dir_conflict(tmp_path: Path) -> None:
    """on_run_exists default 'fail' + 같은 run_id 두 번 → RunDirectoryError → rc=2."""
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "ok.yaml"
    _write_minimal_yaml(
        yaml_path,
        data_dir=data_dir,
        output_dir=tmp_path / "runs",
    )
    rc1 = cmd_run(yaml_path, quiet=True)
    assert rc1 == 0
    rc2 = cmd_run(yaml_path, quiet=True)
    assert rc2 == 2


def test_main_dispatch_run(tmp_path: Path) -> None:
    """``main(argv)`` 가 ``run`` 서브커맨드를 정상 dispatch."""
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "ok.yaml"
    _write_minimal_yaml(
        yaml_path,
        data_dir=data_dir,
        output_dir=tmp_path / "runs",
    )
    rc = main(["run", str(yaml_path), "--quiet"])
    assert rc == 0


# ---------- build_strategy TypeError → ConfigError (PR9 후속 정정) ---------


def test_build_strategy_wraps_typeerror_as_config_error() -> None:
    """잘못된 strategy_params 키로 들어오면 TypeError 가 ConfigError 로 감싸진다."""
    from backtester.core.errors import ConfigError
    from backtester.strategies.registry import build_strategy

    with pytest.raises(ConfigError, match="strategy_params"):
        build_strategy("bbkc_squeeze", {"nonexistent_param": 42})


# ---------- report 명령 (PR 11) --------------------------------------------


def test_parser_report_requires_run_dir() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["report"])


def test_parser_report_parses_quiet_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(["report", "runs/x", "--quiet"])
    assert args.cmd == "report"
    assert args.run_dir == Path("runs/x")
    assert args.quiet is True


def test_cmd_report_returns_2_when_run_dir_missing(tmp_path: Path) -> None:
    rc = cmd_report(tmp_path / "nope", quiet=True)
    assert rc == 2


def test_cmd_report_returns_2_when_events_jsonl_missing(tmp_path: Path) -> None:
    rd = tmp_path / "no_events"
    rd.mkdir()
    rc = cmd_report(rd, quiet=True)
    assert rc == 2


def test_cmd_report_renders_html_after_cmd_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """run 실행 후 같은 run_dir 에 report 실행 → run_chart.html 생성."""
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "ok.yaml"
    output_dir = tmp_path / "runs"
    _write_minimal_yaml(yaml_path, data_dir=data_dir, output_dir=output_dir)
    rc1 = cmd_run(yaml_path, quiet=True)
    assert rc1 == 0
    run_dir = output_dir / "cli_test"

    capsys.readouterr()  # flush prior output
    rc2 = cmd_report(run_dir, quiet=False)
    assert rc2 == 0
    captured = capsys.readouterr()
    assert "run_chart.html" in captured.out
    assert (run_dir / "charts" / "run_chart.html").exists()


def test_main_dispatch_report(tmp_path: Path) -> None:
    """``main(argv)`` 가 ``report`` 서브커맨드를 정상 dispatch."""
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "ok.yaml"
    output_dir = tmp_path / "runs"
    _write_minimal_yaml(yaml_path, data_dir=data_dir, output_dir=output_dir)
    main(["run", str(yaml_path), "--quiet"])
    rc = main(["report", str(output_dir / "cli_test"), "--quiet"])
    assert rc == 0


def test_cmd_run_returns_2_on_invalid_strategy_params(tmp_path: Path) -> None:
    """YAML 의 strategy_params 가 strategy 시그니처와 안 맞으면 cmd_run rc=2."""
    data_dir = _setup_data(tmp_path)
    yaml_path = tmp_path / "bad_params.yaml"
    cfg = BacktestConfig(
        run_id="bad_params",
        data_source=DataSourceConfig(base_dir=data_dir, type="parquet"),
        instruments=[
            Instrument(
                symbol="ETHUSDT",
                asset_class="crypto_perp",
                tick_size=Decimal("0.01"),
                tick_value=Decimal("0.01"),
                contract_multiplier=Decimal("1"),
                quote_currency="USDT",
                base_currency="ETH",
                size_unit="base_asset",
                fee_model=FeeModel(type="flat", taker=Decimal("0")),
            )
        ],
        timeframes_per_symbol={"ETHUSDT": ["1h"]},
        primary_symbol="ETHUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 4, 29, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        strategy_name="bbkc_squeeze",
        strategy_params={"bogus_param": 1},
    )
    cfg.to_yaml(yaml_path)
    rc = cmd_run(yaml_path, quiet=True)
    assert rc == 2
