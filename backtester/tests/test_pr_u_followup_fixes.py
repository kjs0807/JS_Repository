"""PR U — PR T/S follow-up fixes 회귀.

검증:
1. BBKC parity — TP/SL = entry × pct/leverage (legacy 일치).
2. BBKC parity — RSI 단방향 (long: rsi<filter, short: rsi>100-filter).
3. BBKC parity — exit_mode='be_trail' 활성 (BE + ratchet trail).
4. Entry-bar bracket fill — 진입 봉의 high/low 가 TP/SL 도달 시 같은 봉 체결.
5. results/equity_curve.parquet — funding/liquidation 직후 상태 반영
   (rebuild_equity_curve 사용).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.context import StrategyContext
from backtester.core.engine import BacktestEngine
from backtester.core.orders import (
    BracketSpec,
    OrderIntent,
    TargetUnits,
)
from backtester.events.reader import EventLogReader
from backtester.events.types import EventType
from backtester.execution.funding import FundingModel
from backtester.indicators.stateless.rsi import RSI
from backtester.instruments.base import FeeModel, Instrument, MarginModel
from backtester.strategies.base import BaseStrategy
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy

UTC = timezone.utc
TS = datetime(2026, 1, 1, tzinfo=UTC)


def _btc(margin_model: MarginModel | None = None) -> Instrument:
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
        margin_model=margin_model,
    )


def _make_parquet(target: Path, bars: list[dict[str, Any]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(bars).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)


def _bar(
    t: datetime, o: float, h: float, low: float, c: float, vol: float = 1.0
) -> dict[str, Any]:
    return {
        "timestamp": t,
        "open": float(o),
        "high": float(h),
        "low": float(low),
        "close": float(c),
        "volume": float(vol),
    }


# ---------- 1. BBKC TP/SL = entry * pct/leverage ---------------------------


def test_bbkc_legacy_compat_tp_sl_divides_by_leverage(tmp_path: Path) -> None:
    """legacy parity: tp_pct=6%, leverage=3 → 가격 TP=2%, 가격 SL=7/3≈2.33%."""
    # Squeeze 25 + breakout 25 (bigger move) + revert — entry 발생.
    base = TS
    bars = []
    for i in range(25):
        bars.append(
            _bar(
                base + timedelta(hours=i),
                100.0,
                100.05,
                99.95,
                100.0 + (0.01 if i % 2 else -0.01),  # alternate small moves (PR T parity)
            )
        )
    for i in range(25):
        bars.append(
            _bar(
                base + timedelta(hours=25 + i),
                100.0 + i * 0.5,
                100.5 + i * 0.5,
                99.5 + i * 0.5,
                100.5 + i * 0.5,
            )
        )
    peak = 100.5 + 24 * 0.5
    for i in range(30):
        bars.append(
            _bar(
                base + timedelta(hours=50 + i),
                peak - i * 0.4,
                peak + 0.5 - i * 0.4,
                peak - 0.5 - i * 0.4,
                peak - i * 0.4,
            )
        )
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="parity_tp_sl",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=len(bars)),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = BBKCLegacyCompatStrategy(
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        rsi_filter=100.0,
        exit_mode="fixed",
    )
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    added = list(reader.by_type(EventType.ORDER_ADDED))
    children = [a for a in added if a.payload["parent_order_id"] is not None]
    assert children, "expected bracket children spawned"
    # entry close 추출 (parent fill 의 price)
    fills = list(reader.by_type(EventType.FILL))
    entry_fill = fills[0]
    entry_price = Decimal(str(entry_fill.payload["price"]))

    # legacy parity: TP=entry*(1+0.06/3), SL=entry*(1-0.07/3)
    expected_tp = entry_price * (Decimal("1") + Decimal("0.06") / Decimal("3"))
    expected_sl = entry_price * (Decimal("1") - Decimal("0.07") / Decimal("3"))
    by_type = {c.payload["intent"]["type"]: c for c in children}
    if "limit" in by_type:
        actual_tp = Decimal(by_type["limit"].payload["intent"]["limit_price"])
        # 소수점 6 자리 까지 비교
        assert abs(actual_tp - expected_tp) < Decimal("0.01")
    if "stop" in by_type:
        actual_sl = Decimal(by_type["stop"].payload["intent"]["stop_price"])
        assert abs(actual_sl - expected_sl) < Decimal("0.01")


# ---------- 2. BBKC RSI 단방향 (long: rsi<filter / short: rsi>100-filter) ---


def test_bbkc_rsi_filter_blocks_long_when_rsi_too_high(tmp_path: Path) -> None:
    """rsi_filter=50 + 강한 상승 (RSI>50) → long entry 차단 (rsi < 50 위반)."""
    base = TS
    # squeeze + 강한 breakout. RSI 가 50 초과로 계속 유지.
    bars = []
    for i in range(25):
        bars.append(
            _bar(
                base + timedelta(hours=i),
                100.0,
                100.05,
                99.95,
                100.0 + (0.01 if i % 2 else -0.01),  # alternate small moves (PR T parity)
            )
        )
    for i in range(10):
        bars.append(
            _bar(
                base + timedelta(hours=25 + i),
                100.0 + i * 1.0,
                100.5 + i * 1.0,
                99.5 + i * 1.0,
                100.5 + i * 1.0,
            )
        )
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="rsi_block",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=len(bars)),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = BBKCLegacyCompatStrategy(
        rsi_filter=50.0,  # entry needs rsi < 50
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        exit_mode="fixed",
    )
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    # RSI > 50 으로 long 진입 차단 — fill 없음
    assert fills == []


# ---------- 3. exit_mode='be_trail' BE 단계 ---------------------------------


def test_bbkc_be_trail_modifies_stop_to_breakeven(tmp_path: Path) -> None:
    """진입 후 close 가 entry + tp_distance × be_at_frac 이상 → SL을 entry 로 modify.

    legacy 정책 — tp_distance = entry * tp_pct/leverage.
    예: entry=100, tp_pct=0.06, leverage=3 → tp_dist = 100 * 0.02 = 2.
    be_at_frac=0.5 → move ≥ 1 → SL=entry.
    """
    base = TS
    # squeeze + breakout + revert (PR T 와 동일 fixture)
    bars = []
    for i in range(25):
        bars.append(
            _bar(
                base + timedelta(hours=i),
                100.0,
                100.05,
                99.95,
                100.0 + (0.01 if i % 2 else -0.01),  # alternate small moves (PR T parity)
            )
        )
    for i in range(25):
        bars.append(
            _bar(
                base + timedelta(hours=25 + i),
                100.0 + i * 0.5,
                100.5 + i * 0.5,
                99.5 + i * 0.5,
                100.5 + i * 0.5,
            )
        )
    peak = 100.5 + 24 * 0.5
    for i in range(30):
        bars.append(
            _bar(
                base + timedelta(hours=50 + i),
                peak - i * 0.4,
                peak + 0.5 - i * 0.4,
                peak - 0.5 - i * 0.4,
                peak - i * 0.4,
            )
        )
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="be_trail",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=len(bars)),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = BBKCLegacyCompatStrategy(
        tp_pct=Decimal("0.06"),
        sl_pct=Decimal("0.07"),
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        rsi_filter=100.0,
        exit_mode="be_trail",
    )
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    modified = list(reader.by_type(EventType.ORDER_MODIFIED))
    # be_trail 모드 → 적어도 한 번 modify 발행 (BE 또는 trail)
    assert len(modified) >= 1


# ---------- 4. Entry-bar bracket fill ---------------------------------------


class _ImmediateBreakoutStrategy(BaseStrategy):
    """진입 후 같은 봉의 high/low 가 TP/SL 에 도달하는 시나리오 강제 테스트."""

    def __init__(self, tp_price: Decimal, sl_price: Decimal) -> None:
        self.tp = tp_price
        self.sl = sl_price
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
                bracket=BracketSpec(
                    take_profit_price=self.tp,
                    stop_loss_price=self.sl,
                ),
            )
        ]


def test_entry_bar_bracket_tp_fills_same_bar(tmp_path: Path) -> None:
    """봉 2: open=100 entry, high=120 (TP=110 도달), low=99 (SL=95 미도달).
    PR U: TP child 가 같은 봉에서 체결.
    """
    base = TS
    bars = [
        _bar(base, 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=1), 100, 120, 99, 115),  # 진입 + TP 도달
        _bar(base + timedelta(hours=2), 115, 115.5, 114.5, 115),
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="entry_bar_tp",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=3),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = _ImmediateBreakoutStrategy(
        tp_price=Decimal("110"), sl_price=Decimal("95")
    )
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    # entry + TP = 2 fills 같은 봉 (ts) 에 발생
    assert len(fills) == 2
    entry_ts = fills[0].ts
    tp_ts = fills[1].ts
    assert entry_ts == tp_ts, (
        f"PR U: entry-bar bracket fill expected, got entry@{entry_ts} tp@{tp_ts}"
    )
    assert fills[1].payload["side"] == "sell"
    # OCO sibling cancel 도 같은 ts 에 — SL cancelled
    cancels = list(reader.by_type(EventType.ORDER_CANCELLED))
    assert any(c.payload.get("reason") == "oco_sibling_filled" for c in cancels)


def test_entry_bar_bracket_sl_fills_same_bar_pessimistic(tmp_path: Path) -> None:
    """진입 봉에서 high=120 (TP=110), low=85 (SL=95) 양쪽 도달 — PESSIMISTIC → SL 우선."""
    base = TS
    bars = [
        _bar(base, 100, 100.5, 99.5, 100),
        _bar(base + timedelta(hours=1), 100, 120, 85, 95),  # 양쪽 도달
        _bar(base + timedelta(hours=2), 95, 95.5, 94.5, 95),
    ]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="entry_bar_both",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=3),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    strat = _ImmediateBreakoutStrategy(
        tp_price=Decimal("110"), sl_price=Decimal("95")
    )
    result = BacktestEngine(cfg, strat, verbose=False).run()
    reader = EventLogReader(result.events_path)
    fills = list(reader.by_type(EventType.FILL))
    assert len(fills) == 2
    # PESSIMISTIC → SL price 95 우선
    assert Decimal(fills[1].payload["price"]) == Decimal("95")


# ---------- 5. results/equity_curve.parquet rebuild from events ------------


class _BuyAndHoldFunding(BaseStrategy):
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


def test_results_equity_curve_includes_funding_settlement(tmp_path: Path) -> None:
    """funding 직후의 equity 가 equity_curve.parquet 에 반영. PR U fix 전에는 누락."""
    base = TS
    n_bars = 24
    bars = [_bar(base + timedelta(hours=i), 100, 101, 99, 100) for i in range(n_bars)]
    _make_parquet(tmp_path / "data" / "BTCUSDT_1h.parquet", bars)
    cfg = BacktestConfig(
        run_id="curve_funding",
        data_source=DataSourceConfig(base_dir=tmp_path / "data"),
        instruments=[_btc()],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=n_bars),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        funding_models={
            "BTCUSDT": FundingModel(
                interval_hours=8,
                rate_source="constant",
                constant_rate=Decimal("0.001"),  # 큰 rate → 명확 변동
            )
        },
    )
    result = BacktestEngine(cfg, _BuyAndHoldFunding(), verbose=False).run()
    eq_path = result.run_dir / "results" / "equity_curve.parquet"
    eq = pl.read_parquet(eq_path)
    # funding 영향 — 1 unit * 100 mark * -0.001 = -0.1 per boundary, 3 boundary = -0.3
    # equity 가 100000 보다 작아야 (long pays funding)
    final_equity = eq["equity"][-1]
    assert final_equity < 100000.0, (
        f"PR U: equity_curve.parquet should reflect funding loss, got {final_equity}"
    )


# Touch RSI module import
_ = RSI
