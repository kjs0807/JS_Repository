"""PR 16 prep 2차 회귀 — config 정합성 / canonical EventLog / IndicatorEngine 방어.

본 파일이 검증:
- ``DataSourceConfig.bybit_category`` 검증 + YAML round-trip + Engine 전달
- ``execution_model='atr_slippage'`` 가 ConfigError 로 fail-fast
- ``IndicatorEngine.snapshot()`` 이 read-only Mapping (mutate 시 TypeError)
- EventLog 가 canonical JSON 으로 출력 (sort_keys + 고정 separators) — 같은 input
  으로 두 번 쓰면 byte-identical
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.errors import ConfigError
from backtester.data.bybit_source import BybitDataSource
from backtester.events.log import EventLog
from backtester.events.types import Event, EventType
from backtester.indicators.engine import IndicatorEngine
from backtester.indicators.stateless.bb import BollingerBands
from backtester.instruments.base import FeeModel, Instrument

UTC = timezone.utc


# ---------- DataSourceConfig.bybit_category ---------------------------------


def test_data_source_config_default_bybit_category_is_linear(tmp_path: Path) -> None:
    ds = DataSourceConfig(base_dir=tmp_path)
    assert ds.bybit_category == "linear"


def test_data_source_config_accepts_spot_and_inverse(tmp_path: Path) -> None:
    ds_spot = DataSourceConfig(base_dir=tmp_path, type="bybit", bybit_category="spot")
    ds_inv = DataSourceConfig(
        base_dir=tmp_path, type="bybit", bybit_category="inverse"
    )
    assert ds_spot.bybit_category == "spot"
    assert ds_inv.bybit_category == "inverse"


def test_data_source_config_rejects_unknown_bybit_category(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="bybit_category"):
        DataSourceConfig(
            base_dir=tmp_path,
            type="bybit",
            bybit_category="bogus",  # type: ignore[arg-type]
        )


def test_data_source_config_yaml_round_trip_includes_bybit_category(
    tmp_path: Path,
) -> None:
    """type='bybit' 일 때만 ``bybit_category`` 가 YAML 에 직렬화."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="bybit_yaml",
        data_source=DataSourceConfig(
            base_dir=tmp_path / "cache",
            type="bybit",
            bybit_category="spot",
        ),
        instruments=[
            Instrument(
                symbol="BTCUSDT",
                asset_class="crypto_perp",
                tick_size=Decimal("0.1"),
                tick_value=Decimal("0.1"),
                contract_multiplier=Decimal("1"),
                quote_currency="USDT",
                base_currency="BTC",
                size_unit="base_asset",
                fee_model=FeeModel(type="flat", taker=Decimal("0")),
            )
        ],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    yaml_path = tmp_path / "config.yaml"
    cfg.to_yaml(yaml_path)
    text = yaml_path.read_text(encoding="utf-8")
    assert "bybit_category" in text
    assert "spot" in text

    restored = BacktestConfig.from_yaml(yaml_path)
    assert restored.data_source.type == "bybit"
    assert restored.data_source.bybit_category == "spot"


def test_data_source_config_yaml_omits_category_for_non_bybit(tmp_path: Path) -> None:
    """type != 'bybit' 일 때 ``bybit_category`` 키가 YAML 에 등장 안 함."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cfg = BacktestConfig(
        run_id="parquet_yaml",
        data_source=DataSourceConfig(base_dir=tmp_path / "data", type="parquet"),
        instruments=[
            Instrument(
                symbol="BTCUSDT",
                asset_class="crypto_perp",
                tick_size=Decimal("0.1"),
                tick_value=Decimal("0.1"),
                contract_multiplier=Decimal("1"),
                quote_currency="USDT",
                base_currency="BTC",
                size_unit="base_asset",
                fee_model=FeeModel(type="flat", taker=Decimal("0")),
            )
        ],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=24),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    yaml_path = tmp_path / "config.yaml"
    cfg.to_yaml(yaml_path)
    text = yaml_path.read_text(encoding="utf-8")
    assert "bybit_category" not in text


def test_engine_propagates_bybit_category_to_data_source(tmp_path: Path) -> None:
    """Engine ``_build_data_source`` 가 ``DataSourceConfig.bybit_category`` 를 전달."""
    from backtester.core.engine import BacktestEngine
    from backtester.strategies.base import BaseStrategy

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Bybit cache 에 미리 parquet 채워두면 fetcher 호출 안 함
    n = 24
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n)],
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1.0] * n,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))
    df.write_parquet(cache_dir / "BTCUSDT_1h.parquet")

    cfg = BacktestConfig(
        run_id="bybit_engine",
        data_source=DataSourceConfig(
            base_dir=cache_dir, type="bybit", bybit_category="inverse"
        ),
        instruments=[
            Instrument(
                symbol="BTCUSDT",
                asset_class="crypto_perp",
                tick_size=Decimal("0.1"),
                tick_value=Decimal("0.1"),
                contract_multiplier=Decimal("1"),
                quote_currency="USDT",
                base_currency="BTC",
                size_unit="base_asset",
                fee_model=FeeModel(type="flat", taker=Decimal("0")),
            )
        ],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=23),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )

    class _NoopStrategy(BaseStrategy):
        def on_bar(self, ctx):  # type: ignore[no-untyped-def]
            return []

    engine = BacktestEngine(cfg, _NoopStrategy(), verbose=False)
    assert isinstance(engine.data_source, BybitDataSource)
    assert engine.data_source.category == "inverse"


# ---------- atr_slippage config-level fail-fast ------------------------------


def test_config_rejects_atr_slippage_at_post_init(tmp_path: Path) -> None:
    """PR 16 prep 2차: atr_slippage 는 ``BacktestConfig.__post_init__`` 에서 ConfigError."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ConfigError, match="execution_model"):
        BacktestConfig(
            run_id="bad_em",
            data_source=DataSourceConfig(base_dir=tmp_path / "data"),
            instruments=[
                Instrument(
                    symbol="BTCUSDT",
                    asset_class="crypto_perp",
                    tick_size=Decimal("0.1"),
                    tick_value=Decimal("0.1"),
                    contract_multiplier=Decimal("1"),
                    quote_currency="USDT",
                    base_currency="BTC",
                    size_unit="base_asset",
                    fee_model=FeeModel(type="flat", taker=Decimal("0")),
                )
            ],
            timeframes_per_symbol={"BTCUSDT": ["1h"]},
            primary_symbol="BTCUSDT",
            primary_timeframe="1h",
            start=base,
            end=base + timedelta(hours=24),
            initial_equity=Decimal("100000"),
            output_dir=tmp_path / "runs",
            execution_model="atr_slippage",  # type: ignore[arg-type]
        )


# ---------- IndicatorEngine.snapshot read-only -------------------------------


def test_indicator_engine_snapshot_is_read_only() -> None:
    """``IndicatorEngine.snapshot()`` 의 반환은 ``MappingProxyType`` — write 시 TypeError."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    n = 30
    df = pl.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n)],
            "open": [100.0 + i * 0.1 for i in range(n)],
            "high": [100.5 + i * 0.1 for i in range(n)],
            "low": [99.5 + i * 0.1 for i in range(n)],
            "close": [100.2 + i * 0.1 for i in range(n)],
            "volume": [1.0] * n,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")))

    eng = IndicatorEngine()
    eng.precompute(
        {"BTCUSDT": {"1h": df}},
        [BollingerBands(period=5, num_std=2.0)],
    )
    snap = eng.snapshot()
    # Read 는 정상
    assert ("BTCUSDT", "1h") in snap
    out = snap[("BTCUSDT", "1h")]
    assert out.height == n
    # Write 시도는 TypeError (MappingProxyType)
    with pytest.raises(TypeError):
        snap[("ETHUSDT", "1h")] = df  # type: ignore[index]


# ---------- EventLog canonical JSON ------------------------------------------


def _write_two_events(run_dir: Path) -> Path:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with EventLog(run_dir) as log:
        log.append(
            Event(
                ts=base,
                type=EventType.SNAPSHOT,
                payload={
                    "z_field": "z",
                    "alpha": 1,
                    "snapshot_reason": "periodic",
                    "equity": Decimal("100000.5"),
                },
            )
        )
        log.append(
            Event(
                ts=base + timedelta(hours=1),
                type=EventType.SNAPSHOT,
                payload={
                    "snapshot_reason": "periodic",
                    "equity": Decimal("100100.0"),
                },
            )
        )
    return run_dir / "events.jsonl"


def test_event_log_canonical_json_sort_keys(tmp_path: Path) -> None:
    """라인의 keys 가 정렬되어 있어야 — payload, schema_version, ts, type 알파벳 순."""
    p = _write_two_events(tmp_path / "run1")
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # 첫 라인 starts with {"payload":  → keys 가 알파벳 정렬됨
    assert lines[0].startswith('{"payload":')
    # 공백 없는 separators 확인
    assert ", " not in lines[0]
    assert ": " not in lines[0]
    # round-trip 가능
    parsed = json.loads(lines[0])
    assert parsed["schema_version"] >= 1
    assert parsed["type"] == EventType.SNAPSHOT.value


def test_event_log_byte_identical_for_same_input(tmp_path: Path) -> None:
    """같은 events 두 번 쓰면 byte-identical — §13.3 deterministic replay 게이트."""
    p1 = _write_two_events(tmp_path / "run1")
    p2 = _write_two_events(tmp_path / "run2")
    assert p1.read_bytes() == p2.read_bytes()


def test_event_log_canonical_json_preserves_payload_round_trip(tmp_path: Path) -> None:
    """canonical 출력이 dict 키 순서를 정렬하지만, json.loads 후 의미는 그대로."""
    p = _write_two_events(tmp_path / "run")
    lines = p.read_text(encoding="utf-8").splitlines()
    parsed_first = json.loads(lines[0])
    payload = parsed_first["payload"]
    # 모든 키 보존
    assert set(payload.keys()) == {
        "z_field", "alpha", "snapshot_reason", "equity"
    }
    # Decimal 직렬화 (str)
    assert payload["equity"] == "100000.5"
