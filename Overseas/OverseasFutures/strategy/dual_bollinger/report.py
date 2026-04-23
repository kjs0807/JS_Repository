"""Dual Bollinger Band Backtest Reporting.

Report generation and visualization for backtest results.
"""

from typing import Optional
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from strategy.dual_bollinger.engine import DualBBBacktestResult


def print_report(result: DualBBBacktestResult) -> None:
    """Print backtest result summary to console.

    Args:
        result: Backtest result to report
    """
    print("\n" + "=" * 80)
    print("INNER BAND BREAKOUT BACKTEST REPORT")
    print("=" * 80)

    # Strategy Configuration
    print("\n[STRATEGY CONFIGURATION]")
    print(f"Symbol: {result.symbol}")
    print(f"Period: {result.period}")
    print(f"Candle Minutes: {result.config.candle_minutes}")
    print(f"MA Period: {result.config.ma_period}")
    print(f"Inner Sigma: {result.config.sigma_inner}")
    print(f"Outer Sigma: {result.config.sigma_outer}")
    print(f"Breakout %: {result.config.breakout_pct}")
    print(f"RSI Period: {result.config.rsi_period}")
    print(f"RSI Overbought: {result.config.rsi_overbought}")
    print(f"RSI Oversold: {result.config.rsi_oversold}")
    print(f"ATR Stop Multiplier: {result.config.atr_stop_multiplier}")
    print(f"Trailing Stop: {'ON' if result.config.use_trailing_stop else 'OFF'}")
    print(f"Trailing Distance ATR: {result.config.trailing_distance_atr}")
    print(f"Volatility Filter: {'ON' if result.config.vol_filter_enabled else 'OFF'}")
    print(f"Max Bandwidth %: {result.config.max_bandwidth_pct}")
    print(f"Base Quantity: {result.config.base_qty}")
    print(f"Scale Quantity: {result.config.scale_qty}")

    # Performance Metrics
    print("\n[PERFORMANCE METRICS]")
    print(f"Total Return: {result.total_return_pct:.2f}%")
    print(f"Sharpe Ratio: {result.sharpe_ratio:.3f}")
    print(f"Max Drawdown: {result.max_drawdown_pct:.2f}%")
    print(f"Win Rate: {result.win_rate * 100:.2f}%")

    if result.profit_factor == float('inf'):
        print(f"Profit Factor: inf (no losing trades)")
    else:
        print(f"Profit Factor: {result.profit_factor:.3f}")

    # Trade Statistics
    print("\n[TRADE STATISTICS]")
    print(f"Total Trades: {result.total_trades}")
    print(f"Trades with 2nd Entry: {result.trades_with_2nd_entry} "
          f"({result.trades_with_2nd_entry / result.total_trades * 100:.1f}%)"
          if result.total_trades > 0 else "Trades with 2nd Entry: 0")
    print(f"Average Holding Bars: {result.avg_holding_bars:.1f}")

    # Exit Reasons
    print("\n[EXIT REASONS]")
    if result.exits_by_reason:
        total_exits = sum(result.exits_by_reason.values())
        for reason, count in sorted(result.exits_by_reason.items()):
            pct = (count / total_exits * 100) if total_exits > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")
    else:
        print("  No exits recorded")

    # Risk Metrics
    print("\n[RISK METRICS]")
    print(f"Average MAE: ${result.mae_avg:.2f}")
    print(f"Average MFE: ${result.mfe_avg:.2f}")

    # Trade Details
    if result.trades:
        print("\n[TRADE DETAILS - Top 5 Winners]")
        sorted_trades = sorted(result.trades, key=lambda t: t.pnl, reverse=True)
        for i, trade in enumerate(sorted_trades[:5], 1):
            print(f"  {i}. {trade.side} | Entry: ${trade.entry_price:.2f} | "
                  f"Exit: ${trade.exit_price:.2f} | PnL: ${trade.pnl:.2f} | "
                  f"Bars: {trade.holding_bars}")

        print("\n[TRADE DETAILS - Top 5 Losers]")
        for i, trade in enumerate(sorted_trades[-5:][::-1], 1):
            print(f"  {i}. {trade.side} | Entry: ${trade.entry_price:.2f} | "
                  f"Exit: ${trade.exit_price:.2f} | PnL: ${trade.pnl:.2f} | "
                  f"Bars: {trade.holding_bars}")

    print("\n" + "=" * 80 + "\n")


def plot_equity_curve(
    result: DualBBBacktestResult,
    save_path: Optional[str] = None
) -> None:
    """Plot equity curve and drawdown chart.

    Creates a 2-subplot figure:
    1. Equity curve over time
    2. Drawdown percentage over time

    Args:
        result: Backtest result to visualize
        save_path: If provided, save PNG to this path. Otherwise show plot.
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    # Equity curve
    ax1.plot(result.equity_curve, linewidth=1.5, color='#2E86AB')
    ax1.set_ylabel('Equity (USD)', fontsize=12, fontweight='bold')
    ax1.set_title(
        f'Equity Curve - {result.symbol} | {result.period}\n'
        f'Return: {result.total_return_pct:.2f}% | Sharpe: {result.sharpe_ratio:.3f}',
        fontsize=14,
        fontweight='bold'
    )
    ax1.grid(True, alpha=0.3)
    ax1.axhline(y=result.equity_curve[0], color='gray', linestyle='--',
                linewidth=1, alpha=0.5, label='Initial Capital')
    ax1.legend(loc='upper left')

    # Calculate drawdown
    equity_array = np.array(result.equity_curve)
    peak = np.maximum.accumulate(equity_array)
    drawdown = np.where(peak > 0, (peak - equity_array) / peak * 100, 0)

    # Drawdown chart
    ax2.fill_between(range(len(drawdown)), drawdown, 0,
                     color='#A23B72', alpha=0.6)
    ax2.set_ylabel('Drawdown (%)', fontsize=12, fontweight='bold')
    ax2.set_xlabel('Bar Index', fontsize=12, fontweight='bold')
    ax2.set_title(
        f'Drawdown | Max: {result.max_drawdown_pct:.2f}%',
        fontsize=12,
        fontweight='bold'
    )
    ax2.grid(True, alpha=0.3)
    ax2.invert_yaxis()

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Equity curve saved to: {save_path}")
    else:
        plt.show()

    plt.close()
