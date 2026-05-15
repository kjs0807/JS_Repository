"""Bybit runtime-mode resolution (demo vs live).

Single source of truth for ``demo`` vs ``live`` mode. The trading runner
calls into this module to:

  1. resolve the effective mode from config + CLI override,
  2. derive the Bybit REST/WS endpoints from that mode,
  3. look up the right ``BYBIT_{DEMO,LIVE}_API_KEY`` / ``_API_SECRET``
     pair from the environment,
  4. enforce the ``--i-understand-real-money`` safety gate before any
     mainnet REST call is made.

Stage A-hardening:

  * Live mode REJECTS the legacy ``BYBIT_API_KEY`` / ``BYBIT_API_SECRET``
    fallback. Real-money runs must use the prefixed credentials.
  * Demo mode still accepts the legacy fallback but emits a deprecation
    warning so the operator notices.
  * ``--force-live`` is a deprecated alias for ``--mode live``. Combining
    it with ``--mode demo`` is treated as a contradiction and rejected.
  * Bybit's public kline stream is identical for demo and mainnet (only
    private REST endpoints differ); :func:`ws_url_for` exposes that fact
    explicitly so the runner does not silently couple to it.

Secrets are never returned to logs - callers display
:func:`fingerprint(key)` instead. The :func:`resolve_runtime` helper
bundles every gate into one testable function so the runner has no
demo/live branching logic.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

MODE_DEMO = "demo"
MODE_LIVE = "live"
VALID_MODES: Tuple[str, ...] = (MODE_DEMO, MODE_LIVE)

BASE_URL_BY_MODE = {
    MODE_DEMO: "https://api-demo.bybit.com",
    MODE_LIVE: "https://api.bybit.com",
}

# Bybit's public kline stream is the same firehose for demo and mainnet
# (only private REST endpoints differ). We expose this through a helper
# so the runner does not silently couple to a module-level default and
# so future "use a different WS for demo" decisions are a one-line edit.
WS_URL_BY_MODE = {
    MODE_DEMO: "wss://stream.bybit.com/v5/public/linear",
    MODE_LIVE: "wss://stream.bybit.com/v5/public/linear",
}

LIVE_ACK_FLAG = "--i-understand-real-money"


class ModeError(RuntimeError):
    """Raised when mode resolution / safety gate fails. Callers exit non-zero."""


# ---------------------------------------------------------------------------
# Resolution primitives
# ---------------------------------------------------------------------------

def resolve_mode(config_mode: Optional[str], cli_mode: Optional[str]) -> str:
    """Pick the effective mode. CLI override beats config; falls back to demo.

    ``ModeError`` is raised when the chosen value is not in
    :data:`VALID_MODES`. Comparison is case-insensitive and surrounding
    whitespace is stripped.
    """
    chosen_raw = cli_mode or config_mode or MODE_DEMO
    chosen = str(chosen_raw).lower().strip()
    if chosen not in VALID_MODES:
        raise ModeError(
            f"invalid mode {chosen_raw!r}; must be one of {VALID_MODES}"
        )
    return chosen


def base_url_for(mode: str) -> str:
    """Map a resolved mode to its Bybit REST endpoint. Pure function."""
    key = mode.lower().strip() if isinstance(mode, str) else mode
    if key not in BASE_URL_BY_MODE:
        raise ModeError(f"no base_url defined for mode {mode!r}")
    return BASE_URL_BY_MODE[key]


def ws_url_for(mode: str) -> str:
    """Map a resolved mode to its Bybit WebSocket public-stream endpoint.

    Bybit demo's public kline feed lives on the mainnet stream URL
    because only authenticated REST endpoints differ between demo and
    mainnet. Exposing this as a helper keeps the runner mode-agnostic
    and makes the choice explicit in startup logs.
    """
    key = mode.lower().strip() if isinstance(mode, str) else mode
    if key not in WS_URL_BY_MODE:
        raise ModeError(f"no ws_url defined for mode {mode!r}")
    return WS_URL_BY_MODE[key]


def resolve_api_credentials(mode: str) -> Tuple[str, str]:
    """Look up Bybit API credentials for the given mode from the environment.

    Precedence:

    1. ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` when ``mode=demo``
    2. ``BYBIT_LIVE_API_KEY`` / ``BYBIT_LIVE_API_SECRET`` when ``mode=live``
    3. Legacy ``BYBIT_API_KEY`` / ``BYBIT_API_SECRET`` is accepted ONLY in
       demo mode (with deprecation WARN). Live runs MUST supply the
       prefixed pair - the legacy fallback is rejected to avoid a
       quietly-mismatched key/endpoint combination on real money.

    Returns ``("", "")`` when no credentials are found - the caller
    decides whether that is fatal (it always is for the trading runner).
    """
    if mode == MODE_DEMO:
        prefix = "BYBIT_DEMO_"
    elif mode == MODE_LIVE:
        prefix = "BYBIT_LIVE_"
    else:
        raise ModeError(f"cannot resolve credentials for mode {mode!r}")

    key = os.getenv(f"{prefix}API_KEY", "").strip()
    secret = os.getenv(f"{prefix}API_SECRET", "").strip()
    if key and secret:
        return key, secret

    # Legacy fallback - DEMO ONLY. Real-money runs cannot fall back to
    # an un-prefixed key pair because the operator might have left a
    # demo key in BYBIT_API_KEY and gotten lucky in tests.
    if mode == MODE_LIVE:
        return "", ""

    legacy_key = os.getenv("BYBIT_API_KEY", "").strip()
    legacy_secret = os.getenv("BYBIT_API_SECRET", "").strip()
    if legacy_key and legacy_secret:
        logger.warning(
            "Using legacy BYBIT_API_KEY / BYBIT_API_SECRET for mode=%s. "
            "Migrate to %sAPI_KEY / %sAPI_SECRET in .env "
            "(legacy fallback is allowed only in demo mode).",
            mode, prefix, prefix,
        )
        return legacy_key, legacy_secret
    return "", ""


def fingerprint(value: str, head: int = 4, tail: int = 4) -> str:
    """Render a credential as ``head...tail`` with the middle redacted.

    Never returns more than ``head + tail`` characters of the input;
    short inputs (<= ``head + tail``) render as ``***`` so a near-full
    key cannot be reconstructed from a fingerprint. Empty input renders
    as ``(empty)``.
    """
    if value is None or value == "":
        return "(empty)"
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= head + tail:
        return "***"
    return f"{value[:head]}...{value[-tail:]}"


def assert_live_acknowledged(mode: str, ack: bool) -> None:
    """Enforce that ``mode=live`` requires the explicit ack flag.

    Raises :class:`ModeError` when the runner is in live mode and the
    operator did not pass :data:`LIVE_ACK_FLAG`. Demo mode never
    requires acknowledgement.
    """
    if mode == MODE_LIVE and not ack:
        raise ModeError(
            f"mode=live requires {LIVE_ACK_FLAG} - real-money safety gate."
        )


def live_startup_banner(
    *,
    mode: str,
    base_url: str,
    universe: Iterable[str],
    leverage: int,
    equity: float,
    api_key_fingerprint: str,
    estimated_max_notional: Optional[float] = None,
    extras: Optional[dict] = None,
) -> str:
    """Render the live-mode startup banner. Pure string builder (no IO).

    Contains *no* secret values. The runner prints this text and waits
    5 seconds before any REST/WS connection so the operator can abort.
    """
    sep = "=" * 70
    danger = "  *** REAL MONEY ***" if mode == MODE_LIVE else ""
    lines = [
        sep,
        f"  MODE: {mode.upper()}{danger}",
        f"  base_url      : {base_url}",
        f"  api key       : {api_key_fingerprint}",
        f"  universe      : {list(universe)}",
        f"  leverage      : {leverage}x",
        f"  account equity: {equity:,.2f} USDT",
    ]
    if estimated_max_notional is not None:
        lines.append(
            f"  est. max notional (all positions open): "
            f"{estimated_max_notional:,.2f} USDT"
        )
    if extras:
        for k, v in extras.items():
            lines.append(f"  {k}: {v}")
    lines.append(sep)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# One-stop runtime resolver - used by the runner so it has no branching
# ---------------------------------------------------------------------------

def resolve_runtime(
    config_mode: Optional[str],
    cli_mode: Optional[str],
    ack: bool,
    force_live_deprecated: bool = False,
) -> Tuple[str, str, str, str]:
    """Resolve mode, derive base_url, fetch credentials, and check the live gate.

    Parameters
    ----------
    config_mode :
        Value of ``config.app.mode`` (or ``None``).
    cli_mode :
        Value of the ``--mode`` CLI flag (or ``None``).
    ack :
        ``True`` if the operator passed ``--i-understand-real-money``.
    force_live_deprecated :
        Back-compat: legacy ``--force-live`` flag. Without an explicit
        ``--mode`` it implies live; combined with ``--mode demo`` it is
        treated as a contradiction and raises ``ModeError``. The live
        ack flag is still required even via this path.

    Returns
    -------
    (mode, base_url, api_key, api_secret)

    Raises
    ------
    :class:`ModeError`
        Invalid mode, conflicting flags, missing live ack, or missing
        credentials. The runner prints the message and exits non-zero.
    """
    if force_live_deprecated:
        logger.warning(
            "--force-live is deprecated; use --mode live %s instead.",
            LIVE_ACK_FLAG,
        )
        if cli_mode is not None and str(cli_mode).lower().strip() == MODE_DEMO:
            raise ModeError(
                "conflicting flags: --force-live implies live but --mode demo "
                "was also given. Pick one (the deprecated path is --force-live "
                "alone)."
            )
        if cli_mode is None:
            cli_mode = MODE_LIVE

    mode = resolve_mode(config_mode, cli_mode)
    assert_live_acknowledged(mode, ack)
    base_url = base_url_for(mode)

    api_key, api_secret = resolve_api_credentials(mode)
    if not api_key or not api_secret:
        if mode == MODE_LIVE:
            raise ModeError(
                "missing credentials for mode=live: set "
                "BYBIT_LIVE_API_KEY / BYBIT_LIVE_API_SECRET in .env. "
                "Legacy BYBIT_API_KEY / BYBIT_API_SECRET is not accepted "
                "in live mode."
            )
        raise ModeError(
            f"missing credentials for mode={mode}: set "
            f"BYBIT_{mode.upper()}_API_KEY / BYBIT_{mode.upper()}_API_SECRET "
            f"in .env."
        )
    return mode, base_url, api_key, api_secret


__all__ = [
    "MODE_DEMO", "MODE_LIVE", "VALID_MODES",
    "BASE_URL_BY_MODE", "WS_URL_BY_MODE", "LIVE_ACK_FLAG",
    "ModeError",
    "resolve_mode", "base_url_for", "ws_url_for",
    "resolve_api_credentials", "fingerprint",
    "assert_live_acknowledged", "live_startup_banner",
    "resolve_runtime",
]
