"""Stage C-2b: fills.jsonl logger."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.fill_logger import (
    ALL_STATUSES,
    FillLogger,
    STATUS_FILLED,
    STATUS_MISSING_INTENT,
    STATUS_PARTIAL,
    STATUS_TIMEOUT,
)


def _rows(path: Path):
    return [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


class TestSchemaCoverage:
    def test_filled_buy_with_adverse_slippage(self, tmp_path):
        ol = FillLogger(tmp_path / "fills.jsonl")
        ol.log(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, fill_qty=0.01,
            intent_price=70_000.0, fill_price=70_007.0,
            submit_ts_ms=1_000, fill_ts_ms=1_300,
            status=STATUS_FILLED,
        )
        row = _rows(tmp_path / "fills.jsonl")[0]
        # Buy paid 7.0 more than intent → adverse → positive sign.
        assert row["slippage_abs"] == pytest.approx(7.0)
        assert row["slippage_bps"] == pytest.approx(7.0 / 70_000.0 * 10_000)
        assert row["fill_lag_ms"] == 300
        assert row["status"] == STATUS_FILLED
        assert row["fill_lag_ms"] >= 0

    def test_filled_sell_received_more_than_intent_is_favourable(self, tmp_path):
        """Sell receiving more than intent is FAVOURABLE — slippage
        sign convention must be negative for sells that improve."""
        ol = FillLogger(tmp_path / "fills.jsonl")
        ol.log(
            order_id="OID-2", symbol="BTCUSDT", side="Sell",
            intent_qty=0.01, fill_qty=0.01,
            intent_price=70_000.0, fill_price=70_007.0,
            submit_ts_ms=1_000, fill_ts_ms=1_100,
            status=STATUS_FILLED,
        )
        row = _rows(tmp_path / "fills.jsonl")[0]
        # Received 7.0 more on a sell → favourable → negative slippage.
        assert row["slippage_abs"] == pytest.approx(-7.0)
        assert row["slippage_bps"] < 0

    def test_partial_fill_still_computes_slippage(self, tmp_path):
        ol = FillLogger(tmp_path / "fills.jsonl")
        ol.log(
            order_id="OID-3", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, fill_qty=0.004,
            intent_price=70_000.0, fill_price=70_010.0,
            submit_ts_ms=1_000, fill_ts_ms=1_200,
            status=STATUS_PARTIAL,
        )
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["status"] == STATUS_PARTIAL
        assert row["slippage_abs"] == pytest.approx(10.0)

    def test_timeout_row_has_no_slippage(self, tmp_path):
        ol = FillLogger(tmp_path / "fills.jsonl")
        ol.log(
            order_id="OID-4", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, fill_qty=0.0,
            intent_price=70_000.0, fill_price=0.0,
            submit_ts_ms=1_000, fill_ts_ms=601_000,
            status=STATUS_TIMEOUT,
        )
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["status"] == STATUS_TIMEOUT
        assert row["slippage_abs"] is None
        assert row["slippage_bps"] is None
        assert row["fill_lag_ms"] == 600_000

    def test_missing_intent_row_keeps_intent_price_none(self, tmp_path):
        ol = FillLogger(tmp_path / "fills.jsonl")
        ol.log(
            order_id="OID-5", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, fill_qty=0.0,
            intent_price=None, fill_price=0.0,
            submit_ts_ms=1_000, fill_ts_ms=1_000,
            status=STATUS_MISSING_INTENT,
        )
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["status"] == STATUS_MISSING_INTENT
        assert row["intent_price"] is None
        assert row["slippage_abs"] is None
        assert row["slippage_bps"] is None


class TestRobustness:
    def test_creates_parent_dir(self, tmp_path):
        ol = FillLogger(tmp_path / "nested" / "deeper" / "fills.jsonl")
        ol.log(
            order_id="O", symbol="X", side="Buy",
            intent_qty=0.0, fill_qty=0.0,
            intent_price=None, fill_price=0.0,
            submit_ts_ms=0, fill_ts_ms=0,
            status=STATUS_MISSING_INTENT,
        )
        assert (tmp_path / "nested" / "deeper" / "fills.jsonl").exists()

    def test_unknown_status_warns_but_writes(self, tmp_path, caplog):
        ol = FillLogger(tmp_path / "fills.jsonl")
        import logging
        with caplog.at_level(logging.WARNING, logger="src.runtime.fill_logger"):
            ol.log(
                order_id="O", symbol="X", side="Buy",
                intent_qty=0.01, fill_qty=0.01,
                intent_price=100.0, fill_price=101.0,
                submit_ts_ms=0, fill_ts_ms=100,
                status="WEIRD",
            )
        assert any("unknown status" in r.message for r in caplog.records)
        assert _rows(tmp_path / "fills.jsonl")[0]["status"] == "WEIRD"

    def test_write_failure_swallowed(self, tmp_path, caplog):
        ol = FillLogger(tmp_path / "fills.jsonl")
        # Point _path to a directory so the append fails.
        ol._path = tmp_path
        import logging
        with caplog.at_level(logging.ERROR, logger="src.runtime.fill_logger"):
            ol.log(
                order_id="O", symbol="X", side="Buy",
                intent_qty=0.0, fill_qty=0.0,
                intent_price=None, fill_price=0.0,
                submit_ts_ms=0, fill_ts_ms=0,
                status=STATUS_MISSING_INTENT,
            )
        assert any("failed to append" in r.message for r in caplog.records)


class TestTaxonomy:
    def test_all_statuses_unique(self):
        assert len(ALL_STATUSES) == len(set(ALL_STATUSES))
        assert all(isinstance(s, str) and s for s in ALL_STATUSES)
