"""PaperRunner — multi-symbol bar loop for replay-based paper trading.

The missing piece between ``PaperBroker`` and a runnable paper session.
``BacktestEngine`` runs one symbol at a time which loses portfolio-level
accounting across the BIGTHREE universe; this runner iterates bars in
timestamp order across all symbols so the shared PaperBroker sees them
as a single portfolio.

Design
------
1. For each symbol in ``spec.symbols`` we load the full series from
   ``HistoricalDataFeed.get_full_series`` and call
   ``strategy.prepare(full_series)`` once. This mirrors
   ``BacktestEngine``'s fast-path setup.
2. We build a master iteration plan: ``(symbol, local_bar_index, Bar)``
   tuples sorted by timestamp. Ties are broken by a deterministic
   symbol order.
3. Bar loop:
   - ``broker.process_bar(bar)`` (intra-bar TP/SL, order fills, equity
     snapshot append)
   - warmup gate: skip strategy call until the symbol has enough bars
   - ``strategy.on_bar_fast(bar, local_i, cache[sym], broker)``
4. Checkpoint every ``checkpoint_every_bars`` bars: ``broker.save_state``.
5. On SIGINT we save state + close fills/signal file handles cleanly.

Why not live ws
---------------
Live websocket paper is a follow-up task. This runner is designed so a
``LivePaperRunner`` can subclass it and replace the bar source without
touching the bar-loop contract.
"""
from __future__ import annotations

import logging
import signal
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.backtester.engine import BacktestEngine
from src.core.config import BacktestConfig, RiskConfig
from src.core.types import Bar
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.execution.paper_broker import PaperBroker
from src.evaluation.holdout import HoldoutSpec

logger = logging.getLogger(__name__)


@dataclass
class PaperRunStats:
    bars_processed: int = 0
    signals_logged: int = 0
    per_symbol_bars: Dict[str, int] = field(default_factory=dict)


class PaperRunner:
    def __init__(
        self,
        strategy_factory: Callable[[], Any],
        broker: PaperBroker,
        spec: HoldoutSpec,
        db: DBManager,
        checkpoint_every_bars: int = 200,
    ) -> None:
        self._strategy_factory = strategy_factory
        self._broker = broker
        self._spec = spec
        self._db = db
        self._checkpoint_every = max(1, checkpoint_every_bars)
        self._stopped = False
        self._stats = PaperRunStats()
        self._strategy_by_symbol: Dict[str, Any] = {}
        self._cache_by_symbol: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # SIGINT handler — allow graceful save on Ctrl+C
    # ------------------------------------------------------------------

    def _install_signal_handler(self) -> None:
        def _handler(signum: int, frame: Any) -> None:
            logger.warning(
                "[PaperRunner] signal %d received — stopping at next bar",
                signum,
            )
            self._stopped = True
        try:
            signal.signal(signal.SIGINT, _handler)
        except Exception:
            # Non-main thread or restricted env — skip silently
            pass

    # ------------------------------------------------------------------
    # Preparation
    # ------------------------------------------------------------------

    def _prepare_symbols(self) -> List[Tuple[str, int, Bar]]:
        """Load every symbol, prepare caches, and flatten to master plan.

        Master plan is sorted by timestamp; ties broken by symbol order.
        ``local_i`` is the bar index within the symbol's own series.
        This matches what ``BacktestEngine`` would pass to
        ``strategy.on_bar_fast``.
        """
        plan: List[Tuple[int, str, int, Bar]] = []
        sym_to_idx = {sym: i for i, sym in enumerate(self._spec.symbols)}
        for sym in self._spec.symbols:
            feed = HistoricalDataFeed(
                db=self._db,
                symbols=[sym],
                timeframe=self._spec.timeframe,
                start_time=self._spec.warmup_start_ms,
                end_time=self._spec.holdout_end_ms,
            )
            strat = self._strategy_factory()
            if not hasattr(strat, "prepare") or not hasattr(strat, "on_bar_fast"):
                raise ValueError(
                    f"Strategy {type(strat).__name__} is not fast-path compatible; "
                    "PaperRunner requires strategy.prepare + on_bar_fast"
                )
            full = feed.get_full_series(sym)
            cache = strat.prepare(full)
            self._strategy_by_symbol[sym] = strat
            self._cache_by_symbol[sym] = cache
            # Walk the feed via next_bar so each Bar is constructed the
            # same way BacktestEngine would see it.
            local_i = 0
            while feed.has_next():
                bar = feed.next_bar(sym)
                if bar is None:
                    break
                plan.append((int(bar.timestamp), sym, local_i, bar))
                local_i += 1
        plan.sort(key=lambda x: (x[0], sym_to_idx[x[1]]))
        return [(sym, li, bar) for _, sym, li, bar in plan]

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def run(self) -> PaperRunStats:
        self._install_signal_handler()
        plan = self._prepare_symbols()
        logger.info(
            "[PaperRunner] plan=%d bars across %d symbols",
            len(plan), len(self._spec.symbols),
        )

        per_sym_bars: Dict[str, int] = {sym: 0 for sym in self._spec.symbols}
        signals_cursor = self._broker._fills_seen  # so we can log fills

        try:
            for idx, (sym, local_i, bar) in enumerate(plan, start=1):
                if self._stopped:
                    logger.warning(
                        "[PaperRunner] stopped at bar %d/%d", idx, len(plan),
                    )
                    break

                self._broker.process_bar(bar)
                per_sym_bars[sym] += 1

                strat = self._strategy_by_symbol[sym]
                # warmup gate: skip strategy until symbol has warmup_bars
                # local bars. BacktestEngine uses bar_count <= warmup_bars.
                if per_sym_bars[sym] <= getattr(strat, "warmup_bars", 0):
                    continue

                # Log an "entry consideration" signal BEFORE strategy
                # call so even rejected orders leave a trace. We only
                # do this on bars where the symbol is at its current
                # cache index; the cache was built from the full series
                # so local_i aligns naturally.
                try:
                    strat.on_bar_fast(
                        bar, local_i, self._cache_by_symbol[sym], self._broker,
                    )
                except Exception as exc:
                    logger.error(
                        "[PaperRunner] strategy.on_bar_fast failed "
                        "sym=%s i=%d: %s", sym, local_i, exc,
                    )
                    continue

                self._stats.bars_processed += 1
                if self._stats.bars_processed % self._checkpoint_every == 0:
                    self._broker.save_state(extra={
                        "bars_processed": self._stats.bars_processed,
                        "per_symbol_bars": dict(per_sym_bars),
                    })

        finally:
            self._stats.per_symbol_bars = dict(per_sym_bars)
            self._broker.save_state(extra={
                "bars_processed": self._stats.bars_processed,
                "per_symbol_bars": dict(per_sym_bars),
                "final": True,
            })
        return self._stats


__all__ = ["PaperRunner", "PaperRunStats"]
