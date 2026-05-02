"""BacktestConfig factory — crypto perp 빠른 셋업 (Phase 2.5 후속).

매번 ``BacktestConfig(run_id=..., data_source=..., instruments=[...], ...)`` 를 길게
쓰지 않도록 ``crypto_perp_backtest_config(symbol, timeframe, ...)`` 한 번에 Bybit
preset + crypto-friendly default 를 묶는다.

기본값:
- ``initial_equity = 50_000`` USDT — Bybit demo / paper 계정 권장 사이즈.
- ``allow_short = True``.
- ``on_run_exists = "auto_suffix"`` — 같은 run_id 충돌 시 자동 increment.
- ``persist_run_data = "copy"`` — run_dir self-contained.
- ``execution_model = "next_bar_open"`` + ``slippage_bps = 3``.
- ``bar_path_model = PESSIMISTIC`` (보수).
- ``snapshot_every_bars = 1``.
- ``risk_limits``: ``max_leverage=10``, ``max_orders_per_symbol=10`` (보수). 명시
  주입 시 override.

Instrument 는 ``bybit_linear_perp(symbol)`` preset 사용 — fee / exchange_rule /
margin_model 모두 채워짐. 사용자가 별도 instrument 를 주려면 ``instrument`` 인자
override 가능.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.types import BarPathModel
from backtester.execution.funding import FundingModel
from backtester.instruments.base import Instrument
from backtester.instruments.presets import bybit_linear_perp
from backtester.portfolio.risk import RiskLimits

_DEFAULT_INITIAL_EQUITY = Decimal("50000")


def _default_risk_limits() -> RiskLimits:
    """Crypto futures 보수 default — leverage 10x, per-symbol active 10."""
    return RiskLimits(
        max_orders_per_symbol=10,
        max_leverage=Decimal("10"),
    )


def crypto_perp_backtest_config(
    *,
    run_id: str,
    symbol: str,
    timeframe: str,
    data_dir: Path,
    output_dir: Path,
    start: datetime,
    end: datetime,
    initial_equity: Decimal = _DEFAULT_INITIAL_EQUITY,
    strategy_name: str = "",
    strategy_params: dict[str, Any] | None = None,
    instrument: Instrument | None = None,
    funding_model: FundingModel | None = None,
    funding_source_dir: Path | None = None,
    risk_limits: RiskLimits | None = None,
    extra_timeframes: list[str] | None = None,
    allow_short: bool = True,
    slippage_bps: float = 3.0,
    bar_path_model: BarPathModel = BarPathModel.PESSIMISTIC,
    snapshot_every_bars: int = 1,
    on_run_exists: str = "auto_suffix",
    data_source_type: str = "parquet",
) -> BacktestConfig:
    """Crypto perp 백테스트용 ``BacktestConfig`` 빌더.

    Args:
        run_id: 실행 식별자 (output_dir 하위 디렉토리 이름).
        symbol: 거래 심볼 (예: "BTCUSDT"). preset 미등록 시 ``instrument`` 주입 필요.
        timeframe: primary timeframe (예: "1h").
        data_dir: ``DataSourceConfig.base_dir``.
        output_dir: run_dir 부모.
        start / end: 백테스트 범위 (UTC tz-aware).
        initial_equity: 자본 — default 50,000 USDT.
        strategy_name / strategy_params: ``BacktestConfig`` 식별 + 재현용.
        instrument: 명시 주입 시 preset 대신 사용 (예: 커스텀 fee tier).
        funding_model: 단일 symbol funding 정책. ``None`` 이면 funding 미적용.
        funding_source_dir: ``rate_source="from_data_source"`` 사용 시 parquet base.
        risk_limits: 명시 주입 시 default (max_leverage=10, max_orders=10) override.
        extra_timeframes: primary 외 추가 (지표용).
        allow_short / slippage_bps / bar_path_model / snapshot_every_bars /
        on_run_exists: BacktestConfig pass-through.
        data_source_type: "parquet" / "csv" / "bybit". default "parquet".
    """
    if instrument is None:
        instrument = bybit_linear_perp(symbol)
    if instrument.symbol != symbol:
        raise ValueError(
            f"instrument.symbol {instrument.symbol!r} mismatches symbol {symbol!r}"
        )
    if risk_limits is None:
        risk_limits = _default_risk_limits()
    timeframes = [timeframe]
    if extra_timeframes:
        for tf in extra_timeframes:
            if tf not in timeframes:
                timeframes.append(tf)
    funding_models = (
        {symbol: funding_model} if funding_model is not None else {}
    )
    return BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(
            base_dir=data_dir,
            type=data_source_type,  # type: ignore[arg-type]
        ),
        instruments=[instrument],
        timeframes_per_symbol={symbol: timeframes},
        primary_symbol=symbol,
        primary_timeframe=timeframe,
        start=start,
        end=end,
        initial_equity=initial_equity,
        risk_limits=risk_limits,
        execution_model="slippage_bps" if slippage_bps > 0 else "next_bar_open",
        slippage_bps=slippage_bps,
        bar_path_model=bar_path_model,
        snapshot_every_bars=snapshot_every_bars,
        on_run_exists=on_run_exists,  # type: ignore[arg-type]
        persist_run_data="copy",
        output_dir=output_dir,
        strategy_name=strategy_name,
        strategy_params=strategy_params or {},
        funding_models=funding_models,
        funding_source_dir=funding_source_dir,
        allow_short=allow_short,
    )
