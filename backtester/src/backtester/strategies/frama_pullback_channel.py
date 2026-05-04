"""FRAMA Channel pullback-entry strategy.

Differs from ``FRAMAChannelStrategy`` (immediate breakout entry) and
``FRAMAEMA200ChannelStrategy`` (EMA-filtered immediate breakout): instead of
entering on the breakout bar itself, the breakout *arms* a pending entry that
fires only when price subsequently retraces to the FRAMA mid-line. The
breakout candle's wick is reused as the protective stop:

- ``frama_break_up`` arms a long pending. SL = signal candle's ``low``.
- ``frama_break_dn`` arms a short pending. SL = signal candle's ``high``.

After a pending is armed (and on a *later* bar), the entry triggers when:

- Long pending: ``bar.low <= frama`` (pullback touches the mid from above).
- Short pending: ``bar.high >= frama`` (pullback touches the mid from below).

Order is market — the engine fills at the next bar open per its standard
execution model. The bracket carries only the SL; there is no fixed TP.
Exits come from the opposite FRAMA signal (``ClosePosition`` reduce-only),
mirroring the pattern in ``FRAMAEMA200ChannelStrategy.on_bar``.

Pending invalidation: if the SL level is touched by the *current* bar before
entry triggers (e.g. the breakout structure breaks down), the pending is
dropped — entering after that would mean the protective stop is already
beyond the entry candle, which is not a valid setup. This protects long
setups whose ``signal_low`` sits above the FRAMA mid: any "pullback" to mid
would have to pierce ``signal_low`` first, which would have invalidated the
setup anyway.

Same-bar logic order, executed every ``on_bar`` call:

1. If currently in a position: opposite FRAMA signal → market close
   (reduce_only) AND immediately arm an opposite-direction pending using
   THIS bar's wick. The same-bar entry-block guard then prevents that new
   pending from firing on this bar; the next bar onwards reads the new
   setup naturally. Without this swing-flip arming we'd miss every reversal
   trade the exit signal itself reported.
2. Otherwise (flat): SL pre-touch invalidation of any existing pending.
3. Opposite-signal preempt: if this bar fires the opposite-direction
   FRAMA signal vs the current pending, drop the pending. This blocks the
   rare same-bar "pullback trigger + opposite trend signal" race from
   firing an entry the new signal would have warned us against.
4. Pullback entry trigger from the *surviving* prior pending only —
   pending registered this same bar cannot trigger entry on the same bar.
5. Register/refresh pending from this bar's break_up / break_dn signal —
   ONLY if step 4 did not emit an entry. Otherwise the freshly cleared
   pending would be re-registered and sit frozen while the engine fills
   the entry next bar; after eventual close we'd act on a stale setup.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from backtester.core.context import OrderView, StrategyContext
from backtester.core.errors import ConfigError
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    OrderAction,
    OrderIntent,
    TargetMarginPct,
)
from backtester.indicators.base import Indicator
from backtester.indicators.stateful.frama import FRAMAChannel
from backtester.strategies.base import BaseStrategy


class _PendingSetup:
    """Mutable per-symbol pending entry state.

    Kept as a simple mutable record (not frozen dataclass) so a same-direction
    refresh can rewrite ``sl_price`` / ``signal_ts`` in place without
    reallocating, which keeps the per-symbol identity stable across bars.
    """

    __slots__ = ("direction", "sl_price", "signal_ts")

    def __init__(
        self,
        direction: Literal["long", "short"],
        sl_price: Decimal,
        signal_ts: datetime,
    ) -> None:
        self.direction: Literal["long", "short"] = direction
        self.sl_price: Decimal = sl_price
        self.signal_ts: datetime = signal_ts


class FRAMAChannelPullbackStrategy(BaseStrategy):
    """Single-symbol FRAMA pullback strategy.

    Args:
        length / distance / smoothing / volatility_window: forwarded to
            ``FRAMAChannel`` — same defaults as the base strategy.
        timeframe: kept for parameter symmetry with the multi wrapper; the
            single-symbol class reads ``ctx.primary_timeframe`` directly.
        allow_short: if False, short pendings still get *armed* (so the SL
            invalidation logic still applies to them) but the pullback
            trigger silently drops them instead of emitting an entry.
        margin_pct / leverage: ``TargetMarginPct`` sizing parameters. Same
            defaults as the EMA200 variant (``0.03`` / ``3x``) — entries are
            relatively rare so a smaller margin per trade is appropriate.
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
        margin_pct: Decimal | float | str = Decimal("0.03"),
        leverage: Decimal | float | str = Decimal("3"),
    ) -> None:
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
        if self.margin_pct <= 0:
            raise ValueError(f"margin_pct must be > 0, got {self.margin_pct}")
        if self.leverage <= 0:
            raise ValueError(f"leverage must be > 0, got {self.leverage}")
        # Per-symbol pending state. Keyed by symbol so the same strategy
        # instance can be reused across symbols (the multi wrapper relies on
        # this when it instantiates one child per symbol — but the child
        # itself only ever sees its own symbol via ctx swap).
        self._pending: dict[str, _PendingSetup] = {}

    def required_indicators(self) -> list[Indicator]:
        return [self._frama]

    # ---------- pending state helpers --------------------------------------

    def _get_pending(self, symbol: str) -> _PendingSetup | None:
        return self._pending.get(symbol)

    def _set_pending(
        self,
        symbol: str,
        *,
        direction: Literal["long", "short"],
        sl_price: Decimal,
        signal_ts: datetime,
    ) -> None:
        existing = self._pending.get(symbol)
        if existing is not None and existing.direction == direction:
            existing.sl_price = sl_price
            existing.signal_ts = signal_ts
        else:
            self._pending[symbol] = _PendingSetup(
                direction=direction,
                sl_price=sl_price,
                signal_ts=signal_ts,
            )

    def _clear_pending(self, symbol: str) -> None:
        self._pending.pop(symbol, None)

    # ---------- main bar handler -------------------------------------------

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe
        bars = ctx.bars[symbol][tf]
        if bars.height == 0:
            return []

        ind = ctx.indicators[symbol][tf]
        if ind.height == 0:
            return []
        last_idx = ind.height - 1
        frama_val = ind["frama"][last_idx]
        break_up = bool(ind["frama_break_up"][last_idx])
        break_dn = bool(ind["frama_break_dn"][last_idx])

        bar_high_raw = bars["high"][-1]
        bar_low_raw = bars["low"][-1]
        bar_ts = bars["timestamp"][-1]
        if (
            frama_val is None
            or bar_high_raw is None
            or bar_low_raw is None
            or bar_ts is None
        ):
            return []

        frama_dec = Decimal(str(frama_val))
        bar_high = Decimal(str(bar_high_raw))
        bar_low = Decimal(str(bar_low_raw))

        intents: list[OrderIntent] = []

        # ---- 1. exit on opposite signal if currently in position ---------
        pos = ctx.position(symbol)
        if pos is not None and not pos.is_flat:
            if pos.size > 0 and break_dn:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side="sell",
                        type="market",
                        size_spec=ClosePosition(),
                        reason="frama_pullback_exit_long_break_dn",
                        reduce_only=True,
                    )
                )
                # Arm the opposite-direction pending using THIS bar's wick.
                # The same-bar guard in step 4 prevents instant entry; the
                # next on_bar call (after the close fill) sees the setup as
                # if it were freshly registered.
                self._set_pending(
                    symbol,
                    direction="short",
                    sl_price=bar_high,
                    signal_ts=bar_ts,
                )
            elif pos.size < 0 and break_up:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side="buy",
                        type="market",
                        size_spec=ClosePosition(),
                        reason="frama_pullback_exit_short_break_up",
                        reduce_only=True,
                    )
                )
                self._set_pending(
                    symbol,
                    direction="long",
                    sl_price=bar_low,
                    signal_ts=bar_ts,
                )
            # In-position branch never falls through to flat-only steps 2–5.
            return intents

        # ---- 2. SL pre-touch invalidation of any prior pending -----------
        pending = self._get_pending(symbol)
        if pending is not None:
            if pending.direction == "long" and bar_low <= pending.sl_price:
                self._clear_pending(symbol)
                pending = None
            elif pending.direction == "short" and bar_high >= pending.sl_price:
                self._clear_pending(symbol)
                pending = None

        # ---- 3. Opposite-signal preempt --------------------------------------
        # If this bar fires the *opposite* break vs the surviving pending,
        # treat the trend as having reversed and drop the pending. Step 5
        # then registers a fresh pending in the new direction. Same-direction
        # signal is left for step 5 to refresh in place.
        if pending is not None:
            if pending.direction == "long" and break_dn:
                self._clear_pending(symbol)
                pending = None
            elif pending.direction == "short" and break_up:
                self._clear_pending(symbol)
                pending = None

        # ---- 4. Pullback entry trigger (PRIOR pending only) -----------------
        # A pending registered on THIS bar must not trigger on the same bar —
        # the user's rule is "이후 flat 상태에서 ...". Same-bar register would
        # mean the breakout candle itself triggers entry, which makes pullback
        # semantics meaningless.
        if pending is not None and pending.signal_ts != bar_ts:
            triggered_side: Literal["buy", "sell"] | None = None
            if pending.direction == "long" and bar_low <= frama_dec:
                triggered_side = "buy"
            elif pending.direction == "short" and bar_high >= frama_dec:
                # allow_short False → still consume the pending so we don't
                # spam the trigger on every subsequent pullback bar.
                if self.allow_short:
                    triggered_side = "sell"
                else:
                    self._clear_pending(symbol)
                    pending = None
            # ``pending`` is only set to None on the allow_short=False branch
            # above (which leaves ``triggered_side`` as None). The conjunction
            # narrows for mypy and makes the invariant explicit.
            if triggered_side is not None and pending is not None:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=triggered_side,
                        type="market",
                        size_spec=TargetMarginPct(
                            margin_pct=self.margin_pct,
                            leverage=self.leverage,
                        ),
                        reason=(
                            "frama_pullback_long_entry"
                            if pending.direction == "long"
                            else "frama_pullback_short_entry"
                        ),
                        bracket=BracketSpec(stop_loss_price=pending.sl_price),
                    )
                )
                self._clear_pending(symbol)
                emitted_entry = True
            else:
                emitted_entry = False
        else:
            emitted_entry = False

        # ---- 5. Register / refresh pending from this bar's signal --------
        # Skip when an entry intent was just emitted: the engine will fill
        # next bar and we don't want a stale pending sitting around during
        # the now-open position. Without this guard, a same-bar entry-trigger
        # plus fresh break would re-register the pending and fire a stale
        # entry after the position later closes.
        # ``elif`` so that on the rare same-bar break_up + break_dn case
        # break_up wins (matches the FRAMA indicator computation order).
        if not emitted_entry:
            if break_up:
                self._set_pending(
                    symbol,
                    direction="long",
                    sl_price=bar_low,
                    signal_ts=bar_ts,
                )
            elif break_dn:
                self._set_pending(
                    symbol,
                    direction="short",
                    sl_price=bar_high,
                    signal_ts=bar_ts,
                )

        return intents


class FRAMAMultiChannelPullbackStrategy(BaseStrategy):
    """Run ``FRAMAChannelPullbackStrategy`` across multiple symbols.

    Mirrors ``FRAMAMultiChannelStrategy`` / ``FRAMAMultiEMA200ChannelStrategy``:
    the wrapper owns a single FRAMA indicator instance shared by every child,
    so ``IndicatorEngine`` precomputes one set of FRAMA columns per
    ``(symbol, tf)``. Per-symbol pending state lives inside each child, so
    pending isolation is automatic — the wrapper does not need its own state.
    """

    def __init__(
        self,
        *,
        symbols: list[str],
        timeframe: str = "1h",
        child_params: dict[str, Any] | None = None,
    ) -> None:
        if not symbols:
            raise ConfigError(
                "FRAMAMultiChannelPullbackStrategy requires non-empty 'symbols' list"
            )
        if len(set(symbols)) != len(symbols):
            raise ConfigError(
                "FRAMAMultiChannelPullbackStrategy 'symbols' contains "
                f"duplicates: {symbols}"
            )
        self.symbols: list[str] = list(symbols)
        self.timeframe: str = timeframe
        self.child_params: dict[str, Any] = dict(child_params or {})

        length = int(self.child_params.get("length", 26))
        distance = float(str(self.child_params.get("distance", 1.5)))
        smoothing = int(self.child_params.get("smoothing", 5))
        volatility_window = int(self.child_params.get("volatility_window", 200))
        self._frama = FRAMAChannel(
            length=length,
            distance=distance,
            smoothing=smoothing,
            volatility_window=volatility_window,
        )

        self._children: dict[str, FRAMAChannelPullbackStrategy] = {}
        for sym in self.symbols:
            try:
                child = FRAMAChannelPullbackStrategy(**self.child_params)
            except TypeError as e:
                raise ConfigError(
                    "child_params do not match "
                    f"FRAMAChannelPullbackStrategy signature: {e}"
                ) from e
            child._frama = self._frama
            self._children[sym] = child

    def required_indicators(self) -> list[Indicator]:
        return [self._frama]

    def _swap_ctx(self, ctx: StrategyContext, symbol: str) -> StrategyContext:
        return replace(
            ctx,
            primary_symbol=symbol,
            primary_timeframe=self.timeframe,
        )

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        out: list[OrderIntent] = []
        for sym in self.symbols:
            out.extend(self._children[sym].on_bar(self._swap_ctx(ctx, sym)))
        return out

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        out: list[OrderAction] = []
        for sym in self.symbols:
            sym_pending = tuple(o for o in pending if o.symbol == sym)
            out.extend(
                self._children[sym].on_pending_orders(
                    self._swap_ctx(ctx, sym),
                    sym_pending,
                )
            )
        return out


__all__ = [
    "FRAMAChannelPullbackStrategy",
    "FRAMAMultiChannelPullbackStrategy",
]
