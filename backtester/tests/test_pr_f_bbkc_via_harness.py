"""PR F 회귀 — BBKC 가 ``StrategyHarness`` 5 contracts 통과 (전략 개발용 기준 엔진).

이 파일은 BBKCSqueezeStrategy 를 ``StrategyHarness`` 에 넣어 일괄 회귀:
1. no-lookahead
2. deterministic events
3. position state sync (FILL ↔ SNAPSHOT positions)
4. chart 렌더 가능
5. rebuild-results 정합성

후속 전략 (FRAMA, RSI 등) 도 동일 harness 를 사용해 같은 5 contracts 를 통과해야
한다 — PR 16 (FRAMA) 시작 시 본 패턴 그대로 재사용.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy
from tests._strategy_harness import HarnessSpec, StrategyHarness

UTC = timezone.utc


def _btc() -> Instrument:
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


def _build_squeeze_breakout_parquet(
    target: Path,
    n_squeeze: int = 25,
    n_trend: int = 25,
    n_revert: int = 30,
) -> None:
    """squeeze → breakout → mean-revert 시나리오. BBKC 가 진입+청산 신호 발행."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    # squeeze
    for i in range(n_squeeze):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": 100.0,
                "high": 100.05,
                "low": 99.95,
                "close": 100.0 + (0.01 if i % 2 else -0.01),
                "volume": 1.0,
            }
        )
    # breakout
    for i in range(n_trend):
        rows.append(
            {
                "timestamp": base + timedelta(hours=n_squeeze + i),
                "open": 100.0 + i * 0.5,
                "high": 100.5 + i * 0.5,
                "low": 99.5 + i * 0.5,
                "close": 100.5 + i * 0.5,
                "volume": 1.0,
            }
        )
    peak = 100.5 + (n_trend - 1) * 0.5
    # mean revert
    for i in range(n_revert):
        rows.append(
            {
                "timestamp": base + timedelta(hours=n_squeeze + n_trend + i),
                "open": peak - i * 0.4,
                "high": peak + 0.5 - i * 0.4,
                "low": peak - 0.5 - i * 0.4,
                "close": peak - i * 0.4,
                "volume": 1.0,
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _spec(tmp_path: Path) -> HarnessSpec:
    parquet_path = tmp_path / "data" / "BTCUSDT_1h.parquet"
    _build_squeeze_breakout_parquet(parquet_path)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    df = pl.read_parquet(parquet_path)
    end = df["timestamp"][-1] + timedelta(hours=1)
    return HarnessSpec(
        name="bbkc_harness",
        strategy_factory=BBKCSqueezeStrategy,
        instrument=_btc(),
        parquet_path=parquet_path,
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_root=tmp_path,
    )


def test_bbkc_passes_no_lookahead(tmp_path: Path) -> None:
    StrategyHarness(_spec(tmp_path)).assert_no_lookahead()


def test_bbkc_passes_deterministic_events(tmp_path: Path) -> None:
    StrategyHarness(_spec(tmp_path)).assert_deterministic_events()


def test_bbkc_passes_position_state_sync(tmp_path: Path) -> None:
    StrategyHarness(_spec(tmp_path)).assert_position_state_sync()


def test_bbkc_passes_chart_renders(tmp_path: Path) -> None:
    StrategyHarness(_spec(tmp_path)).assert_chart_renders()


def test_bbkc_passes_rebuild_consistency(tmp_path: Path) -> None:
    StrategyHarness(_spec(tmp_path)).assert_rebuild_consistency()


def test_bbkc_passes_all_contracts(tmp_path: Path) -> None:
    """일괄 체크 — 새 전략 추가 시 한 줄로 모든 contract 검증."""
    StrategyHarness(_spec(tmp_path)).assert_all()
