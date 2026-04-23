"""Rule-based strategy variant verdict rules.

This is the holdout-first analogue of ``src/ml/validator.py::HoldoutReport``
for rule-based strategy variants (no ML involved). It takes the same
aggregate/per-symbol metrics shape produced by ``src/evaluation/holdout.py``
and emits a PROMOTE / CONDITIONAL / KILL / NO_EDGE verdict plus the
concrete reasons.

The rule set is derived from the user-fixed memo in
``docs/superpowers/specs/strategies/2026-04-14_bbkc_donchian_rule_based_improvement_memo.md``
section 5 ("Recommended experiment protocol") and the PHASE 1 principles
in ``docs/superpowers/specs/experiments/2026-04-14_experiment_protocol.md``.

No handwritten numbers: the thresholds live in ``VerdictThresholds`` and
``judge_variant_vs_baseline`` reports which rule fired so the reasoning
is auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VerdictThresholds:
    """All tunable thresholds used by ``judge_variant_vs_baseline``.

    Why these defaults — every number below has a concrete round-1
    failure case it defends against:

    - ``min_trades_total``: below ~30 holdout trades the comparison is
      noise. Round 1 memo fixed this as the minimum evidence bar.
    - ``min_symbols_with_trades``: a variant that only trades one symbol
      is not robust. BBKCSqueezeHTFTrend destroyed BTC (-$2133 vs raw)
      while AVAX carried the aggregate — aggregate alone hides this.
    - ``avg_trade_pnl_improve_eps``: fires only when the delta is more
      than broker fees / slippage noise. 0.5$ ≈ round-trip fees on a
      ~$10k position so anything smaller is not real edge.
    - ``mdd_improve_eps``: 1 percentage point of drawdown improvement
      is the smallest delta worth calling "better".
    - ``pnl_improve_eps``: dollar-level pnl improvement threshold for
      the "net pnl up on mixed per-trade" conditional-promote branch.
    - ``destroyed_winner_baseline_pnl_eps``: ignore baseline symbols
      whose profit was tiny when counting destroyed winners — a +$2 pnl
      "winner" isn't a real signal and we don't want to trip on noise.
    - ``destroyed_winner_retention_ratio``: a baseline winner is
      "destroyed" when the variant retains less than this fraction of
      the baseline pnl. 0.5 = "lost more than half the edge".
    - ``max_destroyed_winners``: if the variant destroys this many or
      more baseline winners it is KILL regardless of aggregate. Round 1
      BBKCSqueezeHTFTrend destroyed BTC/ETH/AVAX (3 baseline winners)
      and still had net-positive aggregate — this rule catches it.
    - ``new_symbol_prior_threshold``: if the variant's top-trade symbol
      had this share or less of baseline's total trades, the variant
      has flipped the symbol prior. DonchianTrendFilterADX25's top was
      AVAX (63/212) but baseline AVAX was 1/76 (1.3%) — clear prior
      flip that the old "absolute concentration > 65%" rule missed.
    """

    min_trades_total: int = 30
    min_symbols_with_trades: int = 3
    avg_trade_pnl_improve_eps: float = 0.5
    mdd_improve_eps: float = 0.01
    pnl_improve_eps: float = 1.0
    destroyed_winner_baseline_pnl_eps: float = 50.0
    destroyed_winner_retention_ratio: float = 0.5
    max_destroyed_winners: int = 2
    new_symbol_prior_threshold: float = 0.10


@dataclass
class HoldoutVerdict:
    variant_name: str
    baseline_name: str
    verdict: str                   # PROMOTE / CONDITIONAL_PROMOTE / KILL / NO_EDGE / INSUFFICIENT_DATA
    reasons: List[str] = field(default_factory=list)
    delta_total_pnl: float = 0.0
    delta_avg_trade_pnl: float = 0.0
    delta_win_rate: float = 0.0
    delta_max_drawdown: float = 0.0
    delta_n_trades: int = 0
    symbol_concentration: float = 0.0
    baseline: Dict[str, Any] = field(default_factory=dict)
    variant: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variant": self.variant_name,
            "baseline": self.baseline_name,
            "verdict": self.verdict,
            "reasons": self.reasons,
            "delta": {
                "total_pnl": self.delta_total_pnl,
                "avg_trade_pnl": self.delta_avg_trade_pnl,
                "win_rate": self.delta_win_rate,
                "max_drawdown": self.delta_max_drawdown,
                "n_trades": self.delta_n_trades,
            },
            "symbol_concentration": self.symbol_concentration,
            "baseline_metrics": self.baseline,
            "variant_metrics": self.variant,
        }


def _top_symbol_concentration(per_symbol: Dict[str, Dict[str, Any]]) -> float:
    totals = [abs(m.get("n_trades", 0)) for m in per_symbol.values()]
    total = sum(totals)
    if total <= 0:
        return 0.0
    return max(totals) / total


def _count_active_symbols(per_symbol: Dict[str, Dict[str, Any]]) -> int:
    return sum(1 for m in per_symbol.values() if m.get("n_trades", 0) > 0)


def _count_destroyed_winners(
    baseline_ps: Dict[str, Dict[str, Any]],
    variant_ps: Dict[str, Dict[str, Any]],
    pnl_eps: float,
    retention_ratio: float,
) -> tuple[int, List[str]]:
    """Count baseline-profitable symbols the variant destroyed.

    A symbol is "destroyed" when:
    - baseline symbol pnl > ``pnl_eps`` (was a real winner, not noise)
    - variant symbol pnl < baseline_pnl * ``retention_ratio`` (lost
      more than ``1 - retention_ratio`` of the edge)

    This is how we catch the BBKCSqueezeHTFTrend failure mode where the
    aggregate looked OK but 3 of 5 symbols lost most of their round-1
    edge.
    """
    destroyed: List[str] = []
    for sym, bm in baseline_ps.items():
        b_pnl = float(bm.get("total_pnl", 0.0))
        if b_pnl <= pnl_eps:
            continue
        v_pnl = float(variant_ps.get(sym, {}).get("total_pnl", 0.0))
        if v_pnl < b_pnl * retention_ratio:
            destroyed.append(
                f"{sym}(${b_pnl:+.0f}->${v_pnl:+.0f})"
            )
    return len(destroyed), destroyed


def _detect_new_symbol_prior(
    baseline_ps: Dict[str, Dict[str, Any]],
    variant_ps: Dict[str, Dict[str, Any]],
    threshold: float,
) -> tuple[bool, str]:
    """Return (flip_detected, reason).

    A 'new symbol prior' fires when the variant's most-traded symbol
    had <= ``threshold`` share of the baseline's total activity. This
    catches ``DonchianTrendFilterADX25`` where variant's top was AVAX
    (30% of variant trades) but baseline AVAX was 1.3% — the variant
    has created activity in a symbol the baseline barely touched,
    which is a regime prior flip rather than a clean filter.
    """
    variant_nt = {
        s: int(m.get("n_trades", 0)) for s, m in variant_ps.items()
    }
    if not variant_nt or max(variant_nt.values()) == 0:
        return False, ""
    top_sym = max(variant_nt, key=variant_nt.get)
    baseline_total = sum(
        int(m.get("n_trades", 0)) for m in baseline_ps.values()
    )
    if baseline_total <= 0:
        return False, ""
    baseline_top_share = (
        int(baseline_ps.get(top_sym, {}).get("n_trades", 0)) / baseline_total
    )
    if baseline_top_share <= threshold:
        return True, (
            f"variant top symbol={top_sym} "
            f"had {baseline_top_share:.1%} of baseline trades"
        )
    return False, ""


def judge_variant_vs_baseline(
    variant_name: str,
    variant_result: Dict[str, Any],
    baseline_name: str,
    baseline_result: Dict[str, Any],
    thresholds: Optional[VerdictThresholds] = None,
) -> HoldoutVerdict:
    """Return a verdict for ``variant`` relative to ``baseline``.

    Both inputs must be the dict shape emitted by
    ``run_strategy_on_holdout``::

        {"per_symbol": {sym: metrics, ...}, "aggregate": metrics}

    Rule order (first matching wins for terminal verdicts):

    1. INSUFFICIENT_DATA -- variant has < min_trades_total trades
       OR trades only on fewer than min_symbols_with_trades symbols.
    2. KILL -- destroyed >= max_destroyed_winners baseline-profitable
       symbols (BBKCSqueezeHTFTrend failure mode).
    3. KILL -- new symbol prior flip (DonchianTrendFilterADX25 failure
       mode): variant's top-trade symbol was barely touched by baseline.
    4. KILL -- avg_trade_pnl worse AND max_drawdown worse.
    5. PROMOTE -- avg_trade_pnl better AND max_drawdown not worse.
    6. CONDITIONAL_PROMOTE -- mixed per-trade signal but net aggregate
       pnl improves beyond pnl_improve_eps, OR drawdown improves alone.
    7. NO_EDGE -- nothing moved materially.
    """
    t = thresholds or VerdictThresholds()
    v_agg = variant_result["aggregate"]
    b_agg = baseline_result["aggregate"]
    v_ps = variant_result["per_symbol"]
    b_ps = baseline_result["per_symbol"]

    delta_pnl = float(v_agg["total_pnl"] - b_agg["total_pnl"])
    delta_avg = float(v_agg["avg_trade_pnl"] - b_agg["avg_trade_pnl"])
    delta_wr = float(v_agg["win_rate"] - b_agg["win_rate"])
    delta_dd = float(v_agg["max_drawdown"] - b_agg["max_drawdown"])
    delta_nt = int(v_agg["n_trades"] - b_agg["n_trades"])

    v_conc = _top_symbol_concentration(v_ps)
    v_active = _count_active_symbols(v_ps)

    verdict = HoldoutVerdict(
        variant_name=variant_name,
        baseline_name=baseline_name,
        verdict="NO_EDGE",
        delta_total_pnl=delta_pnl,
        delta_avg_trade_pnl=delta_avg,
        delta_win_rate=delta_wr,
        delta_max_drawdown=delta_dd,
        delta_n_trades=delta_nt,
        symbol_concentration=v_conc,
        baseline=b_agg,
        variant=v_agg,
    )

    # 1. Evidence gate
    if v_agg["n_trades"] < t.min_trades_total:
        verdict.verdict = "INSUFFICIENT_DATA"
        verdict.reasons.append(
            f"n_trades={v_agg['n_trades']} < {t.min_trades_total}"
        )
        return verdict
    if v_active < t.min_symbols_with_trades:
        verdict.verdict = "INSUFFICIENT_DATA"
        verdict.reasons.append(
            f"active_symbols={v_active} < {t.min_symbols_with_trades}"
        )
        return verdict

    # 2. Destroyed winners KILL — baseline had >= N profitable symbols
    # the variant wrecked. Fires before PROMOTE so an aggregate improvement
    # driven by a symbol-prior shift can't mask edge destruction.
    destroyed_count, destroyed_details = _count_destroyed_winners(
        b_ps, v_ps,
        pnl_eps=t.destroyed_winner_baseline_pnl_eps,
        retention_ratio=t.destroyed_winner_retention_ratio,
    )
    if destroyed_count >= t.max_destroyed_winners:
        verdict.verdict = "KILL"
        verdict.reasons.append(
            f"destroyed {destroyed_count} baseline winners: "
            + ", ".join(destroyed_details)
        )
        return verdict

    # 3. New symbol prior flip KILL — variant's top trade symbol was
    # <= new_symbol_prior_threshold of baseline activity.
    flipped, flip_reason = _detect_new_symbol_prior(
        b_ps, v_ps, t.new_symbol_prior_threshold,
    )
    if flipped:
        verdict.verdict = "KILL"
        verdict.reasons.append(flip_reason + " (new symbol prior)")
        return verdict

    # Improvements / regressions use eps to reject fee-noise deltas.
    avg_better = delta_avg > t.avg_trade_pnl_improve_eps
    avg_worse = delta_avg < -t.avg_trade_pnl_improve_eps
    dd_better = delta_dd < -t.mdd_improve_eps
    dd_worse = delta_dd > t.mdd_improve_eps
    pnl_better = delta_pnl > t.pnl_improve_eps
    pnl_worse = delta_pnl < -t.pnl_improve_eps

    # 4. Dual regression KILL
    if avg_worse and dd_worse:
        verdict.verdict = "KILL"
        verdict.reasons.append(
            f"avg_trade_pnl worse ({delta_avg:+.2f}) "
            f"AND mdd worse ({delta_dd:+.1%})"
        )
        return verdict

    # 5. Clean improvement
    if avg_better and not dd_worse:
        verdict.verdict = "PROMOTE"
        verdict.reasons.append(
            f"avg_trade_pnl better ({delta_avg:+.2f}) "
            f"AND mdd not worse ({delta_dd:+.1%})"
        )
        if pnl_better:
            verdict.reasons.append(
                f"total pnl also improves ({delta_pnl:+.2f})"
            )
        return verdict

    # 6a. Mixed but net positive
    if pnl_better and not (avg_worse and dd_worse):
        verdict.verdict = "CONDITIONAL_PROMOTE"
        verdict.reasons.append(
            f"net pnl improves ({delta_pnl:+.2f}) "
            f"on mixed per-trade signal"
        )
        return verdict

    # 6b. Drawdown improvement alone (e.g. variant trades less aggressively)
    if dd_better and not avg_worse:
        verdict.verdict = "CONDITIONAL_PROMOTE"
        verdict.reasons.append(
            f"mdd improves ({delta_dd:+.1%}) without avg worsening"
        )
        return verdict

    # 7. NO_EDGE fallback
    verdict.reasons.append(
        f"no material delta: "
        f"avg{delta_avg:+.2f} pnl{delta_pnl:+.2f} dd{delta_dd:+.1%}"
    )
    return verdict


def format_verdict_line(v: HoldoutVerdict) -> str:
    """One-line verdict summary for console output."""
    return (
        f"[{v.verdict:20s}] {v.variant_name:32s} vs {v.baseline_name:28s} | "
        f"Δpnl={v.delta_total_pnl:+9.2f} "
        f"Δavg={v.delta_avg_trade_pnl:+7.2f} "
        f"Δwr={v.delta_win_rate:+6.1%} "
        f"Δmdd={v.delta_max_drawdown:+6.1%} "
        f"conc={v.symbol_concentration:5.1%}"
    )


__all__ = [
    "VerdictThresholds",
    "HoldoutVerdict",
    "judge_variant_vs_baseline",
    "format_verdict_line",
]
