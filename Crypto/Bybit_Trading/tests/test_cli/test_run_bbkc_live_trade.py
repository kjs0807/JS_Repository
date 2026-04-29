"""run_bbkc_live_trade BBKC_ROUND5_MODE guard (round 5 §2.3, §3 IN #8)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _run_script(args, env_extra=None):
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "scripts.run_bbkc_live_trade", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True, text=True, timeout=20,
    )


def test_round5_mode_rejects_stop_at():
    res = _run_script(
        ["--run-id", "test_guard", "--stop-at", "2026-12-31"],
        env_extra={"BBKC_ROUND5_MODE": "true"},
    )
    assert res.returncode != 0
    out = (res.stderr + res.stdout).lower()
    assert "stop-at" in out or "round5" in out or "bbkc_round5_mode" in out


def test_round5_mode_rejects_stop_in_minutes():
    res = _run_script(
        ["--run-id", "test_guard", "--stop-in-minutes", "5"],
        env_extra={"BBKC_ROUND5_MODE": "true"},
    )
    assert res.returncode != 0
    out = (res.stderr + res.stdout).lower()
    assert "stop-in-minutes" in out or "round5" in out or "bbkc_round5_mode" in out


def test_round5_mode_off_allows_stop_in_minutes_smoke():
    """Smoke: 가드 미설정 시 --stop-in-minutes 통과 (단순 인자 파싱 단계만 확인).

    실제 sweep 실행은 timeout으로 정지될 수 있어서, 매우 짧은 stop으로 빠르게 종료.
    """
    res = _run_script(
        ["--run-id", "smoke_test", "--stop-in-minutes", "0"],
        env_extra={"BBKC_ROUND5_MODE": "false"},
    )
    # parsing OK이면 returncode==0 또는 기능적 실패(API key 없음 등)는 OK.
    # ValueError로 인한 가드 실패만 거부.
    out = (res.stderr + res.stdout).lower()
    assert "bbkc_round5_mode" not in out, "guard should not fire when off"
