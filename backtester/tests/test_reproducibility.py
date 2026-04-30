"""PR 8 Reproducibility 테스트 (spec §13.3).

같은 config + random_seed로 두 번 실행 시 발행 이벤트 시퀀스의 (type, ts, payload)
의미가 동일해야 한다 (의미 동일 — events.jsonl 바이트 단위 동일은 Phase 2 canonical
JSON 도입 후).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

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


def _generate_synthetic(n_bars: int = 50) -> pl.DataFrame:
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


def _config(data_dir: Path, output_dir: Path, run_id: str) -> BacktestConfig:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=100),
        initial_equity=Decimal("100000"),
        output_dir=output_dir,
        random_seed=42,
    )


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _semantic_intent_or_fill(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """의미 비교용 핵심 필드만 추출 (intent_created + fill).

    order_id처럼 비결정적 가능성 있는 필드는 제외 — Phase 1 의미 동일 비교.
    """
    out: list[dict[str, Any]] = []
    for evt in events:
        if evt["type"] == "intent_created":
            intent = evt["payload"]["intent"]
            out.append(
                {
                    "type": "intent_created",
                    "ts": evt["ts"],
                    "symbol": intent["symbol"],
                    "side": intent["side"],
                    "type_": intent["type"],
                    "reason": intent["reason"],
                }
            )
        elif evt["type"] == "fill":
            out.append(
                {
                    "type": "fill",
                    "ts": evt["ts"],
                    "symbol": evt["payload"]["symbol"],
                    "side": evt["payload"]["side"],
                    "price": evt["payload"]["price"],
                    "size": evt["payload"]["size"],
                }
            )
    return out


def test_bbkc_reproducibility_semantic_equality(tmp_path: Path) -> None:
    """같은 config + seed → 두 번 실행한 intent/fill 의미 시퀀스 동일."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df = _generate_synthetic(n_bars=50)
    df.write_parquet(data_dir / "BTCUSDT_1h.parquet")

    # SMA 모드로 단순 산술 — Phase 1 reproducibility 의미 비교에 충분
    BacktestEngine(
        _config(data_dir, tmp_path / "runs", "repro_1"),
        BBKCSqueezeStrategy(kc_use_ema=False),
        verbose=False,
    ).run()
    BacktestEngine(
        _config(data_dir, tmp_path / "runs", "repro_2"),
        BBKCSqueezeStrategy(kc_use_ema=False),
        verbose=False,
    ).run()

    events1 = _read_events(tmp_path / "runs" / "repro_1" / "events.jsonl")
    events2 = _read_events(tmp_path / "runs" / "repro_2" / "events.jsonl")

    sem1 = _semantic_intent_or_fill(events1)
    sem2 = _semantic_intent_or_fill(events2)

    assert sem1 == sem2
    # 신호가 정말 발생했는지도 가벼운 확인 (미발생이면 reproducibility 무의미)
    intent_count = sum(1 for e in sem1 if e["type"] == "intent_created")
    assert intent_count > 0
