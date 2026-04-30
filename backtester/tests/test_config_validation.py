"""PR 7 BacktestConfig.__post_init__ 검증 테스트 (spec §5.1).

§5.1 검증 표의 모든 규칙을 단위 테스트로 직접 매핑.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.errors import ConfigError
from backtester.core.types import BarPathModel
from backtester.instruments.base import FeeModel, Instrument

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
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _good_kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "test",
        "data_source": DataSourceConfig(base_dir=tmp_path),
        "instruments": [_btc()],
        "timeframes_per_symbol": {"BTCUSDT": ["1h"]},
        "primary_symbol": "BTCUSDT",
        "primary_timeframe": "1h",
        "start": datetime(2026, 1, 1, tzinfo=UTC),
        "end": datetime(2026, 1, 2, tzinfo=UTC),
        "initial_equity": Decimal("100000"),
        "output_dir": tmp_path / "runs",
    }
    base.update(overrides)
    return base


# ---------- 정상 생성 -------------------------------------------------------


def test_good_config_constructs(tmp_path: Path) -> None:
    config = BacktestConfig(**_good_kwargs(tmp_path))
    assert config.run_id == "test"
    assert config.snapshot_every_bars == 1  # default
    assert config.on_run_exists == "fail"  # default


# ---------- 숫자 한도 -------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1])
def test_snapshot_every_bars_must_be_positive(tmp_path: Path, bad: int) -> None:
    with pytest.raises(ConfigError, match="snapshot_every_bars"):
        BacktestConfig(**_good_kwargs(tmp_path, snapshot_every_bars=bad))


def test_warmup_bars_negative_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="warmup_bars"):
        BacktestConfig(**_good_kwargs(tmp_path, warmup_bars=-1))


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-100")])
def test_initial_equity_must_be_positive(tmp_path: Path, bad: Decimal) -> None:
    with pytest.raises(ConfigError, match="initial_equity"):
        BacktestConfig(**_good_kwargs(tmp_path, initial_equity=bad))


def test_slippage_bps_negative_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="slippage_bps"):
        BacktestConfig(**_good_kwargs(tmp_path, slippage_bps=-0.5))


def test_random_seed_negative_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="random_seed"):
        BacktestConfig(**_good_kwargs(tmp_path, random_seed=-1))


# ---------- 시간 범위 -------------------------------------------------------


def test_start_must_be_less_than_end(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ConfigError, match="start must be < end"):
        BacktestConfig(
            **_good_kwargs(tmp_path, start=base, end=base)  # 동일
        )


def test_start_after_end_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="start must be < end"):
        BacktestConfig(
            **_good_kwargs(
                tmp_path,
                start=datetime(2026, 1, 2, tzinfo=UTC),
                end=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


# ---------- Literal/Enum --------------------------------------------------


@pytest.mark.parametrize("bad", ["bogus", "FAIL", "", None])
def test_on_run_exists_invalid_value(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(ConfigError, match="on_run_exists"):
        BacktestConfig(**_good_kwargs(tmp_path, on_run_exists=bad))


@pytest.mark.parametrize("bad", ["weird", "Copy", "", None])
def test_persist_run_data_invalid_value(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(ConfigError, match="persist_run_data"):
        BacktestConfig(**_good_kwargs(tmp_path, persist_run_data=bad))


def test_bar_path_model_must_be_enum_member(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="bar_path_model"):
        BacktestConfig(**_good_kwargs(tmp_path, bar_path_model="pessimistic"))


def test_gap_policy_invalid_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="gap_policy"):
        BacktestConfig(**_good_kwargs(tmp_path, gap_policy="bogus"))


def test_execution_model_invalid_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="execution_model"):
        BacktestConfig(**_good_kwargs(tmp_path, execution_model="bogus"))


# ---------- primary_symbol / primary_timeframe -----------------------------


def test_primary_symbol_not_in_instruments(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="primary_symbol"):
        BacktestConfig(**_good_kwargs(tmp_path, primary_symbol="ETHUSDT"))


def test_primary_timeframe_not_in_timeframes(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="primary_timeframe"):
        BacktestConfig(**_good_kwargs(tmp_path, primary_timeframe="4h"))


def test_primary_timeframe_missing_symbol_entry(tmp_path: Path) -> None:
    """primary_symbol이 timeframes_per_symbol에 아예 없는 경우."""
    kwargs = _good_kwargs(tmp_path)
    kwargs["timeframes_per_symbol"] = {"ETHUSDT": ["1h"]}
    with pytest.raises(ConfigError, match="primary_timeframe"):
        BacktestConfig(**kwargs)


# ---------- Phase 2 한도 정의는 검증 안 함 (RiskLimits 자체 dataclass) -----


def test_phase2_risk_limits_passed_through_unvalidated(tmp_path: Path) -> None:
    """RiskLimits의 max_position_size 등은 Config 레벨 검증 대상이 아님."""
    from backtester.portfolio.risk import RiskLimits

    config = BacktestConfig(
        **_good_kwargs(
            tmp_path,
            risk_limits=RiskLimits(
                max_position_size=Decimal("0.001"),
                max_total_exposure=Decimal("100"),
            ),
        )
    )
    assert config.risk_limits.max_position_size == Decimal("0.001")


# ---------- 스모크: 모든 default 사용 가능 ----------------------------------


def test_full_default_optional_fields(tmp_path: Path) -> None:
    """default가 있는 필드는 모두 생략 가능."""
    config = BacktestConfig(**_good_kwargs(tmp_path))
    assert config.gap_policy == "notify"
    assert config.execution_model == "next_bar_open"
    assert config.bar_path_model == BarPathModel.PESSIMISTIC
    assert config.slippage_bps == 0.0
    assert config.warmup_bars == 0
    assert config.random_seed == 0
    assert config.log_level == "INFO"
    assert config.persist_run_data == "copy"
    assert config.snapshot_every_bars == 1
    assert config.on_run_exists == "fail"


def test_basic_construction_smoke(tmp_path: Path) -> None:
    """샘플 시간 범위로 기본 동작 확인."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    config = BacktestConfig(
        **_good_kwargs(
            tmp_path,
            start=base,
            end=base + timedelta(days=10),
        )
    )
    assert config.start < config.end
