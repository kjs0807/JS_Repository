"""Parallel research tracks separated from trade-level strategy code.

See ``docs/superpowers/specs/experiments/2026-04-14_experiment_protocol.md``
section P9 for the separation rules.

This package is **read-only** with respect to the rest of ``src/``:
code here may import from ``src/ml/helpers`` (pure algorithms with no
trade-level side effects), ``src/data_manager/db.py`` (DB read),
and ``src/core/config.py`` (config load). It must NOT import from
``src/strategies``, ``src/execution``, or ``src/backtester`` — those
are the operational surfaces and the whole point of the separation is
that research drift cannot affect them.
"""
