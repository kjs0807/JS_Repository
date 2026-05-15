"""Stage C-1: account.jsonl heartbeat snapshot."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.runtime.account_snapshot import AccountSnapshotWriter


def _fake_portfolio(**overrides):
    p = SimpleNamespace(
        equity=50_000.0, available_margin=40_000.0, used_margin=10_000.0,
        daily_pnl=100.0, realized_pnl=0.0,
        positions=[
            SimpleNamespace(
                symbol="BTCUSDT", side="LONG", qty=0.01, entry_price=75_000.0,
                unrealized_pnl=120.0, stop_loss=70_000.0, take_profit=80_000.0,
            ),
        ],
    )
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def _read_rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


class TestSchema:
    def test_full_row_serialises_all_fields(self, tmp_path):
        w = AccountSnapshotWriter(
            tmp_path / "account.jsonl",
            mode="demo", strategy="BBKCSqueeze",
            universe=["BTCUSDT", "ETHUSDT"], timeframe="1h",
        )
        w.write(
            portfolio=_fake_portfolio(),
            failure_counters={"min_qty": 2, "other": 0},
            breaker_stats={
                "total": 5, "failures": 1, "rate": 0.2,
                "tripped": False, "window_seconds": 3600,
                "threshold": 0.10, "min_sample": 5, "min_failures": 2,
            },
            kill_switch_engaged=False,
            bars_seen=42,
            ws_connected=True,
        )
        row = _read_rows(tmp_path / "account.jsonl")[0]
        assert row["mode"] == "demo"
        assert row["strategy"] == "BBKCSqueeze"
        assert row["universe"] == ["BTCUSDT", "ETHUSDT"]
        assert row["timeframe"] == "1h"
        assert row["equity"] == 50_000.0
        assert row["available_margin"] == 40_000.0
        assert row["positions"][0]["symbol"] == "BTCUSDT"
        assert row["positions"][0]["unrealized_pnl"] == 120.0
        assert row["failure_counters"] == {"min_qty": 2, "other": 0}
        assert row["breaker_stats"]["min_failures"] == 2
        assert row["kill_switch_engaged"] is False
        assert row["bars_seen"] == 42
        assert row["ws_connected"] is True

    def test_kill_switch_engaged_row(self, tmp_path):
        w = AccountSnapshotWriter(
            tmp_path / "account.jsonl",
            mode="live", strategy="BBKCSqueeze",
            universe=["BTCUSDT"], timeframe="1h",
        )
        w.write(
            portfolio=_fake_portfolio(),
            kill_switch_engaged=True,
            kill_switch_reason="file disable_new_entry.flag",
        )
        row = _read_rows(tmp_path / "account.jsonl")[0]
        assert row["kill_switch_engaged"] is True
        assert "disable_new_entry.flag" in row["kill_switch_reason"]


class TestRobustness:
    def test_append_across_multiple_calls(self, tmp_path):
        w = AccountSnapshotWriter(
            tmp_path / "account.jsonl",
            mode="demo", strategy="X", universe=["A"], timeframe="1h",
        )
        for i in range(3):
            w.write(portfolio=_fake_portfolio(equity=50_000.0 + i),
                    bars_seen=i)
        rows = _read_rows(tmp_path / "account.jsonl")
        assert [r["equity"] for r in rows] == [50_000.0, 50_001.0, 50_002.0]
        assert [r["bars_seen"] for r in rows] == [0, 1, 2]

    def test_creates_parent_dir(self, tmp_path):
        w = AccountSnapshotWriter(
            tmp_path / "nested" / "account.jsonl",
            mode="demo", strategy="X", universe=["A"], timeframe="1h",
        )
        w.write(portfolio=_fake_portfolio())
        assert (tmp_path / "nested" / "account.jsonl").exists()

    def test_no_positions_serialises_empty_list(self, tmp_path):
        w = AccountSnapshotWriter(
            tmp_path / "account.jsonl",
            mode="demo", strategy="X", universe=["A"], timeframe="1h",
        )
        w.write(portfolio=_fake_portfolio(positions=[]))
        row = _read_rows(tmp_path / "account.jsonl")[0]
        assert row["positions"] == []
