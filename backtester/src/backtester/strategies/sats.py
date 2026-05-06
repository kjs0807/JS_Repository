"""SATS strategy (Phase 2) — single-symbol entry / single-TP bracket / time stop.

Reads the precomputed ``sats_*`` indicator columns and emits a single market
entry per signal flip with one ``BracketSpec`` carrying the chosen TP leg
and the pivot-anchored SL. Time stop runs through ``ctx.bars_held()`` +
``ClosePosition()`` — ``BracketSpec.time_stop_bars`` is *not* set because the
engine does not auto-process it (PR K spec) and we keep timeout responsibility
on the strategy as the single source of truth.

Phase 1 simplifications versus the full SATS doc plan:

- ``reverse_signal_policy`` is hard-coded to ``ignore_while_position``.
  Same-bar close-then-reverse needs ordering decisions for partial fills,
  flip-on-fill semantics, and a ``BarPathModel`` interaction that we want
  to settle in Phase 3 alongside multi-leg TP.
- Multi-leg TP is out of scope. ``single_tp_level`` picks one of
  ``tp1``/``tp2``/``tp3`` as the bracket TP — the doc flags TP3 as a smoke
  default that does not match Pine's 1/3 split P&L.

Sizing uses ``TargetNotionalPct(notional_pct=...)`` directly. The kwarg is
exposed on the strategy as ``notional_pct`` (not ``margin_pct``) so the name
matches the underlying SizeSpec — leverage is not modelled here, and
``RiskManager`` enforces ``max_total_exposure`` separately. When
leverage-aware sizing becomes needed, follow ``BBKCLegacyCompatStrategy``'s
``TargetMarginPct(margin_pct=, leverage=)`` pattern (separate fields,
distinct semantics) rather than overloading ``notional_pct``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, cast

from backtester.core.context import StrategyContext
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    OrderIntent,
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
        tp_raw = ind[_TP_PRICE_COL[self.single_tp_level]][last_idx]
        if sl_raw is None or tp_raw is None:
            return []

        side: Literal["buy", "sell"] = "buy" if signal == 1 else "sell"
        return [
            OrderIntent(
                symbol=symbol,
                side=side,
                type="market",
                size_spec=TargetNotionalPct(notional_pct=self.notional_pct),
                reason="sats_buy" if signal == 1 else "sats_sell",
                bracket=BracketSpec(
                    take_profit_price=Decimal(str(tp_raw)),
                    stop_loss_price=Decimal(str(sl_raw)),
                ),
            )
        ]


__all__ = ["SATSStrategy"]
