"""Backtester CLI entry point (Phase 1.5 PR 9, spec §14).

Usage::

    backtester run config.yaml [--quiet]

argparse 기반 — typer 등 외부 의존성 없이 표준 라이브러리만. ``run`` 은 YAML config 을
읽어 ``STRATEGY_REGISTRY`` 에서 strategy 를 인스턴스화한 뒤 ``BacktestEngine`` 으로 실행한다.

``--quiet`` 는 INFO 출력을 끈다 (Engine 의 verbose 알림 + CLI 의 final equity 라인 모두).
에러는 stderr 로 그대로 흘러간다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backtester.core.config import BacktestConfig
from backtester.core.engine import BacktestEngine
from backtester.core.errors import ConfigError, RunDirectoryError
from backtester.strategies.registry import build_strategy
from backtester.viz.report import render_metrics_report
from backtester.viz.run_chart import render_run_chart


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtester",
        description="backtester CLI (Phase 1.5).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser(
        "run",
        help="Run a backtest from a YAML config.",
        description="Run a backtest from a YAML config.",
    )
    run.add_argument(
        "config_path",
        type=Path,
        help="Path to the YAML config file.",
    )
    run.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress INFO output (Engine notifications + CLI summary).",
    )

    report = sub.add_parser(
        "report",
        help="Render run_chart.html for an existing run directory.",
        description="Render run_chart.html for an existing run directory.",
    )
    report.add_argument(
        "run_dir",
        type=Path,
        help="Path to runs/{run_id}/.",
    )
    report.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress INFO output (HTML output path).",
    )

    metrics = sub.add_parser(
        "metrics",
        help="Render metrics_report.html (PR 18) for an existing run directory.",
        description="Render metrics_report.html (PR 18) for an existing run directory.",
    )
    metrics.add_argument(
        "run_dir",
        type=Path,
        help="Path to runs/{run_id}/.",
    )
    metrics.add_argument(
        "--periods-per-year",
        type=int,
        default=365,
        help=(
            "Periods per year for annualization (default 365). 1d crypto = 365, "
            "1d stock = 252, 1h crypto = 8760, 1h stock = 6048."
        ),
    )
    metrics.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress INFO output (HTML output path).",
    )

    return parser


def cmd_run(config_path: Path, *, quiet: bool) -> int:
    """``backtester run`` 본체. 정상 종료 시 0, 사용자 오류 시 2."""
    if not config_path.exists():
        print(f"[error] config file not found: {config_path}", file=sys.stderr)
        return 2

    try:
        config = BacktestConfig.from_yaml(config_path)
        strategy = build_strategy(config.strategy_name, config.strategy_params)
    except ConfigError as e:
        print(f"[error] config: {e}", file=sys.stderr)
        return 2

    try:
        engine = BacktestEngine(config, strategy=strategy, verbose=not quiet)
        result = engine.run()
    except RunDirectoryError as e:
        print(f"[error] run directory: {e}", file=sys.stderr)
        return 2

    if not quiet:
        print(f"[INFO] Final equity: {result.final_equity}")
        print(f"[INFO] Run directory: {result.run_dir}")
    return 0


def cmd_report(run_dir: Path, *, quiet: bool) -> int:
    """``backtester report`` 본체 — ``run_dir`` 의 run_chart.html 렌더링."""
    if not run_dir.exists() or not run_dir.is_dir():
        print(f"[error] run dir not found: {run_dir}", file=sys.stderr)
        return 2
    if not (run_dir / "events.jsonl").exists():
        print(
            f"[error] events.jsonl missing in {run_dir} — not a valid run directory",
            file=sys.stderr,
        )
        return 2
    try:
        output = render_run_chart(run_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"[error] render: {e}", file=sys.stderr)
        return 2
    if not quiet:
        print(f"[INFO] Wrote {output}")
    return 0


def cmd_metrics(
    run_dir: Path,
    *,
    periods_per_year: int,
    quiet: bool,
) -> int:
    """``backtester metrics`` 본체 — metrics_report.html 렌더링."""
    if not run_dir.exists() or not run_dir.is_dir():
        print(f"[error] run dir not found: {run_dir}", file=sys.stderr)
        return 2
    if not (run_dir / "events.jsonl").exists():
        print(
            f"[error] events.jsonl missing in {run_dir} — not a valid run directory",
            file=sys.stderr,
        )
        return 2
    try:
        output = render_metrics_report(
            run_dir, periods_per_year=periods_per_year
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"[error] render: {e}", file=sys.stderr)
        return 2
    if not quiet:
        print(f"[INFO] Wrote {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args.config_path, quiet=args.quiet)
    if args.cmd == "report":
        return cmd_report(args.run_dir, quiet=args.quiet)
    if args.cmd == "metrics":
        return cmd_metrics(
            args.run_dir,
            periods_per_year=args.periods_per_year,
            quiet=args.quiet,
        )
    # argparse `required=True` 가 차단하지만 방어적으로
    print(f"[error] unknown command: {args.cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
