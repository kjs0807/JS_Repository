"""Inner Band Breakout Strategy — 7-State Finite State Machine.

Entry: close crosses above/below inner band (crossover detection).
Scale-in: price pulls back to inner band.
Stop: ATR-based dynamic stop (fallback to inner band).
Trailing stop: activates after profit exceeds ATR threshold.
Volatility filter: skip entries when bandwidth > threshold.
Exit: price falls back inside bands, outer+RSI exit, trailing stop.
"""

import math
from typing import Optional, Dict
from datetime import datetime

from strategy.dual_bollinger.config import DualBBConfig
from strategy.dual_bollinger.events import (
    EventType, State, StrategyEvent, Position
)


class DualBBStateMachine:
    """7-state FSM for Inner Band Breakout strategy.

    States: FLAT, LONG_1ST, LONG_2ND, LONG_PARTIAL,
            SHORT_1ST, SHORT_2ND, SHORT_PARTIAL
    """

    def __init__(self, symbol: str, config: DualBBConfig) -> None:
        self.symbol = symbol
        self.config = config
        self.state: State = State.FLAT
        self.position: Optional[Position] = None
        self.bars_since_entry: int = 0
        self.events: list = []
        self.prev_close: Optional[float] = None

    def on_bar(self, timestamp: datetime, open_p: float, high: float,
               low: float, close: float, bands: Dict[str, float]) -> Optional[StrategyEvent]:
        """Process one bar and determine state transition.

        Args:
            timestamp: Bar timestamp.
            open_p: Open price.
            high: High price.
            low: Low price.
            close: Close price.
            bands: Band values at this bar:
                'inner_upper', 'inner_lower', 'std', 'atr', 'bandwidth',
                'outer_upper', 'outer_lower', 'rsi'

        Returns:
            StrategyEvent if transition occurred, None otherwise.
        """
        for key in ('inner_upper', 'inner_lower', 'std'):
            val = bands.get(key)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                self.prev_close = close
                return None

        inner_upper = bands['inner_upper']
        inner_lower = bands['inner_lower']
        outer_upper = bands.get('outer_upper')
        outer_lower = bands.get('outer_lower')
        rsi = bands.get('rsi')
        atr = bands.get('atr')
        bandwidth = bands.get('bandwidth')

        # 트레일링 스탑 업데이트 (포지션 보유 중이면)
        if self.position is not None and self.state != State.FLAT:
            self._update_trailing_stop(close, atr)

        event = None

        if self.state == State.FLAT:
            event = self._handle_flat(timestamp, close, inner_upper, inner_lower,
                                      atr, bandwidth)
        elif self.state == State.LONG_1ST:
            self.bars_since_entry += 1
            event = self._handle_long_1st(timestamp, close, inner_upper,
                                          outer_upper, rsi)
        elif self.state == State.LONG_2ND:
            self.bars_since_entry += 1
            event = self._handle_long_2nd(timestamp, close, inner_upper,
                                          outer_upper, rsi)
        elif self.state == State.LONG_PARTIAL:
            self.bars_since_entry += 1
            event = self._handle_long_partial(timestamp, close, inner_upper, inner_lower)
        elif self.state == State.SHORT_1ST:
            self.bars_since_entry += 1
            event = self._handle_short_1st(timestamp, close, inner_lower,
                                           outer_lower, rsi)
        elif self.state == State.SHORT_2ND:
            self.bars_since_entry += 1
            event = self._handle_short_2nd(timestamp, close, inner_lower,
                                           outer_lower, rsi)
        elif self.state == State.SHORT_PARTIAL:
            self.bars_since_entry += 1
            event = self._handle_short_partial(timestamp, close, inner_upper, inner_lower)

        if event is not None:
            self.events.append(event)

        self.prev_close = close
        return event

    # ─── 트레일링 스탑 업데이트 ─────────────────────────────

    def _update_trailing_stop(self, close: float,
                              atr: Optional[float]) -> None:
        """포지션 보유 중 트레일링 스탑 레벨 업데이트."""
        if not self.config.use_trailing_stop:
            return
        if atr is None or math.isnan(atr) or atr <= 0:
            return

        pos = self.position
        activation_distance = atr * self.config.trailing_activation_atr
        trailing_distance = atr * self.config.trailing_distance_atr

        if pos.side == 'LONG':
            # 최고가 업데이트
            pos.max_favorable_price = max(pos.max_favorable_price, close)
            # 활성화 조건: 수익이 ATR * activation 이상
            if pos.max_favorable_price - pos.avg_price >= activation_distance:
                pos.trailing_active = True
                new_trailing = pos.max_favorable_price - trailing_distance
                pos.trailing_stop_level = max(pos.trailing_stop_level, new_trailing)
        else:  # SHORT
            # 최저가 업데이트
            if pos.max_favorable_price == 0:
                pos.max_favorable_price = close
            pos.max_favorable_price = min(pos.max_favorable_price, close)
            # 활성화 조건: 수익이 ATR * activation 이상
            if pos.avg_price - pos.max_favorable_price >= activation_distance:
                pos.trailing_active = True
                new_trailing = pos.max_favorable_price + trailing_distance
                if pos.trailing_stop_level == 0:
                    pos.trailing_stop_level = new_trailing
                else:
                    pos.trailing_stop_level = min(pos.trailing_stop_level, new_trailing)

    def _check_trailing_stop(self, timestamp: datetime, close: float,
                             exit_side: str) -> Optional[StrategyEvent]:
        """트레일링 스탑 도달 여부 확인."""
        if not self.config.use_trailing_stop or self.position is None:
            return None
        if not self.position.trailing_active:
            return None

        triggered = False
        if self.position.side == 'LONG' and close <= self.position.trailing_stop_level:
            triggered = True
        elif self.position.side == 'SHORT' and close >= self.position.trailing_stop_level:
            triggered = True

        if triggered:
            qty = self.position.total_qty
            trail_level = self.position.trailing_stop_level
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.TRAILING_STOP_EXIT,
                symbol=self.symbol, side=exit_side, qty=qty, price=close,
                reason=f"Trailing stop: close {close:.2f} hit trail {trail_level:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )
        return None

    # ─── FLAT ───────────────────────────────────────────────

    def _handle_flat(self, timestamp: datetime, close: float,
                     inner_upper: float, inner_lower: float,
                     atr: Optional[float] = None,
                     bandwidth: Optional[float] = None) -> Optional[StrategyEvent]:
        """FLAT: detect crossover with breakout_pct threshold to enter."""
        if self.prev_close is None:
            return None

        # 변동성 필터: 밴드폭이 너무 크면 진입 금지
        if (self.config.vol_filter_enabled
                and bandwidth is not None
                and not math.isnan(bandwidth)
                and bandwidth > self.config.max_bandwidth_pct):
            return None

        bp = self.config.breakout_pct / 100.0  # % → ratio

        # ATR 기반 스탑 거리 계산
        atr_valid = atr is not None and not math.isnan(atr) and atr > 0
        atr_stop_dist = atr * self.config.atr_stop_multiplier if atr_valid else 0.0

        # LONG: prev_close <= inner_upper AND close > inner_upper * (1 + bp)
        long_threshold = inner_upper * (1 + bp)
        if self.prev_close <= inner_upper and close > long_threshold:
            qty = self.config.base_qty
            # ATR 기반 스탑 vs inner_lower 중 더 가까운 것 (방어적)
            if atr_valid:
                atr_stop = close - atr_stop_dist
                stop_level = max(atr_stop, inner_lower)  # 더 타이트한 스탑
            else:
                stop_level = inner_lower
            self.position = Position(
                symbol=self.symbol, side='LONG',
                entries=[(close, qty, timestamp)],
                total_qty=qty, avg_price=close,
                stop_loss_level=stop_level,
                max_favorable_price=close,
            )
            old_state = self.state
            self.state = State.LONG_1ST
            self.bars_since_entry = 0
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.ENTRY_1ST,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Breakout: close {close:.2f} > {long_threshold:.2f}, stop={stop_level:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # SHORT: prev_close >= inner_lower AND close < inner_lower * (1 - bp)
        short_threshold = inner_lower * (1 - bp)
        if self.prev_close >= inner_lower and close < short_threshold:
            qty = self.config.base_qty
            if atr_valid:
                atr_stop = close + atr_stop_dist
                stop_level = min(atr_stop, inner_upper)  # 더 타이트한 스탑
            else:
                stop_level = inner_upper
            self.position = Position(
                symbol=self.symbol, side='SHORT',
                entries=[(close, qty, timestamp)],
                total_qty=qty, avg_price=close,
                stop_loss_level=stop_level,
                max_favorable_price=close,
            )
            old_state = self.state
            self.state = State.SHORT_1ST
            self.bars_since_entry = 0
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.ENTRY_1ST,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Breakout: close {close:.2f} < {short_threshold:.2f}, stop={stop_level:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )

        return None

    # ─── LONG_1ST ───────────────────────────────────────────

    def _handle_long_1st(self, timestamp: datetime, close: float,
                         inner_upper: float,
                         outer_upper: Optional[float] = None,
                         rsi: Optional[float] = None) -> Optional[StrategyEvent]:
        """LONG_1ST: trailing stop, outer+RSI exit, stop loss, or pullback for 2nd entry."""
        # 0a. 트레일링 스탑
        event = self._check_trailing_stop(timestamp, close, 'SELL')
        if event is not None:
            return event

        # 0b. Outer band + RSI 조기 익절
        if (outer_upper is not None and rsi is not None
                and not math.isnan(outer_upper) and not math.isnan(rsi)
                and close >= outer_upper
                and rsi >= self.config.rsi_overbought):
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.OUTER_RSI_EXIT,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Outer+RSI exit: close {close:.2f} >= outer_upper {outer_upper:.2f}, RSI {rsi:.1f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # 1. Stop loss
        if close < self.position.stop_loss_level:
            qty = self.position.total_qty
            stop_level = self.position.stop_loss_level
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.STOP_LOSS,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Stop loss: close {close:.2f} < stop {stop_level:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # 2. Pullback to inner_upper → 2nd entry
        if close <= inner_upper:
            qty = self.config.scale_qty
            self.position.entries.append((close, qty, timestamp))
            self.position.total_qty += qty
            total_cost = sum(p * q for p, q, _ in self.position.entries)
            self.position.avg_price = total_cost / self.position.total_qty
            old_state = self.state
            self.state = State.LONG_2ND
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.ENTRY_2ND,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Pullback: close {close:.2f} <= inner_upper {inner_upper:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )
        return None

    # ─── LONG_2ND ───────────────────────────────────────────

    def _handle_long_2nd(self, timestamp: datetime, close: float,
                         inner_upper: float,
                         outer_upper: Optional[float] = None,
                         rsi: Optional[float] = None) -> Optional[StrategyEvent]:
        """LONG_2ND: trailing stop, outer+RSI exit, stop loss, or partial exit."""
        # 0a. 트레일링 스탑
        event = self._check_trailing_stop(timestamp, close, 'SELL')
        if event is not None:
            return event

        # 0b. Outer band + RSI 조기 익절
        if (outer_upper is not None and rsi is not None
                and not math.isnan(outer_upper) and not math.isnan(rsi)
                and close >= outer_upper
                and rsi >= self.config.rsi_overbought):
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.OUTER_RSI_EXIT,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Outer+RSI exit: close {close:.2f} >= outer_upper {outer_upper:.2f}, RSI {rsi:.1f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # 1. Stop loss
        if close < self.position.stop_loss_level:
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.STOP_LOSS,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Stop loss: close {close:.2f} < stop",
                state_before=old_state.value, state_after=self.state.value
            )

        # 2. Price drops below inner_upper → partial exit (추세 약화)
        if close < inner_upper:
            exit_qty = min(self.config.base_qty, self.position.total_qty - 1)
            if exit_qty <= 0:
                return None
            self.position.total_qty -= exit_qty
            old_state = self.state
            self.state = State.LONG_PARTIAL
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.PARTIAL_EXIT,
                symbol=self.symbol, side='SELL', qty=exit_qty, price=close,
                reason=f"Partial exit: close {close:.2f} < inner_upper {inner_upper:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )
        return None

    # ─── LONG_PARTIAL ───────────────────────────────────────

    def _handle_long_partial(self, timestamp: datetime, close: float,
                             inner_upper: float, inner_lower: float) -> Optional[StrategyEvent]:
        """LONG_PARTIAL: trailing stop, full exit on inner_lower break or band re-entry."""
        # 0. 트레일링 스탑
        event = self._check_trailing_stop(timestamp, close, 'SELL')
        if event is not None:
            return event

        # 1. Full exit: close < inner_lower or stop
        if close < inner_lower or close < self.position.stop_loss_level:
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.FULL_EXIT,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Full exit: close {close:.2f} < inner_lower {inner_lower:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # 2. Band re-entry: was above inner_upper, now drops back below
        if self.prev_close is not None and self.prev_close > inner_upper and close <= inner_upper:
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.BAND_EXIT,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Band exit: close {close:.2f} crossed below inner_upper {inner_upper:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )
        return None

    # ─── SHORT_1ST ──────────────────────────────────────────

    def _handle_short_1st(self, timestamp: datetime, close: float,
                          inner_lower: float,
                          outer_lower: Optional[float] = None,
                          rsi: Optional[float] = None) -> Optional[StrategyEvent]:
        """SHORT_1ST: trailing stop, outer+RSI exit, stop loss, or pullback for 2nd entry."""
        # 0a. 트레일링 스탑
        event = self._check_trailing_stop(timestamp, close, 'BUY')
        if event is not None:
            return event

        # 0b. Outer band + RSI 조기 익절
        if (outer_lower is not None and rsi is not None
                and not math.isnan(outer_lower) and not math.isnan(rsi)
                and close <= outer_lower
                and rsi <= self.config.rsi_oversold):
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.OUTER_RSI_EXIT,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Outer+RSI exit: close {close:.2f} <= outer_lower {outer_lower:.2f}, RSI {rsi:.1f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # 1. Stop loss
        if close > self.position.stop_loss_level:
            qty = self.position.total_qty
            stop_level = self.position.stop_loss_level
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.STOP_LOSS,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Stop loss: close {close:.2f} > stop {stop_level:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # 2. Pullback to inner_lower → 2nd entry
        if close >= inner_lower:
            qty = self.config.scale_qty
            self.position.entries.append((close, qty, timestamp))
            self.position.total_qty += qty
            total_cost = sum(p * q for p, q, _ in self.position.entries)
            self.position.avg_price = total_cost / self.position.total_qty
            old_state = self.state
            self.state = State.SHORT_2ND
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.ENTRY_2ND,
                symbol=self.symbol, side='SELL', qty=qty, price=close,
                reason=f"Pullback: close {close:.2f} >= inner_lower {inner_lower:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )
        return None

    # ─── SHORT_2ND ──────────────────────────────────────────

    def _handle_short_2nd(self, timestamp: datetime, close: float,
                          inner_lower: float,
                          outer_lower: Optional[float] = None,
                          rsi: Optional[float] = None) -> Optional[StrategyEvent]:
        """SHORT_2ND: trailing stop, outer+RSI exit, stop loss, or partial exit."""
        # 0a. 트레일링 스탑
        event = self._check_trailing_stop(timestamp, close, 'BUY')
        if event is not None:
            return event

        # 0b. Outer band + RSI 조기 익절
        if (outer_lower is not None and rsi is not None
                and not math.isnan(outer_lower) and not math.isnan(rsi)
                and close <= outer_lower
                and rsi <= self.config.rsi_oversold):
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.OUTER_RSI_EXIT,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Outer+RSI exit: close {close:.2f} <= outer_lower {outer_lower:.2f}, RSI {rsi:.1f}",
                state_before=old_state.value, state_after=self.state.value
            )

        # 1. Stop loss
        if close > self.position.stop_loss_level:
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.STOP_LOSS,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Stop loss: close {close:.2f} > stop",
                state_before=old_state.value, state_after=self.state.value
            )

        # 2. Price rises above inner_lower → partial exit (추세 약화)
        if close > inner_lower:
            exit_qty = min(self.config.base_qty, self.position.total_qty - 1)
            if exit_qty <= 0:
                return None
            self.position.total_qty -= exit_qty
            old_state = self.state
            self.state = State.SHORT_PARTIAL
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.PARTIAL_EXIT,
                symbol=self.symbol, side='BUY', qty=exit_qty, price=close,
                reason=f"Partial exit: close {close:.2f} > inner_lower {inner_lower:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )
        return None

    # ─── SHORT_PARTIAL ──────────────────────────────────────

    def _handle_short_partial(self, timestamp: datetime, close: float,
                              inner_upper: float, inner_lower: float) -> Optional[StrategyEvent]:
        """SHORT_PARTIAL: trailing stop, full exit on inner_upper break or band re-entry."""
        # 0. 트레일링 스탑
        event = self._check_trailing_stop(timestamp, close, 'BUY')
        if event is not None:
            return event

        if close > inner_upper or close > self.position.stop_loss_level:
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.FULL_EXIT,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Full exit: close {close:.2f} > inner_upper {inner_upper:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )

        if self.prev_close is not None and self.prev_close < inner_lower and close >= inner_lower:
            qty = self.position.total_qty
            old_state = self.state
            self.state = State.FLAT
            self.position = None
            return StrategyEvent(
                timestamp=timestamp, event_type=EventType.BAND_EXIT,
                symbol=self.symbol, side='BUY', qty=qty, price=close,
                reason=f"Band exit: close {close:.2f} crossed above inner_lower {inner_lower:.2f}",
                state_before=old_state.value, state_after=self.state.value
            )
        return None

    def reset(self) -> None:
        """Reset state machine to FLAT."""
        self.state = State.FLAT
        self.position = None
        self.bars_since_entry = 0
        self.events.clear()
        self.prev_close = None
