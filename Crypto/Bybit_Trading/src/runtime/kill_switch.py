"""Kill switch for new-entry trading (Stage B-3).

A kill switch BLOCKS NEW POSITIONS but never interferes with managing
existing ones. The strategy keeps running BE / trail / TP exits on
positions that are already open; only ``buy`` / ``sell`` / ``manual_buy``
/ ``manual_sell`` are short-circuited.

Two independent triggers:

  1. Environment variable: ``STRATEGY_DISABLE_NEW_ENTRY=true`` (the
     generic name) OR the legacy ``BBKC_DISABLE_NEW_ENTRY=true`` (kept
     for back-compat with existing operator muscle-memory and runbooks).
     Truthy values: ``true`` / ``1`` / ``yes`` / ``on`` (case-insensitive).
  2. File flag: ``<run_dir>/disable_new_entry.flag`` exists. The file's
     contents are not inspected - presence is the signal. Operators can
     toggle this without restarting the bot.

Either trigger is sufficient; both can be active.

Stage B-5 (circuit breaker) writes the file flag when it auto-trips.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ENV_NAMES = ("STRATEGY_DISABLE_NEW_ENTRY", "BBKC_DISABLE_NEW_ENTRY")
FLAG_FILENAME = "disable_new_entry.flag"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class KillSwitch:
    """Resolve the new-entry kill switch state from env + file flag.

    The object is cheap to query (a few env lookups + a stat call) and
    is safe to call on every order. Construct one per runner instance
    and pass it into the broker; never cache the result.
    """

    def __init__(self, run_dir: Optional[Path] = None) -> None:
        self._run_dir = Path(run_dir) if run_dir is not None else None
        self._flag_path: Optional[Path] = (
            self._run_dir / FLAG_FILENAME if self._run_dir is not None else None
        )

    # -- introspection ------------------------------------------------------
    @property
    def flag_path(self) -> Optional[Path]:
        return self._flag_path

    @property
    def run_dir(self) -> Optional[Path]:
        return self._run_dir

    # -- state --------------------------------------------------------------
    def _env_disabled(self) -> bool:
        for var in ENV_NAMES:
            val = os.getenv(var)
            if val is not None and val.strip().lower() in _TRUTHY:
                return True
        return False

    def _file_disabled(self) -> bool:
        if self._flag_path is None:
            return False
        try:
            return self._flag_path.exists()
        except OSError:
            # Best effort: a transient FS error should not silently leave
            # the kill switch unarmed.
            logger.warning(
                "[kill_switch] could not stat %s; assuming disabled is FALSE",
                self._flag_path,
            )
            return False

    def is_new_entry_disabled(self) -> bool:
        """True when ANY trigger is active."""
        return self._env_disabled() or self._file_disabled()

    def reason(self) -> str:
        """Human-readable description of which trigger fired.

        Returns ``""`` when the switch is not engaged.
        """
        if self._env_disabled():
            for var in ENV_NAMES:
                val = os.getenv(var)
                if val is not None and val.strip().lower() in _TRUTHY:
                    return f"env {var}={val}"
        if self._file_disabled():
            return f"file {self._flag_path}"
        return ""

    # -- mutation (used by the circuit breaker, B5) ------------------------
    def engage_via_file(self, message: str = "engaged by circuit breaker") -> None:
        """Create the file flag with ``message`` as its body.

        Useful for the Stage B-5 circuit breaker to trip the switch from
        inside the bot. Idempotent: safe to call when the flag is already
        present (it gets overwritten).
        """
        if self._flag_path is None:
            logger.error(
                "[kill_switch] engage_via_file called without a run_dir; "
                "no flag file to write."
            )
            return
        try:
            self._flag_path.write_text(message + "\n", encoding="utf-8")
            logger.warning(
                "[kill_switch] FILE FLAG WRITTEN: %s -- %s",
                self._flag_path, message,
            )
        except OSError as exc:
            logger.error(
                "[kill_switch] could not write %s: %s",
                self._flag_path, exc,
            )


__all__ = ["KillSwitch", "ENV_NAMES", "FLAG_FILENAME"]
