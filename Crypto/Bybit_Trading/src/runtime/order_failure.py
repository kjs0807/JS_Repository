"""Stage B-4: Bybit order-failure classification.

Bybit V5 surfaces order failures in two shapes:

  1. pybit raises ``InvalidRequestError`` whose ``__str__`` carries the
     retCode and retMsg (e.g. ``"ErrCode: 110007, ErrMsg: Order does
     not meet minimum order value 5USDT..."``).
  2. ``rest_client.place_order`` catches retCode != 0 and returns
     ``{"error": retMsg}`` to the caller.

We classify either form into a small enum-like taxonomy so the runner
can:

  * file the failure in a per-category counter,
  * include the category in WARN logs and Telegram alerts,
  * feed the Stage B-5 circuit breaker, which only needs the
    success/failure verdict but benefits from a category for diagnostics.

The classifier is pure (no broker state) and lives in
``src.runtime`` so it can be imported by both the broker and the
circuit breaker without a cycle.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple


class OrderFailureCategory:
    """Stable string keys. Adding categories must not change existing values."""
    MIN_QTY = "min_qty"
    MIN_NOTIONAL = "min_notional"
    QTY_STEP = "qty_step"
    POSITION_IDX = "position_idx"
    LEVERAGE = "leverage"
    RISK_REJECT = "risk_reject"      # local RiskManager rejected the order
    NETWORK = "network"
    AUTH = "auth"
    OTHER = "other"


ALL_CATEGORIES: Tuple[str, ...] = (
    OrderFailureCategory.MIN_QTY,
    OrderFailureCategory.MIN_NOTIONAL,
    OrderFailureCategory.QTY_STEP,
    OrderFailureCategory.POSITION_IDX,
    OrderFailureCategory.LEVERAGE,
    OrderFailureCategory.RISK_REJECT,
    OrderFailureCategory.NETWORK,
    OrderFailureCategory.AUTH,
    OrderFailureCategory.OTHER,
)


# Bybit V5 retCode -> category. The codes are from
# https://bybit-exchange.github.io/docs/v5/error_code; only the ones we
# can actually act on are mapped (others fall through to OTHER).
_RETCODE_MAP = {
    # min notional / order value
    110007: OrderFailureCategory.MIN_NOTIONAL,
    110095: OrderFailureCategory.MIN_NOTIONAL,
    # qty below symbol minimum
    110012: OrderFailureCategory.MIN_QTY,
    110014: OrderFailureCategory.MIN_QTY,
    # qty not a multiple of qtyStep / precision
    110017: OrderFailureCategory.QTY_STEP,
    110041: OrderFailureCategory.QTY_STEP,
    # position mode / positionIdx mismatch
    110018: OrderFailureCategory.POSITION_IDX,
    110026: OrderFailureCategory.POSITION_IDX,
    # leverage
    110043: OrderFailureCategory.LEVERAGE,
    110044: OrderFailureCategory.LEVERAGE,
    # auth / signature
    10003: OrderFailureCategory.AUTH,
    10004: OrderFailureCategory.AUTH,
    10005: OrderFailureCategory.AUTH,
    # rate-limit / network-ish
    10006: OrderFailureCategory.NETWORK,
    10016: OrderFailureCategory.NETWORK,
}


# Pattern fallback when retCode is absent or unmapped. Order matters: the
# most specific patterns come first so a generic "qty" tail does not
# swallow a min_qty hit.
_PATTERN_MAP: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bposition[_ ]?idx\b", re.I), OrderFailureCategory.POSITION_IDX),
    (re.compile(r"\bposition\s*mode\b", re.I), OrderFailureCategory.POSITION_IDX),
    (re.compile(r"\bleverage\b", re.I), OrderFailureCategory.LEVERAGE),
    (re.compile(r"\bmin(imum)?\s*order\s*value\b", re.I), OrderFailureCategory.MIN_NOTIONAL),
    (re.compile(r"\bmin(imum)?\s*notional\b", re.I), OrderFailureCategory.MIN_NOTIONAL),
    (re.compile(r"\bmin(imum)?\s*order\s*qty\b", re.I), OrderFailureCategory.MIN_QTY),
    (re.compile(r"\blower\s*than\s*(the\s*)?min(imum)?", re.I), OrderFailureCategory.MIN_QTY),
    (re.compile(r"\bqty.*(step|precision|decimal)\b", re.I), OrderFailureCategory.QTY_STEP),
    (re.compile(r"\bstep\b", re.I), OrderFailureCategory.QTY_STEP),
    (re.compile(r"\bsign(ature)?\b|\bapi[_ ]?key\b", re.I), OrderFailureCategory.AUTH),
    (re.compile(r"\btimeout\b|\bconnection\b|\bgateway\b|\bunreachable\b",
                re.I), OrderFailureCategory.NETWORK),
    (re.compile(r"\brate[_ ]?limit\b|\btoo\s*many\b", re.I), OrderFailureCategory.NETWORK),
)


_RETCODE_REGEXES: Tuple[re.Pattern[str], ...] = (
    re.compile(r"ErrCode:\s*(\d+)", re.I),
    re.compile(r"retCode:\s*(\d+)", re.I),
    re.compile(r"\(\s*retCode\s*=\s*(\d+)", re.I),
    re.compile(r"\bcode\s*=\s*(\d+)", re.I),
)


def _extract_retcode(msg: str) -> Optional[int]:
    for pat in _RETCODE_REGEXES:
        m = pat.search(msg)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def classify_order_failure(error: object) -> str:
    """Classify a Bybit order failure into one of :data:`ALL_CATEGORIES`.

    ``error`` can be the raised exception, a plain string retMsg, or a
    ``{"error": retMsg}`` dict (the shape the rest_client wrapper returns
    when pybit reports retCode != 0 without raising).
    """
    if error is None:
        return OrderFailureCategory.OTHER
    if isinstance(error, dict):
        # Prefer dict["error"], fall back to a stringified form.
        msg = str(error.get("error", error.get("retMsg", error.get("message", error))))
    else:
        msg = str(error)
    code = _extract_retcode(msg)
    if code is not None and code in _RETCODE_MAP:
        return _RETCODE_MAP[code]
    for pattern, category in _PATTERN_MAP:
        if pattern.search(msg):
            return category
    return OrderFailureCategory.OTHER


__all__ = [
    "OrderFailureCategory",
    "ALL_CATEGORIES",
    "classify_order_failure",
]
