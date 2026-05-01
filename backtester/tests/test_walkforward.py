"""PR 17 walk-forward 분석 테스트 (Phase 2)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.analysis.walkforward import (
    WalkforwardResult,
    WalkforwardSplitter,
    WalkforwardWindow,
    WalkforwardWindowResult,
    run_walkforward,
)
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

UTC = timezone.utc
ONE_HOUR = timedelta(hours=1)


# ---------- WalkforwardSplitter 단위 ---------------------------------------


def test_splitter_rolling_generates_consecutive_windows() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=20),
        train_bars=4,
        test_bars=2,
        bar_interval=ONE_HOUR,
        mode="rolling",
    )
    windows = splitter.split()
    # i=0: train [0..4), test [4..6)
    # i=1: train [2..6), test [6..8)
    # i=2: train [4..8), test [8..10)
    # ...
    # 마지막: test_end <= 20 인 i 까지. train_start = i*2, train_end = i*2 + 4,
    # test_end = i*2 + 6. test_end <= 20 → i*2 <= 14 → i <= 7. 그래서 i=0..7 = 8 windows.
    assert len(windows) == 8
    assert windows[0].train_start == base
    assert windows[0].train_end == base + timedelta(hours=4)
    assert windows[0].test_start == base + timedelta(hours=4)
    assert windows[0].test_end == base + timedelta(hours=6)
    # rolling: 두 번째 window 의 train_start 가 test_bars 만큼 이동
    assert windows[1].train_start == base + timedelta(hours=2)
    assert windows[1].train_end == base + timedelta(hours=6)


def test_splitter_expanding_train_grows_test_slides() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=20),
        train_bars=4,
        test_bars=2,
        bar_interval=ONE_HOUR,
        mode="expanding",
    )
    windows = splitter.split()
    # expanding: train_start 항상 base, train_end = base + (train_bars + i*test_bars)*dt
    # i=0: train [0..4), test [4..6)
    # i=1: train [0..6), test [6..8)
    # ...
    # i=k: train [0..4+2k), test [4+2k..6+2k). test_end <= 20 → k <= 7. 8 windows.
    assert len(windows) == 8
    assert all(w.train_start == base for w in windows)
    assert windows[0].train_end == base + timedelta(hours=4)
    assert windows[1].train_end == base + timedelta(hours=6)
    assert windows[7].train_end == base + timedelta(hours=18)


def test_splitter_test_segments_are_contiguous_for_both_modes() -> None:
    """양 mode 에서 test 구간이 연속이어야 한다 (back-to-back OOS)."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    for mode in ("rolling", "expanding"):
        splitter = WalkforwardSplitter(
            start=base,
            end=base + timedelta(hours=20),
            train_bars=4,
            test_bars=2,
            bar_interval=ONE_HOUR,
            mode=mode,
        )
        windows = splitter.split()
        for prev, curr in zip(windows[:-1], windows[1:], strict=True):
            assert curr.test_start == prev.test_end


def test_splitter_too_short_data_yields_empty() -> None:
    """데이터가 train+test 1 window 도 못 채우면 빈 리스트."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    splitter = WalkforwardSplitter(
        start=base,
        end=base + timedelta(hours=3),  # train=4 + test=2 = 6h 필요
        train_bars=4,
        test_bars=2,
        bar_interval=ONE_HOUR,
    )
    assert splitter.split() == []


# ---------- 검증 ------------------------------------------------------------


def test_splitter_rejects_start_at_or_after_end() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="start must be"):
        WalkforwardSplitter(
            start=base,
            end=base,
            train_bars=4,
            test_bars=2,
            bar_interval=ONE_HOUR,
        )


def test_splitter_rejects_non_positive_bars() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="train_bars"):
        WalkforwardSplitter(
            start=base,
            end=base + timedelta(hours=10),
            train_bars=0,
            test_bars=2,
            bar_interval=ONE_HOUR,
        )
    with pytest.raises(ValueError, match="test_bars"):
        WalkforwardSplitter(
            start=base,
            end=base + timedelta(hours=10),
            train_bars=4,
            test_bars=-1,
            bar_interval=ONE_HOUR,
        )


def test_splitter_rejects_non_positive_interval() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="bar_interval"):
        WalkforwardSplitter(
            start=base,
            end=base + timedelta(hours=10),
            train_bars=4,
            test_bars=2,
            bar_interval=timedelta(0),
        )


def test_splitter_rejects_unknown_mode() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="mode"):
        WalkforwardSplitter(
            start=base,
            end=base + timedelta(hours=10),
            train_bars=4,
            test_bars=2,
            bar_interval=ONE_HOUR,
            mode="bogus",  # type: ignore[arg-type]
        )


# ---------- Engine 통합 (run_walkforward) ----------------------------------


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


def _make_synthetic_parquet(target: Path, n_bars: int = 96) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(n_bars):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": 100.0 + i * 0.1,
                "high": 100.5 + i * 0.1,
                "low": 99.5 + i * 0.1,
                "close": 100.2 + i * 0.1,
                "volume": 1.0,
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _base_config(tmp_path: Path) -> BacktestConfig:
    data_dir = tmp_path / "data"
    _make_synthetic_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=96)
    return BacktestConfig(
        run_id="wf_smoke",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 3, 5, tzinfo=UTC),  # 96 시간
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )


def test_run_walkforward_creates_per_window_run_dirs(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
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
        strategy_factory=BBKCSqueezeStrategy,
        splitter=splitter,
    )
    assert isinstance(result, WalkforwardResult)
    assert len(result.windows) > 0
    for i, w in enumerate(result.windows):
        # run_id 패턴: <base>_wf_<i>
        assert w.run_dir.name == f"wf_smoke_wf_{i}"
        assert (w.run_dir / "events.jsonl").exists()
        assert isinstance(w.metrics, dict)
        assert "total_return" in w.metrics


def test_run_walkforward_oos_metrics_only_count_test_segment(tmp_path: Path) -> None:
    """metrics 가 test_start 이후 equity 만으로 계산되는지 — OOS 회귀.

    train 구간에서 equity 변동이 있더라도 metrics.n_periods 는 test 구간 SNAPSHOT
    개수와 일치해야 한다.
    """
    cfg = _base_config(tmp_path)
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
        strategy_factory=BBKCSqueezeStrategy,
        splitter=splitter,
    )
    assert result.windows, "must have at least one window"
    for w in result.windows:
        # test_start 의 anchor SNAPSHOT (train 종료 직후 equity) + test 구간 내 12 봉
        # close = 13 SNAPSHOT. cfg.end -= bar_interval 보정으로 test_end 이후 SNAPSHOT
        # 차단 → 정확히 test_bars + 1.
        assert w.metrics["n_periods"] == 13


def test_aggregate_metrics_returns_per_key_summary(tmp_path: Path) -> None:
    cfg = _base_config(tmp_path)
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
        strategy_factory=BBKCSqueezeStrategy,
        splitter=splitter,
    )
    agg = result.aggregate_metrics()
    assert "total_return" in agg
    assert set(agg["total_return"].keys()) == {
        "mean", "median", "std", "min", "max"
    }
    # 값들은 모두 finite 또는 nan
    for v in agg["total_return"].values():
        assert isinstance(v, float)


def test_aggregate_metrics_empty_result_returns_empty() -> None:
    res = WalkforwardResult(windows=[])
    assert res.aggregate_metrics() == {}


def test_aggregate_metrics_handles_all_nan_values() -> None:
    """모든 window 에서 한 metric 이 nan 이면 그 키의 모든 통계도 nan."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    win = WalkforwardWindow(
        index=0,
        train_start=base,
        train_end=base + timedelta(hours=4),
        test_start=base + timedelta(hours=4),
        test_end=base + timedelta(hours=6),
    )
    results = [
        WalkforwardWindowResult(
            window=win,
            run_dir=Path("/tmp/fake"),
            metrics={"sharpe_ratio": float("nan"), "total_return": 0.05},
        ),
        WalkforwardWindowResult(
            window=win,
            run_dir=Path("/tmp/fake"),
            metrics={"sharpe_ratio": float("nan"), "total_return": 0.10},
        ),
    ]
    agg = WalkforwardResult(windows=results).aggregate_metrics()
    assert math.isnan(agg["sharpe_ratio"]["mean"])
    assert agg["total_return"]["mean"] == pytest.approx(0.075)
