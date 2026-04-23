"""main_research.py CLI 테스트."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


class TestMainResearchCLI:
    def test_backtest_command(self):
        from main_research import build_parser
        args = build_parser().parse_args(["backtest", "--strategy", "BBKCSqueeze", "--symbol", "BTCUSDT", "--tf", "1h"])
        assert args.command == "backtest" and args.strategy == "BBKCSqueeze"

    def test_explore_command(self):
        from main_research import build_parser
        args = build_parser().parse_args(["explore", "--tf", "1h", "4h", "--universe", "top30"])
        assert args.command == "explore" and args.tf == ["1h", "4h"]

    def test_walkforward_command(self):
        from main_research import build_parser
        args = build_parser().parse_args(["walkforward", "--strategy", "X", "--symbol", "BTCUSDT"])
        assert args.command == "walkforward"

    def test_compare_command(self):
        from main_research import build_parser
        args = build_parser().parse_args(["compare", "--strategies", "A", "B", "C"])
        assert args.command == "compare" and args.strategies == ["A", "B", "C"]
