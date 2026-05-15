"""Stage B-4: Bybit order-failure classification."""
from __future__ import annotations

import pytest

from src.runtime.order_failure import (
    ALL_CATEGORIES,
    OrderFailureCategory,
    classify_order_failure,
)


# ---------------------------------------------------------------------------
# retCode -> category (the explicit table wins)
# ---------------------------------------------------------------------------
class TestRetCodeMap:
    def test_min_notional_110007(self):
        msg = "ErrCode: 110007, ErrMsg: Order does not meet minimum order value 5USDT"
        assert classify_order_failure(msg) == OrderFailureCategory.MIN_NOTIONAL

    def test_min_qty_110012(self):
        msg = "ErrCode: 110012, ErrMsg: Order qty lower than the minimum order qty"
        assert classify_order_failure(msg) == OrderFailureCategory.MIN_QTY

    def test_qty_step_110017(self):
        msg = "(retCode=110017) qty precision invalid"
        assert classify_order_failure(msg) == OrderFailureCategory.QTY_STEP

    def test_position_idx_110018(self):
        msg = "ErrCode: 110018, position idx not match position mode"
        assert classify_order_failure(msg) == OrderFailureCategory.POSITION_IDX

    def test_leverage_110043(self):
        msg = "ErrCode: 110043, leverage not modified"
        assert classify_order_failure(msg) == OrderFailureCategory.LEVERAGE

    def test_auth_10004(self):
        msg = "retCode: 10004, sign error"
        assert classify_order_failure(msg) == OrderFailureCategory.AUTH

    def test_network_10006_rate_limit(self):
        msg = "ErrCode: 10006, Too many requests"
        assert classify_order_failure(msg) == OrderFailureCategory.NETWORK


# ---------------------------------------------------------------------------
# pattern fallback (retCode missing or unmapped)
# ---------------------------------------------------------------------------
class TestPatternFallback:
    def test_min_order_value_pattern(self):
        assert classify_order_failure(
            "Order does not meet the minimum order value of 5 USDT"
        ) == OrderFailureCategory.MIN_NOTIONAL

    def test_min_order_qty_pattern(self):
        assert classify_order_failure(
            "qty is lower than the min order qty"
        ) == OrderFailureCategory.MIN_QTY

    def test_position_mode_pattern(self):
        assert classify_order_failure(
            "position mode mismatch with position idx"
        ) == OrderFailureCategory.POSITION_IDX

    def test_leverage_pattern(self):
        assert classify_order_failure(
            "leverage value exceeded the maximum"
        ) == OrderFailureCategory.LEVERAGE

    def test_signature_pattern(self):
        assert classify_order_failure(
            "Sign error - invalid signature"
        ) == OrderFailureCategory.AUTH

    def test_timeout_pattern(self):
        assert classify_order_failure(
            "ConnectionTimeoutError: read timeout"
        ) == OrderFailureCategory.NETWORK

    def test_qty_step_pattern(self):
        assert classify_order_failure(
            "qty has invalid step (precision)"
        ) == OrderFailureCategory.QTY_STEP


# ---------------------------------------------------------------------------
# defensive / edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_unknown_message_is_other(self):
        assert classify_order_failure(
            "Some completely unrelated error text"
        ) == OrderFailureCategory.OTHER

    def test_none_is_other(self):
        assert classify_order_failure(None) == OrderFailureCategory.OTHER

    def test_dict_error_shape(self):
        # rest_client.place_order swallows retCode != 0 and returns:
        #   {"error": "<retMsg>"}
        # The classifier must handle that shape too.
        assert classify_order_failure(
            {"error": "Order does not meet minimum order value 5USDT"}
        ) == OrderFailureCategory.MIN_NOTIONAL

    def test_exception_object(self):
        try:
            raise RuntimeError(
                "ErrCode: 110017, qty precision invalid for symbol BTCUSDT"
            )
        except RuntimeError as exc:
            assert classify_order_failure(exc) == OrderFailureCategory.QTY_STEP


# ---------------------------------------------------------------------------
# taxonomy guard
# ---------------------------------------------------------------------------
class TestTaxonomy:
    def test_all_categories_are_strings(self):
        for cat in ALL_CATEGORIES:
            assert isinstance(cat, str) and cat

    def test_categories_unique(self):
        assert len(ALL_CATEGORIES) == len(set(ALL_CATEGORIES))
