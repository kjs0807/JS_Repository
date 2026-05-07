"""SATS strategy — single-symbol entry, multi-leg or single-TP bracket, time stop.

Reads the precomputed ``sats_*`` indicator columns and emits one market
entry per signal flip. Phase 4 default is the Pine-parity 1/3 split via
``MultiBracketSpec`` (three reduce-only TP limits + one shared SL stop).
Single-TP mode is preserved for legacy / smoke configurations.

Behavioural notes:

- Time stop runs through ``ctx.bars_held()`` + ``ClosePosition()`` —
  ``BracketSpec.time_stop_bars`` / ``MultiBracketSpec`` keep timeout
  responsibility off the engine (single source of truth on the strategy).
- ``reverse_signal_policy`` is hard-coded to ``ignore_while_position``.
  Same-bar close-then-reverse needs ordering decisions for partial fills,
  flip-on-fill semantics, and a ``BarPathModel`` interaction that the doc
  defers to a later phase.
- Sizing uses ``TargetNotionalPct(notional_pct=...)``. ``RiskManager``
  enforces ``max_total_exposure`` separately. When leverage-aware sizing
  becomes needed, follow ``BBKCLegacyCompatStrategy``'s
  ``TargetMarginPct(margin_pct=, leverage=)`` pattern with distinct
  fields rather than overloading ``notional_pct``.

TP split modes:

- ``tp_split_mode="multi"`` (default) — emit ``MultiBracketSpec`` with
  three TP legs at the indicator's ``sats_tp1_price`` / ``sats_tp2_price``
  / ``sats_tp3_price`` and fractions ``tp_size_fractions`` (default
  ``(0.3333, 0.3333, 0.3334)`` so the 1/3 Pine split works as a clean
  ``Decimal``). Long entries naturally produce ascending TP prices and
  short entries descending — both satisfy the engine's side-aware
  ordering invariant without extra effort.
- ``tp_split_mode="single"`` — legacy path, emits ``BracketSpec`` using
  ``single_tp_level`` to pick one of tp1/tp2/tp3. Useful for smoke
  comparisons or for paths that explicitly do not want partial fills.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, cast

from backtester.core.context import StrategyContext
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    MultiBracketSpec,
    OrderIntent,
    TakeProfitLeg,
    TargetNotionalPct,
)
from backtester.indicators.base import Indicator
from backtester.indicators.stateful.sats import (
    PresetT,
    SATSConfig,
    SATSIndicator,
    TPModeT,
)
from backtester.strategies.base import BaseStrategy

_VALID_PRESETS: tuple[str, ...] = (
    "Auto",
    "Custom",
    "Scalping",
    "Default",
    "Swing",
    "Crypto 24/7",
)
_VALID_TP_MODES: tuple[str, ...] = ("Fixed", "Dynamic")
_VALID_SINGLE_TP: tuple[str, ...] = ("tp1", "tp2", "tp3")
_VALID_TP_SPLIT_MODES: tuple[str, ...] = ("multi", "single")

# Pine 1/3 split — last leg absorbs the rounding (0.3333+0.3333+0.3334=1.0000).
_DEFAULT_TP_FRACTIONS: tuple[Decimal, Decimal, Decimal] = (
    Decimal("0.3333"),
    Decimal("0.3333"),
    Decimal("0.3334"),
)

_TP_PRICE_COL = {
    "tp1": "sats_tp1_price",
    "tp2": "sats_tp2_price",
    "tp3": "sats_tp3_price",
}


class SATSStrategy(BaseStrategy):
    """Single-symbol SATS entry strategy (Phase 2 — single TP bracket)."""

    def __init__(
        self,
        *,
        # ── Indicator config (primitive kwargs forwarded to SATSConfig) ──
        preset: str = "Auto",
        timeframe_minutes: int = 60,
        source_col: str = "close",
        atr_len: int = 13,
        base_mult: float = 2.0,
        er_length: int = 20,
        rsi_len: int = 14,
        sl_atr_mult: float = 1.5,
        use_adaptive: bool = True,
        adapt_strength: float = 0.5,
        atr_baseline_len: int = 100,
        use_tqi: bool = True,
        quality_strength: float = 0.4,
        quality_curve: float = 1.5,
        mult_smooth: bool = True,
        use_asym_bands: bool = True,
        asym_strength: float = 0.5,
        use_eff_atr: bool = True,
        use_char_flip: bool = True,
        char_flip_min_age: int = 5,
        char_flip_high: float = 0.55,
        char_flip_low: float = 0.25,
        tqi_weight_er: float = 0.35,
        tqi_weight_vol: float = 0.20,
        tqi_weight_struct: float = 0.25,
        tqi_weight_mom: float = 0.20,
        tqi_struct_len: int = 20,
        tqi_mom_len: int = 10,
        pivot_len: int = 3,
        vol_len: int = 20,
        tp_mode: str = "Fixed",
        tp1_r: float = 1.0,
        tp2_r: float = 2.0,
        tp3_r: float = 3.0,
        dyn_tp_tqi_weight: float = 0.6,
        dyn_tp_vol_weight: float = 0.4,
        dyn_tp_min_scale: float = 0.5,
        dyn_tp_max_scale: float = 2.0,
        dyn_tp_floor_r1: float = 0.5,
        dyn_tp_ceil_r3: float = 8.0,
        # ── Strategy-only kwargs ─────────────────────────────────────────
        tp_split_mode: str = "multi",
        tp_size_fractions: tuple[Decimal | float | str, ...] | None = None,
        single_tp_level: str = "tp3",
        allow_short: bool = True,
        notional_pct: Decimal | float | str = Decimal("0.05"),
        trade_max_age_bars: int | None = 100,
    ) -> None:
        if preset not in _VALID_PRESETS:
            raise ValueError(
                f"preset must be one of {_VALID_PRESETS}, got {preset!r}"
            )
        if tp_mode not in _VALID_TP_MODES:
            raise ValueError(
                f"tp_mode must be one of {_VALID_TP_MODES}, got {tp_mode!r}"
            )
        if tp_split_mode not in _VALID_TP_SPLIT_MODES:
            raise ValueError(
                f"tp_split_mode must be one of {_VALID_TP_SPLIT_MODES}, "
                f"got {tp_split_mode!r}"
            )
        if single_tp_level not in _VALID_SINGLE_TP:
            raise ValueError(
                f"single_tp_level must be one of {_VALID_SINGLE_TP}, "
                f"got {single_tp_level!r}"
            )
        notional = Decimal(str(notional_pct))
        if notional <= 0:
            raise ValueError(f"notional_pct must be > 0, got {notional}")
        if trade_max_age_bars is not None and trade_max_age_bars <= 0:
            # Treat 0 / negative as disabled, matching BBKCLegacy convention.
            trade_max_age_bars = None

        # Multi-leg fractions normalize once at __init__ so on_bar can build
        # the BracketSpec hot-path without re-parsing per signal. Only used
        # when tp_split_mode == "multi"; ignored otherwise.
        fractions: tuple[Decimal, ...]
        if tp_size_fractions is None:
            fractions = _DEFAULT_TP_FRACTIONS
        else:
            fractions = tuple(Decimal(str(f)) for f in tp_size_fractions)
        if tp_split_mode == "multi":
            if len(fractions) != 3:
                raise ValueError(
                    f"tp_split_mode='multi' requires exactly 3 size fractions "
                    f"(one per sats_tp1/2/3 column); got {len(fractions)}"
                )
            for f in fractions:
                if f <= Decimal(0):
                    raise ValueError(
                        f"tp_size_fractions entries must be > 0, got {f}"
                    )
            total = sum(fractions, Decimal(0))
            if total > Decimal(1) or total <= Decimal(0):
                raise ValueError(
                    f"sum of tp_size_fractions must be in (0, 1], got {total}"
                )

        cfg = SATSConfig(
            preset=cast(PresetT, preset),
            timeframe_minutes=timeframe_minutes,
            source_col=source_col,
            atr_len=atr_len,
            base_mult=base_mult,
            er_length=er_length,
            rsi_len=rsi_len,
            sl_atr_mult=sl_atr_mult,
            use_adaptive=use_adaptive,
            adapt_strength=adapt_strength,
            atr_baseline_len=atr_baseline_len,
            use_tqi=use_tqi,
            quality_strength=quality_strength,
            quality_curve=quality_curve,
            mult_smooth=mult_smooth,
            use_asym_bands=use_asym_bands,
            asym_strength=asym_strength,
            use_eff_atr=use_eff_atr,
            use_char_flip=use_char_flip,
            char_flip_min_age=char_flip_min_age,
            char_flip_high=char_flip_high,
            char_flip_low=char_flip_low,
            tqi_weight_er=tqi_weight_er,
            tqi_weight_vol=tqi_weight_vol,
            tqi_weight_struct=tqi_weight_struct,
            tqi_weight_mom=tqi_weight_mom,
            tqi_struct_len=tqi_struct_len,
            tqi_mom_len=tqi_mom_len,
            pivot_len=pivot_len,
            vol_len=vol_len,
            tp_mode=cast(TPModeT, tp_mode),
            tp1_r=tp1_r,
            tp2_r=tp2_r,
            tp3_r=tp3_r,
            dyn_tp_tqi_weight=dyn_tp_tqi_weight,
            dyn_tp_vol_weight=dyn_tp_vol_weight,
            dyn_tp_min_scale=dyn_tp_min_scale,
            dyn_tp_max_scale=dyn_tp_max_scale,
            dyn_tp_floor_r1=dyn_tp_floor_r1,
            dyn_tp_ceil_r3=dyn_tp_ceil_r3,
            trade_max_age_bars=trade_max_age_bars or 0,
        )
        self._sats = SATSIndicator(cfg)
        self.tp_split_mode: Literal["multi", "single"] = cast(
            Literal["multi", "single"], tp_split_mode
        )
        self.tp_size_fractions: tuple[Decimal, ...] = fractions
        self.single_tp_level: Literal["tp1", "tp2", "tp3"] = cast(
            Literal["tp1", "tp2", "tp3"], single_tp_level
        )
        self.allow_short = allow_short
        self.notional_pct = notional
        self.trade_max_age_bars = trade_max_age_bars

    def required_indicators(self) -> list[Indicator]:
        return [self._sats]

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe

        # ── Time stop (held bars >= cap) ─ takes precedence over entries.
        if self.trade_max_age_bars is not None and ctx.has_position(symbol):
            held = ctx.bars_held(symbol)
            if held is not None and held >= self.trade_max_age_bars:
                pos = ctx.position(symbol)
                if pos is not None and not pos.is_flat:
                    close_side: Literal["buy", "sell"] = (
                        "sell" if pos.size > 0 else "buy"
                    )
                    return [
                        OrderIntent(
                            symbol=symbol,
                            side=close_side,
                            type="market",
                            size_spec=ClosePosition(),
                            reason="sats_time_stop",
                            reduce_only=True,
                        )
                    ]

        if not ctx.indicators.has(symbol, tf):
            return []
        ind = ctx.indicators[symbol][tf]
        if ind.height == 0:
            return []
        last_idx = ind.height - 1

        ready = ind["sats_ready"][last_idx]
        if not ready:
            return []
        signal = int(ind["sats_signal"][last_idx])
        if signal == 0:
            return []
        # Phase 1 reverse policy: ignore signals while a position is open.
        if ctx.has_position(symbol):
            return []
        if signal == -1 and not self.allow_short:
            return []

        sl_raw = ind["sats_sl_price"][last_idx]
        if sl_raw is None:
            return []

        side: Literal["buy", "sell"] = "buy" if signal == 1 else "sell"
        bracket = self._build_bracket(ind, last_idx, sl_raw)
        if bracket is None:
            return []
        return [
            OrderIntent(
                symbol=symbol,
                side=side,
                type="market",
                size_spec=TargetNotionalPct(notional_pct=self.notional_pct),
                reason="sats_buy" if signal == 1 else "sats_sell",
                bracket=bracket,
            )
        ]

    def _build_bracket(
        self,
        ind: object,
        last_idx: int,
        sl_raw: object,
    ) -> BracketSpec | MultiBracketSpec | None:
        """Construct the per-signal bracket spec.

        ``multi`` mode reads all three ``sats_tpN_price`` columns and pairs
        them with the strategy's ``tp_size_fractions``. The indicator already
        emits ascending TP prices for long signals and descending for short,
        so the engine's side-aware ordering invariant in
        :meth:`BacktestEngine._validate_bracket_for_intent` is satisfied
        without further work.

        ``single`` mode preserves legacy behavior — one ``BracketSpec`` with
        the TP picked by ``single_tp_level``.
        """
        if self.tp_split_mode == "multi":
            tp1_raw = ind["sats_tp1_price"][last_idx]  # type: ignore[index]
            tp2_raw = ind["sats_tp2_price"][last_idx]  # type: ignore[index]
            tp3_raw = ind["sats_tp3_price"][last_idx]  # type: ignore[index]
            if tp1_raw is None or tp2_raw is None or tp3_raw is None:
                return None
            tp_prices = (tp1_raw, tp2_raw, tp3_raw)
            legs = tuple(
                TakeProfitLeg(
                    price=Decimal(str(p)),
                    size_fraction=frac,
                    label=label,
                )
                for label, p, frac in zip(
                    ("tp1", "tp2", "tp3"),
                    tp_prices,
                    self.tp_size_fractions,
                    strict=True,
                )
            )
            return MultiBracketSpec(
                take_profits=legs,
                stop_loss_price=Decimal(str(sl_raw)),
            )
        # single mode
        tp_raw = ind[_TP_PRICE_COL[self.single_tp_level]][last_idx]  # type: ignore[index]
        if tp_raw is None:
            return None
        return BracketSpec(
            take_profit_price=Decimal(str(tp_raw)),
            stop_loss_price=Decimal(str(sl_raw)),
        )


__all__ = ["SATSStrategy"]
