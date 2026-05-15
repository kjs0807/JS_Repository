"""Stage A-2: generic strategy runner CLI tests.

Exercises the parts that don't require Bybit network calls:

  * Strategy resolution (exact name + slug alias)
  * Param assembly (new ``strategies.<Name>.params`` vs legacy ``bbkc_exit``)
  * argparse parsing for the new CLI surface
  * The wrapper script translates legacy args correctly
  * StrategyTradeRunner refuses to construct on bad input

End-to-end runs that hit the Bybit demo endpoint are covered by the
manual smoke procedure in the Stage A-2 final report.
"""
from __future__ import annotations

import argparse
import logging

import pytest

from src.core.config import AppConfig, BBKCExitConfig, TradingConfig
from src.strategies.registry_builder import build_strategy_registry
from scripts.run_strategy_trade import (
    _strategy_params_from_config,
    build_parser,
    build_strategy_factory,
    resolve_strategy_name,
)


# ---------------------------------------------------------------------------
# resolve_strategy_name
# ---------------------------------------------------------------------------
class TestResolveStrategyName:
    def setup_method(self) -> None:
        self.registry = build_strategy_registry()

    def test_exact_name_bbkc(self):
        assert resolve_strategy_name("BBKCSqueeze", self.registry) == "BBKCSqueeze"

    def test_exact_name_donchian(self):
        # The registry key is Donchian_FixedRR (with the embedded underscore
        # from the class's ``name`` attribute), not DonchianFixedRR.
        assert resolve_strategy_name(
            "Donchian_FixedRR", self.registry,
        ) == "Donchian_FixedRR"

    def test_slug_bbkc(self):
        # slug forms map back to BBKCSqueeze.
        assert resolve_strategy_name("bbkc_squeeze", self.registry) == "BBKCSqueeze"
        assert resolve_strategy_name("BBKC-SQUEEZE", self.registry) == "BBKCSqueeze"
        assert resolve_strategy_name("bbkcsqueeze", self.registry) == "BBKCSqueeze"

    def test_slug_donchian_fixed_rr(self):
        assert resolve_strategy_name(
            "donchian_fixed_rr", self.registry,
        ) == "Donchian_FixedRR"
        assert resolve_strategy_name(
            "donchianfixedrr", self.registry,
        ) == "Donchian_FixedRR"

    def test_unknown_raises_with_available_list(self):
        with pytest.raises(KeyError) as excinfo:
            resolve_strategy_name("NotAStrategy", self.registry)
        msg = str(excinfo.value)
        assert "NotAStrategy" in msg
        assert "BBKCSqueeze" in msg


# ---------------------------------------------------------------------------
# _strategy_params_from_config -BBKC adapter + generic path
# ---------------------------------------------------------------------------
class TestStrategyParamsFromConfig:
    def _cfg(self) -> AppConfig:
        cfg = AppConfig()
        cfg.bbkc_exit = BBKCExitConfig()
        cfg.trading = TradingConfig()
        cfg.strategies = {}
        return cfg

    def test_new_path_overrides_legacy(self):
        """When strategies.BBKCSqueeze.params is present, it wins."""
        cfg = self._cfg()
        cfg.strategies = {
            "BBKCSqueeze": {"params": {
                "exit_mode": "fixed",
                "drop_tp": True,
                "time_stop_bars": 24,
            }}
        }
        # Legacy bbkc_exit still says be_trail / drop_tp=False - it must be ignored.
        cfg.bbkc_exit.mode = "be_trail"
        cfg.bbkc_exit.drop_tp = False
        params = _strategy_params_from_config("BBKCSqueeze", cfg)
        assert params["exit_mode"] == "fixed"
        assert params["drop_tp"] is True
        assert params["time_stop_bars"] == 24

    def test_legacy_bbkc_exit_fallback(self):
        """No strategies.BBKCSqueeze block -> derive params from bbkc_exit."""
        cfg = self._cfg()
        cfg.bbkc_exit.mode = "be_trail"
        cfg.bbkc_exit.trail_be_at_tp_frac = 0.25
        cfg.bbkc_exit.trail_start_at_tp_frac = 0.60
        cfg.bbkc_exit.trail_distance_tp_frac = 0.30
        params = _strategy_params_from_config("BBKCSqueeze", cfg)
        assert params["exit_mode"] == "be_trail"
        assert params["trail_be_at_tp_frac"] == 0.25
        assert params["trail_start_at_tp_frac"] == 0.60
        assert params["trail_distance_tp_frac"] == 0.30

    def test_generic_strategy_no_block_returns_empty(self):
        cfg = self._cfg()
        # Other strategies have no legacy fallback - empty dict is fine
        # because the registry will use __init__ defaults.
        assert _strategy_params_from_config("Donchian_FixedRR", cfg) == {}

    def test_generic_strategy_with_block(self):
        cfg = self._cfg()
        cfg.strategies = {
            "Donchian_FixedRR": {"params": {
                "entry_period": 20,
                "stop_atr": 2.5,
                "tp_r_ratio": 2.0,
            }}
        }
        params = _strategy_params_from_config("Donchian_FixedRR", cfg)
        assert params == {
            "entry_period": 20, "stop_atr": 2.5, "tp_r_ratio": 2.0,
        }


# ---------------------------------------------------------------------------
# build_strategy_factory -produces parameterised instances
# ---------------------------------------------------------------------------
class TestBuildStrategyFactory:
    def test_bbkc_factory_returns_instance(self):
        registry = build_strategy_registry()
        factory = build_strategy_factory(
            "BBKCSqueeze", registry,
            {"exit_mode": "fixed", "drop_tp": True},
        )
        strat = factory()
        # set_params applied:
        assert strat.exit_mode == "fixed"
        assert strat.drop_tp is True
        # factory gives a fresh instance each call:
        assert factory() is not strat

    def test_donchian_factory(self):
        registry = build_strategy_registry()
        factory = build_strategy_factory("Donchian_FixedRR", registry)
        strat = factory()
        assert strat.__class__.__name__ == "DonchianFixedRR"


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------
class TestCliParser:
    def test_required_run_id(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["--run-id", "x"])
        assert args.strategy is None
        assert args.universe is None
        assert args.timeframe is None
        assert args.mode is None
        assert args.i_understand_real_money is False
        assert args.warmup_days == 14

    def test_strategy_and_universe(self):
        parser = build_parser()
        args = parser.parse_args([
            "--run-id", "x", "--strategy", "bbkc_squeeze",
            "--universe", "BTCUSDT", "ETHUSDT",
            "--timeframe", "4h",
        ])
        assert args.strategy == "bbkc_squeeze"
        assert args.universe == ["BTCUSDT", "ETHUSDT"]
        assert args.timeframe == "4h"

    def test_invalid_mode_rejected_by_argparse(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--run-id", "x", "--mode", "paper"])


# ---------------------------------------------------------------------------
# Wrapper CLI compat
# ---------------------------------------------------------------------------
class TestBbkcWrapper:
    def test_wrapper_translates_args(self, monkeypatch):
        """The wrapper must forward to run_strategy_trade.main with the
        BBKC defaults baked in. We mock the generic main and capture the
        forwarded argv."""
        captured: dict = {}

        def fake_main(argv):
            captured["argv"] = list(argv)
            return 0

        from scripts import run_bbkc_live_trade as wrapper
        # Patch the lazy import inside main():
        import scripts.run_strategy_trade as gen
        monkeypatch.setattr(gen, "main", fake_main)
        # Also reload-safe import path that wrapper uses:
        monkeypatch.setenv("BBKC_ROUND5_MODE", "")
        rc = wrapper.main(["--run-id", "wrap_smoke"])
        assert rc == 0
        argv = captured["argv"]
        # Must inject BBKC defaults:
        assert "--strategy" in argv
        assert argv[argv.index("--strategy") + 1] == "BBKCSqueeze"
        assert "--universe" in argv
        # BIGTHREE legacy universe present (order locked):
        i = argv.index("--universe")
        assert argv[i + 1: i + 4] == ["BTCUSDT", "ETHUSDT", "AVAXUSDT"]
        assert "--timeframe" in argv
        assert argv[argv.index("--timeframe") + 1] == "1h"
        assert "--run-id" in argv
        assert argv[argv.index("--run-id") + 1] == "wrap_smoke"

    def test_wrapper_forwards_mode_and_ack(self, monkeypatch):
        captured: dict = {}

        def fake_main(argv):
            captured["argv"] = list(argv)
            return 0

        from scripts import run_bbkc_live_trade as wrapper
        import scripts.run_strategy_trade as gen
        monkeypatch.setattr(gen, "main", fake_main)
        monkeypatch.setenv("BBKC_ROUND5_MODE", "")
        rc = wrapper.main([
            "--run-id", "wrap_live", "--mode", "live",
            "--i-understand-real-money",
        ])
        assert rc == 0
        argv = captured["argv"]
        assert "--mode" in argv
        assert argv[argv.index("--mode") + 1] == "live"
        assert "--i-understand-real-money" in argv

    def test_round5_guard_blocks_stop_at(self, monkeypatch, capsys):
        from scripts import run_bbkc_live_trade as wrapper
        monkeypatch.setenv("BBKC_ROUND5_MODE", "true")
        rc = wrapper.main([
            "--run-id", "rd5", "--stop-at", "2026-12-31",
        ])
        assert rc == 2
        out = capsys.readouterr().out
        assert "BBKC_ROUND5_MODE" in out


# ---------------------------------------------------------------------------
# Structural: strategy code does not branch on demo/live
# ---------------------------------------------------------------------------
class TestStrategyHasNoModeBranching:
    """Spot-check that strategy modules do not import or branch on
    mode/base_url/credentials. The runtime layer is the only place that
    is allowed to see those concepts. This is a heuristic guard - it
    grep-checks for the obvious red flags."""

    FORBIDDEN_TOKENS = (
        "api-demo.bybit.com",
        "BYBIT_API_KEY",
        "BYBIT_DEMO_API_KEY",
        "BYBIT_LIVE_API_KEY",
        "resolve_runtime",
        "from src.core.mode",
    )

    def test_bbkc_squeeze_clean(self):
        text = self._read("src/strategies/bbkc_squeeze.py")
        self._assert_clean("BBKCSqueeze", text)

    def test_donchian_fixed_rr_clean(self):
        text = self._read("src/strategies/donchian_fixed_rr.py")
        self._assert_clean("DonchianFixedRR", text)

    def _read(self, rel: str) -> str:
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent.parent / rel
        return p.read_text(encoding="utf-8")

    def _assert_clean(self, name: str, text: str) -> None:
        offenders = [tok for tok in self.FORBIDDEN_TOKENS if tok in text]
        assert not offenders, (
            f"{name} strategy module references runtime-layer tokens "
            f"{offenders}; mode/credentials must stay in the runtime layer."
        )
