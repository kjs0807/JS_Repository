"""PR 16 prep — gap_policy 활성 + ctx.indicators 엔진 wiring 회귀.

이 파일은 두 가지를 검증한다:
1. ``gap_policy='notify'`` 가 ``strategy.on_data_gap`` 을 실제로 호출하고 verbose 알림을
   stdout 으로 낸다 — 이전엔 GapReport 만 쌓고 정책이 무시됐다.
2. ``gap_policy='ffill'`` 은 명시적 ``NotImplementedError``.
3. Engine 의 strategy.on_bar 호출 경로에 ``ctx.indicators[symbol][tf]`` 가 precomputed
   결과로 채워져 있고 last_closed 컷오프가 적용된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent
from backtester.indicators.base import Indicator
from backtester.indicators.stateless.bb import BollingerBands
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


# ---------- 공통 fixture ----------------------------------------------------


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


def _write_parquet(
    target: Path,
    *,
    timestamps: list[datetime],
) -> None:
    n = len(timestamps)
    df = pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0 + i * 0.1 for i in range(n)],
            "high": [100.5 + i * 0.1 for i in range(n)],
            "low": [99.5 + i * 0.1 for i in range(n)],
            "close": [100.2 + i * 0.1 for i in range(n)],
            "volume": [1.0] * n,
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _config(
    tmp_path: Path,
    *,
    timestamps: list[datetime],
    gap_policy: str = "notify",
) -> BacktestConfig:
    data_dir = tmp_path / "data"
    _write_parquet(data_dir / "BTCUSDT_1h.parquet", timestamps=timestamps)
    return BacktestConfig(
        run_id="gap_policy_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=timestamps[0],
        end=timestamps[-1] + timedelta(hours=1),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        gap_policy=gap_policy,  # type: ignore[arg-type]
    )


# ---------- gap_policy=notify ------------------------------------------------


class _GapRecorderStrategy(BaseStrategy):
    """on_data_gap 호출을 기록하는 더미 전략."""

    def __init__(self) -> None:
        self.gap_calls: list[tuple[str, datetime, datetime]] = []

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def on_data_gap(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[OrderIntent]:
        self.gap_calls.append((symbol, start, end))
        return []


def test_gap_policy_notify_invokes_on_data_gap_callback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 02:00 / 03:00 가 빠진 5봉 시리즈 — 1h 간격
    timestamps = [
        base,
        base + timedelta(hours=1),
        base + timedelta(hours=4),
        base + timedelta(hours=5),
        base + timedelta(hours=6),
    ]
    cfg = _config(tmp_path, timestamps=timestamps)
    strat = _GapRecorderStrategy()
    BacktestEngine(cfg, strat, verbose=True)
    # 단일 갭 — start = 02:00, end = 03:00
    assert len(strat.gap_calls) == 1
    sym, gap_start, gap_end = strat.gap_calls[0]
    assert sym == "BTCUSDT"
    assert gap_start == base + timedelta(hours=2)
    assert gap_end == base + timedelta(hours=3)
    # verbose 알림 stdout 확인
    captured = capsys.readouterr()
    assert "data gap BTCUSDT/1h" in captured.out


def test_gap_policy_notify_no_gap_no_callback(tmp_path: Path) -> None:
    """gap 이 없으면 on_data_gap 호출 없음."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]  # 연속
    cfg = _config(tmp_path, timestamps=timestamps)
    strat = _GapRecorderStrategy()
    BacktestEngine(cfg, strat, verbose=True)
    assert strat.gap_calls == []


def test_gap_policy_ffill_raises_not_implemented(tmp_path: Path) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    cfg = _config(tmp_path, timestamps=timestamps, gap_policy="ffill")
    with pytest.raises(NotImplementedError, match="ffill"):
        BacktestEngine(cfg, _GapRecorderStrategy(), verbose=False)


# ---------- ctx.indicators (Engine wiring) -----------------------------------


class _IndicatorsCaptureStrategy(BaseStrategy):
    """on_bar 가 ctx.indicators 에서 precomputed 결과를 읽고 캡쳐."""

    def __init__(self) -> None:
        self._bb = BollingerBands(period=3, num_std=2.0)
        self.captured_heights: list[int] = []
        self.captured_columns: list[list[str]] = []

    def required_indicators(self) -> list[Indicator]:
        return [self._bb]

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        ind = ctx.indicators[ctx.primary_symbol][ctx.primary_timeframe]
        self.captured_heights.append(ind.height)
        self.captured_columns.append(list(ind.columns))
        return []


def test_engine_wires_ctx_indicators_with_lookahead_clipping(tmp_path: Path) -> None:
    """Engine 이 ctx.indicators 를 항상 채워주고, BarsView 와 같은 height 로 컷오프."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(10)]
    cfg = _config(tmp_path, timestamps=timestamps)
    strat = _IndicatorsCaptureStrategy()
    BacktestEngine(cfg, strat, verbose=False).run()

    # BollingerBands period=3 → required_warmup_bars=period-1=2 → bar_count > 2 부터 호출.
    # 10 봉 - warmup 2 = 8 호출. 첫 호출 시 height=3 (index 0/1/2).
    assert len(strat.captured_heights) == 8
    assert strat.captured_heights == [3, 4, 5, 6, 7, 8, 9, 10]
    # 지표 컬럼: timestamp + BB upper/mid/lower
    assert all("timestamp" in cols for cols in strat.captured_columns)
    assert any(
        any("upper" in c for c in cols) for cols in strat.captured_columns
    )


def test_engine_indicators_view_unknown_pair_raises_in_strategy(
    tmp_path: Path,
) -> None:
    """precompute 안 된 (symbol, tf) 접근은 KeyError — required_indicators 누락 가드."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(hours=i) for i in range(5)]
    cfg = _config(tmp_path, timestamps=timestamps)

    class _BadStrategy(BaseStrategy):
        def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
            ctx.indicators["ETHUSDT"]["1h"]  # unknown
            return []

    with pytest.raises(KeyError, match="not precomputed"):
        BacktestEngine(cfg, _BadStrategy(), verbose=False).run()
