"""Tests for refine_pattern.py — context gathering only, no actual dispatch."""
import json
from pathlib import Path

import scripts.refine_pattern as cli


def test_gather_context_returns_required_files(tmp_path):
    pattern_name = "engulfing_mtf"
    run_id = "2026-04-13_001"
    run_dir = tmp_path / "logs" / "ml" / pattern_name / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(
        json.dumps({"verdict": "WARNING", "metrics": {}, "facts": {}, "hints": {}})
    )
    (run_dir / "meta.json").write_text(json.dumps({"pattern_name": pattern_name}))

    pattern_file = tmp_path / "src" / "ml" / "patterns" / "engulfing_mtf.py"
    pattern_file.parent.mkdir(parents=True)
    pattern_file.write_text("# stub pattern\n")

    prompt_file = tmp_path / "docs" / "superpowers" / "specs" / "ml" / "refinement-agent-prompt.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("# stub prompt\n")

    ctx = cli.gather_context(
        pattern_name=pattern_name,
        logs_root=tmp_path / "logs" / "ml",
        patterns_root=tmp_path / "src" / "ml" / "patterns",
        prompt_template=prompt_file,
    )
    assert ctx["report_path"].exists()
    assert ctx["pattern_source_path"].exists()
    assert ctx["prompt_template_path"].exists()
    assert ctx["run_id"] == run_id


def test_gather_context_picks_latest_run(tmp_path):
    pattern_name = "rsi_divergence"
    base = tmp_path / "logs" / "ml" / pattern_name
    base.mkdir(parents=True)
    (base / "2026-04-01_001").mkdir()
    (base / "2026-04-01_001" / "report.json").write_text("{}")
    (base / "2026-04-13_002").mkdir()
    (base / "2026-04-13_002" / "report.json").write_text("{}")

    patterns_root = tmp_path / "src" / "ml" / "patterns"
    patterns_root.mkdir(parents=True)
    (patterns_root / f"{pattern_name}.py").write_text("# stub\n")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("x")

    ctx = cli.gather_context(
        pattern_name=pattern_name,
        logs_root=tmp_path / "logs" / "ml",
        patterns_root=patterns_root,
        prompt_template=prompt,
    )
    # Latest by sorted name
    assert ctx["run_id"] == "2026-04-13_002"


def test_gather_context_raises_when_no_runs(tmp_path):
    import pytest as _pytest

    patterns_root = tmp_path / "src" / "ml" / "patterns"
    patterns_root.mkdir(parents=True)
    (patterns_root / "engulfing_mtf.py").write_text("x")
    prompt = tmp_path / "p.md"
    prompt.write_text("x")

    with _pytest.raises(FileNotFoundError):
        cli.gather_context(
            pattern_name="engulfing_mtf",
            logs_root=tmp_path / "logs" / "ml",
            patterns_root=patterns_root,
            prompt_template=prompt,
        )
