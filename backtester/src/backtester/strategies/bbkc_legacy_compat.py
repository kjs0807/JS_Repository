"""BBKC Legacy Compat Strategy (Phase 2.5 PR T).

Crypto/Bybit_Trading BBKC 모의매매와 최대한 동일한 entry/exit 생명주기를 backtester
계약 위에 표현. PR H ~ PR S 까지의 모든 인프라 (short, leverage, reduce_only, bracket
TP/SL, OCO, ratchet, time_stop, exchange_rule, liquidation, funding) 를 활용하는
첫 통과 사례.

파라미터 (legacy 호환):
- ``bb_period``, ``bb_std`` — Bollinger Bands.
- ``kc_period``, ``kc_mult``, ``atr_period``, ``kc_use_ema`` — Keltner Channel.
- ``rsi_period``, ``rsi_filter`` — RSI 필터 (None 이면 미사용).
- ``tp_pct``, ``sl_pct`` — % 단위 TP/SL.
- ``leverage`` — TargetMarginPct sizing 시 사용.
- ``margin_pct`` — equity 의 몇 % 를 initial margin 으로 사용 (default 0.1 = 10%).
- ``exit_mode`` — "bracket" (TP/SL bracket child) 또는 "close_on_mid" (legacy mid 청산).
- ``drop_tp`` — exit_mode='bracket' 에서 TP 만 비활성화. SL 만 사용.
- ``time_stop_bars`` — N 봉 보유 후 시장가 reduce_only 청산.
- ``allow_short`` — 양방향 진입 허용.

Entry:
- BBKC squeeze release (BB 가 KC 안 → 밖) + RSI 필터 통과 시 진입.
- 양봉 (close > BB mid) → long. 음봉 → short (allow_short=True 일 때).

Bracket / Time stop / Mid exit 로직은 strategies 본 파일에서 직접 처리.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from backtester.core.context import StrategyContext
from backtester.core.orders import (
    BracketSpec,
    ClosePosition,
    OrderIntent,
    TargetMarginPct,
)
from backtester.indicators.base import Indicator
from backtester.indicators.stateless.bb import BollingerBands
from backtester.indicators.stateless.kc import KeltnerChannel
from backtester.indicators.stateless.rsi import RSI
from backtester.strategies.base import BaseStrategy

ExitMode = Literal["bracket", "close_on_mid"]


class BBKCLegacyCompatStrategy(BaseStrategy):
    """Legacy BBKC futures 모의매매 등가 전략 (PR T)."""

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
        rsi_filter: tuple[float, float] | None = None,
        tp_pct: Decimal = Decimal("0.06"),
        sl_pct: Decimal = Decimal("0.07"),
        leverage: Decimal = Decimal("3"),
        margin_pct: Decimal = Decimal("0.1"),
        exit_mode: ExitMode = "bracket",
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
        self.rsi_filter = rsi_filter
        self.tp_pct = Decimal(str(tp_pct))
        self.sl_pct = Decimal(str(sl_pct))
        self.leverage = Decimal(str(leverage))
        self.margin_pct = Decimal(str(margin_pct))
        self.exit_mode = exit_mode
        self.drop_tp = drop_tp
        self.time_stop_bars = time_stop_bars
        self.allow_short = allow_short

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

    def _build_bracket(
        self,
        entry_price: Decimal,
        side: Literal["buy", "sell"],
    ) -> BracketSpec | None:
        if self.exit_mode != "bracket":
            return None
        # Long buy: TP > entry, SL < entry. Short sell: TP < entry, SL > entry.
        if side == "buy":
            tp = entry_price * (Decimal("1") + self.tp_pct)
            sl = entry_price * (Decimal("1") - self.sl_pct)
        else:
            tp = entry_price * (Decimal("1") - self.tp_pct)
            sl = entry_price * (Decimal("1") + self.sl_pct)
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

        if self.rsi_filter is not None:
            rsi_value = rsi_df[rsi_col][curr_idx]
            if rsi_value is None:
                return []
            low, high = self.rsi_filter
            if not (low <= rsi_value <= high):
                return []

        entry_price = Decimal(str(curr_close))
        # Long if curr_close > mid, short if < mid (allow_short=True).
        if curr_close > curr_mid:
            side: Literal["buy", "sell"] = "buy"
        elif self.allow_short and curr_close < curr_mid:
            side = "sell"
        else:
            return []

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
