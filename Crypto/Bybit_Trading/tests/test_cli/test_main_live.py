"""main_live.py CLI 테스트."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


class TestMainLiveCLI:
    def test_start_command(self):
        from main_live import build_parser
        args = build_parser().parse_args(["start", "--strategies", "BBKCSqueeze", "--mode", "demo"])
        assert args.command == "start" and args.strategies == ["BBKCSqueeze"] and args.mode == "demo"

    def test_start_live_mode(self):
        from main_live import build_parser
        args = build_parser().parse_args(["start", "--strategies", "A", "B", "--mode", "live"])
        assert args.mode == "live"

    def test_status_command(self):
        from main_live import build_parser
        assert build_parser().parse_args(["status"]).command == "status"

    def test_stop_command(self):
        from main_live import build_parser
        assert build_parser().parse_args(["stop"]).command == "stop"

    def test_default_mode_demo(self):
        from main_live import build_parser
        assert build_parser().parse_args(["start", "--strategies", "X"]).mode == "demo"
