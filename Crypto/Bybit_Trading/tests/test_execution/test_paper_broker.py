"""Smoke tests for PaperBroker.

We do NOT run the full PaperRunner here — that needs a real DB. These
tests focus on the PaperBroker behaviors that are safety-critical:

1. Universe guard rejects non-allowed symbols.
2. Signal log writes JSONL correctly.
3. Equity snapshot CSV grows per processed bar.
4. State save/load roundtrip preserves positions + equity.
5. Restored broker has the same open position as before save.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.config import BacktestConfig, RiskConfig
from src.core.types import Bar
from src.execution.paper_broker import PaperBroker


def _make_bar(
    symbol: str = "BTCUSDT", ts: int = 1_000_000,
    open_: float = 100.0, high: float = 101.0,
    low: float = 99.0, close: float = 100.5,
    volume: float = 1.0,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        timeframe="1h",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        turnover=1.0,
    )


def _mk_broker(tmp_path: Path) -> PaperBroker:
    return PaperBroker(
        config=BacktestConfig(initial_capital=10_000.0),
        risk_config=RiskConfig(),
        run_dir=tmp_path / "paper_run",
        symbols_allowed=["BTCUSDT", "ETHUSDT", "AVAXUSDT"],
        run_id="test_run",
    )


class TestUniverseGuard:
    def test_buy_allowed_symbol_returns_order_id(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        oid = b.buy("BTCUSDT", qty=0.001, stop_loss=90.0)
        assert oid != ""

    def test_buy_blocked_symbol_returns_empty(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        oid = b.buy("SOLUSDT", qty=0.001, stop_loss=90.0)
        assert oid == ""

    def test_sell_blocked_symbol_returns_empty(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        oid = b.sell("LINKUSDT", qty=0.001, stop_loss=110.0)
        assert oid == ""

    def test_manual_buy_blocked_symbol(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        oid = b.manual_buy("SOLUSDT", qty=0.001, stop_loss=90.0)
        assert oid == ""


class TestProcessBarLogging:
    def test_equity_curve_appends_per_bar(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        # 3 bars, no orders — expect 3 equity rows + 1 header
        b.process_bar(_make_bar(ts=1))
        b.process_bar(_make_bar(ts=2))
        b.process_bar(_make_bar(ts=3))
        lines = (tmp_path / "paper_run" / "equity_curve.csv").read_text(
            encoding="utf-8",
        ).strip().splitlines()
        assert lines[0] == "ts_ms,equity,realized_pnl,n_open_positions"
        assert len(lines) == 4  # header + 3 bars
        last = lines[-1].split(",")
        assert int(last[0]) == 3
        assert float(last[2]) == 0.0  # no realized pnl
        assert int(last[3]) == 0      # no open positions

    def test_fills_log_written_after_entry_and_close(
        self, tmp_path: Path,
    ) -> None:
        b = _mk_broker(tmp_path)
        # Place buy → fills on next bar open. Close via stop a few bars later.
        b.buy("BTCUSDT", qty=0.01, stop_loss=98.0)
        b.process_bar(_make_bar(ts=1, open_=100.0, close=100.5, high=101.0, low=100.0))
        # Now positioned; drop price below stop to force close.
        b.process_bar(_make_bar(ts=2, open_=100.0, close=97.0, high=100.0, low=97.0))
        fills_path = tmp_path / "paper_run" / "fills.jsonl"
        assert fills_path.exists()
        lines = fills_path.read_text(encoding="utf-8").strip().splitlines()
        # 1 trade row = entry→exit paired
        assert len(lines) >= 1
        row = json.loads(lines[-1])
        assert row["symbol"] == "BTCUSDT"
        assert row["side"] == "LONG"
        assert row["exit_reason"] == "STOP"


class TestSignalLog:
    def test_log_signal_writes_jsonl(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        bar = _make_bar(symbol="ETHUSDT", ts=10, close=2000.0)
        b.log_signal(bar, action="BUY_SIGNAL", reason="rsi lt 70", meta={"rsi": 55.2})
        signals_path = tmp_path / "paper_run" / "signals.jsonl"
        assert signals_path.exists()
        rows = [
            json.loads(line)
            for line in signals_path.read_text(encoding="utf-8").strip().splitlines()
        ]
        assert len(rows) == 1
        assert rows[0]["symbol"] == "ETHUSDT"
        assert rows[0]["action"] == "BUY_SIGNAL"
        assert rows[0]["meta"]["rsi"] == pytest.approx(55.2)


class TestStateRoundtrip:
    def test_save_and_load_state_preserves_scalar_fields(
        self, tmp_path: Path,
    ) -> None:
        b = _mk_broker(tmp_path)
        b.process_bar(_make_bar(ts=1))
        b.save_state(extra={"marker": "saved"})
        loaded = b.load_state()
        assert loaded is not None
        assert loaded["run_id"] == "test_run"
        assert loaded["equity"] == pytest.approx(10_000.0)
        assert loaded["realized_pnl"] == pytest.approx(0.0)
        assert loaded["extra"]["marker"] == "saved"

    def test_restore_from_state_rebuilds_positions(
        self, tmp_path: Path,
    ) -> None:
        # Run 1: open a position, save
        b1 = _mk_broker(tmp_path)
        b1.buy("BTCUSDT", qty=0.01, stop_loss=90.0)
        b1.process_bar(_make_bar(ts=1, open_=100.0))
        # position is now open
        assert b1.get_position("BTCUSDT") is not None
        b1.save_state()

        # Run 2: fresh broker pointing at the same run_dir, restore
        b2 = _mk_broker(tmp_path)
        assert b2.get_position("BTCUSDT") is None  # starts empty
        state = b2.load_state()
        assert state is not None
        b2.restore_from_state(state)
        pos = b2.get_position("BTCUSDT")
        assert pos is not None
        assert pos.side == "LONG"
        assert pos.qty == pytest.approx(0.01)

    def test_save_state_is_atomic_via_tmp(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        b.save_state()
        final = tmp_path / "paper_run" / "paper_state.json"
        tmp = tmp_path / "paper_run" / "paper_state.tmp"
        assert final.exists()
        assert not tmp.exists()  # tmp must be renamed away after write


class TestUniverseAccessor:
    def test_symbols_allowed_is_a_copy(self, tmp_path: Path) -> None:
        b = _mk_broker(tmp_path)
        s = b.symbols_allowed
        s.add("SOLUSDT")
        # Mutating the returned set must not change broker state.
        assert "SOLUSDT" not in b.symbols_allowed
