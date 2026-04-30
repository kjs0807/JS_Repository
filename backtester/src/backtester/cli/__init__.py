"""CLI subpackage (Phase 1.5 PR 9, spec §14).

명령:
- ``backtester run config.yaml [--quiet]`` — YAML config 으로 백테스트 실행

후속 PR:
- ``backtester report runs/{run_id}/ [--quiet]`` (Phase 1.5 PR 11)
- ``backtester rebuild-results runs/{run_id}/`` (Phase 2)
"""

from backtester.cli.main import main

__all__ = ["main"]
