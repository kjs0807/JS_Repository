"""Dual Bollinger Band Backtest Engine.

Event-driven backtest engine for the inner band breakout strategy.
"""

from dataclasses import dataclass, field
import math
from typing import Optional
import pandas as pd
import numpy as np

from strategy.dual_bollinger.config import DualBBConfig
from strategy.dual_bollinger.events import (
    EventType, State, StrategyEvent, Position, TradeRecord
)
from strategy.dual_bollinger.bands import (
    calculate_bands, calculate_rsi, calculate_atr, calculate_bandwidth
)
from strategy.dual_bollinger.state_machine import DualBBStateMachine


@dataclass
class DualBBBacktestResult:
    """Backtest result container."""
    config: DualBBConfig
    symbol: str
    point_value: float
    period: str
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    trades_with_2nd_entry: int
    avg_holding_bars: float
    exits_by_reason: dict
    mae_avg: float
    mfe_avg: float
    equity_curve: list[float]
    events: list[StrategyEvent]
    trades: list[TradeRecord]


class DualBBBacktestEngine:
    """Event-driven backtest engine for Inner Band Breakout strategy."""

    def __init__(self, config: DualBBConfig):
        self.config = config

    def run(
        self,
        df: pd.DataFrame,
        symbol: str,
        initial_capital: float = 100000.0,
        commission_per_contract: float = 0.0,
        point_value: float = 1.0,
        bars_per_day: int = 1
    ) -> DualBBBacktestResult:
        """Run backtest on OHLCV data."""
        bands = calculate_bands(df['close'], self.config)
        rsi_series = calculate_rsi(df['close'], self.config.rsi_period)
        atr_series = calculate_atr(df['high'], df['low'], df['close'],
                                   self.config.atr_stop_period)
        bandwidth_series = calculate_bandwidth(
            bands['inner_upper'], bands['inner_lower'], bands['ma'])
        state_machine = DualBBStateMachine(symbol, self.config)

        equity = initial_capital
        equity_curve = [initial_capital]
        events_log: list[StrategyEvent] = []
        trades: list[TradeRecord] = []

        current_trade_entry_idx: Optional[int] = None
        current_trade_entry_price: Optional[float] = None
        current_trade_direction: Optional[int] = None
        current_trade_qty: int = 0
        current_trade_had_2nd_entry: bool = False
        current_trade_mae: float = 0.0
        current_trade_mfe: float = 0.0

        returns: list[float] = []
        prev_equity = initial_capital

        for i in range(len(df)):
            timestamp = df.index[i]
            open_price = df['open'].iloc[i]
            high_price = df['high'].iloc[i]
            low_price = df['low'].iloc[i]
            close_price = df['close'].iloc[i]

            inner_upper_val = bands['inner_upper'].iloc[i]
            inner_lower_val = bands['inner_lower'].iloc[i]
            outer_upper_val = bands['outer_upper'].iloc[i]
            outer_lower_val = bands['outer_lower'].iloc[i]
            std_val = bands['std'].iloc[i]
            rsi_val = rsi_series.iloc[i]
            atr_val = atr_series.iloc[i]
            bw_val = bandwidth_series.iloc[i]

            if pd.isna(inner_upper_val) or pd.isna(std_val):
                equity_curve.append(equity)
                continue

            bands_at_bar = {
                'std': float(std_val),
                'inner_upper': float(inner_upper_val),
                'inner_lower': float(inner_lower_val),
                'outer_upper': float(outer_upper_val) if not pd.isna(outer_upper_val) else float('nan'),
                'outer_lower': float(outer_lower_val) if not pd.isna(outer_lower_val) else float('nan'),
                'rsi': float(rsi_val) if not pd.isna(rsi_val) else float('nan'),
                'atr': float(atr_val) if not pd.isna(atr_val) else float('nan'),
                'bandwidth': float(bw_val) if not pd.isna(bw_val) else float('nan'),
            }

            event = state_machine.on_bar(
                timestamp, open_price, high_price, low_price, close_price,
                bands_at_bar
            )

            if event is not None:
                events_log.append(event)

                if event.event_type in [EventType.ENTRY_1ST, EventType.ENTRY_2ND]:
                    if event.event_type == EventType.ENTRY_1ST:
                        current_trade_entry_idx = i
                        current_trade_entry_price = event.price
                        current_trade_direction = 1 if event.side == 'BUY' else -1
                        current_trade_qty = event.qty
                        current_trade_had_2nd_entry = False
                        current_trade_mae = 0.0
                        current_trade_mfe = 0.0
                    else:
                        current_trade_had_2nd_entry = True
                        current_trade_qty += event.qty

                    commission = event.qty * commission_per_contract * 2
                    equity -= commission

                elif event.event_type == EventType.PARTIAL_EXIT:
                    if current_trade_entry_idx is not None:
                        partial_pnl = (event.price - current_trade_entry_price) * current_trade_direction * event.qty * point_value
                        equity += partial_pnl
                        current_trade_qty -= event.qty

                elif event.event_type in [
                    EventType.STOP_LOSS, EventType.FULL_EXIT,
                    EventType.BAND_EXIT, EventType.OUTER_RSI_EXIT,
                    EventType.TRAILING_STOP_EXIT,
                    EventType.EMERGENCY_EXIT
                ]:
                    if current_trade_entry_idx is not None:
                        pnl = (event.price - current_trade_entry_price) * current_trade_direction * current_trade_qty * point_value
                        equity += pnl

                        holding_bars = i - current_trade_entry_idx
                        trade = TradeRecord(
                            symbol=symbol,
                            side='LONG' if current_trade_direction == 1 else 'SHORT',
                            entry_price=current_trade_entry_price,
                            exit_price=event.price,
                            qty=current_trade_qty,
                            pnl=pnl,
                            entry_time=df.index[current_trade_entry_idx],
                            exit_time=timestamp,
                            exit_reason=event.event_type.value,
                            had_2nd_entry=current_trade_had_2nd_entry,
                            holding_bars=holding_bars,
                            mae=current_trade_mae,
                            mfe=current_trade_mfe
                        )
                        trades.append(trade)

                        current_trade_entry_idx = None
                        current_trade_entry_price = None
                        current_trade_direction = None
                        current_trade_qty = 0
                        current_trade_had_2nd_entry = False
                        current_trade_mae = 0.0
                        current_trade_mfe = 0.0

            if current_trade_entry_idx is not None and current_trade_entry_price is not None:
                if current_trade_direction == 1:
                    adverse_pnl = (low_price - current_trade_entry_price) * current_trade_qty * point_value
                    favorable_pnl = (high_price - current_trade_entry_price) * current_trade_qty * point_value
                else:
                    adverse_pnl = (current_trade_entry_price - high_price) * current_trade_qty * point_value
                    favorable_pnl = (current_trade_entry_price - low_price) * current_trade_qty * point_value

                current_trade_mae = min(current_trade_mae, adverse_pnl)
                current_trade_mfe = max(current_trade_mfe, favorable_pnl)

            equity_curve.append(equity)

            if prev_equity > 0:
                bar_return = (equity - prev_equity) / prev_equity
                returns.append(bar_return)
            prev_equity = equity

        # Force close remaining position
        if state_machine.state != State.FLAT and state_machine.position is not None:
            final_price = df['close'].iloc[-1]
            exit_side = 'SELL' if state_machine.position.side == 'LONG' else 'BUY'
            exit_qty = state_machine.position.total_qty
            old_state = state_machine.state.value

            final_event = StrategyEvent(
                timestamp=df.index[-1],
                event_type=EventType.EMERGENCY_EXIT,
                symbol=symbol, side=exit_side, qty=exit_qty, price=final_price,
                reason="End of backtest",
                state_before=old_state, state_after=State.FLAT.value
            )
            events_log.append(final_event)

            if current_trade_entry_idx is not None:
                pnl = (final_price - current_trade_entry_price) * current_trade_direction * current_trade_qty * point_value
                equity += pnl
                holding_bars = len(df) - 1 - current_trade_entry_idx
                trade = TradeRecord(
                    symbol=symbol,
                    side=state_machine.position.side,
                    entry_price=current_trade_entry_price,
                    exit_price=final_price,
                    qty=current_trade_qty, pnl=pnl,
                    entry_time=df.index[current_trade_entry_idx],
                    exit_time=df.index[-1],
                    exit_reason=EventType.EMERGENCY_EXIT.value,
                    had_2nd_entry=current_trade_had_2nd_entry,
                    holding_bars=holding_bars,
                    mae=current_trade_mae, mfe=current_trade_mfe
                )
                trades.append(trade)

            equity_curve.append(equity)

        # Statistics
        total_return_pct = ((equity - initial_capital) / initial_capital) * 100

        if len(returns) > 0 and np.std(returns) > 0:
            sharpe_ratio = (np.mean(returns) / np.std(returns)) * math.sqrt(252 * bars_per_day)
        else:
            sharpe_ratio = 0.0

        max_dd_pct = self._calculate_max_drawdown(equity_curve)

        if len(trades) > 0:
            winning_trades = sum(1 for t in trades if t.pnl > 0)
            win_rate = winning_trades / len(trades)
        else:
            win_rate = 0.0

        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)

        trades_with_2nd = sum(1 for t in trades if t.had_2nd_entry)
        avg_holding_bars = sum(t.holding_bars for t in trades) / len(trades) if trades else 0.0

        exits_by_reason = {}
        for trade in trades:
            exits_by_reason[trade.exit_reason] = exits_by_reason.get(trade.exit_reason, 0) + 1

        mae_avg = sum(t.mae for t in trades) / len(trades) if trades else 0.0
        mfe_avg = sum(t.mfe for t in trades) / len(trades) if trades else 0.0

        period = f"{df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}"

        return DualBBBacktestResult(
            config=self.config, symbol=symbol, point_value=point_value, period=period,
            total_return_pct=total_return_pct, sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_dd_pct, win_rate=win_rate,
            profit_factor=profit_factor, total_trades=len(trades),
            trades_with_2nd_entry=trades_with_2nd,
            avg_holding_bars=avg_holding_bars,
            exits_by_reason=exits_by_reason,
            mae_avg=mae_avg, mfe_avg=mfe_avg,
            equity_curve=equity_curve,
            events=events_log, trades=trades
        )

    def _calculate_max_drawdown(self, equity_curve: list[float]) -> float:
        if len(equity_curve) == 0:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = ((peak - equity) / peak) * 100 if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd
