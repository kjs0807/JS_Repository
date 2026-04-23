"""Gap filler smoke tests (pure unit, no network).

We stub Bybit's ``HTTP`` with a fake that returns a deterministic
payload, so the test exercises the paging logic + DB write path
without touching the network or the real pybit library.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.data_manager.db import DBManager
from src.data_manager.gap_filler import (
    INTERVAL_MS,
    fetch_kline_range,
    fill_gap,
)


class _FakeHTTP:
    """Minimal stand-in for ``pybit.unified_trading.HTTP``.

    Returns an entire pre-built kline list on the first call; subsequent
    calls with a tighter ``end`` return an empty list so the pager exits.
    """

    def __init__(self, payload: List[List[str]]) -> None:
        self._payload = payload
        self._calls = 0

    def get_kline(self, **kwargs: Any) -> Dict[str, Any]:
        self._calls += 1
        if self._calls == 1:
            return {"retCode": 0, "result": {"list": list(self._payload)}}
        return {"retCode": 0, "result": {"list": []}}


def _mk_kline_row(open_time_ms: int, close: float = 100.0) -> List[str]:
    """Bybit kline row format: [start, open, high, low, close, volume, turnover]"""
    return [
        str(open_time_ms),
        "100.0",
        "101.0",
        "99.0",
        str(close),
        "1234.56",
        "123456.78",
    ]


class TestFetchKlineRange:
    def test_returns_sorted_deduped_rows(self) -> None:
        # Bybit returns newest-first; our fetcher should sort ascending.
        bars_desc = [
            _mk_kline_row(3 * INTERVAL_MS["60"], close=103.0),
            _mk_kline_row(2 * INTERVAL_MS["60"], close=102.0),
            _mk_kline_row(1 * INTERVAL_MS["60"], close=101.0),
        ]
        http = _FakeHTTP(bars_desc)
        rows = fetch_kline_range(
            http, "BTCUSDT", "60",
            start_ms=0, end_ms=10 * INTERVAL_MS["60"],
        )
        assert [r["open_time"] for r in rows] == [
            1 * INTERVAL_MS["60"],
            2 * INTERVAL_MS["60"],
            3 * INTERVAL_MS["60"],
        ]
        assert rows[-1]["close"] == pytest.approx(103.0)

    def test_empty_payload_returns_empty(self) -> None:
        http = _FakeHTTP([])
        rows = fetch_kline_range(
            http, "BTCUSDT", "60",
            start_ms=0, end_ms=10 * INTERVAL_MS["60"],
        )
        assert rows == []

    def test_unsupported_interval_raises(self) -> None:
        http = _FakeHTTP([])
        with pytest.raises(ValueError):
            fetch_kline_range(
                http, "BTCUSDT", "nonsense",
                start_ms=0, end_ms=1,
            )


class TestFillGap:
    def test_fill_gap_writes_to_db(self, tmp_path: Path) -> None:
        # Spin up a fresh SQLite DB with the project schema
        db = DBManager(
            str(tmp_path / "test.db"),
            str(Path(__file__).resolve().parents[2] / "db" / "schema.sql"),
        )
        db.initialize()

        # Pre-check: empty
        before = db.get_bar_count("BTCUSDT", "1h")
        assert before == 0

        bars = [
            _mk_kline_row(1 * INTERVAL_MS["60"], close=101.0),
            _mk_kline_row(2 * INTERVAL_MS["60"], close=102.0),
        ]
        http = _FakeHTTP(bars)
        inserted = fill_gap(
            db, "BTCUSDT", "60",
            since_ms=0, until_ms=10 * INTERVAL_MS["60"],
            http=http,
        )
        assert inserted == 2
        after = db.get_bar_count("BTCUSDT", "1h")
        assert after == 2

    def test_fill_gap_is_idempotent(self, tmp_path: Path) -> None:
        db = DBManager(
            str(tmp_path / "test.db"),
            str(Path(__file__).resolve().parents[2] / "db" / "schema.sql"),
        )
        db.initialize()
        bars = [_mk_kline_row(1 * INTERVAL_MS["60"], close=101.0)]
        # First fill
        http1 = _FakeHTTP(bars)
        fill_gap(db, "BTCUSDT", "60", 0, 10 * INTERVAL_MS["60"], http=http1)
        # Second fill — same payload, should not duplicate
        http2 = _FakeHTTP(bars)
        fill_gap(db, "BTCUSDT", "60", 0, 10 * INTERVAL_MS["60"], http=http2)
        assert db.get_bar_count("BTCUSDT", "1h") == 1
