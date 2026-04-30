"""Basis-point slippage helper (Phase 2 PR 15a, spec §3.10, §16).

체결 가격에 ``bps/10000`` 비율 슬리피지를 적용:
- ``side="buy"``  → ``price × (1 + bps/10000)`` (불리한 방향: 더 높은 가격에 사야 함)
- ``side="sell"`` → ``price × (1 - bps/10000)`` (불리한 방향: 더 낮은 가격에 팔아야 함)

``bps == 0`` 이면 입력 그대로 반환 (no-op). ``bps < 0`` 은 ``ValueError``
(slippage 는 항상 ≥ 0; 음수 슬리피지 = 유리한 방향 = 의미 없는 모델).
"""

from __future__ import annotations

from decimal import Decimal


def apply_bps_slippage(price: Decimal, side: str, bps: Decimal) -> Decimal:
    """가격에 basis-point 슬리피지 적용.

    ``bps`` 는 Decimal (예: ``Decimal("10")`` = 10 bps = 0.1%).
    """
    if bps < 0:
        raise ValueError(f"bps must be >= 0, got {bps}")
    if bps == 0:
        return price
    factor = bps / Decimal("10000")
    if side == "buy":
        return price * (Decimal("1") + factor)
    if side == "sell":
        return price * (Decimal("1") - factor)
    raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
