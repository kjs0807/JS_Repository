"""PR 8 Lookahead 검출 테스트 (spec §13.2).

전체 데이터로 1번, 절반 데이터로 1번 실행해 동일 시점까지의 시그널 timestamp/방향이
의미적으로 동일해야 한다 (의미 동일 — 바이트 동일은 Phase 2).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

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
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _generate_synthetic(n_bars: int) -> pl.DataFrame:
    """squeeze + release 패턴을 만드는 합성 데이터."""
    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    closes: list[float] = []
    for i in range(n_bars):
        if i < n_bars // 2:
            closes.append(100.0 + ((i % 3) - 1) * 0.02)
        else:
            closes.append(closes[-1] + 1.0)
    opens = [c - 0.05 for c in closes]
    highs = [max(c + 0.1, o) for c, o in zip(closes, opens, strict=True)]
    lows = [min(c - 0.1, o) for c, o in zip(closes, opens, strict=True)]
    return pl.DataFrame(
        {
            "timestamp": [base_ts + timedelta(hours=i) for i in range(n_bars)],
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))


def _write_parquet(df: pl.DataFrame, dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    df.write_parquet(dir_path / "BTCUSDT_1h.parquet")


def _config(data_dir: Path, output_dir: Path, run_id: str, end: datetime) -> BacktestConfig:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=output_dir,
    )


def _intent_signals(events_path: Path) -> list[tuple[str, str]]:
    """events.jsonl에서 (ts, side) 튜플 리스트로 추출 (intent_created만)."""
    out: list[tuple[str, str]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        evt = json.loads(line)
        if evt["type"] == "intent_created":
            out.append((evt["ts"], evt["payload"]["intent"]["side"]))
    return out


def test_lookahead_full_vs_half_signals_match_in_overlap(tmp_path: Path) -> None:
    """전체 50봉 / 앞 30봉 실행해 동일 구간 intent timestamp/side가 일치."""
    data_dir = tmp_path / "data"
    df_full = _generate_synthetic(n_bars=50)
    _write_parquet(df_full, data_dir)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    half_end = base + timedelta(hours=30)
    full_end = base + timedelta(hours=60)

    # 전체 — SMA 모드(예측 가능 산술)로 lookahead 검증
    cfg_full = _config(data_dir, tmp_path / "runs", "lookahead_full", full_end)
    BacktestEngine(
        cfg_full, BBKCSqueezeStrategy(kc_use_ema=False), verbose=False
    ).run()
    full_signals = _intent_signals(
        tmp_path / "runs" / "lookahead_full" / "events.jsonl"
    )

    # 절반
    cfg_half = _config(data_dir, tmp_path / "runs", "lookahead_half", half_end)
    BacktestEngine(
        cfg_half, BBKCSqueezeStrategy(kc_use_ema=False), verbose=False
    ).run()
    half_signals = _intent_signals(
        tmp_path / "runs" / "lookahead_half" / "events.jsonl"
    )

    # full 신호 중 ts <= half_end 인 것만 추출
    half_end_iso = half_end.isoformat()
    full_in_overlap = [(ts, side) for ts, side in full_signals if ts <= half_end_iso]

    # half_signals와 timestamp/side 의미 동일
    assert full_in_overlap == half_signals
