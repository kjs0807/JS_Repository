"""PR B — byte-identical replay 게이트 (spec §13.3).

같은 (config, random_seed, data) 로 두 번 실행하면 ``events.jsonl`` 이 byte-identical
이어야 한다. canonical JSON (sort_keys + 고정 separators, PR 16 prep 2차) + deterministic
order_id (OrderBook ``ord_{counter}``) + Python dict 삽입 순서 보장 + 같은 코드 경로
조합으로 활성.

추가로:
- ``rebuild_results`` 가 두 run 의 ``events.jsonl`` 로부터 동일 equity_curve 를 만든다.
- ``run_dir`` 만 남아도 (bars/indicators 캐시 삭제) ``rebuild_results`` 가 동작.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.analysis.rebuild import rebuild_equity_curve, rebuild_results
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
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
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _make_parquet(target: Path, n_bars: int = 80) -> None:
    """squeeze + breakout 시나리오 — BBKC 가 entry/exit signal 발행하도록."""
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    # squeeze 25 봉: 100 ± 0.05 횡보
    for i in range(25):
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
    # breakout 25 봉: 상승
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=25 + i),
                "open": 100.0 + i * 0.5,
                "high": 100.5 + i * 0.5,
                "low": 99.5 + i * 0.5,
                "close": 100.5 + i * 0.5,
                "volume": 1.0,
            }
        )
    # mean revert 30 봉: 하락
    peak = 100.5 + 24 * 0.5  # 112.5
    for i in range(30):
        rows.append(
            {
                "timestamp": base + timedelta(hours=50 + i),
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


def _build_config(tmp_path: Path, run_id: str) -> BacktestConfig:
    data_dir = tmp_path / "data"
    if not (data_dir / "BTCUSDT_1h.parquet").exists():
        _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=80)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    return BacktestConfig(
        run_id=run_id,
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=80),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        random_seed=42,
    )


def _run_once(tmp_path: Path, run_id: str) -> Path:
    cfg = _build_config(tmp_path, run_id)
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    result = engine.run()
    return result.events_path


def test_two_runs_produce_byte_identical_events_jsonl(tmp_path: Path) -> None:
    """동일 config 두 번 실행 → events.jsonl byte-identical (spec §13.3 게이트)."""
    p1 = _run_once(tmp_path, "run_a")
    p2 = _run_once(tmp_path, "run_b")
    b1 = p1.read_bytes()
    b2 = p2.read_bytes()
    assert b1 == b2, (
        f"events.jsonl differs between identical runs.\n"
        f"size diff: {len(b1)} vs {len(b2)} bytes\n"
        f"first divergence at byte: "
        f"{next((i for i, (a, b) in enumerate(zip(b1, b2, strict=False)) if a != b), 'N/A')}"
    )


def test_two_runs_produce_identical_equity_curves(tmp_path: Path) -> None:
    """events.jsonl 동일 → rebuild_results 의 equity_curve.parquet 도 동일."""
    p1 = _run_once(tmp_path, "run_a")
    p2 = _run_once(tmp_path, "run_b")
    out1 = rebuild_equity_curve(p1.parent)
    out2 = rebuild_equity_curve(p2.parent)
    eq1 = pl.read_parquet(out1)
    eq2 = pl.read_parquet(out2)
    assert eq1.equals(eq2)


def test_order_ids_are_deterministic_across_runs(tmp_path: Path) -> None:
    """order_id 는 OrderBook ``ord_{counter}`` 로 매 run 마다 0 부터 — 두 run 에서 동일 시퀀스."""
    p1 = _run_once(tmp_path, "run_a")
    p2 = _run_once(tmp_path, "run_b")
    reader1 = EventLogReader(p1)
    reader2 = EventLogReader(p2)
    # ORDER_ADDED 이벤트의 order_id 시퀀스 비교
    ids1 = [e.payload["order_id"] for e in reader1.by_type(EventType.ORDER_ADDED)]
    ids2 = [e.payload["order_id"] for e in reader2.by_type(EventType.ORDER_ADDED)]
    assert ids1 == ids2
    # 적어도 하나의 ORDER_ADDED — fixture 가 BBKC entry signal 을 만들도록 구성됨
    assert ids1, "fixture must trigger at least one ORDER_ADDED event"
    # ord_0, ord_1, ... 단조 증가
    for i, oid in enumerate(ids1):
        assert oid == f"ord_{i}"


def test_rebuild_results_works_with_only_events_jsonl(tmp_path: Path) -> None:
    """run_dir 에 events.jsonl + config.yaml 만 남기고 bars/indicators/results 모두
    삭제해도 ``rebuild_results`` 가 ``results/equity_curve.parquet`` 재생성한다.
    """
    p = _run_once(tmp_path, "rebuild_smoke")
    run_dir = p.parent
    # bars / indicators / results / charts 디렉토리 모두 삭제
    for sub in ("bars", "indicators", "results", "charts"):
        sub_path = run_dir / sub
        if sub_path.exists():
            shutil.rmtree(sub_path)
    # rebuild
    outputs = rebuild_results(run_dir)
    assert "equity_curve" in outputs
    eq_path = outputs["equity_curve"]
    assert eq_path.exists()
    eq = pl.read_parquet(eq_path)
    assert eq.height > 0
    assert "equity" in eq.columns


def test_serialize_event_payload_with_sets_is_sorted() -> None:
    """``serialize_event_payload`` 의 set 직렬화가 deterministic 하도록 정렬되어야 한다.

    PR B 발견 — 기존 구현은 ``[serialize(x) for x in set]`` 로 set iteration 순서가
    Python 구현 상세에 의존. byte-identical replay 게이트 활성용 정렬.
    """
    from backtester.events.serialize import serialize_event_payload

    s = {"c", "a", "b"}
    out1 = serialize_event_payload(s)
    out2 = serialize_event_payload(s)
    assert out1 == out2 == ["a", "b", "c"]
