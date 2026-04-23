"""Inner Band Breakout Strategy Optimizer.

Walk-Forward optimization and grid search for parameter tuning.
"""

from dataclasses import dataclass, field
from typing import Optional
import itertools
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from strategy.dual_bollinger.config import DualBBConfig
from strategy.dual_bollinger.engine import DualBBBacktestEngine, DualBBBacktestResult


@dataclass
class ParameterGrid:
    """Parameter ranges for optimization.

    이전 최적화에서 수렴한 값은 고정 (rsi_overbought=70, rsi_oversold=20, breakout_pct=0).
    새로 추가된 ATR 스탑/트레일링/변동성 필터 위주로 탐색.
    """
    ma_period: list[int] = field(default_factory=lambda: [5, 10, 15, 20])
    sigma_inner: list[float] = field(default_factory=lambda: [0.5, 0.7, 1.0])
    sigma_outer: list[float] = field(default_factory=lambda: [2.0, 2.5, 3.0])
    breakout_pct: list[float] = field(default_factory=lambda: [0.0])
    rsi_period: list[int] = field(default_factory=lambda: [14])
    rsi_overbought: list[float] = field(default_factory=lambda: [70.0])
    rsi_oversold: list[float] = field(default_factory=lambda: [20.0])
    atr_stop_multiplier: list[float] = field(default_factory=lambda: [1.5, 2.0, 2.5, 3.0])
    use_trailing_stop: list[bool] = field(default_factory=lambda: [True])
    trailing_activation_atr: list[float] = field(default_factory=lambda: [1.0])
    trailing_distance_atr: list[float] = field(default_factory=lambda: [1.5, 2.0])
    vol_filter_enabled: list[bool] = field(default_factory=lambda: [True, False])
    max_bandwidth_pct: list[float] = field(default_factory=lambda: [8.0])


@dataclass
class WalkForwardWindow:
    """Single walk-forward validation window."""
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_config: Optional[DualBBConfig] = None
    train_result: Optional[DualBBBacktestResult] = None
    test_result: Optional[DualBBBacktestResult] = None


@dataclass
class WalkForwardResult:
    """Walk-forward validation result."""
    symbol: str
    windows: list[WalkForwardWindow]
    combined_oos_equity: list[float]
    combined_oos_return: float
    combined_oos_sharpe: float
    combined_oos_mdd: float
    avg_oos_win_rate: float
    parameter_stability: dict
    best_overall_config: DualBBConfig
    total_combinations_tested: int


class GridSearchOptimizer:
    """Grid search optimizer for parameter combinations."""

    def __init__(self, grid: ParameterGrid, candle_minutes: int):
        self.grid = grid
        self.candle_minutes = candle_minutes

    def _generate_configs(self) -> list[DualBBConfig]:
        """Generate all valid parameter combinations."""
        configs = []
        combos = itertools.product(
            self.grid.ma_period,
            self.grid.sigma_inner,
            self.grid.sigma_outer,
            self.grid.breakout_pct,
            self.grid.rsi_period,
            self.grid.rsi_overbought,
            self.grid.rsi_oversold,
            self.grid.atr_stop_multiplier,
            self.grid.use_trailing_stop,
            self.grid.trailing_activation_atr,
            self.grid.trailing_distance_atr,
            self.grid.vol_filter_enabled,
            self.grid.max_bandwidth_pct,
        )
        for (ma_period, sigma_inner, sigma_outer, breakout_pct,
             rsi_period, rsi_ob, rsi_os, atr_mult,
             use_trail, trail_act, trail_dist,
             vol_filt, max_bw) in combos:
            # sigma_outer > sigma_inner 필수
            if sigma_outer <= sigma_inner:
                continue
            config = DualBBConfig(
                candle_minutes=self.candle_minutes,
                ma_period=ma_period,
                sigma_inner=sigma_inner,
                sigma_outer=sigma_outer,
                breakout_pct=breakout_pct,
                rsi_period=rsi_period,
                rsi_overbought=rsi_ob,
                rsi_oversold=rsi_os,
                atr_stop_multiplier=atr_mult,
                use_trailing_stop=use_trail,
                trailing_activation_atr=trail_act,
                trailing_distance_atr=trail_dist,
                vol_filter_enabled=vol_filt,
                max_bandwidth_pct=max_bw,
                base_qty=1,
                scale_qty=1,
            )
            configs.append(config)
        return configs

    def run(
        self,
        df: pd.DataFrame,
        symbol: str,
        metric: str = 'sharpe_ratio',
        min_trades: int = 10,
        point_value: float = 1.0,
        bars_per_day: int = 1
    ) -> list[tuple[DualBBConfig, DualBBBacktestResult]]:
        """Run grid search over all parameter combinations."""
        configs = self._generate_configs()
        results = []

        print(f"\n[GRID SEARCH] Testing {len(configs)} parameter combinations...")
        print(f"Metric: {metric} | Min trades: {min_trades}\n")

        for i, config in enumerate(configs, 1):
            if i % 5 == 0 or i == 1:
                print(f"Progress: {i}/{len(configs)} ({i/len(configs)*100:.1f}%)")

            engine = DualBBBacktestEngine(config)
            result = engine.run(df, symbol, initial_capital=1000000.0,
                                point_value=point_value, bars_per_day=bars_per_day)

            if result.total_trades >= min_trades:
                results.append((config, result))

        print(f"\n[GRID SEARCH] Complete. {len(results)} valid results found.\n")

        results.sort(key=lambda x: getattr(x[1], metric), reverse=True)
        return results


class WalkForwardValidator:
    """Walk-forward validation for parameter robustness testing."""

    def __init__(self, grid: ParameterGrid, candle_minutes: int,
                 n_windows: int = 5, train_ratio: float = 0.7):
        self.grid = grid
        self.candle_minutes = candle_minutes
        self.n_windows = n_windows
        self.train_ratio = train_ratio

    def _split_windows(self, df: pd.DataFrame) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
        total_rows = len(df)
        window_size = total_rows // self.n_windows
        train_size = int(window_size * self.train_ratio)
        windows = []
        for i in range(self.n_windows):
            start_idx = i * window_size
            end_idx = start_idx + window_size
            if i == self.n_windows - 1:
                end_idx = total_rows
            train_end_idx = start_idx + train_size
            train_df = df.iloc[start_idx:train_end_idx].copy()
            test_df = df.iloc[train_end_idx:end_idx].copy()
            if len(test_df) > 0:
                windows.append((train_df, test_df))
        return windows

    def run(self, df: pd.DataFrame, symbol: str,
            metric: str = 'sharpe_ratio', min_trades: int = 5,
            point_value: float = 1.0, bars_per_day: int = 1) -> WalkForwardResult:
        print(f"\n[WALK-FORWARD] Starting {self.n_windows}-window validation")
        print(f"Train ratio: {self.train_ratio:.1%} | Metric: {metric}\n")

        windows_data = self._split_windows(df)
        wf_windows: list[WalkForwardWindow] = []
        combined_oos_equity: list[float] = []
        parameter_selections = []
        total_combinations = 0

        optimizer = GridSearchOptimizer(self.grid, self.candle_minutes)

        for i, (train_df, test_df) in enumerate(windows_data, 1):
            print(f"\n{'='*60}")
            print(f"Window {i}/{len(windows_data)}")
            print(f"Train: {train_df.index[0]} to {train_df.index[-1]} ({len(train_df)} bars)")
            print(f"Test:  {test_df.index[0]} to {test_df.index[-1]} ({len(test_df)} bars)")

            train_results = optimizer.run(train_df, symbol, metric, min_trades,
                                          point_value, bars_per_day)
            total_combinations += len(train_results)

            if not train_results:
                print(f"[WARNING] No valid results in window {i}. Skipping.")
                continue

            best_config, best_train_result = train_results[0]
            print(f"\n[TRAIN] Best {metric}: {getattr(best_train_result, metric):.3f}")

            test_engine = DualBBBacktestEngine(best_config)
            test_result = test_engine.run(test_df, symbol, initial_capital=1000000.0,
                                          point_value=point_value, bars_per_day=bars_per_day)
            print(f"[TEST]  OOS {metric}: {getattr(test_result, metric):.3f}")

            window = WalkForwardWindow(
                train_start=train_df.index[0].strftime('%Y-%m-%d'),
                train_end=train_df.index[-1].strftime('%Y-%m-%d'),
                test_start=test_df.index[0].strftime('%Y-%m-%d'),
                test_end=test_df.index[-1].strftime('%Y-%m-%d'),
                best_config=best_config,
                train_result=best_train_result,
                test_result=test_result
            )
            wf_windows.append(window)

            if len(combined_oos_equity) == 0:
                combined_oos_equity = test_result.equity_curve.copy()
            else:
                prev_final = combined_oos_equity[-1]
                test_equity = test_result.equity_curve
                initial_test = test_equity[0]
                normalized = [(e - initial_test) + prev_final for e in test_equity]
                combined_oos_equity.extend(normalized[1:])

            parameter_selections.append({
                'ma_period': best_config.ma_period,
                'sigma_inner': best_config.sigma_inner,
                'sigma_outer': best_config.sigma_outer,
                'breakout_pct': best_config.breakout_pct,
                'rsi_period': best_config.rsi_period,
                'rsi_overbought': best_config.rsi_overbought,
                'rsi_oversold': best_config.rsi_oversold,
                'atr_stop_multiplier': best_config.atr_stop_multiplier,
                'trailing_distance_atr': best_config.trailing_distance_atr,
                'vol_filter_enabled': best_config.vol_filter_enabled,
            })

        if combined_oos_equity:
            initial_capital = combined_oos_equity[0]
            final_capital = combined_oos_equity[-1]
            combined_oos_return = ((final_capital - initial_capital) / initial_capital) * 100
            oos_returns = []
            for j in range(1, len(combined_oos_equity)):
                if combined_oos_equity[j-1] > 0:
                    oos_returns.append((combined_oos_equity[j] - combined_oos_equity[j-1]) / combined_oos_equity[j-1])
            combined_oos_sharpe = (np.mean(oos_returns) / np.std(oos_returns)) * np.sqrt(252 * bars_per_day) if len(oos_returns) > 0 and np.std(oos_returns) > 0 else 0.0
            combined_oos_mdd = self._calculate_max_drawdown(combined_oos_equity)
        else:
            combined_oos_return = 0.0
            combined_oos_sharpe = 0.0
            combined_oos_mdd = 0.0

        avg_oos_win_rate = np.mean([w.test_result.win_rate for w in wf_windows]) if wf_windows else 0.0
        parameter_stability = self._calculate_parameter_stability(parameter_selections)
        best_overall_config = self._construct_best_overall_config(parameter_selections)

        return WalkForwardResult(
            symbol=symbol, windows=wf_windows,
            combined_oos_equity=combined_oos_equity,
            combined_oos_return=combined_oos_return,
            combined_oos_sharpe=combined_oos_sharpe,
            combined_oos_mdd=combined_oos_mdd,
            avg_oos_win_rate=avg_oos_win_rate,
            parameter_stability=parameter_stability,
            best_overall_config=best_overall_config,
            total_combinations_tested=total_combinations
        )

    def _calculate_max_drawdown(self, equity_curve: list[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = ((peak - eq) / peak) * 100 if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd

    def _calculate_parameter_stability(self, parameter_selections: list[dict]) -> dict:
        stability = {}
        param_names = ['ma_period', 'sigma_inner', 'sigma_outer', 'breakout_pct',
                       'rsi_period', 'rsi_overbought', 'rsi_oversold',
                       'atr_stop_multiplier', 'trailing_distance_atr', 'vol_filter_enabled']
        for param_name in param_names:
            value_counts = {}
            for selection in parameter_selections:
                value = selection[param_name]
                value_counts[value] = value_counts.get(value, 0) + 1
            stability[param_name] = value_counts
        return stability

    def _construct_best_overall_config(self, parameter_selections: list[dict]) -> DualBBConfig:
        if not parameter_selections:
            return DualBBConfig()
        def mode_value(param_name: str):
            values = [s[param_name] for s in parameter_selections]
            return max(set(values), key=values.count)
        return DualBBConfig(
            candle_minutes=self.candle_minutes,
            ma_period=mode_value('ma_period'),
            sigma_inner=mode_value('sigma_inner'),
            sigma_outer=mode_value('sigma_outer'),
            breakout_pct=mode_value('breakout_pct'),
            rsi_period=mode_value('rsi_period'),
            rsi_overbought=mode_value('rsi_overbought'),
            rsi_oversold=mode_value('rsi_oversold'),
            atr_stop_multiplier=mode_value('atr_stop_multiplier'),
            trailing_distance_atr=mode_value('trailing_distance_atr'),
            vol_filter_enabled=mode_value('vol_filter_enabled'),
            base_qty=1, scale_qty=1,
        )


def print_optimization_report(result: WalkForwardResult) -> None:
    """Print walk-forward validation report."""
    print("\n" + "=" * 80)
    print("WALK-FORWARD OPTIMIZATION REPORT")
    print("=" * 80)
    print(f"\nSymbol: {result.symbol}")
    print(f"Total Windows: {len(result.windows)}")
    print(f"Total Combinations Tested: {result.total_combinations_tested}")

    print("\n[WINDOW RESULTS]")
    print(f"{'Window':<8} {'Period':<25} {'Train Sharpe':<15} {'Test Sharpe':<15} {'Test Return':<15}")
    print("-" * 80)
    for i, window in enumerate(result.windows, 1):
        period_str = f"{window.test_start} to {window.test_end}"
        train_sharpe = window.train_result.sharpe_ratio if window.train_result else 0.0
        test_sharpe = window.test_result.sharpe_ratio if window.test_result else 0.0
        test_return = window.test_result.total_return_pct if window.test_result else 0.0
        print(f"{i:<8} {period_str:<25} {train_sharpe:<15.3f} {test_sharpe:<15.3f} {test_return:<15.2f}%")

    print("\n[COMBINED OUT-OF-SAMPLE PERFORMANCE]")
    print(f"Total Return: {result.combined_oos_return:.2f}%")
    print(f"Sharpe Ratio: {result.combined_oos_sharpe:.3f}")
    print(f"Max Drawdown: {result.combined_oos_mdd:.2f}%")
    print(f"Average Win Rate: {result.avg_oos_win_rate * 100:.2f}%")

    print("\n[PARAMETER STABILITY]")
    for param_name, value_counts in result.parameter_stability.items():
        print(f"\n{param_name}:")
        total = sum(value_counts.values())
        for value, count in sorted(value_counts.items(), key=lambda x: x[1], reverse=True):
            pct = (count / total * 100) if total > 0 else 0
            print(f"  {value}: {count}/{total} ({pct:.1f}%)")

    print("\n[RECOMMENDED CONFIGURATION]")
    best_cfg = result.best_overall_config
    print(f"MA Period: {best_cfg.ma_period}")
    print(f"Sigma Inner: {best_cfg.sigma_inner}")
    print(f"Sigma Outer: {best_cfg.sigma_outer}")
    print(f"Breakout %: {best_cfg.breakout_pct}")
    print(f"RSI Period: {best_cfg.rsi_period}")
    print(f"RSI Overbought: {best_cfg.rsi_overbought}")
    print(f"RSI Oversold: {best_cfg.rsi_oversold}")
    print(f"ATR Stop Multiplier: {best_cfg.atr_stop_multiplier}")
    print(f"Trailing Stop: {'ON' if best_cfg.use_trailing_stop else 'OFF'}")
    print(f"Trailing Distance ATR: {best_cfg.trailing_distance_atr}")
    print(f"Volatility Filter: {'ON' if best_cfg.vol_filter_enabled else 'OFF'}")
    print(f"Max Bandwidth %: {best_cfg.max_bandwidth_pct}")
    print("\n" + "=" * 80 + "\n")


def plot_walk_forward(result: WalkForwardResult, save_path: Optional[str] = None) -> None:
    """Plot walk-forward validation results."""
    fig = plt.figure(figsize=(16, 10))
    gs = fig.add_gridspec(2, 1, hspace=0.3)

    ax1 = fig.add_subplot(gs[0])
    window_labels = [f"W{i}" for i in range(1, len(result.windows) + 1)]
    train_sharpes = [w.train_result.sharpe_ratio if w.train_result else 0 for w in result.windows]
    test_sharpes = [w.test_result.sharpe_ratio if w.test_result else 0 for w in result.windows]
    x = np.arange(len(window_labels))
    width = 0.35
    ax1.bar(x - width/2, train_sharpes, width, label='Train', color='#4A90E2', alpha=0.8)
    ax1.bar(x + width/2, test_sharpes, width, label='Test (OOS)', color='#E94B3C', alpha=0.8)
    ax1.set_xlabel('Window')
    ax1.set_ylabel('Sharpe Ratio')
    ax1.set_title('Train vs Test Sharpe Ratio by Window')
    ax1.set_xticks(x)
    ax1.set_xticklabels(window_labels)
    ax1.legend()
    ax1.grid(True, alpha=0.3, axis='y')
    ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)

    ax2 = fig.add_subplot(gs[1])
    ax2.plot(result.combined_oos_equity, linewidth=2, color='#2E86AB')
    ax2.set_xlabel('Bar Index')
    ax2.set_ylabel('Equity (USD)')
    ax2.set_title(f'Combined OOS Equity | Return: {result.combined_oos_return:.2f}% | Sharpe: {result.combined_oos_sharpe:.3f}')
    ax2.grid(True, alpha=0.3)

    plt.suptitle(f'Walk-Forward Validation - {result.symbol}', fontsize=16, fontweight='bold')

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Walk-forward chart saved to: {save_path}")
    else:
        plt.show()
    plt.close()
