"""Per-run rotating file log (Stage C-1).

Console logging alone is fragile across a 14-day forward run: terminal
scrollback gets dropped, SSH sessions disconnect, container restarts
lose history. Mirroring everything to ``run_dir/run.log`` (with
size-based rotation) gives us a durable trail we can grep after the
fact.

Design choices:

  * Attach to the *root* logger so every module (broker, runner, ws
    client, REST client) is captured without each one having to opt in.
  * ``RotatingFileHandler`` with 5 MB per file, 10 backups → ~50 MB
    upper bound per run. The bot would have to be *extremely* chatty
    to hit that.
  * The handler is tagged with a unique attribute so a re-invocation of
    :func:`install_run_log_handler` for the same path is a no-op. This
    matters for tests that call ``main()`` multiple times in one
    process, and for any future hot-reload of config.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10
_HANDLER_TAG = "_strategy_run_log_handler"

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def install_run_log_handler(
    run_dir: Path,
    *,
    filename: str = "run.log",
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    fmt: str = _DEFAULT_FORMAT,
) -> Optional[logging.Handler]:
    """Attach a rotating file handler to the root logger.

    Idempotent: if a handler was already installed for the same target
    path (via this function), the existing handler is returned and no
    new handler is added.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = (run_dir / filename).resolve()

    root = logging.getLogger()
    for existing in root.handlers:
        tag = getattr(existing, _HANDLER_TAG, None)
        if tag is not None and Path(tag).resolve() == target:
            return existing

    handler = logging.handlers.RotatingFileHandler(
        target,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=False,
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt))
    setattr(handler, _HANDLER_TAG, str(target))
    root.addHandler(handler)

    # Ensure root level allows the configured level to flow through
    # (basicConfig may have set WARNING earlier in some entry paths).
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return handler


def remove_run_log_handlers() -> int:
    """Remove every run-log handler from the root logger.

    Returns the count of handlers removed. Tests use this to clean up
    between runs so they do not leak file handles.
    """
    root = logging.getLogger()
    removed = 0
    for h in list(root.handlers):
        if getattr(h, _HANDLER_TAG, None) is not None:
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
            removed += 1
    return removed


__all__ = [
    "install_run_log_handler",
    "remove_run_log_handlers",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_BACKUP_COUNT",
]
