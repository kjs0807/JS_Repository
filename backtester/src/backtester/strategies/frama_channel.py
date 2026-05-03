"""FRAMA Channel single-symbol strategy (PR 16, BigBeluga port).

Reads precomputed ``frama_break_up`` / ``frama_break_dn`` from
``ctx.indicators[symbol][tf]`` and emits market entries with a fixed-bracket
TP/SL. Mirrors the BBKC futures convention from
``BBKCLegacyCompatStrategy``:

- size_spec = ``TargetMarginPct(margin_pct, leverage)`` (crypto perp standard)
- TP/SL prices = ``entry × (tp_pct / leverage)`` / ``entry × (sl_pct / leverage)``
  — the *price-level* % is the user-facing % divided by leverage so that 6%
  account-PnL TP at 3x leverage clamps to a 2% price move.
- ``ctx.has_position(symbol)`` is the single source of truth for "am I in" —
  no internal ``_has_position`` flag (matches PR A guidance).

Behaviour (per BigBeluga):

- ``frama_break_up`` and not in position → market BUY.
- ``frama_break_dn`` and not in position and ``allow_short`` → market SELL.
- Already in position → no new entry. No scale-in (spec PR I has not enabled it).

Exits are handled entirely by the bracket TP/SL (or stop-only if
``drop_tp=True``). No mid-line / trailing exit in this PR — that is
``BBKCLegacyCompatStrategy``'s territory and would muddy parity testing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from backtester.core.context import StrategyContext
from backtester.core.orders import (
    BracketSpec,
    OrderIntent,
    TargetMarginPct,
)
from backtester.indicators.base import Indicator
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.strategies.base import BaseStrategy


class FRAMAChannelStrategy(BaseStrategy):
    """FRAMA Channel break-out entry with fixed bracket TP/SL.

    Args:
        length: FRAMA window (Pine ``N``). Even, >= 2.
        distance: Channel half-width multiplier. > 0.
        smoothing: SMA window applied on top of recursive Filt. >= 1.
        volatility_window: SMA window over ``high - low``. >= 1.
        timeframe: Reserved for the multi-symbol wrapper. The single-symbol
            strategy reads ``ctx.primary_timeframe`` directly so that running
            the strategy on a different TF only requires changing the config —
            this field is kept here so ``child_params`` stays parameter-symmetric
            between the single and multi variants.
        allow_short: If False, ``frama_break_dn`` is ignored.
        margin_pct: Initial margin fraction of equity per entry.
        leverage: Target leverage for ``TargetMarginPct``. Also divides the
            user-facing TP/SL pct to derive the price-level move.
        tp_pct: Take-profit, account-PnL %. ``None`` → no TP.
        sl_pct: Stop-loss, account-PnL %. ``None`` → no SL.
        drop_tp: If True, only the SL is attached (TP omitted) — useful for
            "let the trend run with a trailing stop" experiments. Note that
            this PR does not implement trailing; the SL is fixed-bracket.
    """

    def __init__(
        self,
        *,
        length: int = 26,
        distance: Decimal | float | str = Decimal("1.5"),
        smoothing: int = 5,
        volatility_window: int = 200,
        timeframe: str = "1h",
        allow_short: bool = True,
        margin_pct: Decimal | float | str = Decimal("0.05"),
        leverage: Decimal | float | str = Decimal("3"),
        tp_pct: Decimal | float | str | None = Decimal("0.06"),
        sl_pct: Decimal | float | str | None = Decimal("0.07"),
        drop_tp: bool = False,
    ) -> None:
        # ``distance`` flows into the indicator as float (Pine semantics) but is
        # accepted as Decimal/str/float so YAML serialisation round-trips
        # cleanly without forcing every config to ``1.5`` literal float.
        self._frama = FRAMAChannel(
            length=length,
            distance=float(Decimal(str(distance))),
            smoothing=smoothing,
            volatility_window=volatility_window,
        )
        self.timeframe = timeframe
        self.allow_short = allow_short
        self.margin_pct = Decimal(str(margin_pct))
        self.leverage = Decimal(str(leverage))
        if self.leverage <= 0:
            raise ValueError(f"leverage must be > 0, got {self.leverage}")
        self.tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None
        self.drop_tp = drop_tp

    def required_indicators(self) -> list[Indicator]:
        return [self._frama]

    def _build_bracket(
        self,
        entry_price: Decimal,
        side: Literal["buy", "sell"],
    ) -> BracketSpec | None:
        # BBKC parity: account-PnL % → price-level % via division by leverage.
        # ``drop_tp=True`` means SL-only bracket; if the user removes both we
        # return None so the engine doesn't attach an empty BracketSpec.
        tp_price: Decimal | None = None
        sl_price: Decimal | None = None
        if self.tp_pct is not None and not self.drop_tp:
            price_tp = self.tp_pct / self.leverage
            tp_price = (
                entry_price * (Decimal("1") + price_tp)
                if side == "buy"
                else entry_price * (Decimal("1") - price_tp)
            )
        if self.sl_pct is not None:
            price_sl = self.sl_pct / self.leverage
            sl_price = (
                entry_price * (Decimal("1") - price_sl)
                if side == "buy"
                else entry_price * (Decimal("1") + price_sl)
            )
        if tp_price is None and sl_price is None:
            return None
        return BracketSpec(take_profit_price=tp_price, stop_loss_price=sl_price)

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe
        bars = ctx.bars[symbol][tf]
        if bars.height < 2:
            return []

        # PR A — ledger is single source of truth for position existence. Risk
        # rejects / partial fills won't desync as they would with an internal
        # flag. ``has_position`` already excludes flat positions.
        if ctx.has_position(symbol):
            return []

        # The indicator must be precomputed; ``required_indicators`` registers
        # ``self._frama`` so IndicatorEngine fills the cache.
        ind_df = ctx.indicators[symbol][tf]
        if ind_df.height == 0:
            return []
        last_idx = ind_df.height - 1
        break_up = ind_df["frama_break_up"][last_idx]
        break_dn = ind_df["frama_break_dn"][last_idx]
        # Polars stores bool as Python bool / None; treat null as no signal.
        if break_up is None and break_dn is None:
            return []

        side: Literal["buy", "sell"] | None = None
        if break_up:
            side = "buy"
        elif break_dn and self.allow_short:
            side = "sell"
        if side is None:
            return []

        curr_close = bars["close"][-1]
        if curr_close is None:
            return []
        entry_price = Decimal(str(curr_close))

        return [
            OrderIntent(
                symbol=symbol,
                side=side,
                type="market",
                size_spec=TargetMarginPct(
                    margin_pct=self.margin_pct,
                    leverage=self.leverage,
                ),
                reason="frama_channel_break_up" if side == "buy" else "frama_channel_break_dn",
                bracket=self._build_bracket(entry_price, side),
            )
        ]
