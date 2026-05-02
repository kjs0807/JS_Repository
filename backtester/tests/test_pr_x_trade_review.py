"""PR X — Trade Review chart 회귀.

검증:
1. identify_trades — entry/exit pair 식별 + open trade 보존.
2. render_trade_review — index.html + 심볼별 trade html 생성.
3. window 슬라이싱 — pre_bars / post_bars 만큼만 (전체가 아닌) 봉 포함.
4. multi-symbol run — 심볼별 separate file.
5. CLI ``backtester trade-review``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.cli.main import main as cli_main
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.events.reader import EventLogReader
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.bbkc_multi_legacy_compat import (
    BBKCMultiLegacyCompatStrategy,
)
from backtester.viz.trade_review import (
    identify_trades,
    render_trade_review,
)

UTC = timezone.utc


def _make_squeeze_breakout(
    target: Path, *, base_price: float = 100.0
) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=i),
                "open": base_price,
                "high": base_price + 0.05,
                "low": base_price - 0.05,
                "close": base_price + (0.01 if i % 2 else -0.01),
                "volume": 1.0,
            }
        )
    for i in range(25):
        rows.append(
            {
                "timestamp": base + timedelta(hours=25 + i),
                "open": base_price + i * 0.5,
                "high": base_price + 0.5 + i * 0.5,
                "low": base_price - 0.5 + i * 0.5,
                "close": base_price + 0.5 + i * 0.5,
                "volume": 1.0,
            }
        )
    peak = base_price + 0.5 + 24 * 0.5
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


def _instrument(symbol: str, base: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0.0006")),
    )


def _run_single(tmp_path: Path) -> Path:
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    _make_squeeze_breakout(data_dir / f"{sym}_1h.parquet")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="trade_review_single",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(sym, "BTC")],
        timeframes_per_symbol={sym: ["1h"]},
        primary_symbol=sym,
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=81),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = BBKCLegacyCompatStrategy(
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        rsi_filter=100.0,
        exit_mode="fixed",
    )
    res = BacktestEngine(cfg, strategy, verbose=False).run()
    return res.run_dir


def _run_multi(tmp_path: Path) -> Path:
    syms = [
        ("BTCUSDT", "BTC", 100.0),
        ("ETHUSDT", "ETH", 50.0),
        ("AVAXUSDT", "AVAX", 25.0),
    ]
    data_dir = tmp_path / "data"
    for s, _, p in syms:
        _make_squeeze_breakout(data_dir / f"{s}_1h.parquet", base_price=p)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="trade_review_multi",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(s, b) for s, b, _ in syms],
        timeframes_per_symbol={s: ["1h"] for s, _, _ in syms},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=81),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = BBKCMultiLegacyCompatStrategy(
        symbols=[s for s, _, _ in syms],
        timeframe="1h",
        child_params={
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "rsi_filter": 100.0,
            "exit_mode": "fixed",
        },
    )
    res = BacktestEngine(cfg, strategy, verbose=False).run()
    return res.run_dir


# ---------- 1. identify_trades ----------------------------------------------


def test_identify_trades_finds_entry_exit_pair(tmp_path: Path) -> None:
    run_dir = _run_single(tmp_path)
    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    assert len(trades) >= 1
    t = trades[0]
    assert t.symbol == "BTCUSDT"
    assert t.direction in {"long", "short"}
    # squeeze breakout fixture 는 양봉 ramp → long 진입.
    assert t.direction == "long"
    # exit 봉 이후 현재 봉 가격 변동에 따라 close.
    if not t.open:
        assert t.exit_ts is not None
        assert t.exit_price is not None


def test_identify_trades_records_fills(tmp_path: Path) -> None:
    run_dir = _run_single(tmp_path)
    reader = EventLogReader(run_dir / "events.jsonl")
    trades = identify_trades(reader)
    for t in trades:
        # 최소 entry 1 fill, close 시 exit 1 fill.
        if t.open:
            assert len(t.fills) >= 1
        else:
            assert len(t.fills) >= 2


# ---------- 2. render produces index + per-trade html -----------------------


def test_render_trade_review_creates_index_and_charts(tmp_path: Path) -> None:
    run_dir = _run_single(tmp_path)
    out = render_trade_review(run_dir, pre_bars=20, post_bars=10)
    assert out.exists()
    assert out.name == "index.html"
    chart_files = list((run_dir / "charts" / "trades").glob("*_trade_*.html"))
    assert len(chart_files) >= 1
    # index.html 에 chart 링크 포함.
    txt = out.read_text(encoding="utf-8")
    for cf in chart_files:
        assert cf.name in txt


# ---------- 3. window slicing -----------------------------------------------


def test_render_trade_review_window_only_subset_of_bars(tmp_path: Path) -> None:
    """trade chart 가 전체 80 봉 fixture 가 아닌 windowed 만 포함."""
    run_dir = _run_single(tmp_path)
    render_trade_review(run_dir, pre_bars=10, post_bars=5)
    chart_files = list((run_dir / "charts" / "trades").glob("BTCUSDT_trade_*.html"))
    assert chart_files
    txt = chart_files[0].read_text(encoding="utf-8")
    # 전체 fixture 의 가장 마지막 timestamp 가 chart 안에 들어가지 않아야 한다 —
    # fixture base = 2026-03-01, 끝은 2026-03-04 hour 09 부근. trade 는 보통 hour
    # 25~50 사이에 종료되므로 windowed chart 는 ~hour 60 까지.
    end_ts_far = "2026-03-04T09"
    # plotly 는 timestamp 를 ISO 로 직렬화 — windowed chart 에서는 이 stamp 가 없어야.
    assert end_ts_far not in txt


# ---------- 4. multi-symbol per-symbol files --------------------------------


def test_render_trade_review_multi_symbol_separate_files(tmp_path: Path) -> None:
    run_dir = _run_multi(tmp_path)
    render_trade_review(run_dir, pre_bars=20, post_bars=10)
    btc = list((run_dir / "charts" / "trades").glob("BTCUSDT_trade_*.html"))
    eth = list((run_dir / "charts" / "trades").glob("ETHUSDT_trade_*.html"))
    avax = list((run_dir / "charts" / "trades").glob("AVAXUSDT_trade_*.html"))
    assert btc, "no BTCUSDT trade chart generated"
    assert eth, "no ETHUSDT trade chart generated"
    assert avax, "no AVAXUSDT trade chart generated"
    # index.html 에 3 심볼 모두 표 헤더로 나타남.
    idx = (run_dir / "charts" / "trades" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "BTCUSDT" in idx
    assert "ETHUSDT" in idx
    assert "AVAXUSDT" in idx


# ---------- 5. CLI ----------------------------------------------------------


def test_cli_trade_review_smoke(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    run_dir = _run_single(tmp_path)
    rc = cli_main(
        ["trade-review", str(run_dir), "--pre-bars", "30", "--post-bars", "20"]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "index.html" in captured.out
    assert (run_dir / "charts" / "trades" / "index.html").exists()


def test_cli_trade_review_quiet(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    run_dir = _run_single(tmp_path)
    rc = cli_main(["trade-review", str(run_dir), "--quiet"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cli_trade_review_missing_dir(tmp_path: Path) -> None:
    rc = cli_main(["trade-review", str(tmp_path / "nonexistent")])
    assert rc == 2


# ---------- 6. zero-trades edge case ----------------------------------------


def test_render_trade_review_no_trades_writes_index_only(tmp_path: Path) -> None:
    """fixture 가 trade 를 trigger 하지 않는 경우 — index.html 에 No trades found 표시."""
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    base = datetime(2026, 3, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": base + timedelta(hours=i),
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
        }
        for i in range(60)
    ]
    target = data_dir / f"{sym}_1h.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC"))
    ).write_parquet(target)
    cfg = BacktestConfig(
        run_id="trade_review_empty",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(sym, "BTC")],
        timeframes_per_symbol={sym: ["1h"]},
        primary_symbol=sym,
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=61),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = BBKCLegacyCompatStrategy(
        leverage=Decimal("3"),
        margin_pct=Decimal("0.05"),
        rsi_filter=100.0,
        exit_mode="fixed",
    )
    res = BacktestEngine(cfg, strategy, verbose=False).run()
    out = render_trade_review(res.run_dir)
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert "No trades found" in txt
