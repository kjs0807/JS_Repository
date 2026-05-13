"""BBKC universe-expansion experiment — backtester with *live-forward* sizing.

Why this script exists
----------------------
The canonical ``BacktestBroker`` has no ``calc_legacy_notional_qty``, so
``BBKCSqueeze`` falls back to ``calc_qty(risk_pct=0.02, stop_distance=...)``
in backtests — that produces a notional of ~0.857 × equity, ~5.7x bigger
than the live forward daemon's ``equity × max_position_pct × leverage``
(= 0.15 × equity at 3x). So canonical backtest PnL/MDD cannot be used to
judge real operation.

This script wraps ``BacktestBroker`` in an ``ExpBroker`` subclass that
exposes ``calc_legacy_notional_qty`` (identical formula to
``BbkcDemoBroker``) plus per-symbol qtyStep/minQty/minNotional rounding,
and runs both single-symbol and true multi-symbol (shared-equity,
chronological) backtests. No change to ``src/`` — the canonical path is
untouched and used only as an optional reference column.

Subcommands
-----------
    single     Phase 5: per-symbol backtest (live-forward sizing)
    combo      Phase 6: pre-declared universe combos (shared equity)
    weights    Phase 7: per-symbol max_position_pct schemes on one universe
    leverage   Phase 8: leverage sweep (tp_pct/sl_pct scaled with leverage)

Common assumptions (match the running forward `be25_st60_di30` cell):
    BBKCSqueeze(bb20/std1.5, kc20/mult1.0, atr14, rsi14, rsi_filter70,
                tp_pct=0.06, sl_pct=0.07, leverage=3, timeframe="1h",
                exit_mode="be_trail", trail_be_at_tp_frac=0.25,
                trail_start_at_tp_frac=0.60, trail_distance_tp_frac=0.30,
                drop_tp=False, time_stop_bars=0)
    fees/slippage/initial_capital from config.backtest.

Usage:
    python -m scripts.bbkc_universe_experiment single
    python -m scripts.bbkc_universe_experiment combo
    python -m scripts.bbkc_universe_experiment weights --universe BTCUSDT ETHUSDT AVAXUSDT
    python -m scripts.bbkc_universe_experiment leverage --universe BTCUSDT ETHUSDT AVAXUSDT
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import BacktestConfig, RiskConfig, load_config
from src.core.types import Bar, BarSeries
from src.data_manager.db import DBManager
from src.execution.backtest_broker import BacktestBroker, TradeRecord
from src.strategies.bbkc_squeeze import BBKCSqueeze

ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "AVAXUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT"]

# Pre-declared universe combos (no search — ex-ante hypotheses).
UNIVERSE_COMBOS: Dict[str, List[str]] = {
    "BTC_ETH":           ["BTCUSDT", "ETHUSDT"],
    "BIGTHREE":          ["BTCUSDT", "ETHUSDT", "AVAXUSDT"],
    "BTC_ETH_SOL":       ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "BTC_ETH_AVAX_SOL":  ["BTCUSDT", "ETHUSDT", "AVAXUSDT", "SOLUSDT"],
    "BTC_ETH_SOL_XRP":   ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
    "BTC_ETH_SOL_DOGE":  ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"],
    "BTC_ETH_AVAX_SOL_XRP": ["BTCUSDT", "ETHUSDT", "AVAXUSDT", "SOLUSDT", "XRPUSDT"],
    "SIX":               ["BTCUSDT", "ETHUSDT", "AVAXUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT"],
}

# Live forward exit cell (config.yaml bbkc_exit) — fixed across this experiment.
FORWARD_EXIT_CELL = dict(
    exit_mode="be_trail",
    trail_be_at_tp_frac=0.25,
    trail_start_at_tp_frac=0.60,
    trail_distance_tp_frac=0.30,
    drop_tp=False,
    time_stop_bars=0,
)
BASE_PARAMS = dict(
    bb_period=20, bb_std=1.5, kc_period=20, kc_mult=1.0,
    atr_period=14, rsi_period=14, rsi_filter=70.0,
    timeframe="1h",
)
# Round-5 leverage / sizing baseline.
DEFAULT_LEVERAGE = 3
DEFAULT_TP_PCT = 0.06
DEFAULT_SL_PCT = 0.07
DEFAULT_MAX_POS_PCT = 0.05

# Leverage sweep: keep *price* TP/SL fixed at 3x's values, scale tp_pct/sl_pct.
TARGET_PRICE_TP = DEFAULT_TP_PCT / DEFAULT_LEVERAGE          # 0.02
TARGET_PRICE_SL = DEFAULT_SL_PCT / DEFAULT_LEVERAGE          # 0.023333...
LEVERAGE_SWEEP = [1, 2, 3, 5]

OUT_DIR = PROJECT_ROOT / "logs" / "research" / "bbkc_squeeze" / "universe_exp"


# ---------------------------------------------------------------------------
# Instrument specs (qtyStep / minQty / minNotional). Static snapshot verified
# 2026-05-13 against Bybit demo get_instruments_info + products_master.
# (Backtest sizing rounds to these so qty matches what the live broker would
#  actually submit.)
# ---------------------------------------------------------------------------
INSTRUMENT_SPECS: Dict[str, Dict[str, float]] = {
    "BTCUSDT":  {"qty_step": 0.001, "min_qty": 0.001, "min_notional": 5.0, "tick_size": 0.1},
    "ETHUSDT":  {"qty_step": 0.01,  "min_qty": 0.01,  "min_notional": 5.0, "tick_size": 0.01},
    "AVAXUSDT": {"qty_step": 0.1,   "min_qty": 0.1,   "min_notional": 5.0, "tick_size": 0.001},
    "XRPUSDT":  {"qty_step": 0.1,   "min_qty": 0.1,   "min_notional": 5.0, "tick_size": 0.0001},
    "SOLUSDT":  {"qty_step": 0.1,   "min_qty": 0.1,   "min_notional": 5.0, "tick_size": 0.01},
    "DOGEUSDT": {"qty_step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0, "tick_size": 0.00001},
}


# ===========================================================================
# Experiment broker — live-forward sizing on top of the canonical BacktestBroker
# ===========================================================================
class ExpBroker(BacktestBroker):
    """BacktestBroker + ``calc_legacy_notional_qty`` (live-forward formula) +
    per-symbol lot-step / minQty / minNotional rounding.

    Sizing (identical to ``BbkcDemoBroker.calc_legacy_notional_qty``):
        equity_now  = realized equity + unrealized PnL of open positions
        margin_alloc = equity_now * max_position_pct
        notional     = margin_alloc * leverage
        raw_qty      = notional / entry_price
        qty          = floor(raw_qty / qty_step) * qty_step
        if qty < min_qty:                    -> 0
        if qty * entry_price < min_notional: -> 0   (live broker would reject)
    """

    def __init__(self, config: BacktestConfig, risk_config: Optional[RiskConfig],
                 leverage: int, max_position_pct: float,
                 symbol_specs: Optional[Dict[str, Dict[str, float]]] = None,
                 apply_lot_rounding: bool = True) -> None:
        super().__init__(config, risk_config)
        self._leverage = int(leverage)
        self._max_position_pct = float(max_position_pct)
        self._specs = symbol_specs or {}
        self._apply_lot_rounding = apply_lot_rounding
        # diagnostics
        self.rejected_below_min_qty = 0
        self.rejected_below_min_notional = 0

    def _equity_now(self) -> float:
        return self._equity + sum(p.unrealized_pnl for p in self._positions.get_all())

    def _round_qty(self, symbol: str, qty: float, entry_price: float) -> float:
        if not self._apply_lot_rounding:
            return float(qty) if qty > 0 else 0.0
        spec = self._specs.get(symbol)
        if not spec:
            return float(qty) if qty > 0 else 0.0
        step = spec.get("qty_step", 0.0)
        if step > 0:
            qty = math.floor(qty / step) * step
        min_q = spec.get("min_qty", 0.0)
        if qty < min_q:
            self.rejected_below_min_qty += 1
            return 0.0
        min_notional = spec.get("min_notional", 0.0)
        if min_notional > 0 and qty * entry_price < min_notional:
            self.rejected_below_min_notional += 1
            return 0.0
        return float(qty)

    def calc_legacy_notional_qty(self, symbol: str, entry_price: float) -> float:
        if entry_price <= 0:
            return 0.0
        margin_alloc = self._equity_now() * self._max_position_pct
        notional = margin_alloc * self._leverage
        return self._round_qty(symbol, notional / entry_price, entry_price)


# ===========================================================================
# Data loading
# ===========================================================================
@dataclass
class SymbolData:
    symbol: str
    bars: List[Bar]            # chronological
    series: BarSeries          # full series (for strategy.prepare)


def load_symbol_data(db: DBManager, symbol: str, timeframe: str = "1h") -> SymbolData:
    df = db.get_bars(symbol, timeframe)
    df = df.sort_values("open_time").reset_index(drop=True)
    bars: List[Bar] = []
    for _, row in df.iterrows():
        bars.append(Bar(
            symbol=symbol, timestamp=int(row["open_time"]), timeframe=timeframe,
            open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
            close=float(row["close"]), volume=float(row["volume"]),
            turnover=(float(row["turnover"]) if "turnover" in df.columns
                      and row["turnover"] is not None and not (
                          isinstance(row["turnover"], float) and math.isnan(row["turnover"]))
                      else None),
        ))
    # BarSeries.bars wants a DataFrame with open/high/low/close/volume (+ timestamp).
    sdf = df[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
    sdf.insert(0, "timestamp", df["open_time"].astype("int64").reset_index(drop=True))
    series = BarSeries(symbol=symbol, timeframe=timeframe, bars=sdf)
    return SymbolData(symbol=symbol, bars=bars, series=series)


# ===========================================================================
# Metrics (uniform: from trade list + equity curve)
# ===========================================================================
def _max_consecutive_losses(pnls: List[float]) -> int:
    best = cur = 0
    for p in pnls:
        if p <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def compute_metrics(trades: List[TradeRecord], equity_curve: List[float],
                    initial_capital: float) -> Dict[str, Any]:
    pnls = [t.pnl for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    eq = np.array(equity_curve, dtype=float) if equity_curve else np.array([initial_capital])
    net_pnl = float(eq[-1] - initial_capital)            # includes entry+exit fees
    sum_trade_pnl = float(sum(pnls))                     # excludes entry fees
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1e-9)
    max_dd = float(dd.max()) if len(dd) else 0.0
    if n > 1:
        arr = np.array(pnls)
        std = float(np.std(arr, ddof=1))
        sharpe = (float(np.mean(arr)) / std) * math.sqrt(252) if std > 0 else 0.0
    else:
        sharpe = 0.0
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    total_fee = float(sum(t.fee for t in trades))        # exit fees only (TradeRecord.fee)
    return {
        "n_trades": n,
        "win_rate": (len(wins) / n) if n else 0.0,
        "total_return_pct": net_pnl / initial_capital,
        "net_pnl_usdt": net_pnl,
        "sum_trade_pnl_usdt": sum_trade_pnl,
        "profit_factor": pf,
        "max_drawdown_pct": max_dd,
        "avg_trade_pnl": (sum_trade_pnl / n) if n else 0.0,
        "avg_win": (gross_profit / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "max_consecutive_losses": _max_consecutive_losses(pnls),
        "sharpe_per_trade": sharpe,
        "exit_fee_sum_usdt": total_fee,
        "fees_included": True,
        "final_equity": float(eq[-1]),
    }


# ===========================================================================
# Backtest runners
# ===========================================================================
def make_strategy(leverage: int, tp_pct: float, sl_pct: float) -> BBKCSqueeze:
    return BBKCSqueeze(leverage=leverage, tp_pct=tp_pct, sl_pct=sl_pct,
                       **BASE_PARAMS, **FORWARD_EXIT_CELL)


def run_single_symbol(sd: SymbolData, cfg: BacktestConfig, risk_cfg: RiskConfig,
                      leverage: int, tp_pct: float, sl_pct: float,
                      max_position_pct: float, apply_lot_rounding: bool = True,
                      return_broker: bool = False):
    broker = ExpBroker(cfg, risk_cfg, leverage=leverage,
                       max_position_pct=max_position_pct,
                       symbol_specs=INSTRUMENT_SPECS,
                       apply_lot_rounding=apply_lot_rounding)
    strat = make_strategy(leverage, tp_pct, sl_pct)
    cache = strat.prepare(sd.series)
    bar_count = 0
    max_concurrent = 0          # always 1 here, kept for symmetry
    notional_peak = 0.0
    cur_date: Optional[str] = None
    for bar in sd.bars:
        d = _utc_date(bar.timestamp)
        if d != cur_date:
            broker._risk.reset_daily()   # live system resets the daily-loss counter each day
            cur_date = d
        broker.process_bar(bar)
        bar_count += 1
        if bar_count <= strat.warmup_bars:
            continue
        strat.on_bar_fast(bar, bar_count - 1, cache, broker)
        pos = broker.get_position(bar.symbol)
        if pos is not None:
            max_concurrent = max(max_concurrent, 1)
            notional_peak = max(notional_peak, pos.qty * bar.close)
    broker.close_all(reason="BACKTEST_END")
    m = compute_metrics(broker.get_trades(), broker.get_equity_curve(), cfg.initial_capital)
    m.update({
        "symbol": sd.symbol,
        "first_bar": _fmt(sd.bars[0].timestamp) if sd.bars else None,
        "last_bar": _fmt(sd.bars[-1].timestamp) if sd.bars else None,
        "n_bars": len(sd.bars),
        "max_concurrent_positions": max_concurrent,
        "peak_notional_usdt": notional_peak,
        "rejected_below_min_qty": broker.rejected_below_min_qty,
        "rejected_below_min_notional": broker.rejected_below_min_notional,
        "leverage": leverage, "tp_pct": tp_pct, "sl_pct": sl_pct,
        "max_position_pct": max_position_pct,
    })
    if return_broker:
        return m, broker
    return m


def run_combo(symbols: List[str], data: Dict[str, SymbolData],
              cfg: BacktestConfig, risk_cfg: RiskConfig, leverage: int,
              tp_pct: float, sl_pct: float,
              per_symbol_max_pos_pct: Dict[str, float],
              apply_lot_rounding: bool = True, return_broker: bool = False):
    """True multi-symbol backtest: one shared ExpBroker, chronological bar
    events across all symbols, one BBKCSqueeze instance (keyed by bar.symbol)."""
    # ExpBroker needs *one* max_position_pct; for per-symbol weights we pass a
    # dict and let the broker pick by symbol via a thin override.
    class _ComboBroker(ExpBroker):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._per_sym = dict(per_symbol_max_pos_pct)
        def calc_legacy_notional_qty(self, symbol: str, entry_price: float) -> float:
            if entry_price <= 0:
                return 0.0
            mpp = self._per_sym.get(symbol, self._max_position_pct)
            notional = self._equity_now() * mpp * self._leverage
            return self._round_qty(symbol, notional / entry_price, entry_price)

    broker = _ComboBroker(cfg, risk_cfg, leverage=leverage,
                          max_position_pct=DEFAULT_MAX_POS_PCT,
                          symbol_specs=INSTRUMENT_SPECS,
                          apply_lot_rounding=apply_lot_rounding)
    strat = make_strategy(leverage, tp_pct, sl_pct)
    caches = {s: strat.prepare(data[s].series) for s in symbols}
    counters = {s: 0 for s in symbols}
    last_close = {s: 0.0 for s in symbols}

    # merged chronological event stream: (timestamp, symbol, bar, idx_in_series)
    events: List[Tuple[int, str, Bar, int]] = []
    for s in symbols:
        for i, bar in enumerate(data[s].bars):
            events.append((bar.timestamp, s, bar, i))
    events.sort(key=lambda e: (e[0], e[1]))

    max_concurrent = 0
    peak_total_notional = 0.0
    # equity curve snapshot per *timestamp* (after processing all symbols at that ts)
    equity_by_ts: Dict[int, float] = {}

    cur_ts = None
    cur_date: Optional[str] = None
    for ts, sym, bar, idx in events:
        d = _utc_date(ts)
        if d != cur_date:
            broker._risk.reset_daily()
            cur_date = d
        broker.process_bar(bar)
        last_close[sym] = bar.close
        counters[sym] += 1
        if counters[sym] > strat.warmup_bars:
            strat.on_bar_fast(bar, idx, caches[sym], broker)
        # snapshot exposure / concurrency after each event
        open_pos = broker.get_positions()
        if open_pos:
            max_concurrent = max(max_concurrent, len(open_pos))
            tot = sum(p.qty * last_close.get(p.symbol, p.entry_price) for p in open_pos)
            peak_total_notional = max(peak_total_notional, tot)
        eq_now = broker._equity + sum(p.unrealized_pnl for p in open_pos)
        equity_by_ts[ts] = eq_now
        cur_ts = ts

    broker.close_all(reason="BACKTEST_END")
    trades = broker.get_trades()
    # build a time-ordered equity curve from the per-ts snapshots
    eq_curve = [cfg.initial_capital] + [equity_by_ts[t] for t in sorted(equity_by_ts)]
    m = compute_metrics(trades, eq_curve, cfg.initial_capital)

    # per-symbol contribution
    contrib: Dict[str, Dict[str, Any]] = {}
    for s in symbols:
        st = [t for t in trades if t.symbol == s]
        contrib[s] = {
            "n_trades": len(st),
            "sum_trade_pnl_usdt": float(sum(t.pnl for t in st)),
            "win_rate": (sum(1 for t in st if t.pnl > 0) / len(st)) if st else 0.0,
        }
    total_contrib_pnl = sum(c["sum_trade_pnl_usdt"] for c in contrib.values()) or 1e-9
    for s in symbols:
        contrib[s]["pnl_share_pct"] = contrib[s]["sum_trade_pnl_usdt"] / total_contrib_pnl

    m.update({
        "universe": symbols,
        "first_bar": _fmt(min(b.timestamp for s in symbols for b in data[s].bars)),
        "last_bar": _fmt(max(b.timestamp for s in symbols for b in data[s].bars)),
        "max_concurrent_positions": max_concurrent,
        "peak_total_notional_usdt": peak_total_notional,
        "rejected_below_min_qty": broker.rejected_below_min_qty,
        "rejected_below_min_notional": broker.rejected_below_min_notional,
        "leverage": leverage, "tp_pct": tp_pct, "sl_pct": sl_pct,
        "per_symbol_max_pos_pct": dict(per_symbol_max_pos_pct),
        "per_symbol_contribution": contrib,
    })
    if return_broker:
        return m, broker
    return m


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


# ===========================================================================
# Reporting helpers
# ===========================================================================
def _pf(x: float) -> str:
    return "inf" if x == float("inf") else f"{x:.2f}"


def print_single_table(rows: List[Dict[str, Any]], title: str) -> None:
    print(f"\n### {title}")
    hdr = ["symbol", "trades", "win%", "ret%", "netPnL$", "PF", "MDD%",
           "avgPnL$", "avgWin$", "avgLoss$", "maxConsecL", "Sharpe/t", "exitFee$",
           "peakNotional$", "rej<minNotional"]
    print("| " + " | ".join(hdr) + " |")
    print("|" + "|".join(["---"] * len(hdr)) + "|")
    for r in rows:
        print("| " + " | ".join(str(x) for x in [
            r["symbol"], r["n_trades"], f'{r["win_rate"]*100:.1f}',
            f'{r["total_return_pct"]*100:+.2f}', f'{r["net_pnl_usdt"]:+,.1f}',
            _pf(r["profit_factor"]), f'{r["max_drawdown_pct"]*100:.2f}',
            f'{r["avg_trade_pnl"]:+,.2f}', f'{r["avg_win"]:+,.1f}', f'{r["avg_loss"]:+,.1f}',
            r["max_consecutive_losses"], f'{r["sharpe_per_trade"]:.2f}',
            f'{r["exit_fee_sum_usdt"]:,.1f}', f'{r.get("peak_notional_usdt",0):,.0f}',
            r.get("rejected_below_min_notional", 0),
        ]) + " |")


def print_combo_table(rows: List[Dict[str, Any]], names: List[str], title: str) -> None:
    print(f"\n### {title}")
    hdr = ["universe", "n", "trades", "win%", "ret%", "netPnL$", "PF", "MDD%",
           "avgPnL$", "maxConsecL", "Sharpe/t", "maxConcurrent", "peakNotional$"]
    print("| " + " | ".join(hdr) + " |")
    print("|" + "|".join(["---"] * len(hdr)) + "|")
    for name, r in zip(names, rows):
        print("| " + " | ".join(str(x) for x in [
            name, len(r["universe"]), r["n_trades"], f'{r["win_rate"]*100:.1f}',
            f'{r["total_return_pct"]*100:+.2f}', f'{r["net_pnl_usdt"]:+,.1f}',
            _pf(r["profit_factor"]), f'{r["max_drawdown_pct"]*100:.2f}',
            f'{r["avg_trade_pnl"]:+,.2f}', r["max_consecutive_losses"],
            f'{r["sharpe_per_trade"]:.2f}', r["max_concurrent_positions"],
            f'{r.get("peak_total_notional_usdt",0):,.0f}',
        ]) + " |")


def _save(name: str, payload: Any) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / name
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {p}")
    return p


# ===========================================================================
# Subcommands
# ===========================================================================
def cmd_single(args, db, cfg, risk_cfg) -> None:
    data = {s: load_symbol_data(db, s) for s in ALL_SYMBOLS}
    live_rows, canon_rows = [], []
    for s in ALL_SYMBOLS:
        live_rows.append(run_single_symbol(
            data[s], cfg, risk_cfg, leverage=DEFAULT_LEVERAGE,
            tp_pct=DEFAULT_TP_PCT, sl_pct=DEFAULT_SL_PCT,
            max_position_pct=DEFAULT_MAX_POS_PCT, apply_lot_rounding=True))
        # canonical reference: calc_qty fallback (disable our notional method by
        # using a plain BacktestBroker). We mimic by running the canonical engine.
    # canonical reference column via canonical BacktestBroker (no calc_legacy_notional_qty)
    from src.backtester.engine import BacktestEngine
    from src.data_manager.feed import HistoricalDataFeed
    for s in ALL_SYMBOLS:
        feed = HistoricalDataFeed(db=db, symbols=[s], timeframe="1h")
        strat = make_strategy(DEFAULT_LEVERAGE, DEFAULT_TP_PCT, DEFAULT_SL_PCT)
        res = BacktestEngine().run(strat, feed, cfg, symbol=s, risk_config=risk_cfg)
        cm = compute_metrics(res.trades, res.equity_curve, cfg.initial_capital)
        cm["symbol"] = s
        cm["peak_notional_usdt"] = 0.0
        canon_rows.append(cm)
    print_single_table(live_rows, "Phase 5 — single-symbol backtest, LIVE-FORWARD sizing "
                       f"(BBKC be25_st60_di30, {DEFAULT_LEVERAGE}x, max_pos_pct={DEFAULT_MAX_POS_PCT}, "
                       "full 1h history)")
    print_single_table(canon_rows, "Reference only — canonical BacktestEngine sizing "
                       "(calc_qty 2%-risk; this is what Round 4/5 research used, NOT live)")
    _save("phase5_single_symbol.json", {"live_forward_sizing": live_rows,
                                        "canonical_reference": canon_rows})


def cmd_combo(args, db, cfg, risk_cfg) -> None:
    needed = sorted({s for combo in UNIVERSE_COMBOS.values() for s in combo})
    data = {s: load_symbol_data(db, s) for s in needed}
    names, rows = [], []
    for name, syms in UNIVERSE_COMBOS.items():
        eq_weights = {s: DEFAULT_MAX_POS_PCT for s in syms}
        r = run_combo(syms, data, cfg, risk_cfg, leverage=DEFAULT_LEVERAGE,
                      tp_pct=DEFAULT_TP_PCT, sl_pct=DEFAULT_SL_PCT,
                      per_symbol_max_pos_pct=eq_weights, apply_lot_rounding=True)
        names.append(name)
        rows.append(r)
    print_combo_table(rows, names, "Phase 6 — universe combos, LIVE-FORWARD sizing, EQUAL weights "
                      f"(max_pos_pct={DEFAULT_MAX_POS_PCT} each, {DEFAULT_LEVERAGE}x, shared equity)")
    # per-symbol contribution detail
    for name, r in zip(names, rows):
        print(f"\n#### {name} per-symbol contribution")
        print("| symbol | trades | sumPnL$ | win% | pnlShare% |")
        print("|---|---|---|---|---|")
        for s in r["universe"]:
            c = r["per_symbol_contribution"][s]
            print(f"| {s} | {c['n_trades']} | {c['sum_trade_pnl_usdt']:+,.1f} | "
                  f"{c['win_rate']*100:.1f} | {c['pnl_share_pct']*100:+.1f} |")
    _save("phase6_combos.json", {name: r for name, r in zip(names, rows)})


def cmd_weights(args, db, cfg, risk_cfg) -> None:
    syms = args.universe
    data = {s: load_symbol_data(db, s) for s in syms}
    # 1) equal weight
    base_pct = DEFAULT_MAX_POS_PCT
    equal = {s: base_pct for s in syms}
    # 2) inverse-volatility weight: scale so the *average* stays base_pct
    #    vol = stdev of 1h log returns over the full series
    vols = {}
    for s in syms:
        cl = data[s].series.bars["close"].to_numpy(dtype=float)
        lr = np.diff(np.log(cl))
        vols[s] = float(np.std(lr, ddof=1))
    inv = {s: 1.0 / vols[s] for s in syms}
    scale = (base_pct * len(syms)) / sum(inv.values())
    invvol = {s: inv[s] * scale for s in syms}
    # 3) performance-weight: from single-symbol live-forward backtest net PnL,
    #    clipped to be non-negative, normalized so average == base_pct.
    single = {s: run_single_symbol(data[s], cfg, risk_cfg, DEFAULT_LEVERAGE,
                                   DEFAULT_TP_PCT, DEFAULT_SL_PCT, base_pct) for s in syms}
    perf_raw = {s: max(single[s]["net_pnl_usdt"], 0.0) for s in syms}
    if sum(perf_raw.values()) <= 0:
        perf = dict(equal)
    else:
        psc = (base_pct * len(syms)) / sum(perf_raw.values())
        perf = {s: perf_raw[s] * psc for s in syms}

    schemes = {"equal": equal, "inverse_vol": invvol, "performance": perf}
    names, rows = [], []
    for nm, w in schemes.items():
        r = run_combo(syms, data, cfg, risk_cfg, DEFAULT_LEVERAGE, DEFAULT_TP_PCT,
                      DEFAULT_SL_PCT, per_symbol_max_pos_pct=w, apply_lot_rounding=True)
        names.append(nm)
        rows.append(r)
    print(f"\n### Phase 7 — per-symbol weight schemes on universe {syms}")
    print("\n#### weight tables (max_position_pct per symbol)")
    print("| scheme | " + " | ".join(syms) + " | sum |")
    print("|" + "|".join(["---"] * (len(syms) + 2)) + "|")
    for nm, w in schemes.items():
        print(f"| {nm} | " + " | ".join(f"{w[s]:.4f}" for s in syms) + f" | {sum(w.values()):.4f} |")
    print_combo_table(rows, names, "Phase 7 — portfolio result by weight scheme")
    print("\n(single-symbol vols — 1h log-return stdev)")
    for s in syms:
        print(f"  {s}: vol={vols[s]:.5f}  single_netPnL=${single[s]['net_pnl_usdt']:+,.1f}  "
              f"single_MDD={single[s]['max_drawdown_pct']*100:.2f}%  "
              f"maxConsecL={single[s]['max_consecutive_losses']}")
    _save("phase7_weights.json", {"universe": syms, "schemes": schemes,
                                  "results": {nm: r for nm, r in zip(names, rows)},
                                  "vols": vols,
                                  "single_symbol": {s: single[s] for s in syms}})


def cmd_slsweep(args, db, cfg, risk_cfg) -> None:
    """Sweep the *price-based* initial stop while keeping price-TP fixed and
    leverage fixed (3x). The be_trail BE/trail triggers are anchored to TP
    (tp_distance = entry * tp_pct / leverage), so this only moves the initial
    backstop — not the trailing logic."""
    syms = args.symbols
    lev = args.leverage
    price_tp = args.price_tp                       # default 0.02 -> tp_pct = 0.06 at 3x
    tp_pct = price_tp * lev
    price_sl_list = sorted(set(args.sl_list + [price_tp, DEFAULT_SL_PCT / DEFAULT_LEVERAGE]))
    data = {s: load_symbol_data(db, s) for s in syms}
    rows: List[Dict[str, Any]] = []
    for s in syms:
        for psl in price_sl_list:
            sl_pct = psl * lev
            m = run_single_symbol(data[s], cfg, risk_cfg, leverage=lev,
                                  tp_pct=tp_pct, sl_pct=sl_pct,
                                  max_position_pct=DEFAULT_MAX_POS_PCT,
                                  apply_lot_rounding=True)
            m["price_sl"] = psl
            m["price_tp"] = price_tp
            m["rr_ratio"] = price_tp / psl
            rows.append(m)
    # also the BTC+ETH and BIGTHREE combos at each SL (equal weights)
    combo_rows: Dict[str, List[Dict[str, Any]]] = {}
    for cname, csyms in (("BTC_ETH", ["BTCUSDT", "ETHUSDT"]),
                         ("BIGTHREE", ["BTCUSDT", "ETHUSDT", "AVAXUSDT"])):
        if not all(s in data for s in csyms):
            for s in csyms:
                if s not in data:
                    data[s] = load_symbol_data(db, s)
        lst = []
        for psl in price_sl_list:
            sl_pct = psl * lev
            r = run_combo(csyms, data, cfg, risk_cfg, leverage=lev, tp_pct=tp_pct,
                          sl_pct=sl_pct,
                          per_symbol_max_pos_pct={s: DEFAULT_MAX_POS_PCT for s in csyms},
                          apply_lot_rounding=True)
            r["price_sl"] = psl; r["price_tp"] = price_tp; r["rr_ratio"] = price_tp / psl
            lst.append(r)
        combo_rows[cname] = lst

    print(f"\n### Stop-loss sweep — price-TP fixed at {price_tp*100:.2f}%, leverage {lev}x, live-forward sizing")
    print("(be_trail BE/trail triggers anchored to TP, unchanged: BE @ "
          f"{price_tp*0.25*100:.2f}% / trail-start @ {price_tp*0.60*100:.2f}% / "
          f"trail-dist {price_tp*0.30*100:.2f}% price move)")
    for s in syms:
        print(f"\n#### {s}")
        print("| price_SL% | sl_pct | R:R(TP/SL) | 거래수 | 승률 | 총수익률 | 총PnL$ | PF | MDD% | 평균익$ | 평균손$ | 최대연속손실 | Sharpe/t |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in [x for x in rows if x["symbol"] == s]:
            mark = " ←현행" if abs(r["price_sl"] - DEFAULT_SL_PCT / DEFAULT_LEVERAGE) < 1e-9 else (
                "  (=TP, symmetric)" if abs(r["price_sl"] - price_tp) < 1e-9 else "")
            print(f"| {r['price_sl']*100:.3f}{mark} | {r['sl_pct']:.4f} | {r['rr_ratio']:.2f} | "
                  f"{r['n_trades']} | {r['win_rate']*100:.1f} | {r['total_return_pct']*100:+.2f} | "
                  f"{r['net_pnl_usdt']:+,.1f} | {_pf(r['profit_factor'])} | {r['max_drawdown_pct']*100:.2f} | "
                  f"{r['avg_win']:+,.1f} | {r['avg_loss']:+,.1f} | {r['max_consecutive_losses']} | "
                  f"{r['sharpe_per_trade']:.2f} |")
    for cname, lst in combo_rows.items():
        print(f"\n#### combo {cname} (equal {DEFAULT_MAX_POS_PCT} each, 3x)")
        print("| price_SL% | R:R | 거래수 | 승률 | 총수익률 | 총PnL$ | PF | MDD% | 최대연속손실 | Sharpe/t | 동시포지션 max |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in lst:
            mark = " ←현행" if abs(r["price_sl"] - DEFAULT_SL_PCT / DEFAULT_LEVERAGE) < 1e-9 else ""
            print(f"| {r['price_sl']*100:.3f}{mark} | {r['rr_ratio']:.2f} | {r['n_trades']} | "
                  f"{r['win_rate']*100:.1f} | {r['total_return_pct']*100:+.2f} | {r['net_pnl_usdt']:+,.1f} | "
                  f"{_pf(r['profit_factor'])} | {r['max_drawdown_pct']*100:.2f} | {r['max_consecutive_losses']} | "
                  f"{r['sharpe_per_trade']:.2f} | {r['max_concurrent_positions']} |")
    _save("phase_slsweep.json", {"single": rows, "combos": combo_rows,
                                 "price_tp": price_tp, "leverage": lev})


def cmd_leverage(args, db, cfg, risk_cfg) -> None:
    syms = args.universe
    if getattr(args, "no_guards", False):
        risk_cfg = RiskConfig(max_position_pct=1.0, max_concurrent=100,
                              daily_loss_limit_pct=10.0, max_drawdown_pct=10.0)
        print("  [--no-guards] daily-loss / max-drawdown halts DISABLED — raw P&L only.")
    data = {s: load_symbol_data(db, s) for s in syms}
    # weights: if --weights-json given use it, else equal
    if args.weights_json:
        w = json.loads(Path(args.weights_json).read_text(encoding="utf-8"))
        weights = {s: float(w[s]) for s in syms}
    else:
        weights = {s: DEFAULT_MAX_POS_PCT for s in syms}
    rows, names = [], []
    for lev in LEVERAGE_SWEEP:
        tp_pct = TARGET_PRICE_TP * lev
        sl_pct = TARGET_PRICE_SL * lev
        r = run_combo(syms, data, cfg, risk_cfg, leverage=lev, tp_pct=tp_pct, sl_pct=sl_pct,
                      per_symbol_max_pos_pct=weights, apply_lot_rounding=True)
        r["price_tp_pct"] = TARGET_PRICE_TP
        r["price_sl_pct"] = TARGET_PRICE_SL
        rows.append(r)
        names.append(f"{lev}x")
    print(f"\n### Phase 8 — leverage sweep on universe {syms} (weights={weights})")
    print("price-based TP/SL held fixed at 2.000% / 2.333%; tp_pct/sl_pct scale with leverage.")
    print("| lev | tp_pct | sl_pct | priceTP% | priceSL% | trades | win% | ret% | netPnL$ | PF | MDD% | maxConsecL | maxConcurrent | peakNotional$ |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for nm, r in zip(names, rows):
        print(f"| {nm} | {r['tp_pct']:.5f} | {r['sl_pct']:.5f} | {r['price_tp_pct']*100:.3f} | "
              f"{r['price_sl_pct']*100:.3f} | {r['n_trades']} | {r['win_rate']*100:.1f} | "
              f"{r['total_return_pct']*100:+.2f} | {r['net_pnl_usdt']:+,.1f} | {_pf(r['profit_factor'])} | "
              f"{r['max_drawdown_pct']*100:.2f} | {r['max_consecutive_losses']} | "
              f"{r['max_concurrent_positions']} | {r.get('peak_total_notional_usdt',0):,.0f} |")
    _save("phase8_leverage.json", {"universe": syms, "weights": weights,
                                   "results": {nm: r for nm, r in zip(names, rows)}})


# ===========================================================================
# Phase WF — fixed-parameter rolling out-of-sample validation
# ===========================================================================
# NOTE: this is *fixed-parameter rolling OOS validation*, NOT rolling
# re-optimization. The be25_st60_di30 cell (and any --sl-eth override) is held
# constant in every window; the "IS" period is reported only as prior-window
# context and is NOT a fit set.

def _git_commit() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def _r_per_trade(trades: List[Any], price_sl: float) -> float:
    rs = []
    for t in trades:
        risk = t.qty * t.entry_price * price_sl
        if risk > 0:
            rs.append(t.pnl / risk)
    return float(np.mean(rs)) if rs else 0.0


def _daily_sharpe(trades: List[Any], oos_start_ms: int, oos_end_ms: int,
                  initial_capital: float) -> float:
    """Sharpe of the daily equity-return series over the OOS window (flat days
    count as 0). Annualized with sqrt(365)."""
    if oos_end_ms <= oos_start_ms:
        return 0.0
    n_days = max(1, int((oos_end_ms - oos_start_ms) / 86_400_000))
    daily = np.zeros(n_days)
    for t in trades:
        d = int((int(t.exit_time) - oos_start_ms) // 86_400_000)
        if 0 <= d < n_days:
            daily[d] += t.pnl
    rets = daily / initial_capital
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    return (float(np.mean(rets)) / sd) * math.sqrt(365) if sd > 0 else 0.0


def _window_oos_metrics(all_trades: List[Any], oos_s: datetime, oos_e: datetime,
                        initial_capital: float, price_sl: float) -> Dict[str, Any]:
    from src.evaluation.holdout import compute_metrics_from_trades
    oms = int(oos_s.replace(tzinfo=timezone.utc).timestamp() * 1000)
    ome = int(oos_e.replace(tzinfo=timezone.utc).timestamp() * 1000)
    win = [t for t in all_trades if oms <= int(t.entry_time) < ome]
    m = compute_metrics_from_trades(win, initial_capital)
    pnls = [t.pnl for t in win]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    gp = sum(wins) if wins else 0.0; gl = abs(sum(losses)) if losses else 0.0
    return {
        "oos_start": oos_s.strftime("%Y-%m-%d"), "oos_end": oos_e.strftime("%Y-%m-%d"),
        "n_trades": int(m.get("n_trades", 0)),
        "win_rate": float(m.get("win_rate", 0.0)),
        "net_pnl": float(m.get("total_pnl", 0.0)),
        "net_pct": float(m.get("total_pnl", 0.0)) / initial_capital,
        "max_dd_pct": float(m.get("max_drawdown", 0.0)),
        "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        "r_per_trade": _r_per_trade(win, price_sl),
        "daily_sharpe": _daily_sharpe(win, oms, ome, initial_capital),
    }


def _max_consec_neg(values: List[float]) -> int:
    best = cur = 0
    for v in values:
        if v < 0:
            cur += 1; best = max(best, cur)
        else:
            cur = 0
    return best


def cmd_wf(args, db, cfg, risk_cfg) -> None:
    import sys as _sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from bbkc_exit_eval import build_wf_windows, _add_months
    from src.core.config import BacktestConfig as _BC
    from src.evaluation.holdout import HoldoutSpec, run_strategy_on_holdout
    from src.backtester.engine import BacktestEngine

    fmt = "%Y-%m-%d"
    data_start = args.data_start or args.anchor_date
    data_end = args.data_end
    live_start = datetime.strptime(args.live_start, fmt)
    is_m, oos_m, step_m = args.is_months, args.oos_months, args.step_months
    # how many windows fit before data_end?
    sdt = datetime.strptime(args.anchor_date, fmt)
    edt = datetime.strptime(data_end, fmt)
    n_windows = 0
    while True:
        is_s = _add_months(sdt, step_m * n_windows)
        oos_e = _add_months(_add_months(is_s, is_m), oos_m)
        if _add_months(is_s, is_m) >= edt:
            break
        n_windows += 1
    n_windows = max(1, n_windows)
    windows = build_wf_windows(args.anchor_date, data_end, is_m, oos_m, step_m, n_windows)

    # configs: name -> (symbols, sl_pct override or None)
    configs: Dict[str, Tuple[List[str], Optional[float]]] = {
        "A_ETH":          (["ETHUSDT"], None),
        "B_BTC":          (["BTCUSDT"], None),
        "C_BTC_ETH":      (["BTCUSDT", "ETHUSDT"], None),
        "D_BIGTHREE":     (["BTCUSDT", "ETHUSDT", "AVAXUSDT"], None),
        "A_ETH_sl1.9":    (["ETHUSDT"], TARGET_PRICE_TP * DEFAULT_LEVERAGE),  # placeholder, set below
    }
    # SL-1.9% variant: price_sl = 0.019 -> sl_pct = 0.019*3
    configs["A_ETH_sl1.9"] = (["ETHUSDT"], 0.019 * DEFAULT_LEVERAGE)

    cost_mults = args.cost_mult
    base_taker, base_slip = cfg.taker_fee_pct, cfg.slippage_pct
    initial = cfg.initial_capital

    # data cache
    _data: Dict[str, SymbolData] = {}
    def _get(sym: str) -> SymbolData:
        if sym not in _data:
            _data[sym] = load_symbol_data(db, sym)
        return _data[sym]

    def run_full_trades(symbols: List[str], sl_pct_override: Optional[float],
                        cost_mult: float) -> List[Any]:
        """Full-history backtest with *live-forward* sizing (ExpBroker, mpp=0.05,
        3x, daily-reset wired) so the run isn't truncated by the never-resetting
        canonical daily-loss / 15%-MDD halt. Returns the trade list.
        single symbol -> run_single_symbol; multi -> shared-equity run_combo."""
        tp_pct = DEFAULT_TP_PCT
        sl_pct = sl_pct_override if sl_pct_override is not None else DEFAULT_SL_PCT
        bc = _BC(initial_capital=initial,
                 taker_fee_pct=base_taker * cost_mult,
                 maker_fee_pct=cfg.maker_fee_pct * cost_mult,
                 slippage_pct=base_slip * cost_mult)
        if len(symbols) == 1:
            _m, broker = run_single_symbol(_get(symbols[0]), bc, risk_cfg,
                                           leverage=DEFAULT_LEVERAGE, tp_pct=tp_pct, sl_pct=sl_pct,
                                           max_position_pct=DEFAULT_MAX_POS_PCT,
                                           apply_lot_rounding=True, return_broker=True)
        else:
            dmap = {s: _get(s) for s in symbols}
            _m, broker = run_combo(symbols, dmap, bc, risk_cfg, leverage=DEFAULT_LEVERAGE,
                                   tp_pct=tp_pct, sl_pct=sl_pct,
                                   per_symbol_max_pos_pct={s: DEFAULT_MAX_POS_PCT for s in symbols},
                                   apply_lot_rounding=True, return_broker=True)
        return broker.get_trades()

    print(f"\n### Phase WF — fixed-parameter rolling OOS validation (NOT re-optimization)")
    print(f"cell=be25_st60_di30 (fixed), 3x, live-forward sizing (ExpBroker mpp=0.05, daily-reset), 1h. "
          f"windows: IS {is_m}m / OOS {oos_m}m / step {step_m}m, anchor {args.anchor_date}, "
          f"data {data_start}..{data_end}  -> {n_windows} windows")
    print(f"live forward started {args.live_start}; OOS windows ending after that are 'overlap_live_oos'.")
    print(f"min trades/window for full confidence: {args.min_trades_per_window}. "
          f"verdict {'excludes' if args.exclude_live_overlap_from_verdict else 'includes'} live-overlap windows.")
    print(f"cost-stress multipliers: {cost_mults}")

    results: Dict[str, Any] = {
        "_meta": {
            "git_commit": _git_commit(),
            "run_utc": datetime.now(timezone.utc).isoformat(),
            "data_start": data_start, "data_end": data_end,
            "anchor": args.anchor_date, "is_months": is_m, "oos_months": oos_m,
            "step_months": step_m, "n_windows": n_windows,
            "live_start": args.live_start, "min_trades_per_window": args.min_trades_per_window,
            "exclude_live_overlap_from_verdict": args.exclude_live_overlap_from_verdict,
            "cost_mults": cost_mults, "sizing": "live-forward (ExpBroker mpp=0.05, leverage 3, qty_step/min rounding, daily reset_daily)",
            "code": "scripts/bbkc_universe_experiment.py wf",
        },
        "configs": {},
    }

    per_config_oos: Dict[str, List[Dict[str, Any]]] = {}
    for cname, (syms, sl_ov) in configs.items():
        price_sl = (sl_ov / DEFAULT_LEVERAGE) if sl_ov is not None else (DEFAULT_SL_PCT / DEFAULT_LEVERAGE)
        # base-cost full-history run
        all_t = run_full_trades(syms, sl_ov, 1.0)
        win_rows = []
        for (is_s, is_e, oos_s, oos_e) in windows:
            m = _window_oos_metrics(all_t, oos_s, oos_e, initial, price_sl)
            # IS context (prior 6mo) — NOT a fit set
            is_m_metrics = _window_oos_metrics(all_t, is_s, is_e, initial, price_sl)
            m["is_r_per_trade"] = is_m_metrics["r_per_trade"]
            m["is_n_trades"] = is_m_metrics["n_trades"]
            m["low_confidence"] = m["n_trades"] < args.min_trades_per_window
            m["live_overlap"] = oos_e > live_start
            win_rows.append(m)
        per_config_oos[cname] = win_rows

        # aggregates
        def agg(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            if not rows:
                return {"n_windows": 0}
            netpcts = [r["net_pct"] for r in rows]
            rpts = [r["r_per_trade"] for r in rows]
            return {
                "n_windows": len(rows),
                "oos_positive": sum(1 for r in rows if r["net_pct"] > 0),
                "frac_positive": sum(1 for r in rows if r["net_pct"] > 0) / len(rows),
                "mean_net_pct": float(np.mean(netpcts)),
                "median_net_pct": float(np.median(netpcts)),
                "mean_r_per_trade": float(np.mean(rpts)),
                "median_r_per_trade": float(np.median(rpts)),
                "worst_net_pct": float(np.min(netpcts)),
                "max_consec_losing_windows": _max_consec_neg(netpcts),
                "mean_is_r_per_trade": float(np.mean([r["is_r_per_trade"] for r in rows])),
                "total_oos_trades": sum(r["n_trades"] for r in rows),
                "mean_daily_sharpe": float(np.mean([r["daily_sharpe"] for r in rows])),
            }
        pure = [r for r in win_rows if not r["live_overlap"]]
        ovl = [r for r in win_rows if r["live_overlap"]]
        hi_conf_pure = [r for r in pure if not r["low_confidence"]]
        agg_all = agg(win_rows); agg_pure = agg(pure); agg_ovl = agg(ovl); agg_hi = agg(hi_conf_pure)
        deg = (agg_pure["mean_r_per_trade"] / agg_pure["mean_is_r_per_trade"]
               if agg_pure.get("mean_is_r_per_trade", 0) > 0 else float("nan"))

        # verdict on pure_backtest_oos (or all if --no-exclude)
        verdict_set = pure if args.exclude_live_overlap_from_verdict else win_rows
        va = agg(verdict_set)
        rules = {}
        rules["oos_positive_>=60%"] = (va.get("frac_positive", 0) >= 0.60, va.get("frac_positive", 0))
        rules["mean_oos_net%_>0"] = (va.get("mean_net_pct", 0) > 0, va.get("mean_net_pct", 0))
        rules["median_oos_R/trade_>0"] = (va.get("median_r_per_trade", 0) > 0, va.get("median_r_per_trade", 0))
        is_r = va.get("mean_is_r_per_trade", 0)
        rules["mean_oos_R/trade_>=50%_IS"] = (
            (va.get("mean_r_per_trade", 0) >= 0.5 * is_r) if is_r > 0 else True,
            f"{va.get('mean_r_per_trade',0):.3f} vs IS {is_r:.3f}")
        rules["worst_oos_window_>=-8%"] = (va.get("worst_net_pct", -1) >= -0.08, va.get("worst_net_pct", 0))
        rules["max_consec_losing_oos_<=2"] = (va.get("max_consec_losing_windows", 99) <= 2,
                                              va.get("max_consec_losing_windows", 0))
        passed = all(v[0] for v in rules.values())
        results["configs"][cname] = {
            "symbols": syms, "price_sl_pct": price_sl,
            "windows": win_rows,
            "agg_all": agg_all, "agg_pure_backtest_oos": agg_pure,
            "agg_overlap_live_oos": agg_ovl, "agg_hi_conf_pure": agg_hi,
            "is_to_oos_degradation": deg,
            "verdict": {"pass": passed, "basis": ("pure_backtest_oos" if args.exclude_live_overlap_from_verdict else "all_oos"),
                        "rules": {k: {"pass": v[0], "value": v[1]} for k, v in rules.items()}},
        }

    # ---- pairwise comparison: A_ETH vs B/C/D (per-window paired diff in R/trade) ----
    from scipy import stats as _st
    comp: Dict[str, Any] = {}
    base_rows = per_config_oos["A_ETH"]
    base_pure_idx = [i for i, r in enumerate(base_rows) if not r["live_overlap"]]
    for other in ("B_BTC", "C_BTC_ETH", "D_BIGTHREE"):
        orows = per_config_oos[other]
        diffs = [base_rows[i]["r_per_trade"] - orows[i]["r_per_trade"] for i in base_pure_idx]
        diffs = [d for d in diffs if not (isinstance(d, float) and math.isnan(d))]
        n = len(diffs)
        k_pos = sum(1 for d in diffs if d > 0)
        # two-sided sign test
        if n > 0:
            tail = max(k_pos, n - k_pos)
            from math import comb
            p_sign = min(1.0, 2.0 * sum(comb(n, j) for j in range(tail, n + 1)) / (2 ** n))
        else:
            p_sign = 1.0
        try:
            wstat, p_wil = _st.wilcoxon(diffs) if n >= 1 and any(d != 0 for d in diffs) else (float("nan"), 1.0)
        except Exception:
            p_wil = float("nan")
        comp[f"A_ETH_vs_{other}"] = {
            "n_windows": n, "n_A_wins": k_pos, "median_diff_R/trade": float(np.median(diffs)) if diffs else 0.0,
            "mean_diff_R/trade": float(np.mean(diffs)) if diffs else 0.0,
            "sign_test_p_2sided": p_sign, "wilcoxon_p_2sided": float(p_wil),
            "A_advantage": (k_pos > n - k_pos and (float(np.median(diffs)) if diffs else 0) > 0 and min(p_sign, p_wil if not math.isnan(p_wil) else 1.0) < 0.10),
        }
    results["A_vs_others"] = comp

    # ---- cost stress on A_ETH and C_BTC_ETH ----
    cost_rows: Dict[str, List[Dict[str, Any]]] = {}
    for cname in ("A_ETH", "C_BTC_ETH"):
        syms, sl_ov = configs[cname]
        price_sl = (sl_ov / DEFAULT_LEVERAGE) if sl_ov is not None else (DEFAULT_SL_PCT / DEFAULT_LEVERAGE)
        rows = []
        for cm in cost_mults:
            all_t = run_full_trades(syms, sl_ov, cm)
            wm = [_window_oos_metrics(all_t, oos_s, oos_e, initial, price_sl)
                  for (_, _, oos_s, oos_e) in windows]
            pure_wm = [w for w, (_, _, _, oe) in zip(wm, windows) if oe <= live_start]
            netpcts = [w["net_pct"] for w in pure_wm]
            rows.append({"cost_mult": cm, "n_windows": len(pure_wm),
                         "oos_positive": sum(1 for w in pure_wm if w["net_pct"] > 0),
                         "mean_net_pct": float(np.mean(netpcts)) if netpcts else 0.0,
                         "mean_r_per_trade": float(np.mean([w["r_per_trade"] for w in pure_wm])) if pure_wm else 0.0,
                         "total_pnl": float(sum(w["net_pnl"] for w in pure_wm))})
        cost_rows[cname] = rows
    results["cost_stress"] = cost_rows

    # ---- print summary ----
    print("\n#### per-config OOS aggregate (pure_backtest_oos = windows ending before live start)")
    print("| config | symbols | OOS+/n | frac+ | mean net% | median net% | mean R/t | median R/t | worst net% | maxConsecLosingW | IS→OOS deg | mean dailySharpe | VERDICT |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for cname, cfgr in results["configs"].items():
        a = cfgr["agg_pure_backtest_oos"]
        v = cfgr["verdict"]
        print(f"| {cname} | {'+'.join(s.replace('USDT','') for s in cfgr['symbols'])} | "
              f"{a.get('oos_positive',0)}/{a.get('n_windows',0)} | {a.get('frac_positive',0)*100:.0f}% | "
              f"{a.get('mean_net_pct',0)*100:+.2f} | {a.get('median_net_pct',0)*100:+.2f} | "
              f"{a.get('mean_r_per_trade',0):+.3f} | {a.get('median_r_per_trade',0):+.3f} | "
              f"{a.get('worst_net_pct',0)*100:+.2f} | {a.get('max_consec_losing_windows',0)} | "
              f"{cfgr['is_to_oos_degradation']:.2f} | {a.get('mean_daily_sharpe',0):.2f} | "
              f"{'PASS' if v['pass'] else 'FAIL'} |")
    print("\n#### verdict rules (basis = pure_backtest_oos)")
    for cname, cfgr in results["configs"].items():
        v = cfgr["verdict"]
        fails = [k for k, r in v["rules"].items() if not r["pass"]]
        print(f"  {cname}: {'PASS' if v['pass'] else 'FAIL'}" + ("" if v["pass"] else f"  (failed: {', '.join(fails)})"))
        for k, r in v["rules"].items():
            print(f"      {'OK ' if r['pass'] else 'NO '}{k}: {r['value']}")
    print("\n#### A_ETH vs others (per-window paired diff in R/trade, pure_backtest_oos)")
    print("| comparison | n | A wins | median ΔR/t | mean ΔR/t | sign-test p | Wilcoxon p | A advantage? |")
    print("|---|---|---|---|---|---|---|---|")
    for k, c in results["A_vs_others"].items():
        print(f"| {k} | {c['n_windows']} | {c['n_A_wins']} | {c['median_diff_R/trade']:+.3f} | "
              f"{c['mean_diff_R/trade']:+.3f} | {c['sign_test_p_2sided']:.3f} | "
              f"{c['wilcoxon_p_2sided']:.3f} | {'YES' if c['A_advantage'] else 'no'} |")
    print("\n#### cost stress (pure_backtest_oos)")
    print("| config | cost_mult | OOS+/n | mean net% | mean R/t | total PnL$ |")
    print("|---|---|---|---|---|---|")
    for cname, rows in results["cost_stress"].items():
        for r in rows:
            print(f"| {cname} | {r['cost_mult']:.2f}x | {r['oos_positive']}/{r['n_windows']} | "
                  f"{r['mean_net_pct']*100:+.2f} | {r['mean_r_per_trade']:+.3f} | {r['total_pnl']:+,.1f} |")
    print("\n#### window list (config A_ETH)")
    print("| # | OOS start | OOS end | live? | lowConf? | trades | win% | net% | R/t | dailySharpe | IS R/t |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(per_config_oos["A_ETH"]):
        print(f"| {i} | {r['oos_start']} | {r['oos_end']} | {'Y' if r['live_overlap'] else ''} | "
              f"{'Y' if r['low_confidence'] else ''} | {r['n_trades']} | {r['win_rate']*100:.1f} | "
              f"{r['net_pct']*100:+.2f} | {r['r_per_trade']:+.3f} | {r['daily_sharpe']:.2f} | {r['is_r_per_trade']:+.3f} |")
    _save("phase_wf.json", results)


# ===========================================================================
def main() -> int:
    # the canonical reference run can spam "order rejected" lines from the
    # never-resetting daily-loss counter (artifact of BacktestEngine not calling
    # reset_daily); silence it — our ExpBroker runs DO reset daily per UTC date.
    logging.getLogger("src.execution.backtest_broker").setLevel(logging.ERROR)
    logging.getLogger("src.execution.risk").setLevel(logging.ERROR)

    ap = argparse.ArgumentParser(description="BBKC universe-expansion experiment")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("single")
    sub.add_parser("combo")
    pf = sub.add_parser("wf")
    pf.add_argument("--anchor-date", default="2024-04-01")
    pf.add_argument("--data-start", default=None, help="defaults to --anchor-date")
    pf.add_argument("--data-end", default="2026-05-13")
    pf.add_argument("--is-months", type=int, default=6)
    pf.add_argument("--oos-months", type=int, default=2)
    pf.add_argument("--step-months", type=int, default=2)
    pf.add_argument("--min-trades-per-window", type=int, default=10)
    pf.add_argument("--live-start", default="2026-04-29")
    pf.add_argument("--exclude-live-overlap-from-verdict", action="store_true", default=True)
    pf.add_argument("--include-live-overlap-in-verdict", dest="exclude_live_overlap_from_verdict",
                    action="store_false")
    pf.add_argument("--cost-mult", nargs="+", type=float, default=[1.0, 1.25, 1.5])
    ps = sub.add_parser("slsweep")
    ps.add_argument("--symbols", nargs="+", default=ALL_SYMBOLS)
    ps.add_argument("--sl-list", nargs="+", type=float,
                    default=[0.015, 0.016, 0.017, 0.018, 0.019, 0.020],
                    help="price-based SL fractions to sweep (e.g. 0.015 = 1.5%)")
    ps.add_argument("--price-tp", type=float, default=0.02, help="price-based TP fraction (fixed)")
    ps.add_argument("--leverage", type=int, default=3)
    pw = sub.add_parser("weights"); pw.add_argument("--universe", nargs="+", required=True)
    pl = sub.add_parser("leverage"); pl.add_argument("--universe", nargs="+", required=True)
    pl.add_argument("--weights-json", default=None,
                    help="path to a json {symbol: max_position_pct}")
    pl.add_argument("--no-guards", action="store_true",
                    help="disable the daily-loss / max-drawdown halts (shows raw P&L; "
                         "NOT live behaviour — the live bot WOULD halt)")
    args = ap.parse_args()

    cfg_full = load_config(str(PROJECT_ROOT / "config.yaml"))
    cfg: BacktestConfig = cfg_full.backtest
    risk_cfg: RiskConfig = cfg_full.risk
    db = DBManager(str(PROJECT_ROOT / cfg_full.app.db_path))
    print(f"DB: {PROJECT_ROOT / cfg_full.app.db_path}")
    print(f"BacktestConfig: initial_capital={cfg.initial_capital} taker_fee={cfg.taker_fee_pct} "
          f"maker_fee={cfg.maker_fee_pct} slippage={cfg.slippage_pct}")
    print(f"RiskConfig: max_position_pct={risk_cfg.max_position_pct} "
          f"max_concurrent={risk_cfg.max_concurrent} daily_loss_limit={risk_cfg.daily_loss_limit_pct} "
          f"max_dd={risk_cfg.max_drawdown_pct}")

    {"single": cmd_single, "combo": cmd_combo, "slsweep": cmd_slsweep, "wf": cmd_wf,
     "weights": cmd_weights, "leverage": cmd_leverage}[args.cmd](args, db, cfg, risk_cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
