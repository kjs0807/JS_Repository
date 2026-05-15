"""src/core/mode.py 단위 테스트.

Covers the six scenarios mandated by the Stage A spec:
  1. mode=demo  → demo base_url + demo keys selected
  2. mode=live without ack → refused
  3. mode=live with ack → live base_url + live keys selected
  4. CLI --mode overrides config app.mode (priority)
  5. fingerprint never leaks the full secret
  6. legacy BYBIT_API_KEY pair is rejected in EVERY mode (Stage C-2c
     removed the demo fallback; every non-BBKC script now uses
     :func:`src.core.mode.resolve_runtime`).
"""
from __future__ import annotations

import logging

import pytest

from src.core import mode as M


# ---------------------------------------------------------------------------
# resolve_mode
# ---------------------------------------------------------------------------

class TestResolveMode:
    def test_default_is_demo(self):
        assert M.resolve_mode(None, None) == M.MODE_DEMO

    def test_config_only_demo(self):
        assert M.resolve_mode("demo", None) == M.MODE_DEMO

    def test_config_only_live(self):
        assert M.resolve_mode("live", None) == M.MODE_LIVE

    def test_cli_overrides_config_to_live(self):
        assert M.resolve_mode("demo", "live") == M.MODE_LIVE

    def test_cli_overrides_config_to_demo(self):
        assert M.resolve_mode("live", "demo") == M.MODE_DEMO

    def test_case_insensitive(self):
        assert M.resolve_mode("LIVE", None) == M.MODE_LIVE
        assert M.resolve_mode(None, "  Demo  ") == M.MODE_DEMO

    @pytest.mark.parametrize("bad", ["paper", "production", "DEMO!"])
    def test_invalid_mode_raises(self, bad):
        with pytest.raises(M.ModeError):
            M.resolve_mode(bad, None)

    def test_empty_string_treated_as_unset_and_defaults_to_demo(self):
        # Empty/None both mean "use default"; only non-empty invalid values raise.
        assert M.resolve_mode("", None) == M.MODE_DEMO
        assert M.resolve_mode(None, "") == M.MODE_DEMO


# ---------------------------------------------------------------------------
# base_url_for
# ---------------------------------------------------------------------------

class TestBaseUrl:
    def test_demo_url(self):
        assert M.base_url_for(M.MODE_DEMO) == "https://api-demo.bybit.com"

    def test_live_url(self):
        assert M.base_url_for(M.MODE_LIVE) == "https://api.bybit.com"

    def test_case_insensitive(self):
        assert M.base_url_for("LIVE") == "https://api.bybit.com"

    def test_invalid_raises(self):
        with pytest.raises(M.ModeError):
            M.base_url_for("paper")


# ---------------------------------------------------------------------------
# resolve_api_credentials
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET",
        "BYBIT_LIVE_API_KEY", "BYBIT_LIVE_API_SECRET",
        "BYBIT_API_KEY", "BYBIT_API_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)


class TestResolveApiCredentials:
    def test_demo_prefix(self, monkeypatch, clean_env):
        monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo_k")
        monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo_s")
        assert M.resolve_api_credentials(M.MODE_DEMO) == ("demo_k", "demo_s")

    def test_live_prefix(self, monkeypatch, clean_env):
        monkeypatch.setenv("BYBIT_LIVE_API_KEY", "live_k")
        monkeypatch.setenv("BYBIT_LIVE_API_SECRET", "live_s")
        assert M.resolve_api_credentials(M.MODE_LIVE) == ("live_k", "live_s")

    def test_demo_does_not_use_live_keys(self, monkeypatch, clean_env):
        # Only live keys are set; demo lookup must NOT pick them up.
        monkeypatch.setenv("BYBIT_LIVE_API_KEY", "live_k")
        monkeypatch.setenv("BYBIT_LIVE_API_SECRET", "live_s")
        assert M.resolve_api_credentials(M.MODE_DEMO) == ("", "")

    def test_live_does_not_use_demo_keys(self, monkeypatch, clean_env):
        monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo_k")
        monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo_s")
        assert M.resolve_api_credentials(M.MODE_LIVE) == ("", "")

    def test_legacy_pair_rejected_in_demo(self, monkeypatch, clean_env, caplog):
        """Stage C-2c: the legacy ``BYBIT_API_KEY`` / ``BYBIT_API_SECRET``
        pair is no longer honoured in *any* mode. With ONLY the legacy
        pair set, demo resolution returns ``("", "")`` so the runner
        will refuse to start with a clear error."""
        monkeypatch.setenv("BYBIT_API_KEY", "legacy_k")
        monkeypatch.setenv("BYBIT_API_SECRET", "legacy_s")
        with caplog.at_level(logging.WARNING, logger="src.core.mode"):
            key, secret = M.resolve_api_credentials(M.MODE_DEMO)
        assert (key, secret) == ("", "")
        # No deprecation warning either — the fallback is removed, not
        # warned about. Quiet rejection is the correct shape.
        assert not any("legacy" in r.message.lower() for r in caplog.records)

    def test_legacy_pair_rejected_in_live(self, monkeypatch, clean_env):
        """Live still refuses the legacy pair (unchanged from Stage A
        hardening; codified here so a regression cannot silently
        re-enable the fallback)."""
        monkeypatch.setenv("BYBIT_API_KEY", "legacy_k")
        monkeypatch.setenv("BYBIT_API_SECRET", "legacy_s")
        assert M.resolve_api_credentials(M.MODE_LIVE) == ("", "")

    def test_prefixed_pair_ignores_legacy_in_demo(self, monkeypatch, clean_env):
        # Both set → the prefixed pair is used, legacy is ignored.
        monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo_k")
        monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo_s")
        monkeypatch.setenv("BYBIT_API_KEY", "legacy_k")
        monkeypatch.setenv("BYBIT_API_SECRET", "legacy_s")
        assert M.resolve_api_credentials(M.MODE_DEMO) == ("demo_k", "demo_s")

    def test_missing_returns_empty(self, clean_env):
        assert M.resolve_api_credentials(M.MODE_DEMO) == ("", "")


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_redacts_middle(self):
        assert M.fingerprint("ABCDEFGHIJKL") == "ABCD...IJKL"

    def test_short_inputs_render_stars(self):
        assert M.fingerprint("ABCD") == "***"
        assert M.fingerprint("ABCDEFGH") == "***"

    def test_empty_renders_placeholder(self):
        assert M.fingerprint("") == "(empty)"
        assert M.fingerprint(None) == "(empty)"

    def test_never_returns_full_secret(self):
        secret = "very_long_secret_value_1234567890_abcdef"
        fp = M.fingerprint(secret)
        assert secret not in fp
        # also guarantee bounded length
        assert len(fp) <= 11  # head(4) + "..." + tail(4) = 11

    def test_custom_head_tail(self):
        assert M.fingerprint("ABCDEFGHIJ", head=2, tail=2) == "AB...IJ"


# ---------------------------------------------------------------------------
# assert_live_acknowledged
# ---------------------------------------------------------------------------

class TestLiveAck:
    def test_demo_no_ack_required(self):
        M.assert_live_acknowledged(M.MODE_DEMO, ack=False)   # must not raise

    def test_demo_with_ack_is_a_no_op(self):
        M.assert_live_acknowledged(M.MODE_DEMO, ack=True)    # must not raise

    def test_live_without_ack_raises(self):
        with pytest.raises(M.ModeError):
            M.assert_live_acknowledged(M.MODE_LIVE, ack=False)

    def test_live_with_ack_ok(self):
        M.assert_live_acknowledged(M.MODE_LIVE, ack=True)

    def test_error_message_mentions_flag(self):
        with pytest.raises(M.ModeError) as excinfo:
            M.assert_live_acknowledged(M.MODE_LIVE, ack=False)
        assert M.LIVE_ACK_FLAG in str(excinfo.value)


# ---------------------------------------------------------------------------
# live_startup_banner
# ---------------------------------------------------------------------------

class TestLiveBanner:
    def test_contains_mode_and_warning(self):
        b = M.live_startup_banner(
            mode=M.MODE_LIVE, base_url="https://api.bybit.com",
            universe=["BTCUSDT", "ETHUSDT"], leverage=3, equity=15000.0,
            api_key_fingerprint="ABCD...WXYZ",
            estimated_max_notional=9000.0,
        )
        assert "LIVE" in b
        assert "REAL MONEY" in b
        assert "ABCD...WXYZ" in b
        assert "15,000" in b
        assert "https://api.bybit.com" in b
        assert "BTCUSDT" in b and "ETHUSDT" in b
        assert "9,000" in b

    def test_demo_banner_no_real_money_marker(self):
        b = M.live_startup_banner(
            mode=M.MODE_DEMO, base_url="https://api-demo.bybit.com",
            universe=["BTCUSDT"], leverage=3, equity=50000.0,
            api_key_fingerprint="abcd...wxyz",
        )
        assert "REAL MONEY" not in b


# ---------------------------------------------------------------------------
# resolve_runtime -the one-stop helper the runner uses
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_keys(monkeypatch, clean_env):
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo_k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo_s")
    monkeypatch.setenv("BYBIT_LIVE_API_KEY", "live_k")
    monkeypatch.setenv("BYBIT_LIVE_API_SECRET", "live_s")


class TestResolveRuntime:
    def test_demo_no_ack_ok(self, setup_keys):
        mode, url, k, s = M.resolve_runtime("demo", None, ack=False)
        assert mode == "demo"
        assert url == "https://api-demo.bybit.com"
        assert (k, s) == ("demo_k", "demo_s")

    def test_live_without_ack_refused(self, setup_keys):
        with pytest.raises(M.ModeError):
            M.resolve_runtime("live", None, ack=False)

    def test_live_with_ack_ok(self, setup_keys):
        mode, url, k, s = M.resolve_runtime("live", None, ack=True)
        assert mode == "live"
        assert url == "https://api.bybit.com"
        assert (k, s) == ("live_k", "live_s")

    def test_cli_mode_overrides_config(self, setup_keys):
        # config=demo, cli=live → live (still needs ack)
        mode, _, k, _ = M.resolve_runtime("demo", "live", ack=True)
        assert mode == "live" and k == "live_k"
        # config=live, cli=demo → demo (no ack needed)
        mode, _, k, _ = M.resolve_runtime("live", "demo", ack=False)
        assert mode == "demo" and k == "demo_k"

    def test_missing_credentials_for_mode_raises(self, monkeypatch, clean_env):
        # No env keys at all.
        with pytest.raises(M.ModeError) as excinfo:
            M.resolve_runtime("demo", None, ack=False)
        assert "credentials" in str(excinfo.value).lower()

    def test_force_live_deprecated_implies_live_and_warns(
        self, setup_keys, caplog,
    ):
        with caplog.at_level(logging.WARNING, logger="src.core.mode"):
            # Without ack, even force_live should be refused.
            with pytest.raises(M.ModeError):
                M.resolve_runtime("demo", None, ack=False, force_live_deprecated=True)
        assert any(
            "deprecated" in r.message.lower() for r in caplog.records
        )

    def test_force_live_with_ack_works(self, setup_keys):
        mode, url, k, _ = M.resolve_runtime(
            "demo", None, ack=True, force_live_deprecated=True,
        )
        assert mode == "live"
        assert url == "https://api.bybit.com"
        assert k == "live_k"

    def test_force_live_with_explicit_cli_demo_raises_conflict(self, setup_keys):
        """Stage A-hardening: combining --force-live with --mode demo is a
        contradiction (force-live implies live). Refuse rather than guess."""
        with pytest.raises(M.ModeError) as excinfo:
            M.resolve_runtime(
                "demo", "demo", ack=False, force_live_deprecated=True,
            )
        assert "conflict" in str(excinfo.value).lower()

    def test_live_legacy_credentials_rejected_with_ack(
        self, monkeypatch, clean_env,
    ):
        """Stage A-hardening + C-2c: live + ack + legacy-only keys must
        STILL fail. C-2c removed the demo fallback too, but this guard
        specifically asserts that operator acknowledgement does not
        magically promote a legacy pair into a valid live credential."""
        monkeypatch.setenv("BYBIT_API_KEY", "legacy_k")
        monkeypatch.setenv("BYBIT_API_SECRET", "legacy_s")
        # No BYBIT_LIVE_* set.
        with pytest.raises(M.ModeError) as excinfo:
            M.resolve_runtime("live", None, ack=True)
        assert "live" in str(excinfo.value).lower()


class TestWsUrlFor:
    def test_demo(self):
        assert M.ws_url_for(M.MODE_DEMO).startswith("wss://stream.bybit.com")

    def test_live(self):
        assert M.ws_url_for(M.MODE_LIVE).startswith("wss://stream.bybit.com")

    def test_documented_intent_same_url_for_both(self):
        # Bybit demo's public kline feed lives on the mainnet stream URL;
        # this test pins the documented intent so a future change becomes
        # an explicit decision.
        assert M.ws_url_for(M.MODE_DEMO) == M.ws_url_for(M.MODE_LIVE)

    def test_invalid_mode_raises(self):
        with pytest.raises(M.ModeError):
            M.ws_url_for("paper")
