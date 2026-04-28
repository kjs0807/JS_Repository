"""BacktestEngine fast path vs legacy 성능 벤치마크."""
from __future__ import annotations
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
logging.getLogger("src").setLevel(logging.ERROR)

from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.donchian_trend_filter import DonchianTrendFilter
from src.backtester.engine import BacktestEngine


class LegacyWrapper:
    """prepare/on_bar_fast 숨김으로 legacy 경로 강제."""
    def __init__(self, inner):
        self._inner = inner
        self.name = inner.name
        self.timeframe = inner.timeframe
    @property
    def warmup_bars(self): return self._inner.warmup_bars
    def on_bar(self, bar, series, broker):
        return self._inner.on_bar(bar, series, broker)
    def on_fill(self, fill): return self._inner.on_fill(fill)
    def get_params(self): return self._inner.get_params()
    def set_params(self, p): return self._inner.set_params(p)


def main():
    db = DBManager(db_path=str(PROJECT_ROOT / "db" / "bybit_data.db"))
    config = BacktestConfig(initial_capital=50000.0, taker_fee_pct=0.00055, slippage_pct=0.0003)
    risk_config = RiskConfig(max_drawdown_pct=0.50, daily_loss_limit_pct=0.50, max_concurrent=10)
    symbol = "BTCUSDT"
    tf = "1h"

    params = dict(entry_period=20, exit_period=10, ema_filter=200,
                  atr_period=14, stop_atr=2.0)

    # Legacy
    print("Legacy path (on_bar with series rebuild)...")
    feed1 = HistoricalDataFeed(db=db, symbols=[symbol], timeframe=tf)
    legacy = LegacyWrapper(DonchianTrendFilter(**params))
    t0 = time.time()
    legacy_result = BacktestEngine().run(legacy, feed1, config, symbol=symbol, risk_config=risk_config)
    legacy_elapsed = time.time() - t0
    print(f"  Elapsed: {legacy_elapsed:.2f}s")
    print(f"  Trades: {legacy_result.total_trades}, PnL: {legacy_result.total_pnl:.0f}")

    # Fast
    print("\nFast path (prepare + on_bar_fast)...")
    feed2 = HistoricalDataFeed(db=db, symbols=[symbol], timeframe=tf)
    fast = DonchianTrendFilter(**params)
    t0 = time.time()
    fast_result = BacktestEngine().run(fast, feed2, config, symbol=symbol, risk_config=risk_config)
    fast_elapsed = time.time() - t0
    print(f"  Elapsed: {fast_elapsed:.2f}s")
    print(f"  Trades: {fast_result.total_trades}, PnL: {fast_result.total_pnl:.0f}")

    # Summary
    speedup = legacy_elapsed / fast_elapsed if fast_elapsed > 0 else 0
    print(f"\n=== Speedup: {speedup:.1f}x ===")
    print(f"  Legacy: {legacy_elapsed:.2f}s")
    print(f"  Fast:   {fast_elapsed:.2f}s")

    # Result parity check
    trades_match = legacy_result.total_trades == fast_result.total_trades
    pnl_match = abs(legacy_result.total_pnl - fast_result.total_pnl) < 0.01
    print(f"\nResults match: {trades_match and pnl_match}")
    print(f"  Trades: legacy={legacy_result.total_trades}, fast={fast_result.total_trades}")
    print(f"  PnL: legacy={legacy_result.total_pnl:.2f}, fast={fast_result.total_pnl:.2f}")


if __name__ == "__main__":
    main()
