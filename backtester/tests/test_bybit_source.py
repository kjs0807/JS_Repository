"""PR 14 BybitDataSource 테스트 (Phase 2, spec §3.1, §16).

네트워크 호출 없이 ``KlineFetcher`` mock 주입으로 cache hit / partial miss / full miss
경로를 모두 커버. Engine 통합 smoke 는 cache 를 미리 채워 fetcher 를 호출 안 하게 함.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from backtester.core.config import BacktestConfig, DataSourceConfig
from backtester.core.engine import BacktestEngine
from backtester.core.errors import DataError
from backtester.data.bybit_source import (
    BybitDataSource,
    BybitKlineRow,
    KlineFetcher,
)
from backtester.instruments.base import FeeModel, Instrument
from backtester.strategies.bbkc_squeeze import BBKCSqueezeStrategy

UTC = timezone.utc


# ---------- 테스트용 helper -------------------------------------------------


def _generate_rows(base: datetime, n: int) -> list[BybitKlineRow]:
    """base 부터 1h 간격 n 봉 (close = 100 + i)."""
    return [
        BybitKlineRow(
            open_time_ms=int((base + timedelta(hours=i)).timestamp() * 1000),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1.0,
        )
        for i in range(n)
    ]


def _make_recording_fetcher(
    rows: list[BybitKlineRow],
    *,
    bybit_descending: bool = True,
) -> tuple[KlineFetcher, list[tuple[str, str, datetime, datetime, str]]]:
    """``rows`` 중 [start, end] 에 해당하는 봉을 Bybit 응답 순서로 반환하는 mock fetcher.

    Bybit 실 응답이 최신 → 과거 순서 (descending) 이라는 사실을 시뮬레이트하기 위해
    기본은 descending. ``BybitDataSource`` 가 이를 ascending 으로 정렬하는지도 검증.
    호출 인자는 ``calls`` 리스트에 적재해 caller 가 검증.
    """
    calls: list[tuple[str, str, datetime, datetime, str]] = []

    def _fetch(
        symbol: str,
        interval_code: str,
        start: datetime,
        end: datetime,
        category: str,
    ) -> list[BybitKlineRow]:
        calls.append((symbol, interval_code, start, end, category))
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        # Bybit v5 ``GET /v5/market/kline`` 의 start/end 는 둘 다 inclusive
        # (open_time 기준으로 bar 가 [start, end] 범위에 들면 응답).
        in_range = [r for r in rows if start_ms <= r.open_time_ms <= end_ms]
        if bybit_descending:
            in_range.sort(key=lambda r: r.open_time_ms, reverse=True)
        return in_range

    return _fetch, calls


# ---------- 단위: cache miss / hit / partial -------------------------------


def test_full_cache_miss_calls_fetcher_and_persists(tmp_path: Path) -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows = _generate_rows(base, n=10)
    fetcher, calls = _make_recording_fetcher(rows)

    src = BybitDataSource(tmp_path / "cache", fetcher=fetcher)
    df, _gap = src.fetch(
        "BTCUSDT", "1h", start=base, end=base + timedelta(hours=10)
    )
    assert df.height == 10
    assert df["timestamp"][0] == base
    assert len(calls) == 1
    cache_path = tmp_path / "cache" / "BTCUSDT_1h.parquet"
    assert cache_path.exists()


def test_cache_hit_skips_fetcher(tmp_path: Path) -> None:
    """cache 가 요청 범위를 완전히 포함하면 fetcher 는 호출되지 않는다."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows = _generate_rows(base, n=20)

    # 1차 fetch: cache 채움
    fetcher1, _ = _make_recording_fetcher(rows)
    src = BybitDataSource(tmp_path / "cache", fetcher=fetcher1)
    src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=20))

    # 2차 fetch: 같은 범위 내부 → fetcher 호출 0 회 검증
    fetcher2, calls2 = _make_recording_fetcher(rows)
    src._fetcher = fetcher2
    df, _gap = src.fetch(
        "BTCUSDT",
        "1h",
        start=base + timedelta(hours=2),
        end=base + timedelta(hours=8),
    )
    assert df.height == 7  # inclusive 양 끝, h=2..8 → 7봉
    assert len(calls2) == 0


def test_partial_tail_miss_fetches_only_new_range(tmp_path: Path) -> None:
    """cache 가 [base, base+10h] 인데 [base, base+15h] 요청 → tail (cache_max, end] 만 호출."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows_full = _generate_rows(base, n=16)

    # cache 에 10봉만
    fetcher_initial, _ = _make_recording_fetcher(rows_full[:10])
    src = BybitDataSource(tmp_path / "cache", fetcher=fetcher_initial)
    src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=10))

    # 추가 fetch: end 가 cache_max 너머
    fetcher_tail, calls_tail = _make_recording_fetcher(rows_full)
    src._fetcher = fetcher_tail
    df, _gap = src.fetch(
        "BTCUSDT", "1h", start=base, end=base + timedelta(hours=15)
    )
    assert df.height == 16  # base ~ base+15h inclusive
    assert len(calls_tail) == 1
    # tail 호출의 start 가 cache_max (=base+9h) 이상
    _, _, head_start, _, _ = calls_tail[0]
    assert head_start >= base + timedelta(hours=9)


def test_partial_head_miss_fetches_older_range(tmp_path: Path) -> None:
    """cache 가 [base+5h, base+15h] 인데 [base, base+15h] 요청 → head [start, cache_min) 호출."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows_full = _generate_rows(base, n=16)

    # cache 를 5h부터 15h까지 채움 (newer bars)
    fetcher_initial, _ = _make_recording_fetcher(rows_full[5:])
    src = BybitDataSource(tmp_path / "cache", fetcher=fetcher_initial)
    src.fetch(
        "BTCUSDT",
        "1h",
        start=base + timedelta(hours=5),
        end=base + timedelta(hours=15),
    )

    # 더 이른 시작 시점 요청
    fetcher_head, calls_head = _make_recording_fetcher(rows_full)
    src._fetcher = fetcher_head
    df, _gap = src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=15))
    assert df.height == 16
    assert len(calls_head) == 1
    _, _, head_start, head_end, _ = calls_head[0]
    assert head_start == base
    # cache_min 까지 (Bybit fetcher 가 [start, end) 처리하므로 inclusive 보정은 머지 단계)
    assert head_end == base + timedelta(hours=5)


def test_descending_bybit_response_is_sorted_ascending(tmp_path: Path) -> None:
    """Bybit 응답은 newest → oldest. fetch() 출력은 ascending 정렬."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows = _generate_rows(base, n=10)
    fetcher, _ = _make_recording_fetcher(rows, bybit_descending=True)
    src = BybitDataSource(tmp_path / "cache", fetcher=fetcher)
    df, _ = src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=10))
    ts = df["timestamp"]
    assert ts.is_sorted()


# ---------- 검증 (DataError) ------------------------------------------------


def test_naive_start_rejected(tmp_path: Path) -> None:
    src = BybitDataSource(tmp_path / "cache", fetcher=lambda *a, **kw: [])
    with pytest.raises(DataError, match="timezone-aware"):
        src.fetch(
            "BTCUSDT",
            "1h",
            start=datetime(2026, 4, 1),  # naive
            end=datetime(2026, 4, 2, tzinfo=UTC),
        )


def test_unsupported_timeframe_rejected(tmp_path: Path) -> None:
    src = BybitDataSource(tmp_path / "cache", fetcher=lambda *a, **kw: [])
    with pytest.raises(DataError, match="does not support timeframe"):
        src.fetch(
            "BTCUSDT",
            "7m",  # 안 들어 있는 TF
            start=datetime(2026, 4, 1, tzinfo=UTC),
            end=datetime(2026, 4, 2, tzinfo=UTC),
        )


def test_start_must_be_before_end(tmp_path: Path) -> None:
    src = BybitDataSource(tmp_path / "cache", fetcher=lambda *a, **kw: [])
    t = datetime(2026, 4, 1, tzinfo=UTC)
    with pytest.raises(DataError, match="start must be < end"):
        src.fetch("BTCUSDT", "1h", start=t, end=t)


def test_cache_dir_must_be_directory(tmp_path: Path) -> None:
    file_not_dir = tmp_path / "f.txt"
    file_not_dir.write_text("x")
    with pytest.raises(DataError, match="not a directory"):
        BybitDataSource(file_not_dir)


# ---------- DataSourceConfig + Engine 통합 ---------------------------------


def test_data_source_config_accepts_bybit(tmp_path: Path) -> None:
    ds = DataSourceConfig(base_dir=tmp_path, type="bybit")
    assert ds.type == "bybit"


def test_engine_with_bybit_data_source_using_prepopulated_cache(
    tmp_path: Path,
) -> None:
    """cache 를 미리 채운 ``BybitDataSource`` 로 BacktestEngine 정상 실행 — 네트워크 미호출.

    Engine 의 ``_build_data_source`` 가 ``ds.type='bybit'`` 분기를 통해 ``BybitDataSource``
    를 생성하고 ``fetch`` 가 cache hit 만으로 작동해야 한다."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    n_bars = 60
    rows = _generate_rows(base, n=n_bars)
    df = pl.DataFrame(
        {
            "timestamp": [
                datetime.fromtimestamp(r.open_time_ms / 1000, tz=UTC) for r in rows
            ],
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
    )
    df.write_parquet(cache_dir / "BTCUSDT_1h.parquet")

    instrument = Instrument(
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
    cfg = BacktestConfig(
        run_id="bybit_engine_smoke",
        data_source=DataSourceConfig(base_dir=cache_dir, type="bybit"),
        instruments=[instrument],
        timeframes_per_symbol={"BTCUSDT": ["1h"]},
        primary_symbol="BTCUSDT",
        primary_timeframe="1h",
        start=base,
        end=base + timedelta(hours=n_bars - 1),
        initial_equity=Decimal("100000"),
        output_dir=tmp_path / "runs",
    )
    engine = BacktestEngine(cfg, BBKCSqueezeStrategy(), verbose=False)
    result = engine.run()
    assert (result.run_dir / "events.jsonl").exists()


# ---------- 빈 응답 / dedup / sort 무결성 -----------------------------------


def test_fetcher_returns_empty_yields_empty_df(tmp_path: Path) -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)

    def _empty(*_a: object, **_kw: object) -> list[BybitKlineRow]:
        return []

    src = BybitDataSource(tmp_path / "cache", fetcher=_empty)
    df, gap = src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=2))
    assert df.height == 0
    assert gap.symbol == "BTCUSDT"


def test_overlapping_fetcher_response_dedups(tmp_path: Path) -> None:
    """fetcher 가 cache 와 겹치는 봉을 반환해도 dedup 후 strictly increasing."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows = _generate_rows(base, n=10)

    # cache 에 0~5h
    fetcher_initial, _ = _make_recording_fetcher(rows[:6])
    src = BybitDataSource(tmp_path / "cache", fetcher=fetcher_initial)
    src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=5))

    # 추가 fetch: tail fetcher 가 cache_max 인 5h 봉도 다시 보냄 (overlapping)
    fetcher_tail, _ = _make_recording_fetcher(rows[5:])
    src._fetcher = fetcher_tail
    df, _ = src.fetch("BTCUSDT", "1h", start=base, end=base + timedelta(hours=9))
    assert df.height == 10
    assert df["timestamp"].is_sorted()
    assert df["timestamp"].n_unique() == df.height
