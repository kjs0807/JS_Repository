"""BBKCSqueeze — Bollinger Band / Keltner Channel Squeeze Breakout (Variant: fixed TP/SL).

Strategy 2 of 9. Breakout category (volatility expansion).
"""
from __future__ import annotations
from typing import Optional
import numpy as np

from src.core.types import Bar, BarSeries
from src.execution.broker import Broker, Fill
from src.strategies.base import IndicatorCache
from src.strategies.indicators.momentum import bollinger, keltner, atr
from src.strategies.indicators.oscillator import rsi


class BBKCSqueeze:
    """BB/KC Squeeze breakout with RSI filter and fixed % TP/SL."""

    name: str = "BBKCSqueeze"

    @staticmethod
    def _validate_exit_params(
        exit_mode: str,
        trail_be_at_tp_frac: float,
        trail_start_at_tp_frac: float,
        trail_distance_tp_frac: float,
    ) -> None:
        """Validate exit-mode invariants. Raises ValueError on violation.

        Shared between __init__ and set_params so dict-based param injection
        (optimizer / walk_forward) cannot bypass the checks.
        """
        if exit_mode not in ("fixed", "be_trail"):
            raise ValueError(
                f"exit_mode must be 'fixed' or 'be_trail', got {exit_mode!r}"
            )
        if exit_mode == "be_trail":
            if not (0 < trail_be_at_tp_frac < trail_start_at_tp_frac < 1.0):
                raise ValueError(
                    f"need 0 < trail_be_at_tp_frac < trail_start_at_tp_frac < 1.0, "
                    f"got be={trail_be_at_tp_frac}, start={trail_start_at_tp_frac}"
                )
            if trail_distance_tp_frac <= 0:
                raise ValueError(
                    f"trail_distance_tp_frac must be > 0, got {trail_distance_tp_frac}"
                )

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 1.5,
        kc_period: int = 20,
        kc_mult: float = 1.0,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_filter: float = 70.0,
        tp_pct: float = 0.06,
        sl_pct: float = 0.07,
        leverage: int = 3,
        timeframe: str = "1h",
        exit_mode: str = "fixed",
        trail_be_at_tp_frac: float = 0.5,
        trail_start_at_tp_frac: float = 0.8,
        trail_distance_tp_frac: float = 0.3,
        drop_tp: bool = False,
        time_stop_bars: int = 0,
    ) -> None:
        self._validate_exit_params(
            exit_mode, trail_be_at_tp_frac, trail_start_at_tp_frac, trail_distance_tp_frac,
        )
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.rsi_filter = rsi_filter
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.leverage = leverage
        self.timeframe = timeframe
        self.exit_mode = exit_mode
        self.trail_be_at_tp_frac = trail_be_at_tp_frac
        self.trail_start_at_tp_frac = trail_start_at_tp_frac
        self.trail_distance_tp_frac = trail_distance_tp_frac
        self.drop_tp = drop_tp
        self.time_stop_bars = time_stop_bars
        self._pos_meta: dict = {}

    @property
    def warmup_bars(self) -> int:
        return max(self.bb_period, self.kc_period, self.atr_period, self.rsi_period) + 10

    def prepare(self, full_series: BarSeries) -> IndicatorCache:
        """전체 시계열에 대해 지표를 사전 계산한다."""
        bb = bollinger(full_series, period=self.bb_period, std=self.bb_std)
        kc = keltner(full_series, ema_period=self.kc_period,
                     atr_period=self.atr_period, atr_mult=self.kc_mult)
        rsi_r = rsi(full_series, period=self.rsi_period)
        # squeeze_on[i] = 1 if BB is inside KC at bar i, else 0
        squeeze_on = ((bb.upper < kc.upper) & (bb.lower > kc.lower)).astype(float)
        return IndicatorCache(arrays={
            "bb_upper": bb.upper,
            "bb_mid": bb.mid,
            "bb_lower": bb.lower,
            "kc_upper": kc.upper,
            "kc_lower": kc.lower,
            "rsi": rsi_r.values,
            "squeeze_on": squeeze_on,
        })

    def on_bar_fast(self, bar: Bar, i: int, cache: IndicatorCache, broker: Broker) -> None:
        """사전 계산된 cache에서 인덱스로 조회."""
        if i < 1:
            return

        sym = bar.symbol
        pos = broker.get_position(sym)

        # ── _pos_meta lazy init / cleanup (on_fill 비의존) ─────────────────
        if pos is None and sym in self._pos_meta:
            del self._pos_meta[sym]
        if pos is not None and sym not in self._pos_meta:
            self._pos_meta[sym] = {
                "be_triggered": False,
                "trail_active": False,
                "bars_held": 0,
            }

        # 포지션 보유 중: bars_held 증가 + 관리
        if pos is not None:
            self._pos_meta[sym]["bars_held"] += 1
            self._manage_position(bar, pos, broker)
            return

        # ── 진입 로직 ─────────────────────────────────────────────────────
        bb_mid = cache.arrays["bb_mid"][i]
        rsi_val = cache.arrays["rsi"][i]
        squeeze_now = cache.arrays["squeeze_on"][i]
        squeeze_prev = cache.arrays["squeeze_on"][i - 1]

        # NaN 체크
        if np.isnan(bb_mid) or np.isnan(rsi_val) or np.isnan(squeeze_now) or np.isnan(squeeze_prev):
            return

        close = bar.close

        # Squeeze 해제 감지: 직전 봉 squeeze ON → 현재 봉 squeeze OFF
        if not (squeeze_prev >= 1.0 and squeeze_now < 1.0):
            return

        price_tp = self.tp_pct / self.leverage
        price_sl = self.sl_pct / self.leverage

        # LONG: 상단 이탈 + RSI 과열 아님
        if close > bb_mid and rsi_val < self.rsi_filter:
            sl = close * (1 - price_sl)
            tp = None if self.drop_tp else close * (1 + price_tp)
            qty = (
                broker.calc_legacy_notional_qty(bar.symbol, close)
                if hasattr(broker, "calc_legacy_notional_qty")
                else broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=close - sl)
            )
            if qty > 0:
                broker.buy(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                           reason=f"BBKCSqueeze LONG rsi={rsi_val:.1f}")

        # SHORT: 하단 이탈 + RSI 과매도 아님
        elif close < bb_mid and rsi_val > (100.0 - self.rsi_filter):
            sl = close * (1 + price_sl)
            tp = None if self.drop_tp else close * (1 - price_tp)
            qty = (
                broker.calc_legacy_notional_qty(bar.symbol, close)
                if hasattr(broker, "calc_legacy_notional_qty")
                else broker.calc_qty(bar.symbol, risk_pct=0.02, stop_distance=sl - close)
            )
            if qty > 0:
                broker.sell(bar.symbol, qty, stop_loss=sl, take_profit=tp,
                            reason=f"BBKCSqueeze SHORT rsi={rsi_val:.1f}")

    def on_bar(self, bar: Bar, series: BarSeries, broker: Broker) -> None:
        """Legacy 경로 — prepare/on_bar_fast가 있으므로 엔진이 이걸 호출 안 함.
        단위 테스트 호환을 위해 on_bar_fast와 동일한 결과 생성."""
        if len(series) < self.warmup_bars:
            return
        # 전체 시리즈에 대해 지표 계산 후 마지막 값 사용
        cache = self.prepare(series)
        idx = len(series) - 1
        self.on_bar_fast(bar, idx, cache, broker)

    def _manage_position(self, bar: Bar, pos, broker: Broker) -> None:
        """포지션 보유 중 관리: be_trail BE/trailing (TP-fraction 단위) + time_stop."""
        sym = bar.symbol
        meta = self._pos_meta[sym]

        # tp_distance = entry × tp_pct / leverage. Safety guards.
        if pos.entry_price <= 0 or self.tp_pct <= 0 or self.leverage <= 0:
            return
        tp_distance = pos.entry_price * self.tp_pct / self.leverage

        close = bar.close
        if pos.side == "LONG":
            move = close - pos.entry_price
        else:
            move = pos.entry_price - close

        if self.exit_mode == "be_trail":
            # BE step (한 번만): close 가 entry 기준 trail_be_at_tp_frac × tp_dist 이상 유리
            if not meta["be_triggered"] and move >= self.trail_be_at_tp_frac * tp_distance:
                broker.update_stop(sym, pos.entry_price)
                meta["be_triggered"] = True

            # Trailing step (활성 후 ratchet only)
            if move >= self.trail_start_at_tp_frac * tp_distance:
                offset = self.trail_distance_tp_frac * tp_distance
                new_sl = (close - offset) if pos.side == "LONG" else (close + offset)

                if not meta["trail_active"]:
                    broker.update_stop(sym, new_sl)
                    meta["trail_active"] = True
                else:
                    if pos.side == "LONG" and new_sl > pos.stop_loss:
                        broker.update_stop(sym, new_sl)
                    elif pos.side == "SHORT" and new_sl < pos.stop_loss:
                        broker.update_stop(sym, new_sl)

        # time_stop fallback (직교 with exit_mode). SL/TP/trailing이 먼저
        # 트리거되면 broker가 포지션을 제거 → 다음 봉에서 pos is None 이라
        # _manage_position 자체가 호출 안 됨. 즉 실질 fallback.
        if self.time_stop_bars > 0 and meta["bars_held"] >= self.time_stop_bars:
            broker.close(sym, reason="time_stop")

    def on_fill(self, fill: Fill) -> None:
        pass

    def get_params(self) -> dict:
        return {
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "kc_period": self.kc_period,
            "kc_mult": self.kc_mult,
            "atr_period": self.atr_period,
            "rsi_period": self.rsi_period,
            "rsi_filter": self.rsi_filter,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "leverage": self.leverage,
            "exit_mode": self.exit_mode,
            "trail_be_at_tp_frac": self.trail_be_at_tp_frac,
            "trail_start_at_tp_frac": self.trail_start_at_tp_frac,
            "trail_distance_tp_frac": self.trail_distance_tp_frac,
            "drop_tp": self.drop_tp,
            "time_stop_bars": self.time_stop_bars,
        }

    def set_params(self, params: dict) -> None:
        """Update parameters with invariant validation.

        Builds candidate values for the four exit-mode invariant inputs,
        validates atomically, then applies all params. On failure no attribute
        is mutated (no partial state).
        """
        # Candidate values: incoming dict overrides current attrs for the
        # four invariant-relevant keys.
        candidate_exit_mode = params.get("exit_mode", self.exit_mode)
        candidate_be = params.get(
            "trail_be_at_tp_frac", self.trail_be_at_tp_frac,
        )
        candidate_start = params.get(
            "trail_start_at_tp_frac", self.trail_start_at_tp_frac,
        )
        candidate_distance = params.get(
            "trail_distance_tp_frac", self.trail_distance_tp_frac,
        )
        self._validate_exit_params(
            candidate_exit_mode, candidate_be, candidate_start, candidate_distance,
        )
        # Validation passed → apply all params
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)


__all__ = ["BBKCSqueeze"]
