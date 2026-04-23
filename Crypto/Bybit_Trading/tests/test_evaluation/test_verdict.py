"""Tests for ``src/evaluation/verdict.py`` — rule-based verdict rules.

Covers every branch of ``judge_variant_vs_baseline`` so the promote/kill
logic is reproducible across experiments. Two kinds of test cases:

1. Synthetic aggregate dicts for rule-specific coverage.
2. Round-1 ``logs/variant_round1/results.json`` replay: the rule set
   MUST produce the same verdicts the user fixed during round 1
   (D2=PROMOTE, ADX20=KILL, ADX25=KILL, HTFTrend=KILL). A regression
   here means the rules drifted away from the empirical truth.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from src.evaluation.verdict import (
    HoldoutVerdict,
    VerdictThresholds,
    format_verdict_line,
    judge_variant_vs_baseline,
)

ROOT = Path(__file__).resolve().parent.parent.parent
ROUND1_PATH = ROOT / "logs" / "variant_round1" / "results.json"


def _make_result(
    total_pnl: float,
    n_trades: int,
    avg_trade_pnl: float,
    max_drawdown: float,
    win_rate: float = 0.5,
    per_symbol: Dict[str, Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if per_symbol is None:
        # Default: 5 symbols each with n_trades/5 trades, with a flat
        # per-symbol pnl split so no destroyed-winner rule fires.
        per = n_trades // 5
        remain = n_trades - per * 5
        per_pnl = total_pnl / 5 if n_trades > 0 else 0.0
        per_symbol = {
            "BTCUSDT":  {"n_trades": per + remain, "total_pnl": per_pnl},
            "ETHUSDT":  {"n_trades": per,          "total_pnl": per_pnl},
            "SOLUSDT":  {"n_trades": per,          "total_pnl": per_pnl},
            "LINKUSDT": {"n_trades": per,          "total_pnl": per_pnl},
            "AVAXUSDT": {"n_trades": per,          "total_pnl": per_pnl},
        }
    return {
        "per_symbol": per_symbol,
        "aggregate": {
            "n_trades": n_trades,
            "total_pnl": total_pnl,
            "avg_trade_pnl": avg_trade_pnl,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
        },
    }


class TestEvidenceGate:
    def test_insufficient_trades_returns_insufficient_data(self) -> None:
        base = _make_result(total_pnl=0.0, n_trades=100, avg_trade_pnl=0.0, max_drawdown=0.1)
        var = _make_result(total_pnl=5.0, n_trades=10, avg_trade_pnl=0.5, max_drawdown=0.1)
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "INSUFFICIENT_DATA"
        assert any("n_trades" in r for r in v.reasons)

    def test_insufficient_active_symbols_returns_insufficient_data(self) -> None:
        base = _make_result(total_pnl=0.0, n_trades=100, avg_trade_pnl=0.0, max_drawdown=0.1)
        ps = {
            "BTCUSDT":  {"n_trades": 20, "total_pnl": 20.0},
            "ETHUSDT":  {"n_trades": 20, "total_pnl": 20.0},
            "SOLUSDT":  {"n_trades": 0,  "total_pnl": 0.0},
            "LINKUSDT": {"n_trades": 0,  "total_pnl": 0.0},
            "AVAXUSDT": {"n_trades": 0,  "total_pnl": 0.0},
        }
        var = _make_result(
            total_pnl=40.0, n_trades=40, avg_trade_pnl=1.0,
            max_drawdown=0.1, per_symbol=ps,
        )
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "INSUFFICIENT_DATA"
        assert any("active_symbols" in r for r in v.reasons)


class TestDestroyedWinners:
    def test_destroyed_two_baseline_winners_kills_variant(self) -> None:
        # Baseline has 3 clear winners: BTC/ETH/AVAX. Variant keeps less
        # than half of each of them — this mimics BBKCSqueezeHTFTrend.
        b_ps = {
            "BTCUSDT":  {"n_trades": 30, "total_pnl": 1000.0},
            "ETHUSDT":  {"n_trades": 30, "total_pnl": 1500.0},
            "SOLUSDT":  {"n_trades": 30, "total_pnl": -500.0},
            "LINKUSDT": {"n_trades": 30, "total_pnl": -500.0},
            "AVAXUSDT": {"n_trades": 30, "total_pnl": 1200.0},
        }
        v_ps = {
            "BTCUSDT":  {"n_trades": 20, "total_pnl": -500.0},  # destroyed
            "ETHUSDT":  {"n_trades": 20, "total_pnl": 300.0},   # destroyed (< 750)
            "SOLUSDT":  {"n_trades": 20, "total_pnl": -200.0},
            "LINKUSDT": {"n_trades": 20, "total_pnl": -200.0},
            "AVAXUSDT": {"n_trades": 20, "total_pnl": 700.0},   # destroyed (< 600? no > 600) -- not destroyed
        }
        base = _make_result(
            total_pnl=2700.0, n_trades=150, avg_trade_pnl=18.0,
            max_drawdown=0.15, per_symbol=b_ps,
        )
        var = _make_result(
            total_pnl=100.0, n_trades=100, avg_trade_pnl=1.0,
            max_drawdown=0.12, per_symbol=v_ps,
        )
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "KILL"
        assert any("destroyed" in r for r in v.reasons)

    def test_single_destroyed_winner_does_not_force_kill(self) -> None:
        # With max_destroyed_winners=2 default, a single destroyed
        # winner should NOT kill outright — fall through to the other
        # rules. Here the aggregate also improves so we end up promoting.
        b_ps = {
            "BTCUSDT":  {"n_trades": 30, "total_pnl": 1000.0},
            "ETHUSDT":  {"n_trades": 30, "total_pnl": 300.0},
            "SOLUSDT":  {"n_trades": 30, "total_pnl": 200.0},
            "LINKUSDT": {"n_trades": 30, "total_pnl": 200.0},
            "AVAXUSDT": {"n_trades": 30, "total_pnl": 200.0},
        }
        v_ps = {
            "BTCUSDT":  {"n_trades": 20, "total_pnl": 100.0},   # 1 destroyed
            "ETHUSDT":  {"n_trades": 30, "total_pnl": 600.0},
            "SOLUSDT":  {"n_trades": 30, "total_pnl": 600.0},
            "LINKUSDT": {"n_trades": 30, "total_pnl": 600.0},
            "AVAXUSDT": {"n_trades": 30, "total_pnl": 600.0},
        }
        base = _make_result(
            total_pnl=1900.0, n_trades=150, avg_trade_pnl=12.6,
            max_drawdown=0.20, per_symbol=b_ps,
        )
        var = _make_result(
            total_pnl=2500.0, n_trades=140, avg_trade_pnl=17.85,
            max_drawdown=0.18, per_symbol=v_ps,
        )
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict != "KILL"


class TestNewSymbolPrior:
    def test_new_symbol_prior_kill(self) -> None:
        # Variant's top symbol (AVAX, 63 trades) had 1 trade in baseline
        # -> 1.3% <= 10% threshold -> KILL.
        b_ps = {
            "BTCUSDT":  {"n_trades": 45, "total_pnl": 400.0},
            "ETHUSDT":  {"n_trades": 0,  "total_pnl": 0.0},
            "SOLUSDT":  {"n_trades": 29, "total_pnl": -200.0},
            "LINKUSDT": {"n_trades": 1,  "total_pnl": -200.0},
            "AVAXUSDT": {"n_trades": 1,  "total_pnl": -200.0},
        }
        v_ps = {
            "BTCUSDT":  {"n_trades": 32, "total_pnl": -500.0},
            "ETHUSDT":  {"n_trades": 31, "total_pnl": -200.0},
            "SOLUSDT":  {"n_trades": 29, "total_pnl": -768.0},
            "LINKUSDT": {"n_trades": 57, "total_pnl": 673.0},
            "AVAXUSDT": {"n_trades": 63, "total_pnl": 2156.0},
        }
        base = _make_result(
            total_pnl=-200.0, n_trades=76, avg_trade_pnl=-2.6,
            max_drawdown=0.20, per_symbol=b_ps,
        )
        var = _make_result(
            total_pnl=1361.0, n_trades=212, avg_trade_pnl=6.4,
            max_drawdown=0.27, per_symbol=v_ps,
        )
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "KILL"
        assert any("new symbol prior" in r or "top symbol" in r for r in v.reasons)


class TestDualRegression:
    def test_dual_regression_kill(self) -> None:
        base = _make_result(total_pnl=100.0, n_trades=100, avg_trade_pnl=1.0, max_drawdown=0.10)
        var = _make_result(total_pnl=-100.0, n_trades=100, avg_trade_pnl=-1.0, max_drawdown=0.20)
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "KILL"
        assert any("worse" in r for r in v.reasons)


class TestPromoteAndConditional:
    def test_clean_promote(self) -> None:
        base = _make_result(total_pnl=100.0, n_trades=100, avg_trade_pnl=1.0, max_drawdown=0.20)
        var = _make_result(total_pnl=200.0, n_trades=100, avg_trade_pnl=2.0, max_drawdown=0.18)
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "PROMOTE"
        assert v.delta_avg_trade_pnl == pytest.approx(1.0)
        assert v.delta_max_drawdown == pytest.approx(-0.02)

    def test_conditional_promote_on_net_pnl(self) -> None:
        base = _make_result(total_pnl=100.0, n_trades=50, avg_trade_pnl=2.0, max_drawdown=0.20)
        var = _make_result(total_pnl=150.0, n_trades=100, avg_trade_pnl=1.5, max_drawdown=0.201)
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "CONDITIONAL_PROMOTE"

    def test_conditional_promote_on_drawdown(self) -> None:
        base = _make_result(total_pnl=100.0, n_trades=100, avg_trade_pnl=1.0, max_drawdown=0.30)
        var = _make_result(total_pnl=100.0, n_trades=100, avg_trade_pnl=1.0, max_drawdown=0.10)
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "CONDITIONAL_PROMOTE"
        assert any("mdd" in r for r in v.reasons)

    def test_no_edge_when_deltas_within_eps(self) -> None:
        base = _make_result(total_pnl=100.0, n_trades=100, avg_trade_pnl=1.0, max_drawdown=0.10)
        var = _make_result(total_pnl=100.3, n_trades=100, avg_trade_pnl=1.003, max_drawdown=0.100)
        v = judge_variant_vs_baseline("V", var, "B", base)
        assert v.verdict == "NO_EDGE"

    def test_thresholds_override(self) -> None:
        base = _make_result(total_pnl=100.0, n_trades=100, avg_trade_pnl=1.0, max_drawdown=0.10)
        var = _make_result(total_pnl=100.0, n_trades=100, avg_trade_pnl=1.3, max_drawdown=0.10)
        assert judge_variant_vs_baseline("V", var, "B", base).verdict == "NO_EDGE"
        t = VerdictThresholds(avg_trade_pnl_improve_eps=0.1)
        v = judge_variant_vs_baseline("V", var, "B", base, thresholds=t)
        assert v.verdict == "PROMOTE"

    def test_format_verdict_line_contains_name_and_verdict(self) -> None:
        base = _make_result(total_pnl=0.0, n_trades=100, avg_trade_pnl=0.0, max_drawdown=0.1)
        var = _make_result(total_pnl=200.0, n_trades=100, avg_trade_pnl=2.0, max_drawdown=0.08)
        v = judge_variant_vs_baseline("MyVariant", var, "MyBase", base)
        line = format_verdict_line(v)
        assert "MyVariant" in line
        assert "MyBase" in line
        assert v.verdict in line

    def test_to_dict_roundtrip(self) -> None:
        base = _make_result(total_pnl=0.0, n_trades=100, avg_trade_pnl=0.0, max_drawdown=0.1)
        var = _make_result(total_pnl=200.0, n_trades=100, avg_trade_pnl=2.0, max_drawdown=0.08)
        v = judge_variant_vs_baseline("V", var, "B", base)
        d = v.to_dict()
        assert d["verdict"] == "PROMOTE"
        assert d["variant"] == "V"
        assert d["baseline"] == "B"
        assert "delta" in d
        assert d["delta"]["avg_trade_pnl"] == pytest.approx(2.0)


# -- Round 1 replay: the rule set must reproduce the user's verdicts ----

@pytest.fixture(scope="module")
def round1() -> Dict[str, Any]:
    if not ROUND1_PATH.exists():
        pytest.skip("round1 results.json not available")
    with ROUND1_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


class TestRound1Replay:
    def test_d2_fixedrr_trend_filter_promotes(self, round1: Dict[str, Any]) -> None:
        v = judge_variant_vs_baseline(
            "DonchianFixedRRTrendFilter",
            round1["DonchianFixedRRTrendFilter"],
            "DonchianFixedRR",
            round1["DonchianFixedRR"],
        )
        # Round 1 user call: PROMOTE (조건부). CONDITIONAL_PROMOTE also OK.
        assert v.verdict in ("PROMOTE", "CONDITIONAL_PROMOTE")

    def test_adx20_kill(self, round1: Dict[str, Any]) -> None:
        v = judge_variant_vs_baseline(
            "DonchianTrendFilterADX20",
            round1["DonchianTrendFilterADX20"],
            "DonchianTrendFilter",
            round1["DonchianTrendFilter"],
        )
        assert v.verdict == "KILL"

    def test_adx25_kill(self, round1: Dict[str, Any]) -> None:
        v = judge_variant_vs_baseline(
            "DonchianTrendFilterADX25",
            round1["DonchianTrendFilterADX25"],
            "DonchianTrendFilter",
            round1["DonchianTrendFilter"],
        )
        assert v.verdict == "KILL"

    def test_bbkc_htftrend_kill(self, round1: Dict[str, Any]) -> None:
        v = judge_variant_vs_baseline(
            "BBKCSqueezeHTFTrend",
            round1["BBKCSqueezeHTFTrend"],
            "BBKCSqueeze",
            round1["BBKCSqueeze"],
        )
        assert v.verdict == "KILL"
