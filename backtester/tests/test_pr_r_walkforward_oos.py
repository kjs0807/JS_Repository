"""PR R — Walkforward / OOS Completion 회귀.

검증:
1. ``state_policy='carryover'`` (default) — PR 17 호환 (한 엔진 실행).
2. ``state_policy='reset'`` — train warmup + 별도 test 엔진.
3. window-aware ``strategy_factory(window)`` 시그니처 자동 디스패치.
4. ``stitched_oos_equity`` — window 별 OOS 구간 시간순 이어붙이기.
5. window 별 run_dir 보존 (chart/report/rebuild 가능).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.analysis.walkforward import (
    WalkforwardSplitter,
    WalkforwardWindow,
    run_walkforward,
)
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc
ONE_HOUR = timedelta(hours=1)


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
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _make_parquet(target: Path, n_bars: int = 96) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0 + i * 0.1 for i in range(n_bars)],
            "high": [100.5 + i * 0.1 for i in range(n_bars)],
            "low": [99.5 + i * 0.1 for i in range(n_bars)],
            "close": [100.2 + i * 0.1 for i in range(n_bars)],
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _base_config(tmp_path: Path, run_id: str = "wf_oos") -> BacktestConfig:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=96)
    return BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 5, tzinfo=UTC),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )


class _BuyOnceStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._sent = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._sent:
            return []
        self._sent = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="market",
                size_spec=TargetUnits(units=Decimal("1")),
                reason="entry",
            )
        ]


# ---------- 1. carryover (default) ------------------------------------------


def test_carryover_state_policy_runs_one_engine_per_window(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path, "wf_carry")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=96),
        train_bars=24,
        test_bars=12,
        bar_interval=ONE_HOUR,
        mode="rolling",
    )
    result = run_walkforward(
        base_config=cfg,
        strategy_factory=_BuyOnceStrategy,
        splitter=splitter,
        state_policy="carryover",
    )
    assert len(result.windows) > 0
    for w in result.windows:
        assert w.run_dir.name.endswith(f"_wf_{w.window.index}")
        assert (w.run_dir / "events.jsonl").exists()


# ---------- 2. reset state policy -------------------------------------------


def test_reset_state_policy_creates_separate_runs(tmp_path: Path) -> None:
    """reset: train warmup 별도 + test 별도 엔진. run_dir 은 test 의 것."""
    cfg = _base_config(tmp_path, "wf_reset")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=48),
        train_bars=12,
        test_bars=12,
        bar_interval=ONE_HOUR,
        mode="rolling",
    )
    result = run_walkforward(
        base_config=cfg,
        strategy_factory=_BuyOnceStrategy,
        splitter=splitter,
        state_policy="reset",
    )
    assert len(result.windows) >= 2
    for w in result.windows:
        # OOS run_dir 이 보존됨
        assert (w.run_dir / "events.jsonl").exists()
        # warmup run_dir 도 별도로 만들어짐 (이름 _warmup 접미사)
        warmup_dir = w.run_dir.parent / f"{cfg.run_id}_wf_{w.window.index}_warmup"
        assert warmup_dir.exists()


# ---------- 3. window-aware factory -----------------------------------------


def test_window_aware_strategy_factory_dispatched(tmp_path: Path) -> None:
    """factory(window: WalkforwardWindow) 시그니처 자동 인식."""
    cfg = _base_config(tmp_path, "wf_aware")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=96),
        train_bars=24,
        test_bars=12,
        bar_interval=ONE_HOUR,
        mode="rolling",
    )
    received_windows: list[int] = []

    def factory(window: WalkforwardWindow) -> BaseStrategy:
        received_windows.append(window.index)
        return _BuyOnceStrategy()

    result = run_walkforward(
        base_config=cfg,
        strategy_factory=factory,
        splitter=splitter,
    )
    assert received_windows == [w.window.index for w in result.windows]


# ---------- 4. stitched OOS equity ------------------------------------------


def test_stitched_oos_equity_concatenates_windows(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path, "wf_stitch")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=96),
        train_bars=24,
        test_bars=12,
        bar_interval=ONE_HOUR,
        mode="rolling",
    )
    result = run_walkforward(
        base_config=cfg,
        strategy_factory=_BuyOnceStrategy,
        splitter=splitter,
    )
    eq = result.stitched_oos_equity()
    assert eq.height > 0
    # 시간순 정렬
    ts_list = eq["timestamp"].to_list()
    assert ts_list == sorted(ts_list)
    # window_index 컬럼이 있고, 모든 window 가 포함됨
    indices = set(eq["window_index"].to_list())
    assert indices == {w.window.index for w in result.windows}


def test_stitched_oos_equity_empty_for_no_windows() -> None:
    from backtester.analysis.walkforward import WalkforwardResult

    eq = WalkforwardResult(windows=[]).stitched_oos_equity()
    assert eq.height == 0
    assert "timestamp" in eq.columns


# ---------- 5. window별 chart / report 가능 ---------------------------------


def test_window_run_dir_supports_chart_render(tmp_path: Path) -> None:
    """window 별 run_dir 이 build_run_chart 입력으로 동작."""
    from backtester.viz.run_chart import build_run_chart

    cfg = _base_config(tmp_path, "wf_chart")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=48),
        train_bars=12,
        test_bars=12,
        bar_interval=ONE_HOUR,
        mode="rolling",
    )
    result = run_walkforward(
        base_config=cfg,
        strategy_factory=_BuyOnceStrategy,
        splitter=splitter,
    )
    assert result.windows
    fig = build_run_chart(result.windows[0].run_dir)
    assert fig is not None
