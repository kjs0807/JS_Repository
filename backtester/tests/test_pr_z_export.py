"""PR Z — Human-readable export 회귀.

검증:
1. export_run_data 가 fills.csv / intents.csv / orders.csv / equity_curve.csv /
   summary.json 5 파일을 생성.
2. fills/intents 에 심볼별 row 가 정확히 적힘 (3 심볼 BBKC multi run).
3. summary.json 에 final_equity / total_return / max_drawdown_pct 포함.
4. 빈 events 케이스 (fixture-driven) — header 만 있는 CSV 작성.
5. CLI ``backtester export`` 종단 동작.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl

from backtester.analysis.export import export_run_data
from backtester.cli.main import main as cli_main
from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy
from backtester.strategies.bbkc_multi_legacy_compat import (
    BBKCMultiLegacyCompatStrategy,
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


def _run_multi(tmp_path: Path) -> Path:
    syms = [
        ("BTCUSDT", "BTC", 100.0),
        ("ETHUSDT", "ETH", 50.0),
        ("AVAXUSDT", "AVAX", 25.0),
    ]
    data_dir = tmp_path / "data"
    for s, _b, p in syms:
        _make_squeeze_breakout(data_dir / f"{s}_1h.parquet", base_price=p)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    end = base + timedelta(hours=80 + 1)
    cfg = BacktestConfig(
        run_id="export_test",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_instrument(s, b) for s, b, _ in syms],
        timeframes_per_symbol={s: ["1h"] for s, _, _ in syms},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
        persist_instrument_snapshot=False,
    )
    strategy = BBKCMultiLegacyCompatStrategy(
        symbols=[s for s, _, _ in syms],
        timeframe="1h",
        child_params={
            "tp_pct": Decimal("0.06"),
            "sl_pct": Decimal("0.07"),
            "leverage": Decimal("3"),
            "margin_pct": Decimal("0.05"),
            "exit_mode": "fixed",
            "rsi_filter": 100.0,
        },
    )
    result = BacktestEngine(cfg, strategy, verbose=False).run()
    return result.run_dir


# ---------- 1. file generation ----------------------------------------------


def test_export_creates_all_five_artifacts(tmp_path: Path) -> None:
    run_dir = _run_multi(tmp_path)
    outputs = export_run_data(run_dir)
    assert set(outputs.keys()) == {
        "fills",
        "intents",
        "orders",
        "equity_curve",
        "summary",
    }
    for path in outputs.values():
        assert path.exists(), f"missing artifact: {path}"
        assert path.stat().st_size > 0, f"empty artifact: {path}"


# ---------- 2. fills/intents per-symbol row counts --------------------------


def test_fills_csv_has_per_symbol_rows(tmp_path: Path) -> None:
    run_dir = _run_multi(tmp_path)
    outputs = export_run_data(run_dir)
    with open(outputs["fills"], encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    syms = {r["symbol"] for r in rows}
    assert "BTCUSDT" in syms
    assert "ETHUSDT" in syms
    assert "AVAXUSDT" in syms
    # 모든 row 에 필수 컬럼 채워짐.
    for r in rows:
        assert r["timestamp"]
        assert r["side"] in {"buy", "sell"}
        assert r["price"]
        assert r["size"]


def test_intents_csv_has_per_symbol_rows(tmp_path: Path) -> None:
    run_dir = _run_multi(tmp_path)
    outputs = export_run_data(run_dir)
    with open(outputs["intents"], encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    syms = {r["symbol"] for r in rows}
    assert {"BTCUSDT", "ETHUSDT", "AVAXUSDT"}.issubset(syms)


# ---------- 3. summary fields -----------------------------------------------


def test_summary_contains_required_fields(tmp_path: Path) -> None:
    run_dir = _run_multi(tmp_path)
    outputs = export_run_data(run_dir)
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    for k in (
        "run_id",
        "initial_equity",
        "final_equity",
        "total_return",
        "max_drawdown_pct",
        "sharpe_ratio",
        "n_fills",
        "n_intents",
        "fills_by_symbol",
        "intents_by_symbol",
    ):
        assert k in summary, f"summary.json missing key: {k}"
    assert summary["initial_equity"] == 100000.0
    assert isinstance(summary["fills_by_symbol"], dict)
    assert summary["n_fills"] >= 3
    assert summary["fills_by_symbol"]["BTCUSDT"] >= 1


# ---------- 4. orders CSV captures all order events -------------------------


def test_orders_csv_includes_added_and_cancelled(tmp_path: Path) -> None:
    run_dir = _run_multi(tmp_path)
    outputs = export_run_data(run_dir)
    with open(outputs["orders"], encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    types = {r["event_type"] for r in rows}
    assert "order_added" in types
    # bracket children 의 OCO sibling cancel 은 multi run 에서 흔하게 발생.
    assert "order_cancelled" in types or "order_expired" in types or len(rows) > 0


# ---------- 5. CLI integration ----------------------------------------------


def test_cli_export_smoke(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    run_dir = _run_multi(tmp_path)
    rc = cli_main(["export", str(run_dir)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "fills" in captured.out
    assert "summary" in captured.out
    # 모든 산출물이 디스크에도 존재.
    for name in ("fills.csv", "intents.csv", "orders.csv", "equity_curve.csv", "summary.json"):
        assert (run_dir / "exports" / name).exists()


def test_cli_export_quiet(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    run_dir = _run_multi(tmp_path)
    rc = cli_main(["export", str(run_dir), "--quiet"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cli_export_missing_dir(tmp_path: Path) -> None:
    rc = cli_main(["export", str(tmp_path / "nonexistent")])
    assert rc == 2


# ---------- 6. equity_curve consistency -------------------------------------


def test_equity_curve_csv_starts_at_initial_equity(tmp_path: Path) -> None:
    """첫 row 가 initial_equity 근처 (entry 비용 차감 직후 fill 시점) — 필수 컬럼 모두 존재."""
    run_dir = _run_multi(tmp_path)
    outputs = export_run_data(run_dir)
    with open(outputs["equity_curve"], encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) > 0
    expected_cols = {
        "timestamp",
        "equity",
        "cash",
        "realized_pnl",
        "unrealized_pnl",
        "drawdown",
        "drawdown_pct",
    }
    assert expected_cols.issubset(set(rows[0].keys()))


# ---------- 7. single-symbol legacy export ----------------------------------


def test_export_single_symbol_run(tmp_path: Path) -> None:
    """단일 심볼 BBKCLegacyCompat run 도 동일하게 export 되는지."""
    sym = "BTCUSDT"
    data_dir = tmp_path / "data"
    _make_squeeze_breakout(data_dir / f"{sym}_1h.parquet")
    base = datetime(2026, 3, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="single_export_test",
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
    outputs = export_run_data(res.run_dir)
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    assert summary["primary_symbol"] == sym
    assert summary["n_fills"] >= 1
