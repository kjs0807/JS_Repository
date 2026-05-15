"""Runtime layer for live/demo strategy execution.

Stage A-2: a strategy-agnostic runner (``StrategyTradeRunner``) that owns
the websocket, gap-fill, heartbeat, and broker dispatch. The strategy is
injected as a factory; the runner has no demo/live branching and no
strategy-specific code. ``BbkcLiveTradeRunner`` is now a thin alias that
points back to this module via the wrapper script.
"""
from src.runtime.strategy_runner import (
    StrategyTradeRunner,
    timeframe_to_ws_interval,
)

__all__ = ["StrategyTradeRunner", "timeframe_to_ws_interval"]
