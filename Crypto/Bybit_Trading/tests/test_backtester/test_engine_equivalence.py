"""BacktestEngine 동등성 검증 — legacy on_bar vs fast path on_bar_fast."""
import pytest
import numpy as np
from src.core.config import BacktestConfig, RiskConfig
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.strategies.donchian_trend_filter import DonchianTrendFilter
from src.strategies.donchian_fixed_rr import DonchianFixedRR
from src.backtester.engine import BacktestEngine


class LegacyStrategyWrapper:
    """기존 on_bar만 사용하도록 강제하는 래퍼.

    prepare/on_bar_fast를 숨겨서 BacktestEngine이 legacy 경로를 쓰게 함.
    """
    def __init__(self, inner):
        self._inner = inner
        self.name = inner.name + "_legacy"
        self.timeframe = inner.timeframe

    @property
    def warmup_bars(self):
        return self._inner.warmup_bars

    def on_bar(self, bar, series, broker):
        return self._inner.on_bar(bar, series, broker)

    def on_fill(self, fill):
        return self._inner.on_fill(fill)

    def get_params(self):
        return self._inner.get_params()

    def set_params(self, params):
        return self._inner.set_params(params)


class TestEngineEquivalence:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_db_path, schema_path):
        self.db = DBManager(db_path=tmp_db_path, schema_path=schema_path)
        self.db.initialize()
        # 300봉 합성 데이터 (의미 있는 거래 발생 충분)
        base_ts = 1700000000000
        bars = []
        np.random.seed(42)
        price = 40000.0
        for i in range(300):
            change = np.random.randn() * 200
            price = max(1000, price + change)
            o = price
            h = price + abs(np.random.randn()) * 50
            l = price - abs(np.random.randn()) * 50
            c = price + np.random.randn() * 30
            bars.append({
                "symbol": "BTCUSDT",
                "open_time": base_ts + i * 3600000,
                "open": o, "high": h, "low": l, "close": c,
                "volume": 1000.0, "turnover": 40000000.0,
            })
        self.db.upsert_bars("BTCUSDT", "1h", bars)
        self.config = BacktestConfig(initial_capital=50000.0,
                                      taker_fee_pct=0.00055, slippage_pct=0.0003)
        self.risk_config = RiskConfig(max_drawdown_pct=0.50,
                                       daily_loss_limit_pct=0.50, max_concurrent=10)

    def _run_both(self, strategy_cls, **params):
        """Fast path와 legacy path 두 번 백테스트하고 결과 쌍 반환."""
        feed1 = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        fast_strategy = strategy_cls(**params)
        fast_result = BacktestEngine().run(fast_strategy, feed1, self.config,
                                            symbol="BTCUSDT", risk_config=self.risk_config)

        feed2 = HistoricalDataFeed(db=self.db, symbols=["BTCUSDT"], timeframe="1h")
        legacy_strategy = LegacyStrategyWrapper(strategy_cls(**params))
        legacy_result = BacktestEngine().run(legacy_strategy, feed2, self.config,
                                              symbol="BTCUSDT", risk_config=self.risk_config)
        return fast_result, legacy_result

    def test_donchian_trend_filter_equivalence(self):
        fast, legacy = self._run_both(
            DonchianTrendFilter,
            entry_period=20, exit_period=10, ema_filter=50, atr_period=14, stop_atr=2.0,
        )
        assert fast.total_trades == legacy.total_trades, \
            f"Trade count mismatch: fast={fast.total_trades} vs legacy={legacy.total_trades}"
        assert abs(fast.total_pnl - legacy.total_pnl) < 0.01, \
            f"PnL mismatch: fast={fast.total_pnl} vs legacy={legacy.total_pnl}"
        for i, (ft, lt) in enumerate(zip(fast.trades, legacy.trades)):
            assert ft.symbol == lt.symbol, f"Trade {i} symbol"
            assert ft.side == lt.side, f"Trade {i} side"
            assert abs(ft.entry_price - lt.entry_price) < 0.01, f"Trade {i} entry"
            assert abs(ft.exit_price - lt.exit_price) < 0.01, f"Trade {i} exit"
            assert abs(ft.pnl - lt.pnl) < 0.01, f"Trade {i} pnl"
            assert ft.exit_reason == lt.exit_reason, f"Trade {i} exit_reason"

    def test_donchian_fixed_rr_equivalence(self):
        fast, legacy = self._run_both(
            DonchianFixedRR,
            entry_period=20, atr_period=14, stop_atr=2.5, tp_r_ratio=2.0,
            trail_activate_atr=1.5, trail_distance_atr=1.0,
        )
        assert fast.total_trades == legacy.total_trades
        assert abs(fast.total_pnl - legacy.total_pnl) < 0.01
        for i, (ft, lt) in enumerate(zip(fast.trades, legacy.trades)):
            assert ft.side == lt.side
            assert abs(ft.entry_price - lt.entry_price) < 0.01
            assert abs(ft.exit_price - lt.exit_price) < 0.01
            assert abs(ft.pnl - lt.pnl) < 0.01

    def test_donchian_trend_filter_multiple_params(self):
        param_sets = [
            dict(entry_period=10, exit_period=5, ema_filter=30, atr_period=14, stop_atr=1.5),
            dict(entry_period=30, exit_period=15, ema_filter=100, atr_period=10, stop_atr=2.5),
            dict(entry_period=55, exit_period=20, ema_filter=50, atr_period=20, stop_atr=3.0),
        ]
        for params in param_sets:
            fast, legacy = self._run_both(DonchianTrendFilter, **params)
            assert fast.total_trades == legacy.total_trades, f"Params {params}: trade count"
            assert abs(fast.total_pnl - legacy.total_pnl) < 0.01, f"Params {params}: PnL"
