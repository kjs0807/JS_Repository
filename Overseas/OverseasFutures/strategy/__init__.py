"""전략 연결 모듈 — DualBB FSM → Paper Trading 주문."""

import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from config.products import PRODUCTS, FuturesProduct
from strategy.dual_bollinger.config import DualBBConfig
from strategy.dual_bollinger.events import EventType, State, StrategyEvent, Position
from strategy.dual_bollinger.state_machine import DualBBStateMachine
from collector.bar_resampler import Bar

logger = logging.getLogger(__name__)

# Default strategy config
DEFAULT_CONFIG = DualBBConfig(
    candle_minutes=60,
    ma_period=20,
    sigma_inner=1.5,
    sigma_outer=3.0,
    breakout_pct=0.0,
    rsi_period=14,
    rsi_overbought=70.0,
    rsi_oversold=30.0,
    atr_stop_period=14,
    atr_stop_multiplier=2.0,
    use_trailing_stop=True,
    trailing_activation_atr=1.0,
    trailing_distance_atr=1.5,
    vol_filter_enabled=True,
    max_bandwidth_pct=8.0,
    base_qty=1,
    scale_qty=1,
)


@dataclass
class IndicatorState:
    """심볼별 지표 상태."""
    closes: List[float] = field(default_factory=list)
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    ma_period: int = 20
    atr_period: int = 14
    rsi_period: int = 14

    def add_bar(self, high: float, low: float, close: float) -> None:
        self.closes.append(close)
        self.highs.append(high)
        self.lows.append(low)
        # 메모리 제한: 필요한 최대 윈도우의 2배만 보관
        max_keep = max(self.ma_period, self.atr_period, self.rsi_period) * 2 + 1
        if len(self.closes) > max_keep:
            self.closes = self.closes[-max_keep:]
            self.highs = self.highs[-max_keep:]
            self.lows = self.lows[-max_keep:]

    def ready(self) -> bool:
        return len(self.closes) >= max(self.ma_period, self.atr_period, self.rsi_period) + 1

    def compute_bands(self, config: DualBBConfig) -> Optional[Dict[str, float]]:
        """볼린저밴드 + ATR + RSI 계산."""
        if not self.ready():
            return None

        period = config.ma_period
        closes = self.closes[-period:]
        ma = statistics.mean(closes)
        std = statistics.stdev(closes) if len(closes) > 1 else 0.0

        inner_upper = ma + config.sigma_inner * std
        inner_lower = ma - config.sigma_inner * std
        outer_upper = ma + config.sigma_outer * std
        outer_lower = ma - config.sigma_outer * std

        # ATR
        atr = self._compute_atr(config.atr_stop_period)

        # RSI
        rsi = self._compute_rsi(config.rsi_period)

        # Bandwidth (%)
        bandwidth = (2 * config.sigma_inner * std / ma * 100) if ma > 0 else 0.0

        return {
            'inner_upper': inner_upper,
            'inner_lower': inner_lower,
            'outer_upper': outer_upper,
            'outer_lower': outer_lower,
            'std': std,
            'atr': atr,
            'rsi': rsi,
            'bandwidth': bandwidth,
            'ma': ma,
        }

    def _compute_atr(self, period: int) -> float:
        if len(self.closes) < 2 or len(self.highs) < period:
            return 0.0
        trs = []
        for i in range(-period, 0):
            h = self.highs[i]
            l = self.lows[i]
            pc = self.closes[i - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 0.0

    def _compute_rsi(self, period: int) -> float:
        if len(self.closes) < period + 1:
            return 50.0
        changes = [self.closes[i] - self.closes[i - 1] for i in range(-period, 0)]
        gains = [c for c in changes if c > 0]
        losses = [-c for c in changes if c < 0]
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


class SymbolTrader:
    """개별 심볼의 전략 실행 + 이벤트 관리."""

    def __init__(self, product: FuturesProduct, config: DualBBConfig = None) -> None:
        self.product = product
        self.config = config or DEFAULT_CONFIG
        self.fsm = DualBBStateMachine(product.symbol, self.config)
        self.indicators = IndicatorState(
            ma_period=self.config.ma_period,
            atr_period=self.config.atr_stop_period,
            rsi_period=self.config.rsi_period,
        )
        self.last_price: Optional[float] = None
        self.events_log: deque = deque(maxlen=500)

    def on_bar(self, bar: Bar, timestamp: datetime) -> Optional[StrategyEvent]:
        """완성 봉 처리 → 지표 계산 → FSM."""
        self.indicators.add_bar(bar.high, bar.low, bar.close)
        self.last_price = bar.close

        bands = self.indicators.compute_bands(self.config)
        if bands is None:
            self.fsm.prev_close = bar.close
            return None

        event = self.fsm.on_bar(timestamp, bar.open, bar.high, bar.low, bar.close, bands)
        if event is not None:
            self.events_log.append(event)
            logger.info(
                "[%s] %s — %s @ %.2f x%d",
                self.product.symbol,
                event.event_type.value,
                event.side,
                event.price,
                event.qty,
            )
        return event


class TradeConnector:
    """전략 → Paper Engine 연결.

    봉 완성 이벤트를 받아 FSM 실행 후 Paper Engine에 주문 전달.
    """

    def __init__(self, configs: Dict[str, DualBBConfig] = None) -> None:
        self.traders: Dict[str, SymbolTrader] = {}
        self.order_callback: Optional[Callable[[StrategyEvent], None]] = None

        for sym, product in PRODUCTS.items():
            cfg = (configs or {}).get(sym, DEFAULT_CONFIG)
            self.traders[sym] = SymbolTrader(product, cfg)

    def on_bar_complete(
        self, symbol: str, bar: Bar, timestamp: datetime
    ) -> Optional[StrategyEvent]:
        """봉 완성 → 전략 실행 → 주문."""
        trader = self.traders.get(symbol)
        if trader is None:
            return None

        event = trader.on_bar(bar, timestamp)
        if event is not None and self.order_callback is not None:
            self.order_callback(event)
        return event

    def get_states(self) -> Dict[str, str]:
        """모든 심볼의 FSM 상태."""
        return {sym: t.fsm.state.value for sym, t in self.traders.items()}

    def get_fsm_states_for_save(self) -> Dict[str, dict]:
        """저장용 FSM 상태."""
        states = {}
        for sym, trader in self.traders.items():
            states[sym] = {
                "state": trader.fsm.state.value,
                "prev_close": trader.fsm.prev_close,
                "bars_since_entry": trader.fsm.bars_since_entry,
                "indicator_closes": trader.indicators.closes[-100:],
                "indicator_highs": trader.indicators.highs[-100:],
                "indicator_lows": trader.indicators.lows[-100:],
            }
            if trader.fsm.position is not None:
                pos = trader.fsm.position
                states[sym]["position"] = {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entries": [(p, q, t.isoformat()) for p, q, t in pos.entries],
                    "total_qty": pos.total_qty,
                    "avg_price": pos.avg_price,
                    "stop_loss_level": pos.stop_loss_level,
                    "trailing_stop_level": pos.trailing_stop_level,
                    "trailing_active": pos.trailing_active,
                    "max_favorable_price": pos.max_favorable_price,
                }
        return states

    def restore_states(self, states: Dict[str, dict]) -> None:
        """저장된 FSM 상태 복원."""
        for sym, data in states.items():
            trader = self.traders.get(sym)
            if trader is None:
                continue

            try:
                trader.fsm.state = State(data.get("state", "FLAT"))
            except ValueError:
                trader.fsm.state = State.FLAT

            trader.fsm.prev_close = data.get("prev_close")
            trader.fsm.bars_since_entry = data.get("bars_since_entry", 0)

            # Restore indicators
            trader.indicators.closes = list(data.get("indicator_closes", []))
            trader.indicators.highs = list(data.get("indicator_highs", []))
            trader.indicators.lows = list(data.get("indicator_lows", []))

            # Restore position
            pos_data = data.get("position")
            if pos_data is not None and trader.fsm.state != State.FLAT:
                entries = []
                for p, q, t in pos_data.get("entries", []):
                    entries.append((p, q, datetime.fromisoformat(t)))
                trader.fsm.position = Position(
                    symbol=pos_data["symbol"],
                    side=pos_data["side"],
                    entries=entries,
                    total_qty=pos_data["total_qty"],
                    avg_price=pos_data["avg_price"],
                    stop_loss_level=pos_data.get("stop_loss_level", 0.0),
                    trailing_stop_level=pos_data.get("trailing_stop_level", 0.0),
                    trailing_active=pos_data.get("trailing_active", False),
                    max_favorable_price=pos_data.get("max_favorable_price", 0.0),
                )

            logger.info("[%s] 상태 복원: %s", sym, trader.fsm.state.value)
