"""BacktestConfig + DataSourceConfig (spec §5).

`@dataclass(frozen=True, kw_only=True)` — Python 3.10+ 키워드 전용 dataclass로
default 필드와 non-default 필드 공존 허용 (spec §5).

`__post_init__`이 §5.1 검증 규칙을 모두 강제 → 잘못된 값으로 BacktestEngine 시작 자체가
불가능 (Fatal ConfigError).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from backtester.core.errors import ConfigError
from backtester.core.types import BarPathModel
from backtester.instruments.base import Instrument
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
    """Phase 1: parquet only. CSV는 Phase 1.5, Bybit는 Phase 2."""

    base_dir: Path
    type: Literal["parquet"] = "parquet"


@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    """백테스트 실행 전체 설정.

    Phase 1 단순화:
    - `instruments`는 spec의 `list[str]` 대신 `list[Instrument]`로 직접 보유 — 별도
      registry/loader 의존 없이 self-contained.
    - `cache_dir` / `sizer_default` / `fee_override` / `strategy_name` /
      `strategy_params`는 Phase 1에서 사용하지 않으므로 정의하지 않음 (Phase 1.5+ 추가).
    - 전략 인스턴스는 Engine 생성 시 별도 인자로 전달 (config는 직렬화 가능 영역만).
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
