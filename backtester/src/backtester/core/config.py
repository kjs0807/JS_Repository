"""BacktestConfig + DataSourceConfig (spec §5, §6.4 / Phase 1.5 PR 9).

`@dataclass(frozen=True, kw_only=True)` — Python 3.10+ 키워드 전용 dataclass로
default 필드와 non-default 필드 공존 허용 (spec §5).

`__post_init__`이 §5.1 검증 규칙을 모두 강제 → 잘못된 값으로 BacktestEngine 시작 자체가
불가능 (Fatal ConfigError).

Phase 1.5 PR 9: `strategy_name` / `strategy_params` 필드 추가 + ``to_yaml`` / ``from_yaml``
양방향 round-trip (spec §6.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml

from backtester.core.errors import ConfigError
from backtester.core.types import BarPathModel
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.risk import RiskLimits

_VALID_ON_RUN_EXISTS: frozenset[str] = frozenset(
    {"fail", "overwrite", "auto_suffix", "archive"}
)
_VALID_PERSIST_RUN_DATA: frozenset[str] = frozenset({"copy", "symlink", "none"})
_VALID_GAP_POLICY: frozenset[str] = frozenset({"notify", "ffill"})
_VALID_EXECUTION_MODEL: frozenset[str] = frozenset(
    {"next_bar_open", "slippage_bps", "atr_slippage"}
)


@dataclass(frozen=True)
class DataSourceConfig:
    """Phase 1: parquet. Phase 1.5: + csv."""

    base_dir: Path
    type: Literal["parquet", "csv"] = "parquet"


# YAML 로드 시 ``BacktestConfig`` 가 모르는 audit 필드 (Engine 이 영속화 단계에서 부착).
# 사용자 작성 config 에는 없을 수 있고, Engine 이 쓴 config.yaml 에는 있을 수 있다.
_AUDIT_FIELDS: frozenset[str] = frozenset({"resolved_run_id", "run_dir"})


@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    """백테스트 실행 전체 설정.

    Phase 1.5 추가 필드:
    - ``strategy_name`` (str): CLI / registry lookup 용 strategy identifier.
      비워두면 ``BacktestEngine(config, strategy=...)`` 로 직접 주입한 경우.
    - ``strategy_params`` (dict): YAML 으로 보존되는 strategy 생성 인자.
      registry 가 ``StrategyClass(**strategy_params)`` 로 인스턴스화.

    YAML round-trip: ``to_yaml`` / ``from_yaml`` 양방향 (spec §6.4). Engine 이 영속화 시
    ``resolved_run_id`` / ``run_dir`` audit 필드를 추가하지만 ``BacktestConfig`` 자체에는
    포함되지 않으며 ``from_yaml`` 은 이들을 무시한다.
    """

    run_id: str

    # 데이터
    data_source: DataSourceConfig
    instruments: list[Instrument]
    timeframes_per_symbol: dict[str, list[str]]
    primary_symbol: str
    primary_timeframe: str
    start: datetime
    end: datetime
    gap_policy: Literal["notify", "ffill"] = "notify"

    # 실행
    execution_model: Literal["next_bar_open", "slippage_bps", "atr_slippage"] = (
        "next_bar_open"
    )
    bar_path_model: BarPathModel = BarPathModel.PESSIMISTIC
    slippage_bps: float = 0.0

    # 포트폴리오
    initial_equity: Decimal
    risk_limits: RiskLimits = field(default_factory=RiskLimits)

    # 워밍업·재현성
    warmup_bars: int = 0
    random_seed: int = 0

    # 출력
    output_dir: Path
    log_level: str = "INFO"

    # Run 영속화·정책
    persist_run_data: Literal["copy", "symlink", "none"] = "copy"
    snapshot_every_bars: int = 1
    on_run_exists: Literal["fail", "overwrite", "auto_suffix", "archive"] = "fail"

    # Phase 1.5 — 전략 식별
    strategy_name: str = ""
    strategy_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 숫자 한도
        if self.snapshot_every_bars < 1:
            raise ConfigError(
                f"snapshot_every_bars must be >= 1, got {self.snapshot_every_bars}"
            )
        if self.warmup_bars < 0:
            raise ConfigError(f"warmup_bars must be >= 0, got {self.warmup_bars}")
        if self.initial_equity <= 0:
            raise ConfigError(
                f"initial_equity must be > 0, got {self.initial_equity}"
            )
        if self.slippage_bps < 0:
            raise ConfigError(f"slippage_bps must be >= 0, got {self.slippage_bps}")
        if self.random_seed < 0:
            raise ConfigError(f"random_seed must be >= 0, got {self.random_seed}")

        # 시간 범위
        if self.start >= self.end:
            raise ConfigError(
                f"start must be < end, got start={self.start}, end={self.end}"
            )

        # Literal/Enum 검증 (dataclass는 런타임에 Literal을 강제하지 않음)
        if self.on_run_exists not in _VALID_ON_RUN_EXISTS:
            raise ConfigError(
                f"on_run_exists must be one of {sorted(_VALID_ON_RUN_EXISTS)}, "
                f"got {self.on_run_exists!r}"
            )
        if self.persist_run_data not in _VALID_PERSIST_RUN_DATA:
            raise ConfigError(
                f"persist_run_data must be one of {sorted(_VALID_PERSIST_RUN_DATA)}, "
                f"got {self.persist_run_data!r}"
            )
        if not isinstance(self.bar_path_model, BarPathModel):
            raise ConfigError(
                f"bar_path_model must be a BarPathModel enum member, "
                f"got {type(self.bar_path_model).__name__}"
            )
        if self.gap_policy not in _VALID_GAP_POLICY:
            raise ConfigError(
                f"gap_policy must be one of {sorted(_VALID_GAP_POLICY)}, "
                f"got {self.gap_policy!r}"
            )
        if self.execution_model not in _VALID_EXECUTION_MODEL:
            raise ConfigError(
                f"execution_model must be one of {sorted(_VALID_EXECUTION_MODEL)}, "
                f"got {self.execution_model!r}"
            )

        # primary_symbol이 instruments에 있는지
        symbols = [inst.symbol for inst in self.instruments]
        if self.primary_symbol not in symbols:
            raise ConfigError(
                f"primary_symbol {self.primary_symbol!r} not in instruments "
                f"{symbols}"
            )

        # primary_timeframe이 timeframes_per_symbol[primary_symbol]에 있는지
        tfs = self.timeframes_per_symbol.get(self.primary_symbol, [])
        if self.primary_timeframe not in tfs:
            raise ConfigError(
                f"primary_timeframe {self.primary_timeframe!r} not in "
                f"timeframes_per_symbol[{self.primary_symbol!r}]={tfs}"
            )

    # ---------- YAML round-trip (spec §6.4) ---------------------------------

    def to_dict(self) -> dict[str, Any]:
        """YAML 직렬화용 dict. Decimal → str, datetime → ISO8601, Path → str,
        Enum → value, dataclass → 평면 dict."""
        return {
            "run_id": self.run_id,
            "data_source": _data_source_to_dict(self.data_source),
            "instruments": [_instrument_to_dict(i) for i in self.instruments],
            "timeframes_per_symbol": {
                k: list(v) for k, v in self.timeframes_per_symbol.items()
            },
            "primary_symbol": self.primary_symbol,
            "primary_timeframe": self.primary_timeframe,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "gap_policy": self.gap_policy,
            "execution_model": self.execution_model,
            "bar_path_model": self.bar_path_model.value,
            "slippage_bps": float(self.slippage_bps),
            "initial_equity": str(self.initial_equity),
            "risk_limits": _risk_limits_to_dict(self.risk_limits),
            "warmup_bars": int(self.warmup_bars),
            "random_seed": int(self.random_seed),
            "output_dir": str(self.output_dir),
            "log_level": self.log_level,
            "persist_run_data": self.persist_run_data,
            "snapshot_every_bars": int(self.snapshot_every_bars),
            "on_run_exists": self.on_run_exists,
            "strategy_name": self.strategy_name,
            "strategy_params": dict(self.strategy_params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BacktestConfig:
        """``to_dict`` 역변환. Engine audit 필드(``resolved_run_id`` / ``run_dir``)는
        무시. 알려지지 않은 키는 ``ConfigError``."""
        known = {f.name for f in fields(cls)} | _AUDIT_FIELDS
        unknown = set(data.keys()) - known
        if unknown:
            raise ConfigError(
                f"BacktestConfig.from_dict received unknown keys: {sorted(unknown)}"
            )
        kwargs: dict[str, Any] = {
            "run_id": data["run_id"],
            "data_source": _data_source_from_dict(data["data_source"]),
            "instruments": [_instrument_from_dict(i) for i in data["instruments"]],
            "timeframes_per_symbol": {
                k: list(v) for k, v in data["timeframes_per_symbol"].items()
            },
            "primary_symbol": data["primary_symbol"],
            "primary_timeframe": data["primary_timeframe"],
            "start": _parse_iso(data["start"]),
            "end": _parse_iso(data["end"]),
            "initial_equity": Decimal(data["initial_equity"]),
            "output_dir": Path(data["output_dir"]),
        }
        # 옵션 필드 (default 가 있는 것들) — 키 있을 때만 적용
        if "gap_policy" in data:
            kwargs["gap_policy"] = data["gap_policy"]
        if "execution_model" in data:
            kwargs["execution_model"] = data["execution_model"]
        if "bar_path_model" in data:
            kwargs["bar_path_model"] = BarPathModel(data["bar_path_model"])
        if "slippage_bps" in data:
            kwargs["slippage_bps"] = float(data["slippage_bps"])
        if "risk_limits" in data:
            kwargs["risk_limits"] = _risk_limits_from_dict(data["risk_limits"])
        if "warmup_bars" in data:
            kwargs["warmup_bars"] = int(data["warmup_bars"])
        if "random_seed" in data:
            kwargs["random_seed"] = int(data["random_seed"])
        if "log_level" in data:
            kwargs["log_level"] = data["log_level"]
        if "persist_run_data" in data:
            kwargs["persist_run_data"] = data["persist_run_data"]
        if "snapshot_every_bars" in data:
            kwargs["snapshot_every_bars"] = int(data["snapshot_every_bars"])
        if "on_run_exists" in data:
            kwargs["on_run_exists"] = data["on_run_exists"]
        if "strategy_name" in data:
            kwargs["strategy_name"] = data["strategy_name"]
        if "strategy_params" in data:
            kwargs["strategy_params"] = dict(data["strategy_params"])
        return cls(**kwargs)

    def to_yaml(self, path: Path) -> None:
        """현 config 을 YAML 로 직렬화."""
        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as fp:
            yaml.safe_dump(data, fp, sort_keys=False, default_flow_style=False)

    @classmethod
    def from_yaml(cls, path: Path) -> BacktestConfig:
        """YAML 파일을 읽어 ``BacktestConfig`` 인스턴스로 복원.
        ``__post_init__`` 검증이 자동 수행되므로 잘못된 값은 ``ConfigError``."""
        with open(path, encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        if not isinstance(data, dict):
            raise ConfigError(
                f"YAML root must be a mapping, got {type(data).__name__}: {path}"
            )
        return cls.from_dict(data)


# ---------- nested helpers --------------------------------------------------


def _parse_iso(value: str) -> datetime:
    """``str.isoformat()`` round-trip 보장. naive 입력은 ConfigError."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ConfigError(
            f"datetime must be timezone-aware, got naive: {value!r}"
        )
    return dt


def _data_source_to_dict(ds: DataSourceConfig) -> dict[str, Any]:
    return {"base_dir": str(ds.base_dir), "type": ds.type}


def _data_source_from_dict(data: dict[str, Any]) -> DataSourceConfig:
    return DataSourceConfig(
        base_dir=Path(data["base_dir"]),
        type=data.get("type", "parquet"),
    )


def _fee_model_to_dict(fm: FeeModel) -> dict[str, Any]:
    return {"type": fm.type, "taker": str(fm.taker), "maker": str(fm.maker)}


def _fee_model_from_dict(data: dict[str, Any]) -> FeeModel:
    return FeeModel(
        type=data["type"],
        taker=Decimal(data["taker"]),
        maker=Decimal(data.get("maker", "0")),
    )


def _instrument_to_dict(inst: Instrument) -> dict[str, Any]:
    return {
        "symbol": inst.symbol,
        "asset_class": inst.asset_class,
        "tick_size": str(inst.tick_size),
        "tick_value": str(inst.tick_value),
        "contract_multiplier": str(inst.contract_multiplier),
        "quote_currency": inst.quote_currency,
        "base_currency": inst.base_currency,
        "size_unit": inst.size_unit,
        "fee_model": _fee_model_to_dict(inst.fee_model),
    }


def _instrument_from_dict(data: dict[str, Any]) -> Instrument:
    return Instrument(
        symbol=data["symbol"],
        asset_class=data["asset_class"],
        tick_size=Decimal(data["tick_size"]),
        tick_value=Decimal(data["tick_value"]),
        contract_multiplier=Decimal(data["contract_multiplier"]),
        quote_currency=data["quote_currency"],
        base_currency=data["base_currency"],
        size_unit=data["size_unit"],
        fee_model=_fee_model_from_dict(data["fee_model"]),
    )


def _risk_limits_to_dict(rl: RiskLimits) -> dict[str, Any]:
    return {
        "max_orders_per_symbol": rl.max_orders_per_symbol,
        "blacklist_symbols": sorted(rl.blacklist_symbols),
        "max_position_size": (
            None if rl.max_position_size is None else str(rl.max_position_size)
        ),
        "max_total_exposure": (
            None if rl.max_total_exposure is None else str(rl.max_total_exposure)
        ),
        "max_leverage": (
            None if rl.max_leverage is None else str(rl.max_leverage)
        ),
        "max_drawdown_halt": rl.max_drawdown_halt,
    }


def _risk_limits_from_dict(data: dict[str, Any]) -> RiskLimits:
    def _opt_decimal(v: Any) -> Decimal | None:
        return None if v is None else Decimal(v)

    return RiskLimits(
        max_orders_per_symbol=int(data.get("max_orders_per_symbol", 5)),
        blacklist_symbols=frozenset(data.get("blacklist_symbols", [])),
        max_position_size=_opt_decimal(data.get("max_position_size")),
        max_total_exposure=_opt_decimal(data.get("max_total_exposure")),
        max_leverage=_opt_decimal(data.get("max_leverage")),
        max_drawdown_halt=data.get("max_drawdown_halt"),
    )
