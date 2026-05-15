"""Generic strategy trade runner (Stage A-2).

Refactored out of the BBKC-only ``BbkcLiveTradeRunner``. The runner is
*strategy-agnostic*: it owns the WebSocket subscription, OHLCV gap-fill,
heartbeat sync, and dispatch into the broker. The strategy itself is
injected as a factory so any strategy in ``src.strategies`` (BBKCSqueeze,
DonchianFixedRR, ...) can drive the same runtime without modification.

Key contracts:

  * The strategy NEVER touches Bybit REST / env vars / config. It only
    receives a :class:`Bar` and a :class:`Broker` interface.
  * The runner NEVER branches on demo vs live. Mode/endpoint/credentials
    are resolved upstream by :mod:`src.core.mode` and baked into the
    :class:`BybitRestClient` / :class:`BybitWebSocketClient` instances
    handed to the runner via the broker.
  * Per-symbol strategy instance: the factory is called once per symbol
    and the resulting instance is reused across bars. Strategies that
    track inter-bar state (e.g. BBKCSqueeze trail-active ratchet) can
    rely on attribute persistence. Strategies that prefer per-bar
    instances can still implement state recovery via
    ``broker.get_position`` inside their ``on_bar`` callback.
"""
from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol

from src.api.ws_client import BybitWebSocketClient
from src.core.types import Bar
from src.data_manager.db import DBManager
from src.data_manager.feed import HistoricalDataFeed
from src.data_manager.gap_filler import (
    current_db_tail_ms,
    fill_gap_for_universe,
)

logger = logging.getLogger(__name__)

HOUR_MS = 60 * 60 * 1000

# Bybit V5 timeframe -> WS kline interval string. Public stream uses minute
# strings ("60" for 1h, "240" for 4h, "D" for daily). Centralised here so
# strategies can request a timeframe in the human-friendly form and the
# runner translates exactly once.
TIMEFRAME_TO_WS_INTERVAL: Dict[str, str] = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}


def timeframe_to_ws_interval(timeframe: str) -> str:
    """Translate a human timeframe (``1h``, ``15m``, ``1M``) to Bybit V5 WS interval.

    Case-sensitive lookup happens first so the case-distinct keys ``1m``
    (1 minute) and ``1M`` (1 month) cannot collide. A case-insensitive
    fallback is allowed for convenience (``1H`` -> ``60``) but only when
    it resolves unambiguously.

    Raises ``ValueError`` on unsupported timeframes - we don't want a
    silent typo routing the runner to no subscription at all.
    """
    raw = (timeframe or "").strip()
    if not raw:
        raise ValueError("timeframe is empty")
    # Case-sensitive lookup first so "1M" stays month, not minute.
    if raw in TIMEFRAME_TO_WS_INTERVAL:
        return TIMEFRAME_TO_WS_INTERVAL[raw]
    # Case-insensitive convenience: accept "1H", "4H" -> "1h", "4h".
    # Only when the lowercase form maps to exactly one registered key.
    candidates = [k for k in TIMEFRAME_TO_WS_INTERVAL
                  if k.lower() == raw.lower()]
    if len(candidates) == 1:
        return TIMEFRAME_TO_WS_INTERVAL[candidates[0]]
    if len(candidates) > 1:
        raise ValueError(
            f"timeframe {timeframe!r} is ambiguous (case-variants: "
            f"{candidates}). Use the exact case (e.g. '1m' for minute, "
            "'1M' for month)."
        )
    raise ValueError(
        f"unsupported timeframe {timeframe!r}; valid: "
        f"{sorted(TIMEFRAME_TO_WS_INTERVAL)}"
    )


class _Broker(Protocol):
    """Minimal broker surface used by the runner.

    The concrete implementation (``BbkcBroker``) provides more methods
    that the strategy itself uses (``buy``, ``sell``, ``update_stop``,
    ``get_position``); the runner only needs sync / portfolio readback.
    """

    def sync(self) -> None: ...
    def live_portfolio(self) -> Any: ...


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class StrategyTradeRunner:
    """Strategy-agnostic Bybit live/demo trade runner.

    Parameters
    ----------
    broker :
        A broker instance (typically :class:`BbkcBroker` or any subclass
        of :class:`LiveBroker`). The runner only calls ``sync`` and
        ``live_portfolio``; strategies call the order methods directly.
    db :
        OHLCV mirror used for gap-fill and as the bar source for the
        strategy's ``prepare`` cache.
    universe :
        Symbols to subscribe to and dispatch on. Strategy factory is
        invoked once per symbol.
    timeframe :
        Strategy timeframe (e.g. ``"1h"``). Translated to the Bybit V5
        WS interval string via :func:`timeframe_to_ws_interval`.
    warmup_days :
        Days of OHLCV history to ensure in the DB before the runner
        starts. The gap-filler reaches back this far on startup.
    stop_at_ms :
        UTC ms epoch at which the runner stops voluntarily. ``0`` means
        run forever (until SIGINT).
    strategy_factory :
        Zero-arg callable that returns a fully-parameterised strategy
        instance. The runner calls it once per symbol and reuses the
        instance across bars.
    ws_url :
        Optional WebSocket URL. ``None`` keeps the
        :class:`BybitWebSocketClient` default (Bybit public stream,
        which serves both demo and mainnet kline data).
    """

    def __init__(
        self,
        broker: _Broker,
        db: DBManager,
        universe: List[str],
        timeframe: str,
        warmup_days: int,
        stop_at_ms: int,
        strategy_factory: Callable[[], Any],
        ws_url: Optional[str] = None,
    ) -> None:
        if not universe:
            raise ValueError("StrategyTradeRunner: universe is empty")
        if not callable(strategy_factory):
            raise TypeError(
                "StrategyTradeRunner: strategy_factory must be callable"
            )
        self._broker = broker
        self._db = db
        self._universe = list(universe)
        self._timeframe = timeframe
        self._ws_interval = timeframe_to_ws_interval(timeframe)
        self._warmup_days = warmup_days
        self._stop_at_ms = stop_at_ms
        self._strategy_factory = strategy_factory
        self._ws_url = ws_url
        self._stopped = False
        self._ws: Optional[BybitWebSocketClient] = None
        self._bars_seen = 0
        # Per-symbol strategy instances. Built lazily on first dispatch
        # so the factory's failure surfaces during the first bar (not
        # during construction) and any factory-time logging interleaves
        # cleanly with the runner startup log.
        self._strategies: Dict[str, Any] = {}
        # Probe one strategy instance to learn warmup_bars without
        # binding it to a symbol. Strategies without a warmup_bars
        # attribute default to 30 (BBKC's value).
        probe = self._strategy_factory()
        self._warmup_bars: int = int(getattr(probe, "warmup_bars", 30))
        self._strategy_name: str = str(
            getattr(probe, "name", probe.__class__.__name__)
        )
        logger.info(
            "[runner] strategy=%s warmup_bars=%d timeframe=%s ws_interval=%s",
            self._strategy_name, self._warmup_bars,
            self._timeframe, self._ws_interval,
        )

    @property
    def universe(self) -> List[str]:
        return list(self._universe)

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def timeframe(self) -> str:
        return self._timeframe

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------
    def _install_signal_handler(self) -> None:
        def _handler(signum: int, frame: Any) -> None:
            logger.warning("[runner] signal %d received, stopping...", signum)
            self._stopped = True
            if self._ws is not None:
                try:
                    self._ws.stop()
                except Exception:
                    pass
        try:
            signal.signal(signal.SIGINT, _handler)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def _initial_sync(self) -> None:
        logger.info("[startup] pulling real account state from Bybit API...")
        try:
            self._broker.sync()
        except Exception as exc:
            logger.error("[startup] account sync failed: %s", exc)
            raise
        portfolio = self._broker.live_portfolio()
        logger.info(
            "[startup] equity=%.2f positions=%d",
            portfolio.equity, len(portfolio.positions),
        )
        for p in portfolio.positions:
            logger.info(
                "[startup] position: %s %s qty=%s entry=%.4f uPnL=%.2f",
                p.symbol, p.side, p.qty, p.entry_price, p.unrealized_pnl,
            )

    def _initial_gap_fill(self) -> None:
        now_ms = _now_ms()
        warmup_since = now_ms - self._warmup_days * 24 * HOUR_MS
        per_sym_since: Dict[str, int] = {}
        for sym in self._universe:
            tail = current_db_tail_ms(self._db, sym, self._ws_interval)
            if tail is None or tail < warmup_since:
                per_sym_since[sym] = warmup_since
            else:
                per_sym_since[sym] = tail + 1
        common_since = min(per_sym_since.values())
        logger.info(
            "[gap_fill] since=%s until=%s universe=%s",
            datetime.fromtimestamp(common_since / 1000, timezone.utc),
            datetime.fromtimestamp(now_ms / 1000, timezone.utc),
            self._universe,
        )
        result = fill_gap_for_universe(
            self._db, self._universe, interval=self._ws_interval,
            since_ms=common_since, until_ms=now_ms,
        )
        for sym, n in result.items():
            logger.info("[gap_fill] %s inserted=%d", sym, n)

    # ------------------------------------------------------------------
    # Bar dispatch
    # ------------------------------------------------------------------
    def _get_strategy(self, symbol: str) -> Any:
        """Per-symbol strategy instance with lazy init via the factory."""
        strat = self._strategies.get(symbol)
        if strat is None:
            strat = self._strategy_factory()
            self._strategies[symbol] = strat
        return strat

    def _dispatch_bar(self, symbol: str, ts_ms: int) -> None:
        df = self._db.get_bars(symbol, self._timeframe)
        df = df.sort_values("open_time").reset_index(drop=True)
        if df.empty or int(df["open_time"].iloc[-1]) != ts_ms:
            fill_gap_for_universe(
                self._db, [symbol], interval=self._ws_interval,
                since_ms=ts_ms - HOUR_MS, until_ms=ts_ms + HOUR_MS,
            )
            df = self._db.get_bars(symbol, self._timeframe)
            df = df.sort_values("open_time").reset_index(drop=True)
            if df.empty or int(df["open_time"].iloc[-1]) != ts_ms:
                logger.error("[dispatch] %s still missing after retry", symbol)
                return
        row = df.iloc[-1]
        bar = Bar(
            symbol=symbol,
            timestamp=int(row["open_time"]),
            timeframe=self._timeframe,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            turnover=(
                float(row["turnover"])
                if "turnover" in df.columns and row["turnover"] is not None
                else None
            ),
        )
        feed = HistoricalDataFeed(
            db=self._db, symbols=[symbol], timeframe=self._timeframe,
        )
        full = feed.get_full_series(symbol)

        strat = self._get_strategy(symbol)

        # Warmup: enough bars must exist for indicators to be valid.
        bars_in_series = len(full.bars) if hasattr(full, "bars") else 0
        if bars_in_series <= self._warmup_bars:
            logger.info(
                "[dispatch] %s warmup %d/%d - skipping",
                symbol, bars_in_series, self._warmup_bars,
            )
            return

        try:
            if hasattr(strat, "prepare") and hasattr(strat, "on_bar_fast"):
                cache = strat.prepare(full)
                i = bars_in_series - 1
                strat.on_bar_fast(bar, i, cache, self._broker)
            elif hasattr(strat, "on_bar"):
                strat.on_bar(bar, full, self._broker)
            else:
                logger.error(
                    "[dispatch] strategy %s has neither on_bar_fast nor on_bar",
                    self._strategy_name,
                )
                return
        except Exception as exc:
            logger.error("[dispatch] strategy error sym=%s: %s", symbol, exc)

        self._bars_seen += 1
        logger.info(
            "[bar] sym=%s ts=%s close=%.4f -> %s dispatched",
            symbol,
            datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat(),
            bar.close,
            self._strategy_name,
        )

    def _on_kline_closed(
        self, symbol: str, interval: str, kline: Dict[str, Any],
    ) -> None:
        if symbol not in self._universe:
            return
        if interval != self._ws_interval:
            return
        try:
            open_time = int(kline["start"])
            row = {
                "symbol": symbol,
                "open_time": open_time,
                "open": float(kline["open"]),
                "high": float(kline["high"]),
                "low": float(kline["low"]),
                "close": float(kline["close"]),
                "volume": float(kline.get("volume", 0.0)),
                "turnover": (
                    float(kline["turnover"])
                    if kline.get("turnover") is not None else None
                ),
            }
            self._db.upsert_bars(symbol, self._timeframe, [row])
        except (KeyError, ValueError) as exc:
            logger.error("[ws] parse error sym=%s: %s", symbol, exc)
            return
        self._dispatch_bar(symbol, open_time)

        if self._stop_at_ms > 0 and _now_ms() >= self._stop_at_ms:
            logger.info("[runner] stop-at reached, shutting down")
            self._stopped = True
            if self._ws is not None:
                try:
                    self._ws.stop()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        self._install_signal_handler()
        self._initial_sync()
        self._initial_gap_fill()

        if self._ws_url:
            ws = BybitWebSocketClient(ws_url=self._ws_url)
        else:
            ws = BybitWebSocketClient()
        ws.on_kline_closed = self._on_kline_closed
        self._ws = ws
        ws.start(self._universe, [self._ws_interval])
        logger.info(
            "[runner] ws started, bar-close subscription on %s interval=%s",
            self._universe, self._ws_interval,
        )
        last_heartbeat = _now_ms()
        try:
            while not self._stopped:
                if self._stop_at_ms > 0 and _now_ms() >= self._stop_at_ms:
                    break
                time.sleep(5.0)
                if _now_ms() - last_heartbeat >= 60_000:
                    try:
                        self._broker.sync()
                        portfolio = self._broker.live_portfolio()
                        pos_str = ", ".join(
                            f"{p.symbol} {p.side} qty={p.qty} "
                            f"entry={p.entry_price:.2f} "
                            f"uPnL={p.unrealized_pnl:+.2f}"
                            for p in portfolio.positions
                        ) or "(none)"
                        ws_state = "up" if ws.is_connected else "down"
                        logger.info(
                            "[heartbeat] bars_seen=%d ws=%s equity=%.2f "
                            "daily_pnl=%+.2f positions=%d [%s]",
                            self._bars_seen,
                            ws_state,
                            portfolio.equity,
                            portfolio.daily_pnl,
                            len(portfolio.positions),
                            pos_str,
                        )
                    except Exception as exc:
                        logger.error("[heartbeat] sync failed: %s", exc)
                    last_heartbeat = _now_ms()
        finally:
            try:
                ws.stop()
            except Exception:
                pass
            try:
                self._broker.sync()
                portfolio = self._broker.live_portfolio()
                logger.info(
                    "[final] equity=%.2f daily_pnl=%+.2f positions=%d bars_seen=%d",
                    portfolio.equity,
                    portfolio.daily_pnl,
                    len(portfolio.positions),
                    self._bars_seen,
                )
            except Exception as exc:
                logger.error("[final] sync failed: %s", exc)


__all__ = ["StrategyTradeRunner", "timeframe_to_ws_interval"]
