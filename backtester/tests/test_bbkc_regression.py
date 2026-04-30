"""PR 8 BBKC 회귀 게이트 (spec §20 PR 8).

기존 모의매매 결과 fixture와 v8 BBKC 출력 비교. fixture / OHLCV 파일이 없으면
pytest.skip — 환경별 데이터 가용성에 의존.

skip 견고성: 임시 디렉토리(tmp_path) 생성/정리 단계에서 OS 권한 문제로 실패하지 않도록
fixture 존재 검사를 임시 디렉토리 요구 **이전에** 수행. 임시 디렉토리는 `tmp_path_factory`로
실제로 필요한 시점에만 mktemp.

fixture 포맷 (CSV, UTF-8):
    symbol,timestamp,direction,source_run_id
    BTCUSDT,2026-01-01T00:00:00+00:00,buy,legacy_run_v1
    ...

OHLCV fixture: backtester/tests/fixtures/{symbol}_{timeframe}.parquet
시그널 fixture: backtester/tests/fixtures/bbkc_signals.csv
"""

from __future__ import annotations

import json
from datetime import timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

UTC = timezone.utc

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SIGNALS_CSV = FIXTURE_DIR / "bbkc_signals.csv"


def _make_instrument(symbol: str) -> Instrument:
    """fixture의 symbol 그대로 Instrument 생성. 회귀는 indicator/시그널 비교가 본질이라
    tick_size/fee 등 microstructure 디테일은 PR8 게이트와 무관 — 합리적 기본값 사용."""
    base = symbol.removesuffix("USDT") if symbol.endswith("USDT") else symbol
    return Instrument(
        symbol=symbol,
        asset_class="crypto_perp",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        quote_currency="USDT",
        base_currency=base,
        size_unit="base_asset",
        fee_model=FeeModel(type="flat", taker=Decimal("0")),
    )


def _intent_signals(
    events_path: Path,
    *,
    sides: set[str] | None = None,
) -> list[tuple[str, str]]:
    """events.jsonl에서 (ts, side) 추출. ``sides`` 지정 시 해당 side만 반환."""
    out: list[tuple[str, str]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        evt = json.loads(line)
        if evt["type"] != "intent_created":
            continue
        side = evt["payload"]["intent"]["side"]
        if sides is not None and side not in sides:
            continue
        out.append((evt["ts"], side))
    return out


# ---------- 포맷 문서화 ------------------------------------------------------


def test_bbkc_regression_fixture_format_documented() -> None:
    """fixtures/README.md 포맷 명세 — tmp_path 미사용 (Path 검사만)."""
    readme = FIXTURE_DIR / "README.md"
    if not readme.exists():
        pytest.skip(f"fixtures/README.md missing: {readme}")
    content = readme.read_text(encoding="utf-8")
    for required in ("symbol", "timestamp", "direction", "source_run_id"):
        assert required in content, f"README must mention column {required!r}"


# ---------- 회귀 비교 (fixture 존재 시) -------------------------------------


def test_bbkc_regression_signals_match_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """fixture 신호 시퀀스와 v8 BBKC 출력이 timestamp+direction 100% 일치.

    skip 검사 → fixture 검증 → tmp 디렉토리 생성 → 백테스트 실행 순서.
    OS temp 권한 문제로 실패 시에도 skip이 정상 동작하도록 tmp는 마지막에 mktemp.

    legacy 호환을 위해 BBKCSqueezeStrategy 기본값(EMA 모드 + bb_std=1.5 + kc_mult=1.0
    + atr_period=14)을 그대로 사용. legacy와 추가 차이(RSI 필터, TP/SL, time_stop, short
    진입 등)는 docstring §Phase 1 한정 참조.
    """
    if not SIGNALS_CSV.exists():
        pytest.skip(
            f"BBKC regression fixture missing: {SIGNALS_CSV}. "
            f"Generate via tools/export_db_to_parquet.py + 모의매매 시그널 export "
            f"in home environment."
        )

    fixture = pl.read_csv(SIGNALS_CSV)
    expected_columns = {"symbol", "timestamp", "direction", "source_run_id"}
    assert expected_columns.issubset(set(fixture.columns)), (
        f"Fixture must have columns {expected_columns}, got {fixture.columns}"
    )

    symbols = fixture["symbol"].unique().to_list()
    assert len(symbols) == 1, (
        f"Phase 1 regression: single symbol only, got {symbols}"
    )
    symbol = symbols[0]

    ohlcv_path = FIXTURE_DIR / f"{symbol}_1h.parquet"
    if not ohlcv_path.exists():
        pytest.skip(
            f"OHLCV fixture missing: {ohlcv_path}. "
            f"Required alongside bbkc_signals.csv for regression."
        )

    # 모든 fixture 검증 통과 → 비로소 임시 디렉토리 생성 (skip 견고성).
    tmp_path = tmp_path_factory.mktemp("bbkc_regression")

    ohlcv = pl.read_parquet(ohlcv_path)
    start = ohlcv["timestamp"][0]
    end = ohlcv["timestamp"][-1]

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ohlcv.write_parquet(data_dir / f"{symbol}_1h.parquet")

    config = BacktestConfig(
        run_id="bbkc_regression",
        data_source=DataSourceConfig(base_dir=data_dir),
        instruments=[_make_instrument(symbol)],
        timeframes_per_symbol={symbol: ["1h"]},
        primary_symbol=symbol,
        primary_timeframe="1h",
        start=start,
        end=end,
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    engine = BacktestEngine(config, BBKCSqueezeStrategy(), verbose=False)
    result = engine.run()

    # PR8 Phase 1 — long-only 회귀. fixture에 등장한 side 만 v8 출력에서 추출.
    # legacy SHORT, TP/SL/trailing 청산, time_stop, RSI<70 필터는 v8 Phase 1 미지원이라
    # v8 buy 시퀀스가 legacy 보다 많을 수 있다. 회귀 게이트는 **subset**:
    # 모든 fixture (timestamp, direction)이 v8 actual에 정확히 포함되면 통과.
    # 누락 시 어떤 fixture entry가 v8에 없는지 명시적으로 보고.
    expected_sides = set(fixture["direction"].unique().to_list())
    actual = _intent_signals(result.events_path, sides=expected_sides)
    actual_set = set(actual)

    expected = [
        (
            ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            direction,
        )
        for ts, direction in zip(
            fixture["timestamp"].to_list(),
            fixture["direction"].to_list(),
            strict=True,
        )
    ]
    expected_set = set(expected)

    missing = sorted(expected_set - actual_set)
    assert not missing, (
        f"BBKC regression: legacy fixture entries missing from v8 actual "
        f"(sides={sorted(expected_sides)}):\n"
        f"  missing: {missing}\n"
        f"  expected ({len(expected)}): {expected}\n"
        f"  actual   ({len(actual)}): {actual[:10]}{'...' if len(actual) > 10 else ''}"
    )
