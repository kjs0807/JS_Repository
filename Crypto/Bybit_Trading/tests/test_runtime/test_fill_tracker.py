"""Stage C-2b: pending-fill reconciliation."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.runtime.fill_logger import (
    FillLogger,
    STATUS_FILLED,
    STATUS_PARTIAL,
    STATUS_TIMEOUT,
)
from src.runtime.fill_tracker import FillTracker, PendingFill


class _Clock:
    def __init__(self, start_ms: int = 1_000_000) -> None:
        self.t = start_ms

    def __call__(self) -> int:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += ms


def _rows(path: Path):
    return [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


# ---------------------------------------------------------------------------
# constructor
# ---------------------------------------------------------------------------
class TestConstructor:
    def test_zero_timeout_raises(self):
        with pytest.raises(ValueError):
            FillTracker(timeout_ms=0)

    def test_negative_timeout_raises(self):
        with pytest.raises(ValueError):
            FillTracker(timeout_ms=-1)


# ---------------------------------------------------------------------------
# registration semantics
# ---------------------------------------------------------------------------
class TestRegister:
    def test_register_records_pending(self):
        tracker = FillTracker()
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=12_345,
        )
        assert tracker.pending_count() == 1
        snap = tracker.pending_snapshot()
        assert snap[0].order_id == "OID-1"
        assert snap[0].submit_ts_ms == 12_345

    def test_register_empty_order_id_is_noop(self):
        tracker = FillTracker()
        tracker.register(
            order_id="", symbol="X", side="Buy",
            intent_qty=0.01, intent_price=100.0,
        )
        assert tracker.pending_count() == 0


# ---------------------------------------------------------------------------
# reconcile happy path
# ---------------------------------------------------------------------------
class TestReconcileHappyPath:
    def test_full_fill_emits_filled_row_and_evicts(self, tmp_path):
        clock = _Clock(start_ms=1_000_000)
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        clock.advance(500)  # 500ms later
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010.5", "cumExecQty": "0.01",
            "orderStatus": "Filled",
        }
        emitted = tracker.reconcile_all(rest, ol)
        assert emitted == 1
        assert tracker.pending_count() == 0  # evicted
        rows = _rows(tmp_path / "fills.jsonl")
        assert len(rows) == 1
        assert rows[0]["status"] == STATUS_FILLED
        assert rows[0]["fill_price"] == pytest.approx(70_010.5)
        assert rows[0]["fill_qty"] == pytest.approx(0.01)
        assert rows[0]["fill_lag_ms"] == 500
        # Buy adverse: +10.5 / 70000 * 10000 = 1.5 bps.
        assert rows[0]["slippage_bps"] == pytest.approx(1.5, rel=1e-3)

    def test_partial_fill_emits_partial_row_keeps_pending(self, tmp_path):
        clock = _Clock()
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        clock.advance(300)
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70005.0", "cumExecQty": "0.005",  # 50% filled
        }
        tracker.reconcile_all(rest, ol)
        rows = _rows(tmp_path / "fills.jsonl")
        assert rows[0]["status"] == STATUS_PARTIAL
        # NOT evicted — partial stays so a future cumExecQty bump can
        # emit a follow-up row.
        assert tracker.pending_count() == 1


# ---------------------------------------------------------------------------
# Hotfix #1: fill_ts_ms uses Bybit updatedTime when available
# ---------------------------------------------------------------------------
class TestFillLagAccuracy:
    def test_updated_time_used_for_fill_ts(self, tmp_path):
        """fill_lag_ms must reflect the *actual* execution latency
        (submit -> exchange-side fill timestamp), not the gap between
        submit and heartbeat observation."""
        clock = _Clock(start_ms=1_000_000)
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),  # 1_000_000
        )
        # Tracker observes much later (60s heartbeat window).
        clock.advance(60_000)  # now = 1_060_000
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.01",
            # Bybit V5 returns ms epoch strings. Order filled 250 ms
            # after submission, observed 60 s later.
            "updatedTime": "1000250",
            "createdTime": "1000050",
        }
        tracker.reconcile_all(rest, ol)
        row = _rows(tmp_path / "fills.jsonl")[0]
        # Real execution latency: 1_000_250 - 1_000_000 = 250 ms,
        # NOT the 60 000 ms observation gap.
        assert row["fill_ts_ms"] == 1_000_250
        assert row["fill_lag_ms"] == 250

    def test_falls_back_to_created_time_when_updated_missing(self, tmp_path):
        clock = _Clock(start_ms=1_000_000)
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        clock.advance(30_000)
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.01",
            "updatedTime": "",
            "createdTime": "1000080",
        }
        tracker.reconcile_all(rest, ol)
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["fill_ts_ms"] == 1_000_080
        assert row["fill_lag_ms"] == 80

    def test_falls_back_to_tracker_clock_when_no_timestamps(self, tmp_path):
        clock = _Clock(start_ms=1_000_000)
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        clock.advance(500)
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.01",
            # Neither updatedTime nor createdTime usable.
        }
        tracker.reconcile_all(rest, ol)
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["fill_ts_ms"] == 1_000_500
        assert row["fill_lag_ms"] == 500

    def test_clock_skew_protects_against_negative_lag(self, tmp_path):
        """If Bybit returns an updatedTime that is somehow earlier
        than our recorded submit_ts_ms (clock skew on the wire) we
        must clamp to submit_ts_ms so fill_lag_ms is never negative."""
        clock = _Clock(start_ms=1_000_000)
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=1_000_000,
        )
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.01",
            "updatedTime": "999950",  # before submit!
        }
        tracker.reconcile_all(rest, ol)
        row = _rows(tmp_path / "fills.jsonl")[0]
        # Clamped: fill_ts_ms == submit_ts_ms, lag == 0.
        assert row["fill_ts_ms"] == 1_000_000
        assert row["fill_lag_ms"] == 0


# ---------------------------------------------------------------------------
# Hotfix #2: partial rows dedup on cumExecQty
# ---------------------------------------------------------------------------
class TestPartialDedup:
    def test_same_partial_observed_twice_writes_one_row(self, tmp_path):
        clock = _Clock()
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.005",  # 50% filled
            "updatedTime": str(clock()),
        }
        # First reconcile: emits one partial row.
        emitted_1 = tracker.reconcile_all(rest, ol)
        # Second reconcile, SAME cumExecQty: must NOT emit anything.
        clock.advance(60_000)
        emitted_2 = tracker.reconcile_all(rest, ol)
        assert emitted_1 == 1
        assert emitted_2 == 0
        # Only one partial row landed on disk.
        rows = _rows(tmp_path / "fills.jsonl")
        assert len(rows) == 1
        assert rows[0]["status"] == STATUS_PARTIAL
        # Pending entry stays (partial not evicted).
        assert tracker.pending_count() == 1

    def test_growing_cum_qty_emits_new_partial_row(self, tmp_path):
        clock = _Clock()
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        rest = MagicMock()
        # Tick 1: 30% filled.
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.003",
        }
        tracker.reconcile_all(rest, ol)
        # Tick 2: 60% filled (grew).
        rest.get_order.return_value = {
            "avgPrice": "70012", "cumExecQty": "0.006",
        }
        tracker.reconcile_all(rest, ol)
        # Tick 3: still 60% (frozen).
        tracker.reconcile_all(rest, ol)
        rows = _rows(tmp_path / "fills.jsonl")
        assert len(rows) == 2
        assert [r["fill_qty"] for r in rows] == [0.003, 0.006]

    def test_full_fill_after_partial_still_emits_and_evicts(self, tmp_path):
        clock = _Clock()
        tracker = FillTracker(clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.004",  # 40%
        }
        tracker.reconcile_all(rest, ol)
        rest.get_order.return_value = {
            "avgPrice": "70015", "cumExecQty": "0.01",   # 100%
        }
        tracker.reconcile_all(rest, ol)
        rows = _rows(tmp_path / "fills.jsonl")
        assert len(rows) == 2
        assert rows[0]["status"] == STATUS_PARTIAL
        assert rows[1]["status"] == STATUS_FILLED
        # Full fill evicts.
        assert tracker.pending_count() == 0


class TestReconcileNoFillYet:
    def test_empty_response_keeps_pending_no_row(self, tmp_path):
        tracker = FillTracker()
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
        )
        rest = MagicMock()
        rest.get_order.return_value = {}
        emitted = tracker.reconcile_all(rest, ol)
        assert emitted == 0
        assert tracker.pending_count() == 1
        assert not (tmp_path / "fills.jsonl").exists()

    def test_zero_avg_price_keeps_pending(self, tmp_path):
        tracker = FillTracker()
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
        )
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "0", "cumExecQty": "0",
        }
        emitted = tracker.reconcile_all(rest, ol)
        assert emitted == 0
        assert tracker.pending_count() == 1

    def test_rest_exception_keeps_pending_does_not_propagate(self, tmp_path):
        tracker = FillTracker()
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
        )
        rest = MagicMock()
        rest.get_order.side_effect = RuntimeError("network timeout")
        # Must NOT raise.
        emitted = tracker.reconcile_all(rest, ol)
        assert emitted == 0
        assert tracker.pending_count() == 1


# ---------------------------------------------------------------------------
# timeout / aging
# ---------------------------------------------------------------------------
class TestTimeout:
    def test_aged_pending_emits_timeout_row_and_evicts(self, tmp_path):
        clock = _Clock(start_ms=1_000_000)
        tracker = FillTracker(timeout_ms=5_000, clock=clock)
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
            submit_ts_ms=clock(),
        )
        # Past the timeout.
        clock.advance(6_000)
        rest = MagicMock()
        rest.get_order.return_value = {}
        emitted = tracker.reconcile_all(rest, ol)
        assert emitted == 1
        assert tracker.pending_count() == 0
        row = _rows(tmp_path / "fills.jsonl")[0]
        assert row["status"] == STATUS_TIMEOUT
        assert row["fill_qty"] == 0.0
        assert row["slippage_abs"] is None
        # REST should not even have been queried — aging fires first.
        rest.get_order.assert_not_called()


# ---------------------------------------------------------------------------
# Never feeds the circuit breaker
# ---------------------------------------------------------------------------
class TestNoCircuitBreakerCoupling:
    def test_tracker_has_no_breaker_reference(self):
        """C-2b invariant: reconciliation lives in its own swim-lane.
        The tracker must not accept a circuit_breaker argument and
        must not expose anything that could feed one."""
        sig = FillTracker.__init__.__doc__ or ""
        # Constructor must not mention breaker.
        assert "breaker" not in FillTracker.__init__.__code__.co_varnames

    def test_reconcile_does_not_call_any_breaker_method(self, tmp_path):
        """Sanity: even if the rest_client happened to have a record()
        method, FillTracker must never call it."""
        tracker = FillTracker()
        ol = FillLogger(tmp_path / "fills.jsonl")
        tracker.register(
            order_id="OID-1", symbol="BTCUSDT", side="Buy",
            intent_qty=0.01, intent_price=70_000.0,
        )
        rest = MagicMock()
        rest.get_order.return_value = {
            "avgPrice": "70010", "cumExecQty": "0.01",
        }
        tracker.reconcile_all(rest, ol)
        # The only method tracker may call on rest is get_order.
        assert rest.method_calls
        called = {c[0] for c in rest.method_calls}
        assert called == {"get_order"}
