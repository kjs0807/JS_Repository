"""Strategy Registry Builder — Donchian + BBKCSqueeze 중앙 등록.

각 전략의 Coarse Grid 탐색 공간을 여기 정의. Fine Grid는 Coarse Top-1 주변
±1 step으로 자동 생성 (explore_strategy.py에서 처리).

전략은 심볼/타임프레임에 무관한 제너럴 구조이며, SYMBOLS_DEFAULT/TIMEFRAMES_DEFAULT를
바꾸거나 cfg 단위로 override하여 다른 유니버스에 그대로 적용할 수 있다.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.strategies.registry import StrategyRegistry
from src.strategies.donchian_trend_filter import DonchianTrendFilter
from src.strategies.donchian_fixed_rr import DonchianFixedRR
from src.strategies.bbkc_squeeze import BBKCSqueeze
# 2026-04-14 rule-based improvement round variants
from src.strategies.donchian_fixed_rr_trend_filter import (
    DonchianFixedRRTrendFilter,
)
from src.strategies.donchian_trend_filter_adx import (
    DonchianTrendFilterADX20,
    DonchianTrendFilterADX25,
)
from src.strategies.bbkc_squeeze_htf_trend import BBKCSqueezeHTFTrend


SYMBOLS_DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT"]
TIMEFRAMES_DEFAULT = ["1h", "4h"]


STRATEGY_CONFIGS: Dict[str, Dict[str, Any]] = {
    "DonchianTrendFilter": {
        "cls": DonchianTrendFilter,
        "coarse_grid": {
            "entry_period": [10, 30, 55],
            "exit_period": [5, 10, 20],
            "ema_filter": [100, 200, 300],
            "stop_atr": [1.5, 2.5],
        },
        "symbols": SYMBOLS_DEFAULT,
        "timeframes": TIMEFRAMES_DEFAULT,
        "reference_symbols": [],
    },
    "DonchianFixedRR": {
        "cls": DonchianFixedRR,
        "coarse_grid": {
            "entry_period": [10, 30, 55],
            "stop_atr": [2.0, 3.0],
            "tp_r_ratio": [1.5, 2.5, 4.0],
        },
        "symbols": SYMBOLS_DEFAULT,
        "timeframes": TIMEFRAMES_DEFAULT,
        "reference_symbols": [],
    },
    "BBKCSqueeze": {
        "cls": BBKCSqueeze,
        "coarse_grid": {
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0],
            "kc_mult": [1.0, 1.5],
            "rsi_filter": [65, 70, 75],
            "tp_pct": [0.04, 0.06, 0.08],
            "sl_pct": [0.05, 0.07],
        },
        # 2026-04-25 round 2: exit_mode × trail_distance_r × time_stop_bars
        # 12 cells, evaluated separately from coarse_grid (see scripts/bbkc_exit_eval.py).
        # Indicator params are FIXED at 2026-03-30 winner values during the exit round.
        "exit_round_grid": [
            {"cell_id": "F0",     "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 0},
            {"cell_id": "F24",    "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 24},
            {"cell_id": "F48",    "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 48},
            {"cell_id": "F72",    "exit_mode": "fixed",    "trail_distance_r": None, "time_stop_bars": 72},
            {"cell_id": "T05_0",  "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 0},
            {"cell_id": "T05_24", "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 24},
            {"cell_id": "T05_48", "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 48},
            {"cell_id": "T05_72", "exit_mode": "be_trail", "trail_distance_r": 0.5,  "time_stop_bars": 72},
            {"cell_id": "T10_0",  "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 0},
            {"cell_id": "T10_24", "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 24},
            {"cell_id": "T10_48", "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 48},
            {"cell_id": "T10_72", "exit_mode": "be_trail", "trail_distance_r": 1.0,  "time_stop_bars": 72},
        ],
        "symbols": SYMBOLS_DEFAULT,
        "timeframes": TIMEFRAMES_DEFAULT,
        "reference_symbols": [],
    },
    # ---------------------------------------------------------------
    # 2026-04-14 rule-based improvement variants (D2 / D1 / B1)
    #
    # These are SINGLE-CELL "variants", not grid searches. The goal of
    # this round is to compare each variant to its baseline on a fixed
    # holdout using ONE parameter set per variant, so the grids here
    # contain exactly one value per axis. If any variant PROMOTES in
    # the holdout report, a follow-up round will re-expand its grid.
    # ---------------------------------------------------------------
    "DonchianFixedRRTrendFilter": {
        "cls": DonchianFixedRRTrendFilter,
        "coarse_grid": {
            "entry_period": [20],
            "stop_atr": [2.5],
            "tp_r_ratio": [2.0],
            "ema_filter": [200],
        },
        "symbols": SYMBOLS_DEFAULT,
        "timeframes": TIMEFRAMES_DEFAULT,
        "reference_symbols": [],
    },
    "DonchianTrendFilterADX20": {
        "cls": DonchianTrendFilterADX20,
        "coarse_grid": {
            "entry_period": [20],
            "exit_period": [10],
            "ema_filter": [200],
            "stop_atr": [2.0],
            "adx_period": [14],
        },
        "symbols": SYMBOLS_DEFAULT,
        "timeframes": TIMEFRAMES_DEFAULT,
        "reference_symbols": [],
    },
    "DonchianTrendFilterADX25": {
        "cls": DonchianTrendFilterADX25,
        "coarse_grid": {
            "entry_period": [20],
            "exit_period": [10],
            "ema_filter": [200],
            "stop_atr": [2.0],
            "adx_period": [14],
        },
        "symbols": SYMBOLS_DEFAULT,
        "timeframes": TIMEFRAMES_DEFAULT,
        "reference_symbols": [],
    },
    "BBKCSqueezeHTFTrend": {
        "cls": BBKCSqueezeHTFTrend,
        "coarse_grid": {
            "bb_period": [20],
            "bb_std": [1.5],
            "kc_mult": [1.0],
            "rsi_filter": [70],
            "tp_pct": [0.06],
            "sl_pct": [0.07],
            "htf_ema_period": [50],
        },
        "symbols": SYMBOLS_DEFAULT,
        "timeframes": TIMEFRAMES_DEFAULT,
        "reference_symbols": [],
    },
}


STRATEGY_NAMES: List[str] = list(STRATEGY_CONFIGS.keys())


def get_strategy_config(name: str) -> Dict[str, Any]:
    """Get strategy configuration by name."""
    if name not in STRATEGY_CONFIGS:
        raise KeyError(f"Strategy not registered: {name}. Available: {STRATEGY_NAMES}")
    return STRATEGY_CONFIGS[name]


def build_strategy_registry() -> StrategyRegistry:
    """Build a StrategyRegistry populated with all registered strategies."""
    registry = StrategyRegistry()
    for name, cfg in STRATEGY_CONFIGS.items():
        registry.register(cfg["cls"], param_space=cfg["coarse_grid"])
    return registry


__all__ = [
    "STRATEGY_CONFIGS",
    "STRATEGY_NAMES",
    "get_strategy_config",
    "build_strategy_registry",
]
