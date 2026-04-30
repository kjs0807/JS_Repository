"""PR 7 SNAPSHOT 정책 테스트 (spec §3.15, §11.5, §20)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

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
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _make_parquet(tmp_path: Path, n_bars: int = 5) -> Path:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0 + i for i in range(n_bars)],
            "high": [101.0 + i for i in range(n_bars)],
            "low": [99.0 + i for i in range(n_bars)],
            "close": [100.5 + i for i in range(n_bars)],
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.write_parquet(data_dir / "BTCUSDT_1h.parquet")
    return data_dir


def _config(tmp_path: Path, n_bars: int = 5, **overrides: Any) -> BacktestConfig:
    data_dir = _make_parquet(tmp_path, n_bars=n_bars)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs: dict[str, Any] = {
        "run_id": "test",
        "data_source": DataSourceConfig(base_dir=data_dir),
        "instruments": [_btc()],
        "timeframes_per_symbol": {"BTCUSDT": ["1h"]},
        "primary_symbol": "BTCUSDT",
        "primary_timeframe": "1h",
        "start": base,
        "end": base + timedelta(hours=n_bars + 5),
        "initial_equity": Decimal("100000"),
        "output_dir": tmp_path / "runs",
    }
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class _NoOp(BaseStrategy):
    def on_bar(self, ctx: StrategyContext):  # type: ignore[no-untyped-def]
        return []


class _BuyOnce(BaseStrategy):
    def __init__(self) -> None:
        self._fired = False

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        if self._fired:
            return []
        self._fired = True
        return [
            OrderIntent(
                symbol="BTCUSDT",
                side="buy",
                type="market",
                size_spec=TargetUnits(units=Decimal("1")),
                reason="entry",
            )
        ]


# ---------- snapshot_every_bars 주기 ----------------------------------------


def test_periodic_snapshot_every_n_bars(tmp_path: Path) -> None:
    """snapshot_every_bars=2 → 봉 카운트가 2의 배수일 때만 periodic SNAPSHOT."""
    config = _config(tmp_path, n_bars=5, snapshot_every_bars=2)
    engine = BacktestEngine(config, _NoOp(), verbose=False)
    result = engine.run()

    events = _read_events(result.events_path)
    periodic = [
        e
        for e in events
        if e["type"] == "snapshot" and e["payload"]["snapshot_reason"] == "periodic"
    ]
    # 5개 봉, every 2 → bar_count 2, 4 → 2개 periodic
    assert len(periodic) == 2


def test_periodic_snapshot_every_one_bar(tmp_path: Path) -> None:
    """snapshot_every_bars=1 (기본) → 매 봉마다 periodic."""
    config = _config(tmp_path, n_bars=5, snapshot_every_bars=1)
    engine = BacktestEngine(config, _NoOp(), verbose=False)
    result = engine.run()

    events = _read_events(result.events_path)
    periodic = [
        e
        for e in events
        if e["type"] == "snapshot" and e["payload"]["snapshot_reason"] == "periodic"
    ]
    assert len(periodic) == 5  # 5 봉 모두


# ---------- FILL 직후 SNAPSHOT (주기 무관) ---------------------------------


def test_fill_snapshot_fires_regardless_of_period(tmp_path: Path) -> None:
    """snapshot_every_bars=100 (희박) 에서도 FILL 직후 즉시 reason='fill' SNAPSHOT 발행."""
    config = _config(tmp_path, n_bars=5, snapshot_every_bars=100)
    engine = BacktestEngine(config, _BuyOnce(), verbose=False)
    result = engine.run()

    assert result.num_fills == 1
    events = _read_events(result.events_path)
    fill_snaps = [
        e
        for e in events
        if e["type"] == "snapshot" and e["payload"]["snapshot_reason"] == "fill"
    ]
    assert len(fill_snaps) == 1


# ---------- snapshot_reason 필드 강제 ---------------------------------------


def test_all_snapshot_events_have_snapshot_reason(tmp_path: Path) -> None:
    """모든 SNAPSHOT 이벤트에 snapshot_reason 필드가 항상 존재 (헬퍼 사용 강제)."""
    config = _config(tmp_path, n_bars=5)
    engine = BacktestEngine(config, _BuyOnce(), verbose=False)
    result = engine.run()

    events = _read_events(result.events_path)
    snaps = [e for e in events if e["type"] == "snapshot"]
    assert len(snaps) > 0
    for snap in snaps:
        assert "snapshot_reason" in snap["payload"]
        assert snap["payload"]["snapshot_reason"] in {
            "fill",
            "settlement",
            "expire",
            "periodic",
        }


# ---------- 같은 ts에 여러 SNAPSHOT 허용 -----------------------------------


def test_same_ts_can_have_fill_and_periodic_snapshots(tmp_path: Path) -> None:
    """첫 매수 fill이 발생한 봉에서 fill SNAPSHOT + periodic SNAPSHOT 둘 다 같은 ts에 기록."""
    config = _config(tmp_path, n_bars=5, snapshot_every_bars=1)
    engine = BacktestEngine(config, _BuyOnce(), verbose=False)
    result = engine.run()

    events = _read_events(result.events_path)
    snaps_by_ts: dict[str, list[str]] = {}
    for e in events:
        if e["type"] == "snapshot":
            snaps_by_ts.setdefault(e["ts"], []).append(e["payload"]["snapshot_reason"])

    # FILL이 발생한 ts에 reason='fill' + reason='periodic' 동시 존재
    multi_reason_ts = [
        ts for ts, reasons in snaps_by_ts.items() if "fill" in reasons and "periodic" in reasons
    ]
    assert len(multi_reason_ts) >= 1


# ---------- ConfigError가 Engine 생성 전에 발생 -----------------------------


def test_config_error_raises_before_engine_init(tmp_path: Path) -> None:
    """spec §20 PR 7: ConfigError는 Engine 인스턴스화 전에 raise.

    BacktestConfig.__post_init__이 실패하면 BacktestConfig() 생성 자체가 실패하므로
    Engine.__init__는 도달조차 못 한다.
    """
    from backtester.core.errors import ConfigError

    data_dir = _make_parquet(tmp_path, n_bars=3)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ConfigError):
        BacktestConfig(
            run_id="bad",
            data_source=DataSourceConfig(base_dir=data_dir),
            instruments=[_btc()],
            timeframes_per_symbol={"BTCUSDT": ["1h"]},
            primary_symbol="BTCUSDT",
            primary_timeframe="1h",
            start=base,
            end=base + timedelta(hours=10),
            initial_equity=Decimal("100000"),
            output_dir=tmp_path / "runs",
            snapshot_every_bars=0,  # invalid
        )


# pytest 모듈 레벨 import 누락 방지
import pytest  # noqa: E402
