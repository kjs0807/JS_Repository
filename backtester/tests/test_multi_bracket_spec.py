"""Phase 3 — MultiBracketSpec / TakeProfitLeg dataclass invariants.

Pure-data tests: no engine, no orderbook. Coverage:

1. TakeProfitLeg rejects size_fraction outside (0, 1] and non-positive price.
2. MultiBracketSpec rejects empty TP tuple.
3. MultiBracketSpec rejects sum of size_fractions outside (0, 1].
4. MultiBracketSpec rejects duplicate TP prices.
5. MultiBracketSpec rejects non-positive stop_loss_price (when set).
6. total_fraction property correctness, has_any always True.
7. SATS-style 1/3 split (0.3333 + 0.3333 + 0.3334) is accepted.
8. Partial-sum split (0.3 + 0.3 + 0.3 = 0.9) is accepted (residual stays
   under SL until manual close).
9. ``BracketLike`` type alias accepts both BracketSpec and MultiBracketSpec
   in OrderIntent.bracket.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtester.core.orders import (
    BracketLike,
    BracketSpec,
    MultiBracketSpec,
    OrderIntent,
    TakeProfitLeg,
    TargetUnits,
)


# ---------- TakeProfitLeg ---------------------------------------------------


def test_tpleg_rejects_zero_size_fraction() -> None:
    with pytest.raises(ValueError, match="size_fraction"):
        TakeProfitLeg(price=Decimal("100"), size_fraction=Decimal("0"))


def test_tpleg_rejects_negative_size_fraction() -> None:
    with pytest.raises(ValueError, match="size_fraction"):
        TakeProfitLeg(price=Decimal("100"), size_fraction=Decimal("-0.1"))


def test_tpleg_rejects_size_fraction_above_one() -> None:
    with pytest.raises(ValueError, match="size_fraction"):
        TakeProfitLeg(price=Decimal("100"), size_fraction=Decimal("1.01"))


def test_tpleg_accepts_size_fraction_one() -> None:
    leg = TakeProfitLeg(price=Decimal("100"), size_fraction=Decimal("1"))
    assert leg.size_fraction == Decimal("1")


def test_tpleg_rejects_zero_price() -> None:
    with pytest.raises(ValueError, match="price"):
        TakeProfitLeg(price=Decimal("0"), size_fraction=Decimal("0.5"))


def test_tpleg_rejects_negative_price() -> None:
    with pytest.raises(ValueError, match="price"):
        TakeProfitLeg(price=Decimal("-5"), size_fraction=Decimal("0.5"))


# ---------- MultiBracketSpec ------------------------------------------------


def _leg(price: str, frac: str, label: str = "") -> TakeProfitLeg:
    return TakeProfitLeg(
        price=Decimal(price), size_fraction=Decimal(frac), label=label
    )


def test_multibracket_rejects_empty_tps() -> None:
    with pytest.raises(ValueError, match="requires >= 1 TP legs"):
        MultiBracketSpec(take_profits=())


def test_multibracket_rejects_sum_zero() -> None:
    # All legs at 0 size_fraction is rejected at the leg level first; this
    # asserts the composite rejection path with a single zero fraction would
    # also fail — but TakeProfitLeg already blocks zero, so use sum > 1 case
    # to exercise the composite check.
    with pytest.raises(ValueError, match="size_fractions"):
        MultiBracketSpec(
            take_profits=(_leg("110", "0.6"), _leg("120", "0.5"))
        )


def test_multibracket_rejects_sum_above_one() -> None:
    with pytest.raises(ValueError, match="size_fractions"):
        MultiBracketSpec(
            take_profits=(
                _leg("110", "0.4"),
                _leg("120", "0.4"),
                _leg("130", "0.4"),
            )
        )


def test_multibracket_rejects_duplicate_prices() -> None:
    with pytest.raises(ValueError, match="distinct prices"):
        MultiBracketSpec(
            take_profits=(
                _leg("110", "0.5"),
                _leg("110", "0.5"),
            )
        )


def test_multibracket_rejects_non_positive_stop_loss() -> None:
    with pytest.raises(ValueError, match="stop_loss_price"):
        MultiBracketSpec(
            take_profits=(_leg("110", "0.5"),),
            stop_loss_price=Decimal("0"),
        )


def test_multibracket_total_fraction() -> None:
    spec = MultiBracketSpec(
        take_profits=(
            _leg("110", "0.3333"),
            _leg("120", "0.3333"),
            _leg("130", "0.3334"),
        ),
        stop_loss_price=Decimal("95"),
    )
    assert spec.total_fraction == Decimal("1.0000")
    assert spec.has_any() is True


def test_multibracket_partial_sum_under_one_is_accepted() -> None:
    # 0.3 + 0.3 + 0.3 = 0.9 — leaves 0.1 of position under the SL when all
    # TPs fill (engine doesn't auto-cancel SL in that case).
    spec = MultiBracketSpec(
        take_profits=(
            _leg("110", "0.3"),
            _leg("120", "0.3"),
            _leg("130", "0.3"),
        ),
        stop_loss_price=Decimal("95"),
    )
    assert spec.total_fraction == Decimal("0.9")


def test_multibracket_single_leg_accepted() -> None:
    # Equivalent to BracketSpec with TP only. We allow this — the engine
    # branch is identical and partial sizing still meaningful.
    spec = MultiBracketSpec(
        take_profits=(_leg("120", "0.5"),),
        stop_loss_price=None,
    )
    assert spec.total_fraction == Decimal("0.5")


def test_multibracket_no_stop_loss_is_accepted() -> None:
    spec = MultiBracketSpec(
        take_profits=(_leg("110", "0.5"), _leg("120", "0.5")),
        stop_loss_price=None,
    )
    assert spec.stop_loss_price is None
    assert spec.has_any() is True


# ---------- OrderIntent.bracket union ---------------------------------------


def test_order_intent_accepts_bracket_spec() -> None:
    b: BracketLike = BracketSpec(
        take_profit_price=Decimal("110"), stop_loss_price=Decimal("95")
    )
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
        bracket=b,
    )
    assert isinstance(intent.bracket, BracketSpec)


def test_order_intent_accepts_multi_bracket_spec() -> None:
    b: BracketLike = MultiBracketSpec(
        take_profits=(_leg("110", "0.5"), _leg("120", "0.5")),
        stop_loss_price=Decimal("95"),
    )
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size_spec=TargetUnits(units=Decimal("1")),
        bracket=b,
    )
    assert isinstance(intent.bracket, MultiBracketSpec)


# ---------- frozen dataclass invariants -------------------------------------


def test_multibracket_is_immutable() -> None:
    spec = MultiBracketSpec(
        take_profits=(_leg("110", "0.5"), _leg("120", "0.5")),
        stop_loss_price=Decimal("95"),
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        spec.stop_loss_price = Decimal("80")  # type: ignore[misc]


def test_tpleg_is_immutable() -> None:
    leg = _leg("110", "0.5")
    with pytest.raises(Exception):
        leg.price = Decimal("120")  # type: ignore[misc]
