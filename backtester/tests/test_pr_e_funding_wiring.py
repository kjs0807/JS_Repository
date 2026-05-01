"""PR E 회귀 — FundingProcessor → Engine wiring + Ledger.on_settle 활성.

검증:
- ``BacktestConfig.funding_models`` (심볼별 FundingModel) YAML round-trip.
- Engine 이 funding_processor 를 만들고, funding boundary 마다 CashFlow 발행 →
  Ledger.on_settle → SETTLE 이벤트 + SNAPSHOT(reason='settlement').
- LONG 보유 + rate>0 → cash 감소. SHORT + rate>0 → cash 증가.
- flat 또는 boundary 아님 → SETTLE 없음.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import OrderIntent, TargetUnits
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.execution.funding import FundingModel
from backtester.instruments.base import FeeModel, Instrument
from backtester.portfolio.ledger import CashFlow, Ledger
from backtester.strategies.base import BaseStrategy

UTC = timezone.utc


# ---------- Ledger.on_settle 단위 -------------------------------------------


def test_ledger_on_settle_credits_positive_amount() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_settle(
        CashFlow(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            symbol="BTCUSDT",
            amount=Decimal("10"),
            reason="funding",
        )
    )
    assert led.cash == Decimal("100010")


def test_ledger_on_settle_debits_negative_amount() -> None:
    led = Ledger(initial_equity=Decimal("100000"))
    led.on_settle(
        CashFlow(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            symbol="BTCUSDT",
            amount=Decimal("-25"),
            reason="funding",
        )
    )
    assert led.cash == Decimal("99975")


# ---------- BacktestConfig.funding_models YAML --------------------------------


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


def _make_parquet(target: Path, n_bars: int = 24) -> None:
    """24h 시리즈 — funding boundary (8h) 가 3 회 발생하도록."""
    base = datetime(2026, 1, 1, tzinfo=UTC)  # UTC 자정
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n_bars)],
            "open": [100.0] * n_bars,
            "high": [101.0] * n_bars,
            "low": [99.0] * n_bars,
            "close": [100.0] * n_bars,
            "volume": [1.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)


def _config(
    tmp_path: Path,
    funding_models: dict[str, FundingModel] | None = None,
) -> BacktestConfig:
    data_dir = tmp_path / "data"
    _make_parquet(data_dir / "BTCUSDT_1h.parquet", n_bars=24)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs = dict(
        run_id="pr_e_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    if funding_models is not None:
        kwargs["funding_models"] = funding_models
    return BacktestConfig(**kwargs)  # type: ignore[arg-type]


def test_funding_models_yaml_round_trip(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8,
                rate_source="constant",
                constant_rate=Decimal("0.0001"),
            )
        },
    )
    yaml_path = tmp_path / "config.yaml"
    cfg.to_yaml(yaml_path)
    text = yaml_path.read_text(encoding="utf-8")
    assert "funding_models" in text
    assert "interval_hours: 8" in text
    assert "0.0001" in text

    restored = BacktestConfig.from_yaml(yaml_path)
    fm = restored.funding_models["BTCUSDT"]
    assert fm.interval_hours == 8
    assert fm.rate_source == "constant"
    assert fm.constant_rate == Decimal("0.0001")


# ---------- Engine wiring ---------------------------------------------------


class _BuyAndHoldStrategy(BaseStrategy):
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


def test_engine_emits_settle_events_at_funding_boundaries(tmp_path: Path) -> None:
    """24h 시리즈 + 8h funding boundary + LONG → SETTLE 이벤트 3 회 (08:00/16:00/00:00)."""
    cfg = _config(
        tmp_path,
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8,
                rate_source="constant",
                constant_rate=Decimal("0.0001"),
            )
        },
    )
    result = BacktestEngine(cfg, _BuyAndHoldStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    settles = list(reader.by_type(EventType.SETTLE))
    # 24h 시리즈 t0=00:00, ClockEvent ts = 01:00..24:00 (마감 시각).
    # funding boundary (UTC 8h 정렬): 08:00 / 16:00 / 00:00 (다음날) — 3 회.
    # 단, position 은 첫 buy intent 가 봉 1 (ts=01:00 close) 에서 발행되어 봉 2 open
    # (ts=01:00 ~ 02:00 사이) 에 fill. funding boundary 08:00 부터 LONG.
    assert len(settles) >= 3
    # 모든 settle 의 amount 가 음수 (LONG + rate>0 → 지불)
    for s in settles:
        amt = Decimal(s.payload["amount"])
        assert amt < 0, f"expected negative amount for LONG funding pay, got {amt}"


def test_engine_no_settle_when_funding_models_empty(tmp_path: Path) -> None:
    """funding_models 비어 있으면 funding_processor 가 None — SETTLE 이벤트 없음."""
    cfg = _config(tmp_path)  # default: funding_models = {}
    result = BacktestEngine(cfg, _BuyAndHoldStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    settles = list(reader.by_type(EventType.SETTLE))
    assert settles == []


def test_engine_settle_emits_snapshot_with_settlement_reason(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8,
                rate_source="constant",
                constant_rate=Decimal("0.0001"),
            )
        },
    )
    result = BacktestEngine(cfg, _BuyAndHoldStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    settles = list(reader.by_type(EventType.SETTLE))
    settle_ts = {s.ts for s in settles}
    settlement_snapshots = [
        s for s in reader.by_type(EventType.SNAPSHOT)
        if s.payload.get("snapshot_reason") == "settlement"
        and s.ts in settle_ts
    ]
    # 각 SETTLE ts 마다 SNAPSHOT(reason='settlement') 한 개씩
    assert len(settlement_snapshots) == len(settles)


def test_engine_funding_decreases_cash_for_long(tmp_path: Path) -> None:
    """LONG 보유 + rate>0 → 누적 funding 만큼 cash 감소."""
    cfg = _config(
        tmp_path,
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8,
                rate_source="constant",
                constant_rate=Decimal("0.0001"),
            )
        },
    )
    result = BacktestEngine(cfg, _BuyAndHoldStrategy(), verbose=False).run()
    reader = EventLogReader(result.events_path)
    settles = list(reader.by_type(EventType.SETTLE))
    total_funding = sum(Decimal(s.payload["amount"]) for s in settles)
    assert total_funding < 0
    # 정확 금액: 1 BTC * 100 mark * -0.0001 = -0.01 per boundary, 3 boundaries
    # → -0.03
    assert total_funding == Decimal("-0.03"), (
        f"expected -0.03 for 3x (1 unit * 100 mark * -0.0001), "
        f"got {total_funding}"
    )
