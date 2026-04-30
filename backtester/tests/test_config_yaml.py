"""PR 9 BacktestConfig YAML round-trip 테스트 (Phase 1.5, spec §6.4)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.errors import ConfigError
from backtester.core.types import BarPathModel
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.risk import RiskLimits

UTC = timezone.utc


def _btc_instrument() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _full_config(tmp_path: Path) -> BacktestConfig:
    """모든 필드 (default 포함) 채운 회귀용 config."""
    return BacktestConfig(
        run_id="ytest_round_trip",
        data_source=DataSourceConfig(base_dir=tmp_path / "data", type="parquet"),
        instruments=[_btc_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 4, 30, tzinfo=UTC),
        gap_policy="notify",
        execution_model="next_bar_open",
        bar_path_model=BarPathModel.PESSIMISTIC,
        slippage_bps=2.5,
        initial_equity=Decimal("100000.50"),
        risk_limits=RiskLimits(
            max_orders_per_symbol=10,
            blacklist_symbols=frozenset({"DOGEUSDT", "SHIBUSDT"}),
        ),
        warmup_bars=20,
        random_seed=42,
        output_dir=tmp_path / "runs",
        log_level="DEBUG",
        persist_run_data="copy",
        snapshot_every_bars=5,
        on_run_exists="auto_suffix",
        strategy_name="bbkc_squeeze",
        strategy_params={
            "bb_period": 20,
            "bb_num_std": 1.5,
            "kc_use_ema": True,
        },
    )


# ---------- 새 필드 ---------------------------------------------------------


def test_strategy_name_and_params_default_empty(tmp_path: Path) -> None:
    """기존 호출자가 strategy_name 안 줘도 default 로 동작 — Phase 1 코드 호환."""
    cfg = BacktestConfig(
        run_id="x",
        data_source=DataSourceConfig(base_dir=tmp_path / "d"),
        instruments=[_btc_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC),
        initial_equity=Decimal("100"),
        output_dir=tmp_path / "r",
    )
    assert cfg.strategy_name == ""
    assert cfg.strategy_params == {}


# ---------- to_dict / from_dict --------------------------------------------


def test_to_dict_returns_yaml_friendly_types(tmp_path: Path) -> None:
    cfg = _full_config(tmp_path)
    d = cfg.to_dict()
    # primitives only — pyyaml safe_dump-able
    assert isinstance(d["initial_equity"], str)  # Decimal → str
    assert isinstance(d["start"], str)  # datetime → ISO
    assert isinstance(d["output_dir"], str)  # Path → str
    assert d["bar_path_model"] == "pessimistic"  # enum value
    assert sorted(d["risk_limits"]["blacklist_symbols"]) == ["DOGEUSDT", "SHIBUSDT"]
    assert d["strategy_name"] == "bbkc_squeeze"
    assert d["strategy_params"]["bb_period"] == 20


def test_from_dict_round_trip_preserves_equality(tmp_path: Path) -> None:
    cfg = _full_config(tmp_path)
    d = cfg.to_dict()
    cfg2 = BacktestConfig.from_dict(d)
    assert cfg2 == cfg


def test_from_dict_rejects_unknown_keys(tmp_path: Path) -> None:
    cfg = _full_config(tmp_path)
    d = cfg.to_dict()
    d["bogus_key"] = 42
    with pytest.raises(ConfigError, match="unknown keys"):
        BacktestConfig.from_dict(d)


def test_from_dict_ignores_audit_fields(tmp_path: Path) -> None:
    """Engine 영속화 시 추가하는 ``resolved_run_id`` / ``run_dir`` 는 read 시 무시."""
    cfg = _full_config(tmp_path)
    d = cfg.to_dict()
    d["resolved_run_id"] = "ytest_round_trip_2"
    d["run_dir"] = str(tmp_path / "runs" / "ytest_round_trip_2")
    cfg2 = BacktestConfig.from_dict(d)
    assert cfg2 == cfg


# ---------- to_yaml / from_yaml --------------------------------------------


def test_yaml_round_trip_via_file(tmp_path: Path) -> None:
    cfg = _full_config(tmp_path)
    yaml_path = tmp_path / "config.yaml"
    cfg.to_yaml(yaml_path)
    assert yaml_path.exists()

    # 사람이 읽을 수 있는지 sanity (특정 키 존재)
    text = yaml_path.read_text(encoding="utf-8")
    assert "run_id: ytest_round_trip" in text
    assert "strategy_name: bbkc_squeeze" in text
    assert "bar_path_model: pessimistic" in text

    cfg2 = BacktestConfig.from_yaml(yaml_path)
    assert cfg2 == cfg


def test_yaml_decimal_precision_preserved(tmp_path: Path) -> None:
    cfg = BacktestConfig(
        run_id="x",
        data_source=DataSourceConfig(base_dir=tmp_path / "d"),
        instruments=[_btc_instrument()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC),
        initial_equity=Decimal("123456789.123456789"),
        output_dir=tmp_path / "r",
    )
    yaml_path = tmp_path / "c.yaml"
    cfg.to_yaml(yaml_path)
    cfg2 = BacktestConfig.from_yaml(yaml_path)
    assert cfg2.initial_equity == Decimal("123456789.123456789")


def test_yaml_user_authored_minimal(tmp_path: Path) -> None:
    """default 가 있는 옵션 필드 일부 생략 OK — __post_init__ 통과 가능한 minimal."""
    yaml_text = (
        "run_id: minimal\n"
        f"data_source:\n  base_dir: {tmp_path / 'data'}\n  type: parquet\n"
        "instruments:\n"
        "  - symbol: BTCUSDT\n"
        "    asset_class: crypto_perp\n"
        "    tick_size: '0.01'\n"
        "    tick_value: '0.01'\n"
        "    contract_multiplier: '1'\n"
        "    quote_currency: USDT\n"
        "    base_currency: BTC\n"
        "    size_unit: base_asset\n"
        "    fee_model:\n      type: flat\n      taker: '0'\n"
        "timeframes_per_symbol:\n  BTCUSDT: [1h]\n"
        "primary_symbol: BTCUSDT\n"
        "primary_timeframe: 1h\n"
        "start: '2026-03-01T00:00:00+00:00'\n"
        "end: '2026-04-01T00:00:00+00:00'\n"
        "initial_equity: '10000'\n"
        f"output_dir: {tmp_path / 'runs'}\n"
    )
    p = tmp_path / "minimal.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    cfg = BacktestConfig.from_yaml(p)
    assert cfg.run_id == "minimal"
    assert cfg.gap_policy == "notify"  # default
    assert cfg.bar_path_model == BarPathModel.PESSIMISTIC  # default
    assert cfg.persist_run_data == "copy"  # default


def test_yaml_invalid_root_type_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- list\n- not a mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a mapping"):
        BacktestConfig.from_yaml(p)


def test_yaml_validation_runs_on_load(tmp_path: Path) -> None:
    """잘못된 값 (initial_equity <= 0) 은 from_yaml 시점에 ConfigError."""
    cfg = _full_config(tmp_path)
    d = cfg.to_dict()
    d["initial_equity"] = "0"
    p = tmp_path / "bad.yaml"
    with open(p, "w", encoding="utf-8") as fp:
        yaml.safe_dump(d, fp)
    with pytest.raises(ConfigError, match="initial_equity"):
        BacktestConfig.from_yaml(p)


def test_yaml_naive_datetime_rejected(tmp_path: Path) -> None:
    cfg = _full_config(tmp_path)
    d = cfg.to_dict()
    d["start"] = "2026-03-01T00:00:00"  # naive (no offset)
    p = tmp_path / "bad.yaml"
    with open(p, "w", encoding="utf-8") as fp:
        yaml.safe_dump(d, fp)
    with pytest.raises(ConfigError, match="timezone-aware"):
        BacktestConfig.from_yaml(p)


# ---------- DataSourceConfig.type 검증 (PR9 후속 정정) ---------------------


def test_data_source_config_rejects_unknown_type(tmp_path: Path) -> None:
    """Literal 은 런타임 강제 안 되지만 __post_init__ 가 ConfigError 로 차단."""
    with pytest.raises(ConfigError, match="DataSourceConfig.type"):
        DataSourceConfig(base_dir=tmp_path, type="sqlite")  # type: ignore[arg-type]


def test_data_source_config_accepts_csv(tmp_path: Path) -> None:
    ds = DataSourceConfig(base_dir=tmp_path, type="csv")
    assert ds.type == "csv"


def test_yaml_data_source_unknown_type_rejected(tmp_path: Path) -> None:
    """YAML 에서 type='sqlite' 같은 값이 들어오면 from_yaml 시점에 ConfigError."""
    cfg = _full_config(tmp_path)
    d = cfg.to_dict()
    d["data_source"]["type"] = "sqlite"
    p = tmp_path / "bad.yaml"
    with open(p, "w", encoding="utf-8") as fp:
        yaml.safe_dump(d, fp)
    with pytest.raises(ConfigError, match="DataSourceConfig.type"):
        BacktestConfig.from_yaml(p)
