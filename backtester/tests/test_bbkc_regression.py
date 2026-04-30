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


def _intent_signals(events_path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        evt = json.loads(line)
        if evt["type"] == "intent_created":
            out.append((evt["ts"], evt["payload"]["intent"]["side"]))
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
        instruments=[_btc()],
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

    actual = _intent_signals(result.events_path)
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

    assert actual == expected, (
        f"BBKC regression mismatch:\n"
        f"  expected ({len(expected)}): {expected[:5]}...\n"
        f"  actual   ({len(actual)}): {actual[:5]}..."
    )
