"""Refinement convenience script — gathers context for a subagent dispatch.

This script does NOT call any LLM directly. It resolves the paths the
developer will hand to a superpowers subagent (general-purpose or debugger),
then prints a summary. The actual review / diff proposal happens in a
manual subagent invocation from Claude Code.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict


def gather_context(
    pattern_name: str,
    logs_root: Path,
    patterns_root: Path,
    prompt_template: Path,
) -> Dict[str, Path]:
    pattern_log_dir = Path(logs_root) / pattern_name
    if not pattern_log_dir.exists():
        raise FileNotFoundError(f"No runs found at {pattern_log_dir}")
    runs = sorted(p for p in pattern_log_dir.iterdir() if p.is_dir())
    if not runs:
        raise FileNotFoundError(f"No runs in {pattern_log_dir}")
    latest = runs[-1]
    report_path = latest / "report.json"
    pattern_source = Path(patterns_root) / f"{pattern_name}.py"
    if not report_path.exists():
        raise FileNotFoundError(report_path)
    if not pattern_source.exists():
        raise FileNotFoundError(pattern_source)
    prompt_path = Path(prompt_template)
    if not prompt_path.exists():
        raise FileNotFoundError(prompt_path)
    return {
        "run_id": latest.name,
        "run_dir": latest,
        "report_path": report_path,
        "pattern_source_path": pattern_source,
        "prompt_template_path": prompt_path,
    }


def _parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("pattern", help="Pattern name (e.g. rsi_divergence)")
    p.add_argument("--logs-root", default="logs/ml")
    p.add_argument("--patterns-root", default="src/ml/patterns")
    p.add_argument(
        "--prompt-template",
        default="docs/superpowers/specs/ml/refinement-agent-prompt.md",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    ctx = gather_context(
        pattern_name=args.pattern,
        logs_root=Path(args.logs_root),
        patterns_root=Path(args.patterns_root),
        prompt_template=Path(args.prompt_template),
    )
    print("Refinement context ready.")
    print(f"  Pattern:        {args.pattern}")
    print(f"  Run ID:         {ctx['run_id']}")
    print(f"  Run dir:        {ctx['run_dir']}")
    print(f"  Report:         {ctx['report_path']}")
    print(f"  Pattern source: {ctx['pattern_source_path']}")
    print(f"  Prompt:         {ctx['prompt_template_path']}")
    print()
    print("Next step: open Claude Code and dispatch a subagent with these inputs:")
    print("  Skill / Agent: superpowers:debugger or general-purpose")
    print(
        "  Read these files: "
        f"{ctx['report_path']}, {ctx['pattern_source_path']}, "
        f"{ctx['prompt_template_path']}"
    )
    print("  Use the prompt template format. Apply suggestions manually after review.")


if __name__ == "__main__":
    main(sys.argv[1:])
