"""Stage C-1: unified orders.jsonl audit log."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.order_logger import (
    ALL_RESULTS,
    OrderLogger,
    RESULT_EXCHANGE_REJECT,
    RESULT_KILL_SWITCH_BLOCK,
    RESULT_RISK_REJECT,
    RESULT_SUCCESS,
)


def _read_rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


class TestSchemaCoverage:
    def test_success_row_has_all_required_fields(self, tmp_path):
        ol = OrderLogger(tmp_path / "orders.jsonl")
        ol.log(
            action="buy", symbol="BTCUSDT", side="Buy", qty=0.01,
            source="STRATEGY", reason="entry signal",
            result=RESULT_SUCCESS,
            order_id="OID-1",
            stop_loss=70_000.0, take_profit=80_000.0,
            equity_snapshot=48_000.0,
        )
        rows = _read_rows(tmp_path / "orders.jsonl")
        assert len(rows) == 1
        row = rows[0]
        # Every documented field must be present.
        for k in (
            "ts", "ts_ms", "event_type", "action", "symbol", "side", "qty",
            "source", "reason", "result", "failure_category",
            "failure_message", "order_id", "stop_loss", "take_profit",
            "breaker_eligible", "circuit_breaker_tripped",
            "kill_switch_engaged", "equity_snapshot",
        ):
            assert k in row, f"missing field {k}"
        assert row["result"] == RESULT_SUCCESS
        assert row["order_id"] == "OID-1"
        assert row["equity_snapshot"] == 48_000.0

    def test_risk_reject_marked_breaker_ineligible(self, tmp_path):
        """B2 finalised in C-1: risk_reject must serialise with
        breaker_eligible=False so downstream replay agrees with the
        in-memory breaker state."""
        ol = OrderLogger(tmp_path / "orders.jsonl")
        ol.log(
            action="buy", symbol="BTCUSDT", side="Buy", qty=0.01,
            source="STRATEGY", reason="entry",
            result=RESULT_RISK_REJECT,
            failure_category="risk_reject",
            failure_message="daily loss limit",
            breaker_eligible=False,
        )
        row = _read_rows(tmp_path / "orders.jsonl")[0]
        assert row["breaker_eligible"] is False
        assert row["result"] == RESULT_RISK_REJECT

    def test_kill_switch_block_recorded(self, tmp_path):
        ol = OrderLogger(tmp_path / "orders.jsonl")
        ol.log(
            action="buy", symbol="BTCUSDT", side="",
            qty=0.0, source="STRATEGY", reason="ks engaged",
            result=RESULT_KILL_SWITCH_BLOCK,
            failure_message="file disable_new_entry.flag",
            breaker_eligible=False,
            kill_switch_engaged=True,
        )
        row = _read_rows(tmp_path / "orders.jsonl")[0]
        assert row["result"] == RESULT_KILL_SWITCH_BLOCK
        assert row["kill_switch_engaged"] is True
        assert row["breaker_eligible"] is False


class TestAppending:
    def test_multiple_writes_append(self, tmp_path):
        ol = OrderLogger(tmp_path / "orders.jsonl")
        for i in range(3):
            ol.log(
                action="buy", symbol=f"S{i}", side="Buy", qty=0.01,
                source="STRATEGY", reason=str(i),
                result=RESULT_SUCCESS, order_id=f"OID-{i}",
            )
        rows = _read_rows(tmp_path / "orders.jsonl")
        assert [r["order_id"] for r in rows] == ["OID-0", "OID-1", "OID-2"]


class TestRobustness:
    def test_creates_parent_dir(self, tmp_path):
        ol = OrderLogger(tmp_path / "nested" / "deeper" / "orders.jsonl")
        ol.log(action="buy", symbol="X", result=RESULT_SUCCESS)
        assert (tmp_path / "nested" / "deeper" / "orders.jsonl").exists()

    def test_write_failure_is_logged_not_raised(self, tmp_path, caplog):
        """A disk-full / permission error during append must never break
        the broker's order path. The logger swallows it and emits an
        ERROR record."""
        ol = OrderLogger(tmp_path / "orders.jsonl")
        # Point _path to an unwritable target (a directory).
        ol._path = tmp_path
        import logging
        with caplog.at_level(logging.ERROR, logger="src.runtime.order_logger"):
            ol.log(action="buy", symbol="X", result=RESULT_SUCCESS)
        assert any(
            "failed to append" in r.message for r in caplog.records
        )


class TestTaxonomy:
    def test_all_results_unique_and_non_empty(self):
        assert len(ALL_RESULTS) == len(set(ALL_RESULTS))
        assert all(isinstance(r, str) and r for r in ALL_RESULTS)

    def test_unknown_result_still_writes_with_warning(self, tmp_path, caplog):
        ol = OrderLogger(tmp_path / "orders.jsonl")
        import logging
        with caplog.at_level(logging.WARNING, logger="src.runtime.order_logger"):
            ol.log(action="buy", symbol="X", result="UNKNOWN_RESULT_KIND")
        assert any("unknown result" in r.message for r in caplog.records)
        rows = _read_rows(tmp_path / "orders.jsonl")
        assert rows[0]["result"] == "UNKNOWN_RESULT_KIND"
