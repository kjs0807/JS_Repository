"""PR 7 더미 buy-and-hold 통합 테스트 (spec §20 PR 7 acceptance)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


def _btc(taker: str = "0") -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        asset_class="crypto_perp",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency="BTC",
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal(taker)),
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


class _BuyOnceHold(BaseStrategy):
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


def _config(tmp_path: Path) -> BacktestConfig:
    data_dir = _make_parquet(tmp_path, n_bars=5)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return BacktestConfig(
        run_id="test_buy_hold",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc(taker="0")],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=10),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )


# ---------- BacktestResult 정확성 -------------------------------------------


def test_buy_and_hold_returns_valid_result(tmp_path: Path) -> None:
    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()

    assert result.requested_run_id == "test_buy_hold"
    assert result.resolved_run_id == "test_buy_hold"
    assert result.run_dir == tmp_path / "runs" / "test_buy_hold"
    assert result.num_intents == 1
    assert result.num_fills == 1


def test_buy_and_hold_final_equity_reflects_holdings(tmp_path: Path) -> None:
    """첫 봉 마감 시 buy → 두번째 봉 open(=101)에 1 BTC 매수.
    마지막 봉(idx=4) close=104.5에서 mtm.
    cash = 100000 - 101 = 99899
    holdings_market_value = 1 * 104.5 = 104.5
    equity = 99899 + 104.5 = 100003.5
    """
    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()
    assert result.final_equity == Decimal("100003.5")
    # total_return = (100003.5 - 100000) / 100000 = 0.000035
    assert result.total_return == Decimal("0.000035")


# ---------- 산출 파일 ------------------------------------------------------


def test_buy_and_hold_creates_config_files(tmp_path: Path) -> None:
    """Phase 1.5+: result.config_path 는 config.yaml. config.json 도 audit 으로 유지."""
    import yaml

    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()

    # canonical (Phase 1.5+) = yaml
    assert result.config_path.name == "config.yaml"
    assert result.config_path.exists()
    cfg_yaml = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
    assert cfg_yaml["run_id"] == "test_buy_hold"
    assert cfg_yaml["resolved_run_id"] == "test_buy_hold"
    assert cfg_yaml["primary_symbol"] == "BTCUSDT"
    assert cfg_yaml["primary_timeframe"] == "1h"
    assert "run_dir" in cfg_yaml
    # initial_equity 는 Decimal → str
    assert cfg_yaml["initial_equity"] == "100000"

    # Phase 1 audit (config.json) 도 동시에 기록
    json_path = result.run_dir / "config.json"
    assert json_path.exists()
    cfg_json = json.loads(json_path.read_text(encoding="utf-8"))
    assert cfg_json["run_id"] == "test_buy_hold"


def test_buy_and_hold_creates_events_jsonl(tmp_path: Path) -> None:
    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()
    assert result.events_path.exists()
    lines = result.events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 0


def test_buy_and_hold_creates_equity_curve(tmp_path: Path) -> None:
    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()
    eq_path = result.run_dir / "results" / "equity_curve.parquet"
    assert eq_path.exists()
    eq = pl.read_parquet(eq_path)
    assert eq.height == 5  # 봉 5개 → on_market 5번


def test_buy_and_hold_creates_bars_persist(tmp_path: Path) -> None:
    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()
    bars_path = result.run_dir / "bars" / "BTCUSDT_1h.parquet"
    assert bars_path.exists()


# ---------- 이벤트 시퀀스 검증 ----------------------------------------------


def test_buy_and_hold_event_sequence(tmp_path: Path) -> None:
    """이벤트 종류 카운트 검증."""
    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()

    events = [
        json.loads(line)
        for line in result.events_path.read_text(encoding="utf-8").splitlines()
    ]
    types = [e["type"] for e in events]

    assert types.count("intent_created") == 1
    assert types.count("order_added") == 1
    assert types.count("fill") == 1
    # fill SNAPSHOT 1개 + periodic SNAPSHOT 5개 = 6개
    snap_count = types.count("snapshot")
    assert snap_count == 6


def test_all_event_lines_have_schema_version(tmp_path: Path) -> None:
    engine = BacktestEngine(_config(tmp_path), _BuyOnceHold(), verbose=False)
    result = engine.run()
    for line in result.events_path.read_text(encoding="utf-8").splitlines():
        evt = json.loads(line)
        assert evt["schema_version"] == 1
