"""BB-KC Squeeze 전략 (TTM Squeeze 변형, Phase 1 long-only).

신호 발행 도구: `tools/export_db_to_parquet.py` (skeleton — 집 환경에서 채워 OHLCV 캐시 생성).

진입 (Entry): squeeze release 직후 + close > BB middle
- Squeeze ON: BB가 KC 안쪽 (BB_upper < KC_upper AND BB_lower > KC_lower)
- Release: 직전 봉 squeeze였으나 현재 봉 squeeze 아님

청산 (Exit): close < BB middle (mean revert)

기본 파라미터는 legacy 모의매매 호환:
- bb_period=20, bb_num_std=1.5
- kc_period=20, kc_multiplier=1.0, kc_atr_period=14, kc_use_ema=True

Phase 1 한정 (legacy 대비 차이 — 회귀 fixture 비교 시 인지 필요):
- short 진입 미지원 — Sizer가 차단 (Phase 2 활성)
- TP / SL / trailing stop 미지원 — limit/stop 주문이 Phase 2
- time_stop / expires_at 미지원 — Phase 1.5에서 expiration 도입
- RSI / 추가 모멘텀 필터 미지원 — Phase 1.5+ 추가
- 청산은 close < BB middle (mean revert) 하나만

Phase 1 한정 — `_has_position` desync 리스크:
- intent 발행 시점에 즉시 갱신. Risk reject / Sizer reject로 실제 주문이 OrderBook에
  add되지 않으면 strategy 내부 상태와 ledger 실제 상태가 어긋날 수 있다.
- Phase 1 기본 환경(빈 risk_limits, simple market)에서는 buy intent가 거의 통과 →
  desync 위험 낮음. Phase 1.5+에서 `ctx.position()` 등으로 ledger 기반 동기화 예정.
"""

from __future__ import annotations

from decimal import Decimal

from backtester.core.context import StrategyContext
from backtester.core.orders import ClosePosition, OrderIntent, TargetUnits
from backtester.indicators.base import Indicator
from backtester.indicators.stateless.bb import BollingerBands
from backtester.indicators.stateless.kc import KeltnerChannel
from backtester.strategies.base import BaseStrategy


class BBKCSqueezeStrategy(BaseStrategy):
    """BB-KC Squeeze 전략 (Phase 1, legacy-호환 기본값)."""

    def __init__(
        self,
        *,
        bb_period: int = 20,
        bb_num_std: float = 1.5,
        kc_period: int = 20,
        kc_multiplier: float = 1.0,
        kc_atr_period: int = 14,
        kc_use_ema: bool = True,
        order_size: Decimal | None = None,
    ) -> None:
        self._bb = BollingerBands(period=bb_period, num_std=bb_num_std)
        self._kc = KeltnerChannel(
            period=kc_period,
            multiplier=kc_multiplier,
            atr_period=kc_atr_period,
            use_ema=kc_use_ema,
        )
        self._order_size: Decimal = order_size if order_size is not None else Decimal("1")
        self._has_position: bool = False

    def required_indicators(self) -> list[Indicator]:
        return [self._bb, self._kc]

    @staticmethod
    def _is_squeezed(
        bb_upper: float | None,
        bb_lower: float | None,
        kc_upper: float | None,
        kc_lower: float | None,
    ) -> bool:
        """BB가 KC 안쪽이면 squeeze. 워밍업 등으로 None이면 미정 → False."""
        if bb_upper is None or bb_lower is None or kc_upper is None or kc_lower is None:
            return False
        return bb_upper < kc_upper and bb_lower > kc_lower

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe
        bars = ctx.bars[symbol][tf]

        # 직전 봉과 현재 봉 squeeze 비교에 최소 2봉 필요. 워밍업은 Engine이 보장하지만
        # 데이터가 짧을 때 방어적으로 한 번 더 확인.
        n = bars.height
        if n < 2:
            return []

        bb_df = self._bb.compute(bars)
        kc_df = self._kc.compute(bars)

        bb_upper_col = f"{self._bb.name}_upper"
        bb_lower_col = f"{self._bb.name}_lower"
        bb_mid_col = f"{self._bb.name}_mid"
        kc_upper_col = f"{self._kc.name}_upper"
        kc_lower_col = f"{self._kc.name}_lower"

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
            return []  # 워밍업 미충족

        # Entry: squeeze release + 상승 모멘텀 + 미보유
        if released and curr_close > curr_mid and not self._has_position:
            self._has_position = True  # 주의: intent 발행 시점에 즉시 갱신 (Phase 1)
            return [
                OrderIntent(
                    symbol=symbol,
                    side="buy",
                    type="market",
                    size_spec=TargetUnits(units=self._order_size),
                    reason="bbkc_squeeze_release",
                )
            ]

        # Exit: 보유 중 + 가격이 mid 하회
        if self._has_position and curr_close < curr_mid:
            self._has_position = False  # 주의: intent 발행 시점에 즉시 갱신 (Phase 1)
            return [
                OrderIntent(
                    symbol=symbol,
                    side="sell",
                    type="market",
                    size_spec=ClosePosition(),
                    reason="bbkc_close_below_mid",
                )
            ]

        return []
