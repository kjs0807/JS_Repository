"""``python -m backtester`` 진입점 — ``backtester.cli.main`` 위임."""

from __future__ import annotations

import sys

from backtester.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
