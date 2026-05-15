"""core/config.py 단위 테스트."""
import pytest
import yaml
from src.core.config import (
    AppConfig, AppSettings, BacktestConfig, RiskConfig,
    DataConfig, AlertConfig, load_config,
)


class TestAppSettings:
    def test_defaults(self):
        s = AppSettings()
        assert s.base_url == "https://api-demo.bybit.com"
        assert s.leverage == 3
        assert s.mode == "demo"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "test_key_123")
        monkeypatch.setenv("BYBIT_API_SECRET", "test_secret_456")
        s = AppSettings()
        assert s.api_key == "test_key_123"
        assert s.api_secret == "test_secret_456"

    def test_api_key_empty_when_no_env(self, monkeypatch):
        monkeypatch.delenv("BYBIT_API_KEY", raising=False)
        monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
        s = AppSettings()
        assert s.api_key == ""
        assert s.api_secret == ""


class TestBacktestConfig:
    def test_defaults(self):
        c = BacktestConfig()
        assert c.initial_capital == 50000.0
        assert c.taker_fee_pct == 0.00055


class TestRiskConfig:
    def test_defaults(self):
        r = RiskConfig()
        assert r.max_position_pct == 0.05
        assert r.max_drawdown_pct == 0.15


class TestDataConfig:
    def test_defaults(self):
        d = DataConfig()
        assert d.universe_size == 30
        assert "DOGEUSDT" in d.meme_blacklist
        assert "1h" in d.default_timeframes


class TestAlertConfig:
    def test_defaults(self):
        a = AlertConfig()
        assert a.telegram_enabled is False
        assert a.alert_on_trade is True


class TestLoadConfig:
    def test_load_default_config(self, tmp_path):
        config = load_config(config_path=str(tmp_path / "nonexistent.yaml"))
        assert isinstance(config, AppConfig)
        assert config.app.leverage == 3

    def test_load_from_yaml(self, tmp_path):
        yaml_content = {
            "app": {"leverage": 5, "mode": "live"},
            "backtest": {"initial_capital": 100000.0},
            "risk": {"max_drawdown_pct": 0.20},
            "data": {"universe_size": 50},
            "alert": {"telegram_enabled": True},
        }
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        config = load_config(config_path=str(yaml_path))
        assert config.app.leverage == 5
        assert config.backtest.initial_capital == 100000.0
        assert config.risk.max_drawdown_pct == 0.20

    def test_partial_yaml_uses_defaults(self, tmp_path):
        yaml_content = {"app": {"leverage": 10}}
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        config = load_config(config_path=str(yaml_path))
        assert config.app.leverage == 10
        assert config.backtest.initial_capital == 50000.0

    def test_env_overrides_telegram(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat456")
        config = load_config(config_path=str(tmp_path / "nonexistent.yaml"))
        assert config.alert.telegram_token == "bot123"
        assert config.alert.telegram_chat_id == "chat456"

    # ── Stage A: base_url is derived from app.mode (single source of truth) ──
    def test_base_url_derived_for_demo(self, tmp_path):
        import yaml as _yaml
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(_yaml.dump({"app": {"mode": "demo"}}), encoding="utf-8")
        cfg = load_config(config_path=str(yaml_path))
        assert cfg.app.mode == "demo"
        assert cfg.app.base_url == "https://api-demo.bybit.com"

    def test_base_url_derived_for_live(self, tmp_path):
        import yaml as _yaml
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(_yaml.dump({"app": {"mode": "live"}}), encoding="utf-8")
        cfg = load_config(config_path=str(yaml_path))
        assert cfg.app.mode == "live"
        assert cfg.app.base_url == "https://api.bybit.com"

    def test_explicit_yaml_base_url_is_overridden_with_warning(
        self, tmp_path, caplog,
    ):
        import logging as _logging
        import yaml as _yaml
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            _yaml.dump({"app": {"mode": "demo", "base_url": "https://example.com"}}),
            encoding="utf-8",
        )
        with caplog.at_level(_logging.WARNING, logger="src.core.config"):
            cfg = load_config(config_path=str(yaml_path))
        assert cfg.app.base_url == "https://api-demo.bybit.com"
        assert any("overridden" in r.message.lower() for r in caplog.records)

    def test_invalid_mode_fails_fast(self, tmp_path):
        """Stage A-hardening: typo in app.mode must raise, NOT silently fallback.

        Previously load_config logged ERROR and fell back to demo. That made
        a typo like ``mode: liev`` silently route to demo on a machine the
        operator thought was configured for live (or vice versa). Now we
        raise ``ModeError`` so the runner cannot start at all.
        """
        import yaml as _yaml
        import pytest as _pytest
        from src.core.mode import ModeError
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            _yaml.dump({"app": {"mode": "paper"}}), encoding="utf-8",
        )
        with _pytest.raises(ModeError) as excinfo:
            load_config(config_path=str(yaml_path))
        assert "paper" in str(excinfo.value)


# ── Round 5: BBKCExitConfig + BBKC_EXIT_MODE env override (§7.1) ──────────


from src.core.config import BBKCExitConfig


class TestBBKCExitConfig:
    def test_defaults_match_round4_winner(self):
        c = BBKCExitConfig()
        assert c.mode == "be_trail"
        assert c.trail_be_at_tp_frac == 0.25
        assert c.trail_start_at_tp_frac == 0.60
        assert c.trail_distance_tp_frac == 0.30
        assert c.drop_tp is False
        assert c.time_stop_bars == 0

    def test_loaded_from_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BBKC_EXIT_MODE", raising=False)
        yaml_content = """
app:
  mode: demo
bbkc_exit:
  mode: be_trail
  trail_be_at_tp_frac: 0.25
  trail_start_at_tp_frac: 0.60
  trail_distance_tp_frac: 0.30
  drop_tp: false
  time_stop_bars: 0
"""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_config(str(yaml_path))
        assert isinstance(cfg.bbkc_exit, BBKCExitConfig)
        assert cfg.bbkc_exit.mode == "be_trail"
        assert cfg.bbkc_exit.trail_be_at_tp_frac == 0.25
        assert cfg.bbkc_exit.trail_start_at_tp_frac == 0.60
        assert cfg.bbkc_exit.trail_distance_tp_frac == 0.30
        assert cfg.bbkc_exit.drop_tp is False
        assert cfg.bbkc_exit.time_stop_bars == 0

    def test_env_override_to_fixed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BBKC_EXIT_MODE", "fixed")
        yaml_content = """
app:
  mode: demo
bbkc_exit:
  mode: be_trail
  trail_be_at_tp_frac: 0.25
  trail_start_at_tp_frac: 0.60
  trail_distance_tp_frac: 0.30
  drop_tp: false
  time_stop_bars: 0
"""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        cfg = load_config(str(yaml_path))
        assert cfg.bbkc_exit.mode == "fixed"

    def test_defaults_when_yaml_missing_block(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BBKC_EXIT_MODE", raising=False)
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("app:\n  mode: demo\n", encoding="utf-8")
        cfg = load_config(str(yaml_path))
        assert cfg.bbkc_exit.mode == "be_trail"
        assert cfg.bbkc_exit.trail_be_at_tp_frac == 0.25
        assert cfg.bbkc_exit.trail_start_at_tp_frac == 0.60
        assert cfg.bbkc_exit.trail_distance_tp_frac == 0.30
