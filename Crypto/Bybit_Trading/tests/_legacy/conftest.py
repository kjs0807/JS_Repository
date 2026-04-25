"""Conftest for legacy tests: add _legacy/ + parent to sys.path.

Mirrors the runtime setup in _legacy/run_bbkc_trade.py so tests can do
`from api.rest_client import BybitRestClient` etc.
"""
from __future__ import annotations
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_DIR = PROJECT_ROOT / "_legacy"
PARENT_DIR = PROJECT_ROOT

for p in (str(PARENT_DIR), str(LEGACY_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
