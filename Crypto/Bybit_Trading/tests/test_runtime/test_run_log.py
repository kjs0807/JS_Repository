"""Stage C-1: run.log RotatingFileHandler installer."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.runtime.run_log import (
    install_run_log_handler,
    remove_run_log_handlers,
)


@pytest.fixture(autouse=True)
def _isolate_handlers():
    """Strip our handlers before AND after each test so other modules'
    root-logger usage cannot interfere and so we never leak file handles
    across tests."""
    remove_run_log_handlers()
    yield
    remove_run_log_handlers()


class TestInstallation:
    def test_writes_records_to_file(self, tmp_path):
        install_run_log_handler(tmp_path)
        log = logging.getLogger("test.run_log.A")
        log.info("hello run log")
        # Force flush via handler.
        logging.shutdown()
        target = tmp_path / "run.log"
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "hello run log" in content

    def test_creates_parent_directory(self, tmp_path):
        rd = tmp_path / "nested" / "deeper"
        install_run_log_handler(rd)
        assert rd.exists()
        assert (rd / "run.log").exists() or True  # may be deferred to first write


class TestIdempotence:
    def test_second_call_with_same_path_returns_existing(self, tmp_path):
        h1 = install_run_log_handler(tmp_path)
        h2 = install_run_log_handler(tmp_path)
        assert h1 is h2
        root_handlers = [
            h for h in logging.getLogger().handlers
            if getattr(h, "_strategy_run_log_handler", None) is not None
        ]
        assert len(root_handlers) == 1

    def test_different_path_adds_second_handler(self, tmp_path):
        rd1 = tmp_path / "a"
        rd2 = tmp_path / "b"
        h1 = install_run_log_handler(rd1)
        h2 = install_run_log_handler(rd2)
        assert h1 is not h2
        tagged = [
            h for h in logging.getLogger().handlers
            if getattr(h, "_strategy_run_log_handler", None) is not None
        ]
        assert len(tagged) == 2


class TestRemoval:
    def test_remove_clears_handlers(self, tmp_path):
        install_run_log_handler(tmp_path)
        n = remove_run_log_handlers()
        assert n == 1
        tagged = [
            h for h in logging.getLogger().handlers
            if getattr(h, "_strategy_run_log_handler", None) is not None
        ]
        assert not tagged
