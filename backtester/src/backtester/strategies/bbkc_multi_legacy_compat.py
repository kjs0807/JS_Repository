"""BBKC Multi-Symbol Legacy Compat Strategy (Phase 2.5 PR W).

Crypto/Bybit_Trading 의 ``Multi-Symbol BBKC`` 운영 형태 — 여러 심볼에 대해 동일 BBKC
legacy compat 로직을 동시에 가동 — 을 backtester 단일 strategy 인스턴스로 표현.

기존엔 BBKCLegacyCompatStrategy 가 ``ctx.primary_symbol`` 만 다루므로 multi-symbol
운영은 외부 wrapper / 여러 run dir 가 필요했다. PR W 는 단일 strategy 가 ``symbols``
리스트에 대해 child BBKC 를 hold + 매 ``on_bar`` 마다 모든 심볼에 대해 child 를
호출한다 (``dataclasses.replace`` 로 ctx 의 primary_symbol/timeframe 만 교체).

설계:
- per-symbol child = ``BBKCLegacyCompatStrategy`` — 같은 ``child_params`` 로 instantiate.
- 지표 인스턴스는 multi 가 own — children 의 BB/KC/RSI 인스턴스를 multi 의 것으로 교체
  → IndicatorEngine 가 같은 (BB, KC, RSI) 를 (sym, tf) 별로 1 회만 precompute. (중복
  Indicator 가 들어가면 horizontal concat 컬럼 충돌.)
- ``required_indicators`` 는 multi 의 3 개 indicator 만 반환.
- ``on_bar(ctx)`` / ``on_pending_orders(ctx, pending)`` 모두 child 에게 ``replace`` 한
  ctx 로 위임. 결과 intent / action 는 합쳐서 반환.

제약:
- 모든 심볼이 같은 ``timeframe`` 에서 close 한다고 가정 (legacy 운영 형태). 멀티 TF
  multi-symbol 은 후속 PR.
- ``primary_symbol`` 은 BacktestConfig 가 결정 — 그 심볼이 ``symbols`` 안에 포함돼야 한다.
- 각 child 는 자체 ``_meta`` (per-symbol be_trail 상태) 를 유지 — symbol cross-talk 없음.

YAML 예 (preset_loader 가 strategy_params 그대로 전달)::

    strategy_name: bbkc_multi_legacy_compat
    strategy_params:
      symbols: [BTCUSDT, ETHUSDT, AVAXUSDT]
      timeframe: 1h
      child_params:
        leverage: "3"
        margin_pct: "0.05"
        tp_pct: "0.06"
        sl_pct: "0.07"
        rsi_filter: 70
        exit_mode: be_trail
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from backtester.core.context import OrderView, StrategyContext
from backtester.core.errors import ConfigError
from backtester.core.orders import OrderAction, OrderIntent
from backtester.indicators.base import Indicator
from backtester.indicators.stateless.bb import BollingerBands
from backtester.indicators.stateless.kc import KeltnerChannel
from backtester.indicators.stateless.rsi import RSI
from backtester.strategies.base import BaseStrategy
from backtester.strategies.bbkc_legacy_compat import BBKCLegacyCompatStrategy


class BBKCMultiLegacyCompatStrategy(BaseStrategy):
    """N 심볼에 대해 동시 BBKC legacy compat 운영 (PR W).

    Args:
        symbols: 운영 심볼 리스트. 비어 있으면 ``ConfigError``.
        timeframe: 모든 심볼이 공유하는 primary timeframe (legacy 단일 TF 가정).
        child_params: 각 child ``BBKCLegacyCompatStrategy`` 에 전달할 kwargs.
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
                "BBKCMultiLegacyCompatStrategy requires non-empty 'symbols' list"
            )
        # 중복 차단 — 같은 심볼 두 번이면 child 두 번 호출되어 ledger 일관성 깨짐.
        if len(set(symbols)) != len(symbols):
            raise ConfigError(
                f"BBKCMultiLegacyCompatStrategy 'symbols' contains duplicates: {symbols}"
            )
        self.symbols: list[str] = list(symbols)
        self.timeframe: str = timeframe
        self.child_params: dict[str, Any] = dict(child_params or {})

        # multi 가 own 하는 지표 — child instance 교체용.
        bb_period = int(self.child_params.get("bb_period", 20))
        bb_std = float(self.child_params.get("bb_std", 1.5))
        kc_period = int(self.child_params.get("kc_period", 20))
        kc_mult = float(self.child_params.get("kc_mult", 1.0))
        atr_period = int(self.child_params.get("atr_period", 14))
        kc_use_ema = bool(self.child_params.get("kc_use_ema", True))
        rsi_period = int(self.child_params.get("rsi_period", 14))
        self._bb = BollingerBands(period=bb_period, num_std=bb_std)
        self._kc = KeltnerChannel(
            period=kc_period,
            multiplier=kc_mult,
            atr_period=atr_period,
            use_ema=kc_use_ema,
        )
        self._rsi = RSI(period=rsi_period)

        # children — 같은 child_params 로 N 회 instantiate. 그 후 indicator 만 multi 의
        # 것으로 교체해 IndicatorEngine 가 한 세트만 precompute 하도록 한다.
        self._children: dict[str, BBKCLegacyCompatStrategy] = {}
        for sym in self.symbols:
            try:
                child = BBKCLegacyCompatStrategy(**self.child_params)
            except TypeError as e:
                raise ConfigError(
                    f"child_params do not match BBKCLegacyCompatStrategy signature: {e}"
                ) from e
            child._bb = self._bb
            child._kc = self._kc
            child._rsi = self._rsi
            self._children[sym] = child

    def required_indicators(self) -> list[Indicator]:
        return [self._bb, self._kc, self._rsi]

    def _swap_ctx(self, ctx: StrategyContext, symbol: str) -> StrategyContext:
        return replace(
            ctx,
            primary_symbol=symbol,
            primary_timeframe=self.timeframe,
        )

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        out: list[OrderIntent] = []
        for sym in self.symbols:
            child = self._children[sym]
            intents = child.on_bar(self._swap_ctx(ctx, sym))
            out.extend(intents)
        return out

    def on_pending_orders(
        self,
        ctx: StrategyContext,
        pending: tuple[OrderView, ...],
    ) -> list[OrderAction]:
        out: list[OrderAction] = []
        for sym in self.symbols:
            child = self._children[sym]
            # symbol 별 pending 만 child 에게 — child 의 SL stop 매칭이 ``ctx.primary_symbol``
            # 비교를 안 하지만, multi 환경에서는 다른 심볼의 stop 이 pending 에 섞여 있어
            # 잘못된 modify 가 발생할 수 있다. 미리 필터.
            sym_pending = tuple(o for o in pending if o.symbol == sym)
            actions = child.on_pending_orders(self._swap_ctx(ctx, sym), sym_pending)
            out.extend(actions)
        return out
