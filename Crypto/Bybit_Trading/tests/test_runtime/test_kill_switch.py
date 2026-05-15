"""Stage B-3: kill switch resolution (env vars + file flag)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime.kill_switch import KillSwitch, ENV_NAMES, FLAG_FILENAME


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_NAMES:
        monkeypatch.delenv(var, raising=False)


def test_clean_env_no_run_dir_is_off():
    ks = KillSwitch()
    assert ks.is_new_entry_disabled() is False
    assert ks.reason() == ""


def test_legacy_env_engages(monkeypatch):
    monkeypatch.setenv("BBKC_DISABLE_NEW_ENTRY", "true")
    ks = KillSwitch()
    assert ks.is_new_entry_disabled() is True
    assert "BBKC_DISABLE_NEW_ENTRY" in ks.reason()


def test_generic_env_engages(monkeypatch):
    monkeypatch.setenv("STRATEGY_DISABLE_NEW_ENTRY", "1")
    ks = KillSwitch()
    assert ks.is_new_entry_disabled() is True
    assert "STRATEGY_DISABLE_NEW_ENTRY" in ks.reason()


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES", "on"])
def test_truthy_env_values_engage(monkeypatch, value):
    monkeypatch.setenv("STRATEGY_DISABLE_NEW_ENTRY", value)
    assert KillSwitch().is_new_entry_disabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "anything-else"])
def test_falsy_env_values_do_not_engage(monkeypatch, value):
    monkeypatch.setenv("STRATEGY_DISABLE_NEW_ENTRY", value)
    assert KillSwitch().is_new_entry_disabled() is False


def test_file_flag_engages(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ks = KillSwitch(run_dir=run_dir)
    assert ks.is_new_entry_disabled() is False
    (run_dir / FLAG_FILENAME).write_text("ops trip")
    assert ks.is_new_entry_disabled() is True
    assert str(run_dir / FLAG_FILENAME) in ks.reason()


def test_remove_file_flag_disengages(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    flag = run_dir / FLAG_FILENAME
    flag.write_text("")
    ks = KillSwitch(run_dir=run_dir)
    assert ks.is_new_entry_disabled() is True
    flag.unlink()
    assert ks.is_new_entry_disabled() is False


def test_engage_via_file_creates_flag(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ks = KillSwitch(run_dir=run_dir)
    assert not (run_dir / FLAG_FILENAME).exists()
    ks.engage_via_file(message="auto from circuit breaker")
    assert (run_dir / FLAG_FILENAME).exists()
    assert "circuit breaker" in (run_dir / FLAG_FILENAME).read_text(encoding="utf-8")
    assert ks.is_new_entry_disabled() is True


def test_engage_via_file_without_run_dir_logs_but_does_not_crash(caplog):
    ks = KillSwitch()  # no run_dir
    import logging
    with caplog.at_level(logging.ERROR, logger="src.runtime.kill_switch"):
        ks.engage_via_file("ignored")
    assert any("run_dir" in r.message for r in caplog.records)


def test_env_and_file_both_active_is_still_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_DISABLE_NEW_ENTRY", "true")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / FLAG_FILENAME).touch()
    ks = KillSwitch(run_dir=run_dir)
    assert ks.is_new_entry_disabled() is True
    # reason() prefers env first (priority order is implementation detail
    # but must not be ""):
    assert ks.reason() != ""
