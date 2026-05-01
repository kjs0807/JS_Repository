"""BBKC Legacy Compat Strategy (Phase 2.5 PR T, PR U parity 정정).

Crypto/Bybit_Trading BBKC 모의매매와 최대한 동일한 entry/exit 생명주기를 backtester
계약 위에 표현. PR H ~ PR S 까지의 모든 인프라 (short, leverage, reduce_only, bracket
TP/SL, OCO, ratchet, time_stop, exchange_rule, liquidation, funding) 를 활용.

PR U parity 정정:
- TP/SL: legacy 와 동일하게 ``tp_pct/leverage`` / ``sl_pct/leverage`` 가격 % 적용.
  legacy: ``price_tp = tp_pct/leverage`` → 3x leverage + tp_pct=6% 면 가격 변동 2%.
  이전 PR T 는 leverage 분할 없이 entry × tp_pct 였음.
- RSI 필터: legacy 와 동일한 단방향 임계 — long ``rsi < rsi_filter``,
  short ``rsi > 100 - rsi_filter``. 이전 PR T 는 (low, high) range 였음.
- ``exit_mode='be_trail'`` 활성: tp_distance=entry × tp_pct/leverage 기준.
  * be_trigger: move >= ``trail_be_at_tp_frac * tp_distance`` → SL=entry 로 modify.
  * trail_start: move >= ``trail_start_at_tp_frac * tp_distance`` → SL=close ∓ ``trail_
    distance_tp_frac * tp_distance``. ratchet only (PR M).

파라미터 (legacy 호환):
- ``bb_period`` / ``bb_std`` / ``kc_period`` / ``kc_mult`` / ``atr_period`` /
  ``kc_use_ema``: Bollinger / Keltner.
- ``rsi_period`` / ``rsi_filter`` (float, default 70): one-sided.
- ``tp_pct`` / ``sl_pct``: % (price-level = pct/leverage 적용).
- ``leverage``: TargetMarginPct + price-level pct 분할.
- ``margin_pct``: initial margin %.
- ``exit_mode``: "fixed" (= bracket TP/SL only) | "be_trail" | "close_on_mid".
- ``trail_be_at_tp_frac`` / ``trail_start_at_tp_frac`` / ``trail_distance_tp_frac``:
  be_trail 모드 파라미터 (legacy default 0.5 / 0.8 / 0.3).
- ``drop_tp`` / ``time_stop_bars`` / ``allow_short``: 동일.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from backtester.core.context import OrderView, StrategyContext
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    OrderAction,
    OrderIntent,
    TargetMarginPct,
)
from backtester.indicators.base import Indicator
from backtester.indicators.stateless.bb import BollingerBands
from backtester.indicators.stateless.kc import KeltnerChannel
from backtester.indicators.stateless.rsi import RSI
from backtester.strategies.base import BaseStrategy

ExitMode = Literal["fixed", "be_trail", "close_on_mid"]


class BBKCLegacyCompatStrategy(BaseStrategy):
    """Legacy BBKC futures 모의매매 등가 전략 (PR T + PR U parity)."""

    def __init__(
        self,
        *,
        bb_period: int = 20,
        bb_std: float = 1.5,
        kc_period: int = 20,
        kc_mult: float = 1.0,
        atr_period: int = 14,
        kc_use_ema: bool = True,
        rsi_period: int = 14,
        rsi_filter: float = 70.0,
        tp_pct: Decimal = Decimal("0.06"),
        sl_pct: Decimal = Decimal("0.07"),
        leverage: Decimal = Decimal("3"),
        margin_pct: Decimal = Decimal("0.1"),
        exit_mode: ExitMode = "fixed",
        trail_be_at_tp_frac: Decimal = Decimal("0.5"),
        trail_start_at_tp_frac: Decimal = Decimal("0.8"),
        trail_distance_tp_frac: Decimal = Decimal("0.3"),
        drop_tp: bool = False,
        time_stop_bars: int | None = None,
        allow_short: bool = False,
    ) -> None:
        self._bb = BollingerBands(period=bb_period, num_std=bb_std)
        self._kc = KeltnerChannel(
            period=kc_period,
            multiplier=kc_mult,
            atr_period=atr_period,
            use_ema=kc_use_ema,
        )
        self._rsi = RSI(period=rsi_period)
        self.rsi_filter = float(rsi_filter)
        self.tp_pct = Decimal(str(tp_pct))
        self.sl_pct = Decimal(str(sl_pct))
        self.leverage = Decimal(str(leverage))
        self.margin_pct = Decimal(str(margin_pct))
        self.exit_mode = exit_mode
        self.trail_be_at_tp_frac = Decimal(str(trail_be_at_tp_frac))
        self.trail_start_at_tp_frac = Decimal(str(trail_start_at_tp_frac))
        self.trail_distance_tp_frac = Decimal(str(trail_distance_tp_frac))
        self.drop_tp = drop_tp
        self.time_stop_bars = time_stop_bars
        self.allow_short = allow_short
        # PR U: be_trail 내부 상태 (per-symbol) — legacy _pos_meta 동등.
        self._meta: dict[str, dict[str, Any]] = {}

    def required_indicators(self) -> list[Indicator]:
        return [self._bb, self._kc, self._rsi]

    @staticmethod
    def _is_squeezed(
        bb_upper: float | None,
        bb_lower: float | None,
        kc_upper: float | None,
        kc_lower: float | None,
    ) -> bool:
        if (
            bb_upper is None
            or bb_lower is None
            or kc_upper is None
            or kc_lower is None
        ):
            return False
        return bb_upper < kc_upper and bb_lower > kc_lower

    def _price_pct(self) -> tuple[Decimal, Decimal]:
        """Legacy parity: ``price_tp = tp_pct / leverage``, ``price_sl = sl_pct / leverage``."""
        price_tp = self.tp_pct / self.leverage
        price_sl = self.sl_pct / self.leverage
        return price_tp, price_sl

    def _build_bracket(
        self,
        entry_price: Decimal,
        side: Literal["buy", "sell"],
    ) -> BracketSpec | None:
        if self.exit_mode == "close_on_mid":
            return None
        # PR U: legacy parity — price-level pct = pct / leverage.
        price_tp, price_sl = self._price_pct()
        if side == "buy":
            tp = entry_price * (Decimal("1") + price_tp)
            sl = entry_price * (Decimal("1") - price_sl)
        else:
            tp = entry_price * (Decimal("1") - price_tp)
            sl = entry_price * (Decimal("1") + price_sl)
        if self.drop_tp:
            return BracketSpec(
                stop_loss_price=sl,
                time_stop_bars=self.time_stop_bars,
            )
        return BracketSpec(
            take_profit_price=tp,
            stop_loss_price=sl,
            time_stop_bars=self.time_stop_bars,
        )

    def _meta_for(self, symbol: str) -> dict[str, Any]:
        m = self._meta.get(symbol)
        if m is None:
            m = {"be_triggered": False, "trail_active": False, "last_sl": None}
            self._meta[symbol] = m
        return m

    def _reset_meta(self, symbol: str) -> None:
        self._meta[symbol] = {
            "be_triggered": False,
            "trail_active": False,
            "last_sl": None,
        }

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe
        bars = ctx.bars[symbol][tf]
        if bars.height < 2:
            return []

        # Time stop 우선 검사 — 보유 중이고 N 봉 초과 → close.
        if self.time_stop_bars is not None and ctx.has_position(symbol):
            held = ctx.bars_held(symbol)
            if held is not None and held >= self.time_stop_bars:
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
                            reason="bbkc_time_stop",
                            reduce_only=True,
                        )
                    ]

        # Mid exit (legacy) — exit_mode='close_on_mid' 인 경우만.
        bb_df = ctx.indicators[symbol][tf] if ctx.indicators.has(symbol, tf) else None
        if bb_df is None:
            bb_df = self._bb.compute(bars)
        bb_upper_col = f"{self._bb.name}_upper"
        bb_lower_col = f"{self._bb.name}_lower"
        bb_mid_col = f"{self._bb.name}_mid"
        kc_upper_col = f"{self._kc.name}_upper"
        kc_lower_col = f"{self._kc.name}_lower"
        rsi_col = self._rsi.name

        # 통합 ctx.indicators 에 위 컬럼이 모두 있어야 함 (Engine precompute).
        cols = bb_df.columns
        if not all(
            c in cols for c in (bb_upper_col, bb_lower_col, bb_mid_col, rsi_col)
        ):
            # fallback — 직접 compute
            bb_df = self._bb.compute(bars)
            kc_df = self._kc.compute(bars)
            rsi_df = self._rsi.compute(bars)
        else:
            kc_df = bb_df
            rsi_df = bb_df

        n = bars.height
        prev_idx = n - 2
        curr_idx = n - 1

        prev_squeeze = self._is_squeezed(
            bb_df[bb_upper_col][prev_idx],
            bb_df[bb_lower_col][prev_idx],
            kc_df[kc_upper_col][prev_idx],
            kc_df[kc_lower_col][prev_idx],
        )
        curr_squeeze = self._is_squeezed(
            bb_df[bb_upper_col][curr_idx],
            bb_df[bb_lower_col][curr_idx],
            kc_df[kc_upper_col][curr_idx],
            kc_df[kc_lower_col][curr_idx],
        )
        released = prev_squeeze and not curr_squeeze

        curr_close = bars["close"][curr_idx]
        curr_mid = bb_df[bb_mid_col][curr_idx]
        if curr_close is None or curr_mid is None:
            return []

        # Mid exit (legacy)
        if self.exit_mode == "close_on_mid" and ctx.has_position(symbol):
            pos = ctx.position(symbol)
            assert pos is not None
            # long + close < mid → sell. short + close > mid → buy.
            if pos.size > 0 and curr_close < curr_mid:
                return [
                    OrderIntent(
                        symbol=symbol,
                        side="sell",
                        type="market",
                        size_spec=ClosePosition(),
                        reason="bbkc_close_below_mid",
                        reduce_only=True,
                    )
                ]
            if pos.size < 0 and curr_close > curr_mid:
                return [
                    OrderIntent(
                        symbol=symbol,
                        side="buy",
                        type="market",
                        size_spec=ClosePosition(),
                        reason="bbkc_close_above_mid",
                        reduce_only=True,
                    )
                ]

        # 진입 — release 직후 + 미보유. RSI 필터 통과 필요 (있을 경우).
        if not released:
            return []
        if ctx.has_position(symbol):
            return []

        rsi_value = rsi_df[rsi_col][curr_idx]
        if rsi_value is None:
            return []

        entry_price = Decimal(str(curr_close))
        # PR U: legacy 단방향 RSI 필터.
        # LONG: close > mid AND rsi < rsi_filter
        # SHORT: close < mid AND rsi > 100 - rsi_filter
        if curr_close > curr_mid and rsi_value < self.rsi_filter:
            side: Literal["buy", "sell"] = "buy"
        elif (
            self.allow_short
            and curr_close < curr_mid
            and rsi_value > (100.0 - self.rsi_filter)
        ):
            side = "sell"
        else:
            return []

        # 새 entry 시 meta reset.
        self._reset_meta(symbol)
        return [
            OrderIntent(
                symbol=symbol,
                side=side,
                type="market",
                size_spec=TargetMarginPct(
                    margin_pct=self.margin_pct,
                    leverage=self.leverage,
                ),
                reason="bbkc_legacy_release",
                bracket=self._build_bracket(entry_price, side),
            )
        ]

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        """PR U: be_trail 모드에서 SL stop 의 BE / trailing modify 발행."""
        if self.exit_mode != "be_trail":
            return []
        symbol = ctx.primary_symbol
        pos = ctx.position(symbol)
        if pos is None or pos.is_flat:
            return []
        # SL stop child 찾기 — reason="bracket_sl:..." + reduce_only stop
        sl_order: OrderView | None = None
        for o in pending:
            if (
                o.symbol == symbol
                and o.type == "stop"
                and o.stop_price is not None
                and (o.side == ("sell" if pos.size > 0 else "buy"))
            ):
                sl_order = o
                break
        if sl_order is None:
            return []

        # legacy parity: tp_distance = entry × tp_pct / leverage.
        if pos.avg_price <= 0 or self.tp_pct <= 0 or self.leverage <= 0:
            return []
        tp_distance = pos.avg_price * self.tp_pct / self.leverage
        bars = ctx.bars[symbol][ctx.primary_timeframe]
        if bars.height == 0:
            return []
        close = Decimal(str(bars["close"][-1]))
        meta = self._meta_for(symbol)

        # move = 유리 방향 이동
        if pos.size > 0:
            move = close - pos.avg_price
        else:
            move = pos.avg_price - close

        actions: list[OrderAction] = []
        new_sl: Decimal | None = None

        # BE: move ≥ trail_be_at_tp_frac * tp_distance → SL = entry
        if (
            not meta["be_triggered"]
            and move >= self.trail_be_at_tp_frac * tp_distance
        ):
            new_sl = pos.avg_price
            meta["be_triggered"] = True

        # Trail: move ≥ trail_start_at_tp_frac * tp_distance → SL = close ∓ offset.
        # ratchet (PR M): long 위로만 / short 아래로만 — OrderBook.modify 가 강제.
        if move >= self.trail_start_at_tp_frac * tp_distance:
            offset = self.trail_distance_tp_frac * tp_distance
            trail_sl = (
                (close - offset) if pos.size > 0 else (close + offset)
            )
            # ratchet 우호 방향만 적용
            old_sl = sl_order.stop_price
            assert old_sl is not None
            if pos.size > 0 and trail_sl > old_sl:
                new_sl = trail_sl
                meta["trail_active"] = True
            elif pos.size < 0 and trail_sl < old_sl:
                new_sl = trail_sl
                meta["trail_active"] = True

        if new_sl is not None and new_sl != sl_order.stop_price:
            actions.append(
                OrderAction(
                    type="modify",
                    order_id=sl_order.id,
                    modify_stop_price=new_sl,
                )
            )
            meta["last_sl"] = new_sl
        return actions
