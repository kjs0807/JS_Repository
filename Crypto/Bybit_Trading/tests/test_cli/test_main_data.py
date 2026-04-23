"""main_data.py CLI 테스트."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from unittest.mock import patch, MagicMock


class TestMainDataCLI:
    def test_collect_command_parsed(self):
        from main_data import build_parser
        args = build_parser().parse_args(["collect", "--symbols", "BTCUSDT", "ETHUSDT", "--tf", "1h", "--days", "30"])
        assert args.command == "collect" and args.symbols == ["BTCUSDT", "ETHUSDT"] and args.days == 30

    def test_fillgaps_command_parsed(self):
        from main_data import build_parser
        args = build_parser().parse_args(["fill-gaps", "--tf", "1h"])
        assert args.command == "fill-gaps" and args.tf == "1h"

    def test_universe_command_parsed(self):
        from main_data import build_parser
        args = build_parser().parse_args(["universe", "--top", "50"])
        assert args.command == "universe" and args.top == 50

    def test_info_command_parsed(self):
        from main_data import build_parser
        args = build_parser().parse_args(["info", "--symbol", "BTCUSDT", "--tf", "1h"])
        assert args.command == "info" and args.symbol == "BTCUSDT"

    def test_cmd_info_runs(self):
        from main_data import cmd_info
        import argparse
        mock_db = MagicMock()
        mock_db.get_bar_count.return_value = 1000
        mock_db.get_bar_range.return_value = (1700000000000, 1700100000000)
        args = argparse.Namespace(symbol="BTCUSDT", tf="1h")
        cmd_info(args, mock_db)  # no error
