"""RSI divergence daily regime research module.

This is a **research track**, not a live strategy. See
``docs/superpowers/specs/experiments/2026-04-14_rsi_regime_research_problem.md``
for the problem restatement.

TL;DR of why this exists as a separate module:
- Trade-level RSI divergence ML was KILLED (RSI/Engulfing/BBKC filter).
- That failure tested whether divergence-at-event predicts TP-first on
  a triple barrier at 1h/4h/1d. It didn't.
- This module asks a DIFFERENT question: given a daily RSI divergence,
  is the FORWARD REGIME (next N days) classifiably different from
  unconditional base rate? This is not a trade signal — it's a
  market-context measurement.

The module exposes:

- ``divergence_events`` : build a dataset of confirmed daily divergence
  events from a price/RSI series, with features attached.
- ``regime_labels``     : compute forward horizon log returns and
  classify each event into DOWN / FLAT / UP regimes.
- ``evaluator``         : IS/OOS split + per-type regime distribution +
  lift vs baseline unconditional distribution.

None of these touch the trade-level pipeline. Artifacts land in
``logs/research/rsi_regime/`` (not ``logs/d2_*`` or ``logs/bbkc_*``).
"""
