"""통합 트레이딩 엔진 모듈.

3개 전략(PairsTrading, BBKCSqueeze, RSIMACDStrategy)을
1h봉 기반으로 동시 운용하는 멀티 전략 모의거래 엔진.

15분봉 확정 이벤트를 수신하여 1h 리샘플링 후:
  - BBKCSqueeze (1h): 브레이크아웃 모멘텀, 고정 TP6%/SL7%
  - RSIMACDStrategy (1h): 평균회귀, RSI(14,20,70), 고정 TP6%/SL5%
  - PairsTrading (1h): Z-Score 평균회귀, Z@0.0 복귀 청산
  - IchimokuCloud: OFF (비활성화)
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
import time as _time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
from config.products import round_price, get_product

from config.settings import strategy_params, backtest_config, settings, PAIRS_LIST
from engine.scorer import calc_calmar_ratio, calc_monthly_winrate_std
from db.db_manager import DBManager
from api.rest_client import BybitRestClient
from risk.risk_manager import RiskManager, RiskAction
from strategies.base import Signal
from strategies.pairs_trading import PairsTrading
from strategies.bb_kc_squeeze import BBKCSqueeze
from strategies.ichimoku_cloud import IchimokuCloud
from strategies.rsi_macd import RSIMACDStrategy
from paper_engine.pair_selector import PairSelector

logger = logging.getLogger(__name__)

# 전략 파라미터는 config.settings.strategy_params (Single Source of Truth)에서 가져옴
def _get_pairs_params() -> dict:
    """PairsTrading 파라미터를 settings에서 구성한다."""
    return {
        "pairs": list(PAIRS_LIST),
        "zscore_window": strategy_params.pairs_zscore_window,
        "entry_threshold": strategy_params.pairs_entry_z,
        "exit_threshold": strategy_params.pairs_exit_z,
        "stop_threshold": strategy_params.pairs_stop_z,
        "stop_pct": strategy_params.pairs_stop_pct,
        "tp_pct": strategy_params.pairs_tp_pct,
        "adf_pvalue_threshold": strategy_params.pairs_adf_pvalue,
    }


def _get_bbkc_params() -> dict:
    """BBKCSqueeze 파라미터를 settings에서 구성한다."""
    return {
        "bb_period": strategy_params.bb_period,
        "bb_std": strategy_params.bb_std,
        "kc_period": strategy_params.bbkc_kc_period,
        "kc_mult": strategy_params.kc_atr_mult,
        "atr_period": strategy_params.bbkc_atr_period,
        "rsi_period": strategy_params.bbkc_rsi_period,
        "rsi_filter": strategy_params.bbkc_rsi_filter,
        "stop_atr_mult": strategy_params.bbkc_stop_atr,
        "tp_atr_mult": strategy_params.bbkc_tp_atr,
        "exit_mode": strategy_params.bbkc_exit_mode,
        "tp_pct": strategy_params.bbkc_tp_pct,
        "sl_pct": strategy_params.bbkc_sl_pct,
    }


def _get_ichimoku_params() -> dict:
    """IchimokuCloud 파라미터를 settings에서 구성한다."""
    return {
        "tenkan": strategy_params.ichimoku_tenkan,
        "kijun": strategy_params.ichimoku_kijun,
        "senkou": strategy_params.ichimoku_senkou,
        "stop_atr_mult": strategy_params.ichimoku_stop_atr,
        "tp_atr_mult": strategy_params.ichimoku_tp_atr,
    }


def _get_rsimacd_params() -> dict:
    """RSIMACDStrategy 파라미터를 settings에서 구성한다."""
    return {
        "rsi_period": strategy_params.rsimacd_rsi_period,
        "rsi_oversold": strategy_params.rsi_oversold,
        "rsi_overbought": strategy_params.rsi_overbought,
        "macd_fast": strategy_params.rsimacd_macd_fast,
        "macd_slow": strategy_params.rsimacd_macd_slow,
        "macd_signal": strategy_params.rsimacd_macd_signal,
        "adx_period": strategy_params.rsimacd_adx_period,
        "adx_min_threshold": strategy_params.rsimacd_adx_min,
        "atr_period": strategy_params.rsimacd_atr_period,
        "stop_atr_mult": strategy_params.rsimacd_stop_atr,
        "tp_atr_mult": strategy_params.rsimacd_tp_atr,
        "exit_mode": strategy_params.rsimacd_exit_mode,
        "tp_pct": strategy_params.rsimacd_tp_pct,
        "sl_pct": strategy_params.rsimacd_sl_pct,
    }

# 30m = 15m 봉 2개, 1H = 15m 봉 4개, 4H = 15m 봉 16개
_30M_BARS = 2
_1H_BARS = 4
_4H_BARS = 16

# 봉 버퍼 최대 크기 (1600 15m봉 → 1h 버퍼 ~400봉, PairsTrading OLS 250 + Z-Score min_periods 50 충족)
_BUF_MAX = 1600


class _PositionInfo:
    """열린 포지션 정보 (엔진 내부 추적용).

    Attributes:
        trade_id: trade_log.id
        strategy: 전략명
        symbol: 심볼
        direction: "LONG" | "SHORT"
        entry_price: 진입가
        stop_loss: 현재 스톱로스 가격
        take_profit: 익절 가격
        quantity: 수량
        leverage: 레버리지
        margin_used: 사용 마진
        entry_bar: 진입 시점의 봉 카운터
        bar_count: 봉 진행 수 (holding_bars 계산용)
        max_favorable: 최대 유리 방향 가격 변동
        max_adverse: 최대 불리 방향 가격 변동
        atr: 진입 시 ATR (트레일링 스톱용)
        entry_time: 진입 시각 (ISO 문자열)
        entry_fee: 진입 시 수수료
        linked_position_key: 연결된 B-leg 포지션 키 (PairsTrading용)
    """

    def __init__(
        self,
        trade_id: int,
        strategy: str,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float],
        quantity: float,
        leverage: int,
        margin_used: float,
        atr: float,
        entry_time: str,
        entry_fee: float = 0.0,
        linked_position_key: Optional[Tuple[str, str]] = None,
    ) -> None:
        self.trade_id = trade_id
        self.strategy = strategy
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.quantity = quantity
        self.leverage = leverage
        self.margin_used = margin_used
        self.atr = atr
        self.entry_time = entry_time
        self.entry_fee = entry_fee
        self.linked_position_key = linked_position_key
        self.bar_count: int = 0
        self.max_favorable: float = 0.0
        self.max_adverse: float = 0.0
        # F2 (round 2): 체결가 기반 desired SL/TP. 정상 동기화 시 stop_loss/take_profit과 동일.
        # set_trading_stop 실패 시 stop_loss/take_profit은 signal-based 값 유지(서버와 일치),
        # desired_*는 actual-fill-based 값을 보관 (다음 sync 사이클이나 수동 재시도용).
        self.desired_stop_loss: float = stop_loss
        self.desired_take_profit: Optional[float] = take_profit
        self.sl_tp_resync_failed: bool = False


class TradingEngine:
    """4개 전략을 동시 운용하는 통합 모의거래 엔진.

    WebSocket에서 15분봉이 확정될 때마다 on_new_bar_15m()을 호출하면
    전략별 시그널 생성, 리스크 체크, 주문 실행, trade_log 기록까지
    전체 파이프라인을 처리한다.

    Attributes:
        db: DBManager 인스턴스
        rest_client: BybitRestClient 인스턴스
        risk_manager: RiskManager 인스턴스
        leverage: 레버리지 배수
        taker_fee_pct: 테이커 수수료 비율
        slippage_pct: 슬리피지 비율
    """

    def __init__(
        self,
        db: DBManager,
        rest_client: BybitRestClient,
        risk_manager: RiskManager,
        leverage: int = 3,
        taker_fee_pct: float = 0.00055,
        slippage_pct: float = 0.0003,
    ) -> None:
        """TradingEngine 초기화.

        Args:
            db: DBManager 인스턴스
            rest_client: BybitRestClient 인스턴스 (Demo API)
            risk_manager: RiskManager 인스턴스
            leverage: 레버리지 배수 (기본 3)
            taker_fee_pct: 테이커 수수료 비율 (기본 0.055%)
            slippage_pct: 슬리피지 비율 (기본 0.03%)
        """
        self.db = db
        self.rest_client = rest_client
        self.risk_manager = risk_manager
        self.leverage = leverage
        self.taker_fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct

        # 전략 인스턴스 생성 (settings 기반 파라미터)
        self._pairs_strategy = PairsTrading(**_get_pairs_params())
        self._bbkc_strategy = BBKCSqueeze(**_get_bbkc_params())
        self._ichimoku_strategy = IchimokuCloud(**_get_ichimoku_params())
        self._rsimacd_strategy = RSIMACDStrategy(**_get_rsimacd_params())

        # 전략 활성화 상태 (True=활성, False=비활성)
        self._strategy_enabled: Dict[str, bool] = {
            "PairsTrading": True,
            "BBKCSqueeze": True,
            "IchimokuCloud": False,
            "RSIMACDStrategy": True,
        }

        # 15m 봉 버퍼: symbol -> list of bar dicts
        self._buf_15m: Dict[str, List[dict]] = defaultdict(list)

        # (카운터 대신 벽시계 정렬 리샘플링 사용 -- _resample_30m/_resample_4h 버퍼)

        # 30m 봉 버퍼: symbol -> list of resampled bar dicts
        self._buf_30m: Dict[str, List[dict]] = defaultdict(list)

        # 1H 봉 버퍼: symbol -> list of resampled bar dicts
        self._buf_1h: Dict[str, List[dict]] = defaultdict(list)

        # 4H 봉 버퍼: symbol -> list of resampled bar dicts
        self._buf_4h: Dict[str, List[dict]] = defaultdict(list)

        # 30m 임시 버퍼 (리샘플 누적용): symbol -> list (최대 _30M_BARS개)
        self._resample_30m: Dict[str, List[dict]] = defaultdict(list)

        # 1H 임시 버퍼 (리샘플 누적용): symbol -> list (최대 _1H_BARS개)
        self._resample_1h: Dict[str, List[dict]] = defaultdict(list)

        # 4H 임시 버퍼 (리샘플 누적용): symbol -> list (최대 _4H_BARS개)
        self._resample_4h: Dict[str, List[dict]] = defaultdict(list)

        # 열린 포지션: (strategy_name, symbol) -> _PositionInfo
        self._positions: Dict[Tuple[str, str], _PositionInfo] = {}

        # 쿨다운: (strategy_name, symbol) -> 재진입 허용 봉 번호
        self._cooldown_until: Dict[Tuple[str, str], int] = {}

        # 페어별 실시간 공적분 유효성: (sym_a, sym_b) -> bool
        self._pair_coint_valid: Dict[Tuple[str, str], bool] = {}

        # 총 처리 봉 수 (통계용)
        self._total_bars: int = 0

        # 심볼별 최소 주문 단위 캐시: symbol -> (min_qty, qty_step)
        self._qty_spec_cache: Dict[str, Tuple[float, float]] = {}

        # API 포지션 동기화 마지막 실행 시각 (time.time() epoch 초)
        self._last_sync_time: float = 0.0

        # 동적 페어 선택기
        self._pair_selector = PairSelector(db=self.db)
        self._last_pair_selection_time: float = 0.0
        self._pair_selection_interval: float = 24 * 3600  # 24시간
        self._restart_requested: bool = False

        logger.info(
            "TradingEngine 초기화 완료: 전략=PairsTrading/BBKCSqueeze/IchimokuCloud/RSIMACDStrategy "
            "레버리지=%dx",
            leverage,
        )

        # DB에서 오늘 실현 PnL을 RiskManager에 주입 (크래시 복구)
        try:
            today_pnl = self.db.get_daily_pnl()
            if today_pnl != 0.0:
                self.risk_manager._daily_tracker._daily_pnl = today_pnl
                logger.info("일일 PnL 부트스트랩: %.2f USDT", today_pnl)
        except Exception as exc:
            logger.warning("일일 PnL 부트스트랩 실패: %s", exc)

        # 헤지모드(BothSide) 전환 보장
        try:
            self.rest_client.switch_position_mode(mode=3)
            logger.info("헤지모드(BothSide) 설정 완료")
        except Exception as exc:
            if "110025" in str(exc):
                logger.info("헤지모드 이미 활성화됨")
            else:
                logger.warning("헤지모드 전환 실패: %s", exc)

        # 심볼별 레버리지 설정 (Bybit 계정 기본값 덮어쓰기)
        from config.settings import settings as _app_settings
        for sym in _app_settings.symbols:
            try:
                self.rest_client.set_leverage(
                    symbol=sym,
                    buy_leverage=self.leverage,
                    sell_leverage=self.leverage,
                )
            except Exception as exc:
                if "110043" in str(exc):
                    # 이미 동일 레버리지 설정됨
                    pass
                else:
                    logger.warning("레버리지 설정 실패 %s: %s", sym, exc)
        logger.info("심볼별 레버리지 %dx 설정 완료", self.leverage)

        # DB에서 과거 봉을 버퍼에 미리 로드 (워밍업 즉시 완료)
        self._prefill_buffers()

        # 동적 페어 선별 (초기 1회)
        self._update_pairs()

        # 크래시 복구: 이전 포지션/봉 카운터 상태 복원 + API 대조 검증
        self.load_state()
        self._reconcile_with_api()

    @staticmethod
    def _get_project_root() -> str:
        """프로젝트 루트 경로를 반환한다.

        PyInstaller exe 실행 시 sys.executable의 부모 디렉토리,
        일반 Python 실행 시 __file__ 기준 상위 디렉토리를 사용한다.
        """
        import sys as _sys
        if getattr(_sys, "frozen", False):
            return os.path.dirname(os.path.abspath(_sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _prefill_buffers(self) -> None:
        """DB의 과거 봉 데이터를 메모리 버퍼에 미리 로드한다.

        이렇게 하면 WS 시작 직후 첫 봉부터 시그널 생성이 가능하다.
        전략별 워밍업을 위해 15m 최근 600봉, 4H 최근 200봉을 로드한다.
        PairsTrading B측 심볼의 워밍업이 부족하면 DB에서 추가 로드를 시도한다.
        """
        from config.settings import settings as _current_settings
        symbols = _current_settings.symbols
        loaded_count = 0

        for symbol in symbols:
            try:
                # 15m 봉 로드 (최근 600봉)
                df_15m = self.db.get_ohlcv(symbol, "15m")
                if df_15m is not None and len(df_15m) > 0:
                    tail = df_15m.tail(min(len(df_15m), _BUF_MAX))
                    records = tail.reset_index().to_dict("records")
                    bars_15m = []
                    for rec in records:
                        ot = rec.get("open_time", 0)
                        bars_15m.append({
                            "open_time": int(ot) if not isinstance(ot, int) else ot,
                            "open": float(rec["open"]),
                            "high": float(rec["high"]),
                            "low": float(rec["low"]),
                            "close": float(rec["close"]),
                            "volume": float(rec.get("volume", 0)),
                        })
                    self._buf_15m[symbol] = bars_15m
                    loaded_count += len(bars_15m)

                    # 30m 버퍼 생성 (벽시계 정렬: open_time % 30m == 0)
                    _30M_MS = 30 * 60 * 1000
                    bars_30m = []
                    chunk_30m: list = []
                    for b in bars_15m:
                        if b["open_time"] % _30M_MS == 0 and chunk_30m:
                            bars_30m.append(self._merge_bars(chunk_30m))
                            chunk_30m = [b]
                        else:
                            chunk_30m.append(b)
                    if chunk_30m:
                        bars_30m.append(self._merge_bars(chunk_30m))
                    self._buf_30m[symbol] = bars_30m

                # 1H 버퍼: DB ohlcv_1h에서 직접 로드 (심볼 간 정각 정렬 보장)
                try:
                    df_1h = self.db.get_ohlcv(symbol, "1h", limit=_BUF_MAX)
                    if df_1h is not None and len(df_1h) > 0:
                        records_1h = df_1h.reset_index().to_dict("records")
                        bars_1h_db = []
                        for rec in records_1h:
                            ot = rec.get("open_time", 0)
                            if not isinstance(ot, int):
                                ot = int(pd.Timestamp(ot).value // 1_000_000)
                            bars_1h_db.append({
                                "open_time": ot,
                                "open": float(rec["open"]),
                                "high": float(rec["high"]),
                                "low": float(rec["low"]),
                                "close": float(rec["close"]),
                                "volume": float(rec.get("volume", 0)),
                            })
                        self._buf_1h[symbol] = bars_1h_db
                    else:
                        self._buf_1h[symbol] = []
                except Exception as exc:
                    logger.warning("1H 봉 로드 실패 %s: %s", symbol, exc)
                    self._buf_1h[symbol] = []

                # 4H 봉 로드 (DBManager.get_ohlcv 사용)
                try:
                    df_4h = self.db.get_ohlcv(symbol, "4h", limit=200)
                    if df_4h is not None and len(df_4h) > 0:
                        records_4h = df_4h.reset_index().to_dict("records")
                        bars_4h = []
                        for rec in records_4h:
                            ot = rec.get("open_time", 0)
                            bars_4h.append({
                                "open_time": int(ot) if not isinstance(ot, int) else ot,
                                "open": float(rec["open"]),
                                "high": float(rec["high"]),
                                "low": float(rec["low"]),
                                "close": float(rec["close"]),
                                "volume": float(rec.get("volume", 0)),
                            })
                        self._buf_4h[symbol] = bars_4h
                except Exception as exc:
                    logger.warning("4H 봉 로드 실패 %s: %s", symbol, exc)

            except Exception as exc:
                logger.warning("버퍼 사전 로드 실패 %s: %s", symbol, exc)

        # PairsTrading B측 심볼 워밍업 최종 검증 (_buf_1h는 이미 DB에서 로드됨)
        required = self._pairs_strategy.required_warmup()
        for _, sym_b in self._pairs_strategy.pairs:
            buf_1h_len = len(self._buf_1h.get(sym_b, []))
            if buf_1h_len < required:
                logger.warning(
                    "PairsTrading B측 %s 1h 워밍업 부족: %d/%d봉 — 해당 페어 시그널 불가",
                    sym_b, buf_1h_len, required,
                )
            else:
                logger.info(
                    "PairsTrading B측 %s 1h 워밍업 OK: %d봉 (필요=%d)",
                    sym_b, buf_1h_len, required,
                )

        # 현재 진행 중인 1H 구간의 15m 봉을 _resample_1h에 적재 (첫 1H 봉 누락 방지)
        now_ms = int(_time.time() * 1000)
        _1H_MS = 3600 * 1000
        current_hour_start = (now_ms // _1H_MS) * _1H_MS
        for symbol, bars in self._buf_15m.items():
            partial = [b for b in bars if b["open_time"] >= current_hour_start]
            if partial:
                self._resample_1h[symbol] = partial

        logger.info(
            "버퍼 사전 로드 완료: %d개 심볼, 15m %d봉 로드",
            len(symbols), loaded_count,
        )

    # -- 외부 진입점 --------------------------------------------------------

    def on_new_bar_15m(self, symbol: str, bar: dict) -> None:
        """15분봉 확정 시 호출되는 메인 파이프라인.

        파이프라인:
          1. 봉 버퍼에 저장
          2. 15m 전략 실행 (PairsTrading, RSIMACDStrategy)
          3. 30m 봉 누적 -> 2개 모이면 IchimokuCloud 실행
          4. 4H 봉 누적 -> 16개 모이면 BBKCSqueeze 실행
          5. 시그널 -> signal_log 기록
          6. 리스크 체크 -> 주문 실행 -> trade_log 기록 (entry)
          7. 열린 포지션 스톱/TP 체크 -> trade_log 업데이트 (exit)

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            bar: 15분봉 dict. 키: open_time(ms int), open, high, low, close, volume
        """
        try:
            bar = self._normalize_bar(bar)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("봉 정규화 실패 %s: %s", symbol, exc)
            return

        # 1. 15m 버퍼 저장 + DB 저장
        self._buf_15m[symbol].append(bar)
        if len(self._buf_15m[symbol]) > _BUF_MAX:
            self._buf_15m[symbol] = self._buf_15m[symbol][-_BUF_MAX:]
        self._total_bars += 1

        try:
            self.db.upsert_ohlcv_by_timeframe(symbol, "15", [bar])
        except Exception as exc:
            logger.debug("15m DB 저장 실패 %s: %s", symbol, exc)

        # 열린 포지션 스톱/TP 체크 (현재 봉 close 기준)
        self._check_open_positions_for_symbol(symbol, float(bar["close"]))

        # 포지션 변동 추적 (save_state용)
        positions_before = set(self._positions.keys())

        # 2. 30m 벽시계 정렬 리샘플링 (DB 저장용, 전략 실행 없음)
        open_time = int(bar["open_time"])
        _30M_MS = 30 * 60 * 1000
        is_30m_boundary = (open_time % _30M_MS == 0)

        if is_30m_boundary and self._resample_30m[symbol]:
            bar_30m = self._merge_bars(self._resample_30m[symbol])
            self._resample_30m[symbol] = [bar]
            self._buf_30m[symbol].append(bar_30m)
            if len(self._buf_30m[symbol]) > _BUF_MAX:
                self._buf_30m[symbol] = self._buf_30m[symbol][-_BUF_MAX:]
            try:
                self.db.upsert_ohlcv_by_timeframe(symbol, "30", [bar_30m])
            except Exception as exc:
                logger.debug("30m DB 저장 실패 %s: %s", symbol, exc)
        else:
            self._resample_30m[symbol].append(bar)

        # 3. 1H 벽시계 정렬 리샘플링 + 전략 실행 (핵심)
        _1H_MS = 60 * 60 * 1000
        is_1h_boundary = (open_time % _1H_MS == 0)

        if is_1h_boundary and self._resample_1h[symbol]:
            bar_1h = self._merge_bars(self._resample_1h[symbol])
            self._resample_1h[symbol] = [bar]
            self._buf_1h[symbol].append(bar_1h)
            if len(self._buf_1h[symbol]) > _BUF_MAX:
                self._buf_1h[symbol] = self._buf_1h[symbol][-_BUF_MAX:]
            try:
                self.db.upsert_ohlcv_by_timeframe(symbol, "60", [bar_1h])
            except Exception as exc:
                logger.debug("1H DB 저장 실패 %s: %s", symbol, exc)

            # 전 전략 1h 기반 실행
            for sig in self._run_1h_strategies(symbol):
                self._process_signal(sig)
        else:
            self._resample_1h[symbol].append(bar)

        # 4. 4H 벽시계 정렬 리샘플링 (DB 저장용, 전략 실행 없음)
        _4H_MS = 4 * 60 * 60 * 1000
        is_4h_boundary = (open_time % _4H_MS == 0)

        if is_4h_boundary and self._resample_4h[symbol]:
            bar_4h = self._merge_bars(self._resample_4h[symbol])
            self._resample_4h[symbol] = [bar]
            self._buf_4h[symbol].append(bar_4h)
            if len(self._buf_4h[symbol]) > _BUF_MAX:
                self._buf_4h[symbol] = self._buf_4h[symbol][-_BUF_MAX:]
            try:
                self.db.upsert_ohlcv_by_timeframe(symbol, "240", [bar_4h])
            except Exception as exc:
                logger.debug("4H DB 저장 실패 %s: %s", symbol, exc)
        else:
            self._resample_4h[symbol].append(bar)

        # 24시간마다 심볼 유니버스 갱신 + 동적 페어 재선별 + 전략 성과 체크
        if _time.time() - self._last_pair_selection_time > self._pair_selection_interval:
            try:
                from config.symbol_manager import get_symbol_manager
                sm = get_symbol_manager()
                old_pairs = set(sm.pairs_universe)
                if sm.maybe_refresh():
                    new_pairs = set(sm.pairs_universe)
                    if old_pairs != new_pairs:
                        added = new_pairs - old_pairs
                        removed = old_pairs - new_pairs
                        logger.info(
                            "심볼 유니버스 변경 감지: 추가=%s, 제거=%s → 재시작 예약",
                            added or "없음", removed or "없음",
                        )
                        self._restart_requested = True
                    else:
                        logger.info("심볼 유니버스 갱신 완료 (변경 없음, 페어=%d개)", len(sm.pairs_universe))
            except Exception as exc:
                logger.warning("심볼 유니버스 갱신 실패: %s", exc)
            self._update_pairs()
            self._check_strategy_performance()

        # API 포지션 동기화 (60초 간격)
        self._maybe_sync_positions()

        # 포지션 변동 시 상태 저장
        positions_after = set(self._positions.keys())
        if positions_before != positions_after:
            try:
                self.save_state()
            except Exception as exc:
                logger.debug("상태 저장 실패: %s", exc)

    def _update_pairs(self) -> None:
        """동적 페어 선별 후 PairsTrading 전략을 업데이트한다.

        select_pairs()가 빈 리스트를 반환하면 기존 페어를 유지한다.
        예외 발생 시 경고 로그만 남기고 기존 페어를 보존한다.
        """
        try:
            new_pairs = self._pair_selector.select_pairs()
            if new_pairs:
                self._pairs_strategy.pairs = new_pairs
                self._last_pair_selection_time = _time.time()
                logger.info(
                    "동적 페어 선별: %d개 페어 - %s",
                    len(new_pairs),
                    new_pairs,
                )
            else:
                logger.warning("동적 페어 선별 결과 없음: 기존 페어 유지")
                self._last_pair_selection_time = _time.time()
        except Exception as exc:
            logger.warning("동적 페어 선별 실패: %s", exc)
            self._last_pair_selection_time = _time.time()

    def check_open_positions(self, prices: Dict[str, float]) -> None:
        """외부에서 현재 가격을 주입하여 모든 포지션 스톱/TP 체크.

        WebSocket 실시간 가격 업데이트 시 호출 가능.

        Args:
            prices: 심볼 -> 현재 가격 딕셔너리
        """
        for symbol, price in prices.items():
            self._check_open_positions_for_symbol(symbol, price)

    def get_status(self) -> dict:
        """현재 엔진 상태 요약을 반환한다.

        Returns:
            상태 딕셔너리:
              - open_positions: 열린 포지션 목록
              - position_count: 포지션 수
              - total_bars_processed: 처리된 봉 수
              - daily_pnl: 오늘 실현 PnL
              - risk_status: RiskManager 상태
        """
        positions_info = []
        for pos_key, pos in self._positions.items():
            positions_info.append({
                "strategy": pos.strategy,
                "symbol": pos.symbol,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "quantity": pos.quantity,
                "margin_used": pos.margin_used,
                "bar_count": pos.bar_count,
            })

        # API 기준 잔고/PnL 조회 (source of truth)
        api_equity = self.risk_manager._current_equity
        daily_pnl = 0.0
        try:
            api_positions = self.rest_client.get_positions()
            for p in api_positions:
                daily_pnl += float(p.get("curRealisedPnl", 0))
                daily_pnl += float(p.get("unrealisedPnl", 0))
            bal = self.rest_client.get_wallet_balance()
            if bal:
                for coin in bal.get("coin", []):
                    if coin.get("coin") == "USDT":
                        api_equity = float(coin.get("walletBalance", api_equity))
                        self.risk_manager._current_equity = api_equity
                        break
        except Exception as exc:
            logger.warning("API 상태 조회 실패, 내부값 사용: %s", exc)

        # 쿨다운 상태
        cooldowns = {}
        for cd_key, cd_bar in self._cooldown_until.items():
            remaining = max(0, cd_bar - self._total_bars)
            if remaining > 0:
                cooldowns[f"{cd_key[0]}|{cd_key[1]}"] = remaining

        # 공적분 상태
        coint_status = {}
        for pair_key, valid in self._pair_coint_valid.items():
            coint_status[f"{pair_key[0]}-{pair_key[1]}"] = valid

        return {
            "open_positions": positions_info,
            "position_count": len(self._positions),
            "total_bars_processed": self._total_bars,
            "daily_pnl": daily_pnl,
            "risk_status": self.risk_manager.get_status(),
            "cooldowns": cooldowns,
            "pair_cointegration": coint_status,
        }

    # -- 전략 활성화 관리 ----------------------------------------------------

    def set_strategy_enabled(self, name: str, enabled: bool) -> None:
        """전략 활성화/비활성화.

        Args:
            name: 전략명 (PairsTrading, BBKCSqueeze, IchimokuCloud, RSIMACDStrategy)
            enabled: True=활성, False=비활성
        """
        if name in self._strategy_enabled:
            self._strategy_enabled[name] = enabled
            logger.info("전략 %s: %s", name, "활성화" if enabled else "비활성화")

    def get_strategy_enabled(self) -> Dict[str, bool]:
        """전략 활성화 상태 반환."""
        return dict(self._strategy_enabled)

    def get_strategy_metrics(self) -> Dict[str, dict]:
        """전략별 성과 메트릭(Calmar, 승률, 거래 수)을 trade_log에서 계산한다.

        Returns:
            전략명 -> {calmar, winrate, monthly_wr_std, trade_count} 딕셔너리
        """
        try:
            all_trades = self.db.get_recent_trades(limit=2000)
        except Exception as exc:
            logger.warning("trade_log 조회 실패: %s", exc)
            return {}

        # 전략별 분류
        by_strategy: Dict[str, list] = {}
        for t in all_trades:
            strat = t.get("strategy", "Unknown")
            by_strategy.setdefault(strat, []).append(t)

        metrics: Dict[str, dict] = {}
        for strat, trades in by_strategy.items():
            closed = [t for t in trades if t.get("exit_time")]
            count = len(closed)
            if count == 0:
                metrics[strat] = {"calmar": 0.0, "winrate": 0.0, "monthly_wr_std": 1.0, "trade_count": 0}
                continue

            wins = sum(1 for t in closed if (t.get("net_pnl") or 0) > 0)
            winrate = wins / count

            # 에퀴티 커브 근사 (누적 PnL)
            capital = backtest_config.initial_capital
            equity = [capital]
            for t in sorted(closed, key=lambda x: x.get("exit_time", "")):
                capital += (t.get("net_pnl") or 0)
                equity.append(capital)

            calmar = calc_calmar_ratio(equity, backtest_config.initial_capital)
            wr_std = calc_monthly_winrate_std(closed)

            metrics[strat] = {
                "calmar": round(calmar, 4),
                "winrate": round(winrate, 4),
                "monthly_wr_std": round(wr_std, 4),
                "trade_count": count,
            }

        return metrics

    def _check_strategy_performance(self) -> None:
        """전략별 Calmar Ratio로 자동 활성화/비활성화를 판단한다.

        최소 거래 수 미달 전략은 판단하지 않는다.
        비활성화 시 기존 포지션은 유지하되 새 진입만 차단한다.
        """
        metrics = self.get_strategy_metrics()
        min_trades = strategy_params.min_trades_for_eval
        disable_threshold = strategy_params.auto_disable_calmar
        enable_threshold = strategy_params.auto_enable_calmar

        for strat, m in metrics.items():
            if strat not in self._strategy_enabled:
                continue
            if m["trade_count"] < min_trades:
                continue

            calmar = m["calmar"]
            currently_enabled = self._strategy_enabled[strat]

            if currently_enabled and calmar < disable_threshold:
                self._strategy_enabled[strat] = False
                logger.warning(
                    "전략 자동 비활성화: %s (Calmar=%.4f < %.1f)",
                    strat, calmar, disable_threshold,
                )
            elif not currently_enabled and calmar >= enable_threshold:
                self._strategy_enabled[strat] = True
                logger.info(
                    "전략 자동 재활성화: %s (Calmar=%.4f >= %.1f)",
                    strat, calmar, enable_threshold,
                )

    # -- 전략 실행 ----------------------------------------------------------

    def _run_1h_strategies(self, symbol: str) -> List[Signal]:
        """1h 봉 기반 전 전략 실행 (BBKCSqueeze, RSIMACDStrategy, PairsTrading).

        Args:
            symbol: 심볼

        Returns:
            발생한 Signal 목록
        """
        from config.symbol_manager import STRATEGY_SYMBOLS

        signals: List[Signal] = []
        buf = self._buf_1h[symbol]
        if len(buf) < 30:
            return signals

        df = self._buf_to_df(buf)

        # BBKCSqueeze (1h) — strategy_symbols에 있는 심볼만
        if symbol in STRATEGY_SYMBOLS and self._strategy_enabled.get("BBKCSqueeze", True):
            if len(buf) >= self._bbkc_strategy.required_warmup():
                try:
                    sig = self._bbkc_strategy.generate_signal(df.copy(), symbol)
                    if sig is not None:
                        signals.append(sig)
                except Exception as exc:
                    logger.debug("BBKCSqueeze 오류 %s: %s", symbol, exc)

        # RSIMACDStrategy (1h) — strategy_symbols에 있는 심볼만
        if symbol in STRATEGY_SYMBOLS and self._strategy_enabled.get("RSIMACDStrategy", True):
            try:
                sig = self._rsimacd_strategy.generate_signal(df.copy(), symbol)
                if sig is not None:
                    signals.append(sig)
            except Exception as exc:
                logger.debug("RSIMACDStrategy 오류 %s: %s", symbol, exc)

        # PairsTrading (1h): 이 심볼이 페어 A인 경우만 실행
        if not self._strategy_enabled.get("PairsTrading", True):
            return signals
        for sym_a, sym_b in self._pairs_strategy.pairs:
            if sym_a != symbol:
                continue
            buf_b = self._buf_1h.get(sym_b)
            required_warmup = self._pairs_strategy.required_warmup()
            logger.debug(
                "PairsTrading 버퍼 체크: A=%s(%d봉) B=%s(%d봉) 필요=%d",
                sym_a, len(buf), sym_b, len(buf_b) if buf_b else 0, required_warmup,
            )
            if not buf_b or len(buf_b) < required_warmup:
                continue
            df_b = self._buf_to_df(buf_b)

            # 공적분은 pair_selector(24h 주기, 52일 Engle-Granger)에서 이미 검증됨
            # 매 1h봉마다 단기 버퍼로 재검정하면 방법론·lookback 불일치로 오탐 발생
            self._pair_coint_valid[(sym_a, sym_b)] = True

            try:
                sig = self._pairs_strategy.generate_signal_pair(
                    df.copy(), df_b, symbol, sym_b
                )
                if sig is not None:
                    signals.append(sig)
            except Exception as exc:
                logger.debug("PairsTrading 오류 %s-%s: %s", symbol, sym_b, exc)

        # PairsTrading Z-Score 복귀 청산 체크 (열린 포지션)
        self._check_pairs_zscore_exit(symbol)

        return signals

    def _check_pairs_zscore_exit(self, symbol: str) -> None:
        """PairsTrading 열린 포지션의 Z-Score 복귀 청산을 체크한다.

        Z-Score가 exit_z(0.0) 이내로 복귀하면 청산.
        1h 봉 확정 시점에만 호출.
        """
        pos_key = ("PairsTrading", symbol)
        pos = self._positions.get(pos_key)
        if pos is None:
            return

        # 이 심볼이 A-leg인 페어를 찾기
        for sym_a, sym_b in self._pairs_strategy.pairs:
            if sym_a != symbol:
                continue

            buf_a = self._buf_1h.get(sym_a)
            buf_b = self._buf_1h.get(sym_b)
            if not buf_a or not buf_b or len(buf_a) < 50 or len(buf_b) < 50:
                continue

            df_a = self._buf_to_df(buf_a)
            df_b = self._buf_to_df(buf_b)

            try:
                from indicators.zscore import calc_pair_zscore
                zdf = calc_pair_zscore(df_a, df_b, window=strategy_params.pairs_zscore_window)
                if zdf.empty:
                    continue
                cur_z = float(zdf.iloc[-1]["zscore"])
                if pd.isna(cur_z):
                    continue

                exit_z = strategy_params.pairs_exit_z  # 0.0

                # 롱 스프레드(Z < -entry로 진입) -> Z >= -exit_z 일 때 청산
                # 숏 스프레드(Z > entry로 진입) -> Z <= exit_z 일 때 청산
                should_exit = False
                if pos.direction == "LONG" and cur_z >= -exit_z:
                    should_exit = True
                elif pos.direction == "SHORT" and cur_z <= exit_z:
                    should_exit = True

                if should_exit:
                    current_price = float(buf_a[-1]["close"])
                    logger.info(
                        "PairsTrading Z-Score 복귀 청산: %s Z=%.3f (exit=%.1f)",
                        symbol, cur_z, exit_z,
                    )
                    self._close_position(pos_key, current_price, "SIGNAL")
            except Exception as exc:
                logger.debug("PairsTrading Z청산 오류 %s: %s", symbol, exc)
            break  # 해당 심볼의 페어는 하나

    # -- F2 헬퍼: 체결가 기반 SL/TP 재계산 + Bybit 동기화 -----------------------

    def _compute_desired_sl_tp(
        self, strategy_name: str, direction: str, avg_price: float,
    ) -> Tuple[Optional[float], Optional[float]]:
        """체결가 avg_price 기준으로 SL/TP 재계산.

        BBKCSqueeze (fixed mode): pct/leverage 기반.
        RSIMACDStrategy: 동일 패턴.
        그 외 (PairsTrading 등) 또는 BBKC ATR mode: (None, None) — F2 미적용.

        Returns:
            (sl, tp). 둘 다 None이면 caller는 resync 호출하지 말 것.
        """
        sp = getattr(self, "_strategy_params", None) or strategy_params
        lev = self.leverage

        if strategy_name == "BBKCSqueeze":
            if getattr(sp, "bbkc_exit_mode", "fixed") != "fixed":
                return None, None
            tp_pct = sp.bbkc_tp_pct / lev
            sl_pct = sp.bbkc_sl_pct / lev
        elif strategy_name == "RSIMACDStrategy":
            tp_pct = sp.rsimacd_tp_pct / lev
            sl_pct = sp.rsimacd_sl_pct / lev
        else:
            return None, None

        if direction == "LONG":
            sl = avg_price * (1 - sl_pct)
            tp = avg_price * (1 + tp_pct)
        else:
            sl = avg_price * (1 + sl_pct)
            tp = avg_price * (1 - tp_pct)
        return sl, tp

    def _resync_sl_tp(
        self, symbol: str, sl: Optional[float], tp: Optional[float], position_idx: int,
    ) -> bool:
        """Bybit set_trading_stop으로 SL/TP 1회 푸시.

        Returns:
            True if API 호출이 예외를 던졌고 동기화 실패. False if 성공 (예외 없음).
        """
        try:
            self.rest_client.set_trading_stop(
                symbol=symbol,
                stop_loss=sl,
                take_profit=tp,
                position_idx=position_idx,
            )
            return False
        except Exception as exc:
            logger.warning(
                "set_trading_stop failed for %s (sl=%s tp=%s): %s",
                symbol, sl, tp, exc,
            )
            return True

    # -- 시그널 처리 --------------------------------------------------------

    def _process_signal(self, signal: Signal) -> None:
        """시그널을 받아 signal_log 기록 -> 리스크 체크 -> 주문 -> trade_log 기록.

        PairsTrading 시그널이면 A-leg와 B-leg를 동시에 진입한다.

        Args:
            signal: 전략에서 생성된 Signal 객체
        """
        symbol = signal.symbol
        strategy_name = signal.strategy_name
        pos_key: Tuple[str, str] = (strategy_name, symbol)

        # 이미 해당 (전략, 심볼)에 포지션이 있으면 스킵
        if pos_key in self._positions:
            logger.debug("포지션 이미 보유 중, 스킵: %s/%s", strategy_name, symbol)
            return

        # 쿨다운 체크: STOP 청산 후 일정 봉 수 동안 재진입 금지
        cooldown_bar = self._cooldown_until.get(pos_key, 0)
        if self._total_bars < cooldown_bar:
            logger.debug(
                "쿨다운 중 스킵: %s/%s (남은 봉: %d)",
                strategy_name, symbol, cooldown_bar - self._total_bars,
            )
            return

        # 리스크 체크 (signal_log보다 먼저 — 차단된 시그널은 기록 안 함)
        open_symbols = [pos.symbol for pos in self._positions.values()]
        risk_action = self.risk_manager.check_entry_allowed(
            capital=self.risk_manager._current_equity,
            current_positions=open_symbols,
        )
        if risk_action != RiskAction.ALLOW:
            logger.info(
                "리스크 차단 (%s): %s %s 진입 불가",
                risk_action.value, strategy_name, symbol,
            )
            return

        # signal_log 기록 (리스크 통과 후)
        signal_id = self._log_signal(signal)

        # 포지션 사이징 (전략별 차등: BBKC/RSI 5%, Pairs 3%)
        equity = self.risk_manager._current_equity
        if strategy_name == "PairsTrading":
            pos_pct = self.risk_manager.params.pairs_position_pct  # 3%
        else:
            pos_pct = self.risk_manager.params.max_position_pct    # 5%

        try:
            margin_alloc = equity * pos_pct
            notional = margin_alloc * self.leverage
            qty = self._round_qty(symbol, notional / signal.entry_price)
            margin_used = margin_alloc
        except (ValueError, ZeroDivisionError) as exc:
            logger.warning("포지션 사이징 실패 %s: %s", symbol, exc)
            return

        if qty <= 0:
            logger.debug("계산된 수량 0 이하 (min_qty 미달), 스킵: %s", symbol)
            return

        # min_notional 체크 (Bybit 최소 주문 명목가)
        spec = get_product(symbol)
        notional_check = qty * signal.entry_price
        if notional_check < spec.min_notional:
            logger.debug(
                "min_notional 미달 스킵: %s (%.4f < %.1f USDT)",
                symbol, notional_check, spec.min_notional,
            )
            return

        # Signal에서 ATR 값 추출
        atr_val = signal.atr

        # 슬리피지 반영 진입가
        slip_mult = (1 + self.slippage_pct) if signal.direction == "LONG" else (1 - self.slippage_pct)
        entry_price_actual = signal.entry_price * slip_mult

        # 전략별 고정 TP/SL 덮어쓰기 (슬리피지 반영 진입가 기준, 마진% → 가격% 변환)
        if strategy_name == "BBKCSqueeze":
            if strategy_params.bbkc_exit_mode == "fixed":
                tp_price_pct = strategy_params.bbkc_tp_pct / self.leverage
                sl_price_pct = strategy_params.bbkc_sl_pct / self.leverage
                if signal.direction == "LONG":
                    signal.stop_loss = entry_price_actual * (1 - sl_price_pct)
                    signal.take_profit = entry_price_actual * (1 + tp_price_pct)
                else:
                    signal.stop_loss = entry_price_actual * (1 + sl_price_pct)
                    signal.take_profit = entry_price_actual * (1 - tp_price_pct)
        elif strategy_name == "RSIMACDStrategy":
            tp_price_pct = strategy_params.rsimacd_tp_pct / self.leverage
            sl_price_pct = strategy_params.rsimacd_sl_pct / self.leverage
            if signal.direction == "LONG":
                signal.stop_loss = entry_price_actual * (1 - sl_price_pct)
                signal.take_profit = entry_price_actual * (1 + tp_price_pct)
            else:
                signal.stop_loss = entry_price_actual * (1 + sl_price_pct)
                signal.take_profit = entry_price_actual * (1 - tp_price_pct)

        # 주문 실행 (Demo API) - A-leg
        side = "Buy" if signal.direction == "LONG" else "Sell"

        order_result: Optional[dict] = None
        try:
            # 헤지모드: LONG=1, SHORT=2
            pos_idx = 1 if signal.direction == "LONG" else 2
            order_result = self.rest_client.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="Market",
                position_idx=pos_idx,
                stop_loss=round_price(symbol, signal.stop_loss) if signal.stop_loss else None,
                take_profit=round_price(symbol, signal.take_profit) if signal.take_profit else None,
            )
            order_id = order_result.get("orderId", "") if order_result else ""
            if not order_id:
                logger.error(
                    "주문 응답에 orderId 없음 %s/%s: %s",
                    strategy_name, symbol, order_result,
                )
                return
            logger.info(
                "주문 실행: %s %s %s x%.4f @ ~%.4f | orderId=%s | %s",
                strategy_name, side, symbol, qty,
                entry_price_actual, order_id, signal.reason,
            )
        except Exception as exc:
            logger.error("주문 실패 %s/%s: %s -- 포지션 등록 안 함", strategy_name, symbol, exc)
            return

        # 주문 후 API 체결 검증 (API가 source of truth)
        _time.sleep(0.5)
        api_verified = False
        try:
            api_positions = self.rest_client.get_positions()
            api_map = self._build_api_map(api_positions)
            if (symbol, signal.direction) not in api_map:
                logger.warning(
                    "주문 체결 미확인 (API에 포지션 없음): %s/%s %s orderId=%s — 포지션 등록 안 함",
                    strategy_name, symbol, signal.direction, order_id,
                )
                return
            # API 기준 실제 체결 정보로 덮어쓰기
            api_pos = api_map[(symbol, signal.direction)]
            qty = float(api_pos["size"])
            entry_price_actual = float(api_pos["avgPrice"])
            api_verified = True
            logger.info(
                "API 체결 확인: %s/%s %s qty=%.4f entry=%.6f",
                strategy_name, symbol, signal.direction, qty, entry_price_actual,
            )
        except Exception as exc:
            logger.warning("주문 후 API 검증 실패: %s — orderId 기반으로 진행", exc)

        # F2 (round 2 design §4.6): 체결가 기반 SL/TP 재계산 + Bybit 1회 재반영.
        # api_verified 일 때만 의미가 있음 (entry_price_actual이 실제 avgPrice).
        # 성공 시: signal.stop_loss/take_profit를 actual-기반으로 갱신 (포지션 등록에 반영).
        # 실패 시: signal 유지(=서버측 원본 값과 일치) + desired_* 별도 보관.
        sl_tp_resync_failed = False
        desired_sl: float = signal.stop_loss
        desired_tp: Optional[float] = signal.take_profit
        if api_verified:
            sl_actual, tp_actual = self._compute_desired_sl_tp(
                strategy_name, signal.direction, entry_price_actual,
            )
            if sl_actual is not None:
                pos_idx_resync = 1 if signal.direction == "LONG" else 2
                sl_tp_resync_failed = self._resync_sl_tp(
                    symbol, sl_actual, tp_actual, pos_idx_resync,
                )
                desired_sl = sl_actual
                desired_tp = tp_actual
                if not sl_tp_resync_failed:
                    signal.stop_loss = sl_actual
                    signal.take_profit = tp_actual

        # 진입 수수료 계산 및 저장
        entry_fee = entry_price_actual * qty * self.taker_fee_pct

        # trade_log 기록 (entry)
        entry_time = datetime.now(timezone.utc).isoformat()
        trade_id = self.db.insert_trade_log({
            "signal_id": signal_id,
            "strategy": strategy_name,
            "symbol": symbol,
            "direction": signal.direction,
            "entry_time": entry_time,
            "entry_price": entry_price_actual,
            "quantity": qty,
            "leverage": self.leverage,
            "margin_used": margin_used,
            "fee": entry_fee,
            "slippage": entry_price_actual * qty * self.slippage_pct,
            "notes": f"order_id={order_id}",
        })

        # PairsTrading B-leg 처리
        b_leg_pos_key: Optional[Tuple[str, str]] = None
        if strategy_name == "PairsTrading":
            b_leg_pos_key = self._open_b_leg(signal, qty, entry_time, signal_id)

        # A-leg 포지션 등록
        pos = _PositionInfo(
            trade_id=trade_id,
            strategy=strategy_name,
            symbol=symbol,
            direction=signal.direction,
            entry_price=entry_price_actual,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            quantity=qty,
            leverage=self.leverage,
            margin_used=margin_used,
            atr=atr_val,
            entry_time=entry_time,
            entry_fee=entry_fee,
            linked_position_key=b_leg_pos_key,
        )
        # F2: actual-fill-based desired SL/TP + 동기화 실패 플래그 (§4.6)
        pos.desired_stop_loss = desired_sl
        pos.desired_take_profit = desired_tp
        pos.sl_tp_resync_failed = sl_tp_resync_failed
        self._positions[pos_key] = pos

        # B-leg 포지션에 A-leg 링크 설정
        if b_leg_pos_key is not None and b_leg_pos_key in self._positions:
            self._positions[b_leg_pos_key].linked_position_key = pos_key

        logger.info(
            "포지션 등록: trade_id=%d %s %s %s x%.4f SL=%.4f TP=%s",
            trade_id, strategy_name, signal.direction, symbol, qty,
            signal.stop_loss,
            f"{signal.take_profit:.4f}" if signal.take_profit else "없음",
        )

    def _open_b_leg(
        self,
        signal: Signal,
        qty: float,
        entry_time: str,
        signal_id: int,
    ) -> Optional[Tuple[str, str]]:
        """PairsTrading B-leg 포지션을 진입한다.

        A-leg LONG이면 B-leg SHORT, A-leg SHORT이면 B-leg LONG.

        Args:
            signal: A-leg 시그널
            qty: A-leg과 동일한 수량
            entry_time: 진입 시각
            signal_id: 원본 시그널 ID

        Returns:
            B-leg 포지션 키 또는 None (해당 페어를 찾지 못한 경우)
        """
        # 시그널의 reason에서 B-leg 심볼 추출, 또는 pairs에서 탐색
        symbol_a = signal.symbol
        symbol_b: Optional[str] = None
        for sym_a, sym_b in self._pairs_strategy.pairs:
            if sym_a == symbol_a:
                symbol_b = sym_b
                break

        if symbol_b is None:
            logger.warning("PairsTrading B-leg 심볼을 찾을 수 없음: %s", symbol_a)
            return None

        b_pos_key: Tuple[str, str] = (signal.strategy_name, symbol_b)
        if b_pos_key in self._positions:
            logger.debug("B-leg 포지션 이미 보유 중, 스킵: %s", symbol_b)
            return None

        # B-leg 방향: A와 반대
        b_direction = "SHORT" if signal.direction == "LONG" else "LONG"
        b_side = "Sell" if b_direction == "SHORT" else "Buy"

        # B-leg 진입가 (최근 1h 봉 close 기준)
        buf_b = self._buf_1h.get(symbol_b)
        if not buf_b:
            logger.warning("B-leg 봉 버퍼 없음: %s", symbol_b)
            return None
        b_close = float(buf_b[-1]["close"])
        b_slip_mult = (1 + self.slippage_pct) if b_direction == "LONG" else (1 - self.slippage_pct)
        b_entry_price = b_close * b_slip_mult

        # B-leg 수량: A-leg과 동일 명목가치 기준 (qty * A_price / B_price)
        b_qty = self._round_qty(symbol_b, qty * signal.entry_price / b_close)
        if b_qty <= 0:
            logger.debug("B-leg 수량 0 이하, 스킵: %s", symbol_b)
            return None

        # B-leg SL/TP (A-leg과 동일 비율) — 주문 전에 계산
        if b_direction == "SHORT":
            b_stop_loss = b_close * (1 + 0.03)
            b_take_profit = b_close * (1 - 0.02)
        else:
            b_stop_loss = b_close * (1 - 0.03)
            b_take_profit = b_close * (1 + 0.02)

        # B-leg 주문 실행 (헤지모드: B-leg 방향에 맞는 positionIdx)
        b_pos_idx = 1 if b_side == "Buy" else 2
        try:
            self.rest_client.place_order(
                symbol=symbol_b,
                side=b_side,
                qty=b_qty,
                order_type="Market",
                position_idx=b_pos_idx,
                stop_loss=round_price(symbol_b, b_stop_loss),
                take_profit=round_price(symbol_b, b_take_profit),
            )
            logger.info(
                "B-leg 주문: %s %s %s x%.4f @ ~%.4f",
                signal.strategy_name, b_side, symbol_b, b_qty, b_entry_price,
            )
        except Exception as exc:
            logger.error(
                "B-leg 주문 실패 %s: %s -- 포지션 등록하지 않고 return",
                symbol_b, exc,
            )
            return None

        # B-leg 수수료
        b_entry_fee = b_entry_price * b_qty * self.taker_fee_pct
        b_margin = b_entry_price * b_qty / self.leverage

        # B-leg trade_log 기록
        b_trade_id = self.db.insert_trade_log({
            "signal_id": signal_id,
            "strategy": signal.strategy_name,
            "symbol": symbol_b,
            "direction": b_direction,
            "entry_time": entry_time,
            "entry_price": b_entry_price,
            "quantity": b_qty,
            "leverage": self.leverage,
            "margin_used": b_margin,
            "fee": b_entry_fee,
            "slippage": b_entry_price * b_qty * self.slippage_pct,
            "notes": f"B-leg of {signal.symbol}",
        })

        # ATR 대용: 최근 20봉 종가 표준편차
        recent_b = [float(b["close"]) for b in buf_b[-20:]]
        b_atr = statistics.stdev(recent_b) if len(recent_b) > 1 else b_close * 0.01

        # B-leg 포지션 등록
        b_pos = _PositionInfo(
            trade_id=b_trade_id,
            strategy=signal.strategy_name,
            symbol=symbol_b,
            direction=b_direction,
            entry_price=b_entry_price,
            stop_loss=b_stop_loss,
            take_profit=b_take_profit,
            quantity=b_qty,
            leverage=self.leverage,
            margin_used=b_margin,
            atr=b_atr,
            entry_time=entry_time,
            entry_fee=b_entry_fee,
            linked_position_key=None,  # A-leg에서 설정됨
        )
        self._positions[b_pos_key] = b_pos

        logger.info(
            "B-leg 포지션 등록: trade_id=%d %s %s %s x%.4f",
            b_trade_id, signal.strategy_name, b_direction, symbol_b, b_qty,
        )
        return b_pos_key

    # -- 포지션 모니터링 ----------------------------------------------------

    def _check_open_positions_for_symbol(self, symbol: str, current_price: float) -> None:
        """단일 심볼의 열린 포지션 스톱/TP 체크.

        Args:
            symbol: 심볼
            current_price: 현재 가격
        """
        # 해당 심볼이 포함된 모든 포지션 키를 수집
        keys_to_check = [
            k for k in self._positions if k[1] == symbol
        ]

        for pos_key in keys_to_check:
            pos = self._positions.get(pos_key)
            if pos is None:
                continue

            pos.bar_count += 1

            # 최대 유리/불리 방향 가격 갱신
            if pos.direction == "LONG":
                favorable = current_price - pos.entry_price
                adverse = pos.entry_price - current_price
            else:
                favorable = pos.entry_price - current_price
                adverse = current_price - pos.entry_price
            pos.max_favorable = max(pos.max_favorable, favorable)
            pos.max_adverse = max(pos.max_adverse, adverse)

            # 트레일링 스톱 갱신 (활성화 조건: 수익이 trailing_activation_atr * ATR 이상)
            if pos.atr > 0:
                activation_dist = self.risk_manager.params.trailing_activation_atr * pos.atr
                if pos.direction == "LONG":
                    profit_dist = current_price - pos.entry_price
                else:
                    profit_dist = pos.entry_price - current_price
                if profit_dist >= activation_dist:
                    pos.stop_loss = self.risk_manager.update_trailing_stop(
                        current_price=current_price,
                        current_stop=pos.stop_loss,
                        direction=pos.direction,
                        atr=pos.atr,
                    )

            # 청산 조건 체크
            exit_reason: Optional[str] = None

            if pos.direction == "LONG":
                if current_price <= pos.stop_loss:
                    exit_reason = "STOP"
                elif pos.take_profit is not None and current_price >= pos.take_profit:
                    exit_reason = "TP"
            else:  # SHORT
                if current_price >= pos.stop_loss:
                    exit_reason = "STOP"
                elif pos.take_profit is not None and current_price <= pos.take_profit:
                    exit_reason = "TP"

            if exit_reason is not None:
                self._close_position(pos_key, current_price, exit_reason)

    def _close_position(
        self, pos_key: Tuple[str, str], exit_price_raw: float, exit_reason: str
    ) -> None:
        """포지션 청산 처리: 청산 주문 -> trade_log 업데이트 -> RiskManager 기록.

        청산 주문 실패 시 API에서 실제 포지션 수량을 조회하여 재시도한다.
        2차 시도도 실패하면 내부 포지션을 유지하고 다음 봉에서 재시도한다.
        PairsTrading이면 linked position도 동시 청산한다.

        Args:
            pos_key: (strategy_name, symbol) 포지션 키
            exit_price_raw: 청산 기준 가격
            exit_reason: 청산 사유 ("STOP" | "TP" | "SIGNAL" | "FORCE")
        """
        pos = self._positions.get(pos_key)
        if pos is None:
            return

        symbol = pos.symbol
        close_side = "Sell" if pos.direction == "LONG" else "Buy"
        close_success = False

        # 헤지모드: LONG 포지션=1, SHORT 포지션=2
        close_pos_idx = 1 if pos.direction == "LONG" else 2

        # 1차 시도: 내부 수량으로 청산
        try:
            self.rest_client.place_order(
                symbol=symbol,
                side=close_side,
                qty=pos.quantity,
                order_type="Market",
                position_idx=close_pos_idx,
            )
            close_success = True
        except Exception as exc:
            logger.warning(
                "청산 1차 실패 %s: %s, API 포지션 확인 후 재시도",
                symbol, exc,
            )

            # 2차 시도: API에서 실제 수량 조회 후 재시도
            try:
                api_positions = self.rest_client.get_positions()
                expected_side = "Buy" if pos.direction == "LONG" else "Sell"
                api_pos = next(
                    (p for p in api_positions if p["symbol"] == symbol and p["side"] == expected_side), None
                )
                if api_pos:
                    real_qty = float(api_pos["size"])
                    real_side = "Sell" if api_pos["side"] == "Buy" else "Buy"
                    real_pos_idx = 1 if api_pos["side"] == "Buy" else 2
                    self.rest_client.place_order(
                        symbol=symbol,
                        side=real_side,
                        qty=real_qty,
                        order_type="Market",
                        position_idx=real_pos_idx,
                    )
                    close_success = True
                    logger.info(
                        "청산 2차 성공 %s: qty=%s side=%s",
                        symbol, real_qty, real_side,
                    )
                else:
                    # API에 포지션이 없으면 이미 청산된 것으로 처리
                    close_success = True
                    logger.info(
                        "API에 포지션 없음 %s: 이미 청산된 것으로 처리", symbol
                    )
            except Exception as exc2:
                logger.error(
                    "청산 2차도 실패 %s: %s -- 수동 확인 필요!", symbol, exc2
                )

        if not close_success:
            # 내부 포지션 유지, 다음 봉에서 재시도
            logger.error(
                "청산 최종 실패 %s: 내부 포지션 유지, 다음 봉에서 재시도",
                symbol,
            )
            return

        # STOP 청산 시 쿨다운 등록 (PairsTrading)
        if exit_reason == "STOP" and pos.strategy == "PairsTrading":
            cooldown_bars = strategy_params.pairs_cooldown_bars
            self._cooldown_until[pos_key] = self._total_bars + cooldown_bars
            logger.info(
                "쿨다운 등록: %s/%s %d봉 후 재진입 가능",
                pos.strategy, symbol, cooldown_bars,
            )

        # 슬리피지 반영 청산가
        if pos.direction == "LONG":
            exit_price = exit_price_raw * (1 - self.slippage_pct)
        else:
            exit_price = exit_price_raw * (1 + self.slippage_pct)

        # PnL 계산 (수수료 정확 반영)
        if pos.direction == "LONG":
            price_diff = exit_price - pos.entry_price
        else:
            price_diff = pos.entry_price - exit_price

        gross_pnl = price_diff * pos.quantity
        exit_fee = exit_price * pos.quantity * self.taker_fee_pct
        net_pnl = gross_pnl - pos.entry_fee - exit_fee

        logger.info(
            "청산 주문 완료: %s %s %s x%.4f @ ~%.4f | 이유: %s | net_pnl=%.2f",
            pos.strategy, close_side, symbol, pos.quantity,
            exit_price, exit_reason, net_pnl,
        )

        # trade_log DB 업데이트를 먼저 수행 (메모리 제거 전)
        exit_time = datetime.now(timezone.utc).isoformat()
        total_fee = pos.entry_fee + exit_fee
        try:
            self.db.update_trade_log(pos.trade_id, {
                "exit_time": exit_time,
                "exit_price": exit_price,
                "gross_pnl": gross_pnl,
                "fee": total_fee,
                "net_pnl": net_pnl,
                "exit_reason": exit_reason,
                "holding_bars": pos.bar_count,
                "max_favorable": pos.max_favorable,
                "max_adverse": pos.max_adverse,
            })
        except Exception as exc:
            logger.error(
                "trade_log 업데이트 실패 (trade_id=%d, %s/%s): %s",
                pos.trade_id, pos.strategy, symbol, exc,
            )

        # DB 기록 후 내부 포지션 제거
        self._positions.pop(pos_key, None)

        # RiskManager 결과 기록
        is_win = net_pnl > 0
        self.risk_manager.record_trade_result(pnl=net_pnl, is_win=is_win)

        logger.info(
            "포지션 청산 완료: %s/%s %s | net_pnl=%.2f USDT | %d봉 보유",
            pos.strategy, symbol, exit_reason, net_pnl, pos.bar_count,
        )

        # 연결된 B-leg/A-leg 동시 청산
        linked_key = pos.linked_position_key
        if linked_key is not None and linked_key in self._positions:
            linked_pos = self._positions[linked_key]
            # B-leg의 현재가를 봉 버퍼에서 가져옴
            linked_buf = self._buf_15m.get(linked_pos.symbol)
            if linked_buf:
                linked_price = float(linked_buf[-1]["close"])
            else:
                linked_price = linked_pos.entry_price  # 버퍼 없으면 진입가 사용
            self._close_position(linked_key, linked_price, exit_reason)

    # -- API 포지션 동기화 -------------------------------------------------

    def _build_api_map(
        self, api_positions: List[Dict[str, Any]]
    ) -> Dict[Tuple[str, str], dict]:
        """API 포지션 리스트를 (symbol, direction) 키 맵으로 변환한다.

        헤지모드에서 같은 심볼의 LONG/SHORT이 동시에 존재할 수 있으므로
        symbol 단독이 아닌 (symbol, direction) 복합 키를 사용한다.

        Args:
            api_positions: get_positions() 반환값

        Returns:
            {(symbol, direction): api_pos_dict} 맵
        """
        api_map: Dict[Tuple[str, str], dict] = {}
        for p in api_positions:
            direction = "LONG" if p.get("side") == "Buy" else "SHORT"
            api_map[(p["symbol"], direction)] = p
        return api_map

    def sync_positions_with_api(self) -> None:
        """API 실제 포지션과 내부 포지션을 동기화한다 (API가 source of truth).

        매 15분봉 처리 후 _maybe_sync_positions()을 통해 주기적으로 호출된다.

        동기화 규칙:
          1. API에 있는데 내부에 없음 → 경고 로그만 (API 포지션은 건드리지 않음)
          2. 내부에 있는데 API에 없음 → API 기준 청산됨 → 내부 제거
          3. 양쪽에 있지만 수량 불일치 → API 기준으로 보정
        """
        try:
            api_positions = self.rest_client.get_positions()
        except Exception as exc:
            logger.warning("API 포지션 조회 실패 (동기화 스킵): %s", exc)
            return

        api_map = self._build_api_map(api_positions)

        # 내부 포지션의 (symbol, direction) 셋
        internal_sym_dir: Dict[Tuple[str, str], list] = {}
        for pos_key, pos in self._positions.items():
            sd = (pos.symbol, pos.direction)
            internal_sym_dir.setdefault(sd, []).append(pos_key)

        # 1. API에 있는데 내부에 없는 포지션 → 경고만 (API 포지션 유지)
        for (sym, direction), api_pos in api_map.items():
            if (sym, direction) not in internal_sym_dir:
                logger.warning(
                    "[동기화] API에만 존재하는 포지션 (수동 관리 필요): %s %s size=%s avgPrice=%s",
                    sym, direction,
                    api_pos.get("size", "?"),
                    api_pos.get("avgPrice", "?"),
                )

        # 2. 내부에 있는데 API에 없는 포지션 → API 기준 이미 청산됨 → 내부 제거
        for pos_key, pos in list(self._positions.items()):
            sd = (pos.symbol, pos.direction)
            if sd not in api_map:
                logger.info(
                    "[동기화] API에서 청산 확인: %s/%s %s qty=%.4f → 내부 제거",
                    pos.strategy, pos.symbol, pos.direction, pos.quantity,
                )
                self._force_close_position(pos_key, "API_CONFIRMED_CLOSE")

        # 3. 양쪽에 있지만 수량 불일치 → API 기준으로 보정
        for pos_key, pos in list(self._positions.items()):
            sd = (pos.symbol, pos.direction)
            if sd in api_map:
                api_qty = float(api_map[sd]["size"])
                if pos.quantity > 0 and abs(api_qty - pos.quantity) / pos.quantity > 0.01:
                    logger.info(
                        "[동기화] 수량 보정 (API 기준): %s 내부=%.4f → API=%.4f",
                        pos.symbol, pos.quantity, api_qty,
                    )
                    pos.quantity = api_qty

        # 4. API 잔고 동기화 → RiskManager._current_equity 갱신
        try:
            bal = self.rest_client.get_wallet_balance()
            for coin in bal.get("coin", []):
                if coin.get("coin") == "USDT":
                    api_equity = float(coin.get("walletBalance", 0))
                    if api_equity > 0:
                        self.risk_manager._current_equity = api_equity
                        logger.debug("[동기화] 잔고 갱신: %.2f USDT", api_equity)
                    break
        except Exception as exc:
            logger.warning("[동기화] 잔고 조회 실패: %s", exc)

    def _reconcile_with_api(self) -> None:
        """엔진 시작 시 engine_state.json과 API 포지션을 대조 검증한다.

        load_state() 직후 호출되며:
          - state 포지션 ↔ API 포지션 매칭 → 그대로 유지 (TP/SL/전략 정보 보존)
          - state에만 있고 API에 없음 → 이미 청산됨 → 내부 제거
          - API에만 있고 state에 없음 → 유령 포지션 → API에서 청산
          - 수량 불일치 → API 기준으로 보정
        """
        try:
            api_positions = self.rest_client.get_positions()
        except Exception as exc:
            logger.warning("[시작 동기화] API 조회 실패, 스킵: %s", exc)
            return

        api_map = self._build_api_map(api_positions)

        # 내부 포지션의 (symbol, direction) → pos_key 매핑
        internal_sym_dir: Dict[Tuple[str, str], list] = {}
        for pos_key, pos in self._positions.items():
            sd = (pos.symbol, pos.direction)
            internal_sym_dir.setdefault(sd, []).append(pos_key)

        matched = 0
        removed = 0
        ghost_closed = 0
        qty_adjusted = 0

        # 1. state에 있는 포지션 검증
        for pos_key, pos in list(self._positions.items()):
            sd = (pos.symbol, pos.direction)
            if sd in api_map:
                # 매칭 성공 → 수량 보정
                api_qty = float(api_map[sd]["size"])
                if pos.quantity > 0 and abs(api_qty - pos.quantity) / pos.quantity > 0.01:
                    logger.info(
                        "[시작 동기화] 수량 보정: %s/%s 내부=%.4f → API=%.4f",
                        pos.strategy, pos.symbol, pos.quantity, api_qty,
                    )
                    pos.quantity = api_qty
                    qty_adjusted += 1
                matched += 1
                logger.info(
                    "[시작 동기화] 포지션 확인: %s/%s %s qty=%.4f TP=%.4f SL=%.4f",
                    pos.strategy, pos.symbol, pos.direction, pos.quantity,
                    pos.take_profit or 0.0, pos.stop_loss,
                )
            else:
                # API에 없음 → 이미 청산됨
                logger.warning(
                    "[시작 동기화] state에만 존재, 제거: %s/%s %s",
                    pos.strategy, pos.symbol, pos.direction,
                )
                self._force_close_position(pos_key, "STARTUP_SYNC_CLOSE")
                removed += 1

        # 2. API에만 있는 포지션 → 경고만 (API 포지션 유지, 수동 관리 필요)
        for (sym, direction), api_pos in api_map.items():
            if (sym, direction) not in internal_sym_dir:
                logger.warning(
                    "[시작 동기화] API에만 존재하는 포지션 (수동 관리 필요): %s %s size=%s avgPrice=%s",
                    sym, direction,
                    api_pos.get("size", "?"),
                    api_pos.get("avgPrice", "?"),
                )

        logger.info(
            "[시작 동기화] 완료: 매칭=%d, 수량보정=%d, state제거=%d",
            matched, qty_adjusted, removed,
        )

    def _force_close_position(
        self, pos_key: Tuple[str, str], exit_reason: str
    ) -> None:
        """API 주문 없이 내부 포지션만 정리하고 trade_log에 강제 청산 기록한다.

        API에 이미 포지션이 없는 상황에서 내부 상태를 정리하는 용도.

        Args:
            pos_key: (strategy_name, symbol) 포지션 키
            exit_reason: 청산 사유 (예: "API_SYNC_CLOSE")
        """
        pos = self._positions.pop(pos_key, None)
        if pos is None:
            return

        # 최근 봉 close를 청산가로 사용
        buf = self._buf_15m.get(pos.symbol)
        if buf:
            exit_price = float(buf[-1]["close"])
        else:
            exit_price = pos.entry_price  # 봉 없으면 진입가 사용

        # PnL 계산
        if pos.direction == "LONG":
            price_diff = exit_price - pos.entry_price
        else:
            price_diff = pos.entry_price - exit_price

        gross_pnl = price_diff * pos.quantity
        exit_fee = exit_price * pos.quantity * self.taker_fee_pct
        net_pnl = gross_pnl - pos.entry_fee - exit_fee

        # trade_log 업데이트
        exit_time = datetime.now(timezone.utc).isoformat()
        total_fee = pos.entry_fee + exit_fee
        try:
            self.db.update_trade_log(pos.trade_id, {
                "exit_time": exit_time,
                "exit_price": exit_price,
                "gross_pnl": gross_pnl,
                "fee": total_fee,
                "net_pnl": net_pnl,
                "exit_reason": exit_reason,
                "holding_bars": pos.bar_count,
                "max_favorable": pos.max_favorable,
                "max_adverse": pos.max_adverse,
            })
        except Exception as exc:
            logger.warning("강제 청산 trade_log 업데이트 실패: %s", exc)

        # RiskManager 결과 기록
        is_win = net_pnl > 0
        self.risk_manager.record_trade_result(pnl=net_pnl, is_win=is_win)

        logger.info(
            "[동기화] 강제 청산 완료: %s/%s %s | net_pnl=%.2f USDT | 사유=%s",
            pos.strategy, pos.symbol, pos.direction, net_pnl, exit_reason,
        )

        # 연결된 B-leg도 강제 청산
        linked_key = pos.linked_position_key
        if linked_key is not None and linked_key in self._positions:
            self._force_close_position(linked_key, exit_reason)

    def _maybe_sync_positions(self) -> None:
        """마지막 동기화에서 60초 이상 경과했으면 sync_positions_with_api()를 호출한다."""
        now = _time.time()
        if now - self._last_sync_time >= 60.0:
            self.sync_positions_with_api()
            self._last_sync_time = now

    # -- 유틸리티 -----------------------------------------------------------

    def _log_signal(self, signal: Signal) -> int:
        """signal_log 테이블에 시그널을 기록하고 id를 반환한다.

        Args:
            signal: Signal 객체

        Returns:
            signal_log.id
        """
        strategy_type = self._get_strategy_type(signal.strategy_name)
        snapshot = json.dumps({
            "strength": signal.strength,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
        })
        try:
            signal_id = self.db.insert_signal_log({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "strategy": signal.strategy_name,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "signal_strength": signal.strength,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "atr": signal.atr,
                "reason": signal.reason,
                "regime": strategy_type,
                "indicators_snapshot": snapshot,
            })
            return signal_id
        except Exception as exc:
            logger.error("signal_log 기록 실패: %s", exc)
            return -1

    def _get_qty_spec(self, symbol: str) -> Tuple[float, float]:
        """심볼의 최소 주문 수량 및 수량 스텝을 반환한다 (캐시 사용).

        Args:
            symbol: 심볼 (예: "BTCUSDT")

        Returns:
            (min_qty, qty_step) 튜플. API 조회 실패 시 (0.001, 0.001) 반환.
        """
        if symbol in self._qty_spec_cache:
            return self._qty_spec_cache[symbol]

        try:
            infos = self.rest_client.get_instruments_info(symbol=symbol)
            if infos:
                lot = infos[0].get("lotSizeFilter", {})
                min_qty = float(lot.get("minOrderQty", 0.001))
                qty_step = float(lot.get("qtyStep", 0.001))
                self._qty_spec_cache[symbol] = (min_qty, qty_step)
                return min_qty, qty_step
        except Exception as exc:
            logger.warning("상품 스펙 조회 실패 %s: %s", symbol, exc)

        # 조회 실패 시 기본값
        self._qty_spec_cache[symbol] = (0.001, 0.001)
        return 0.001, 0.001

    def _round_qty(self, symbol: str, qty: float) -> float:
        """수량을 거래소 qty_step에 맞게 내림 처리한다.

        Args:
            symbol: 심볼
            qty: 원시 계산 수량

        Returns:
            qty_step 기준으로 내림한 수량. min_qty 미달이면 0.0 반환.
        """
        min_qty, qty_step = self._get_qty_spec(symbol)
        if qty_step <= 0:
            qty_step = 0.001

        # qty_step 기준 내림
        rounded = math.floor(qty / qty_step) * qty_step

        # 부동소수점 오차 제거: qty_step의 소수 자릿수에 맞게 반올림
        decimals = max(0, -int(math.floor(math.log10(qty_step)))) if qty_step < 1 else 0
        rounded = round(rounded, decimals)

        if rounded < min_qty:
            return 0.0
        return rounded

    @staticmethod
    def _get_strategy_type(strategy_name: str) -> str:
        """전략명으로 MR/TF 타입 판별.

        PairsTrading -> MR, RSIMACDStrategy -> MR,
        IchimokuCloud -> TF, BBKCSqueeze -> TF.

        Args:
            strategy_name: 전략명

        Returns:
            "MR" 또는 "TF"
        """
        mr_strategies = {"PairsTrading", "RSIMACDStrategy"}
        return "MR" if strategy_name in mr_strategies else "TF"

    # -- 실시간 틱 처리 ----------------------------------------------------

    def on_tick(self, symbol: str, price: float) -> None:
        """실시간 틱으로 스톱/TP 체크 (시그널 생성은 하지 않음).

        confirm=False 봉에서 호출하여 열린 포지션의 즉시 청산 여부를 판단한다.

        Args:
            symbol: 심볼 (예: "BTCUSDT")
            price: 현재 가격
        """
        self._check_open_positions_for_symbol(symbol, price)

    # -- 최신 가격 조회 ---------------------------------------------------

    def get_latest_prices(self) -> Dict[str, float]:
        """각 심볼의 15m 버퍼 마지막 봉 close를 반환한다.

        Returns:
            심볼 -> 최신 종가 딕셔너리
        """
        prices: Dict[str, float] = {}
        for symbol, buf in self._buf_15m.items():
            if buf:
                prices[symbol] = float(buf[-1]["close"])
        return prices

    # -- 엔진 상태 직렬화 (크래시 복구) ----------------------------------

    def save_state(self) -> None:
        """열린 포지션, 리샘플링 버퍼 상태를 JSON 파일로 저장한다.

        저장 경로: logs/engine_state.json
        """
        state: dict = {
            "total_bars": self._total_bars,
            "positions": {},
            "resample_30m_counts": {},
            "resample_1h_counts": {},
            "resample_4h_counts": {},
            "cooldown_until": {},
        }
        # 쿨다운 상태 저장
        for cd_key, cd_bar in self._cooldown_until.items():
            cd_str = f"{cd_key[0]}|{cd_key[1]}"
            state["cooldown_until"][cd_str] = cd_bar
        for pos_key, pos in self._positions.items():
            key_str = f"{pos_key[0]}|{pos_key[1]}"
            state["positions"][key_str] = {
                "trade_id": pos.trade_id,
                "strategy": pos.strategy,
                "symbol": pos.symbol,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "quantity": pos.quantity,
                "leverage": pos.leverage,
                "margin_used": pos.margin_used,
                "atr": pos.atr,
                "entry_time": pos.entry_time,
                "entry_fee": pos.entry_fee,
                "bar_count": pos.bar_count,
                "max_favorable": pos.max_favorable,
                "max_adverse": pos.max_adverse,
                "linked_position_key": f"{pos.linked_position_key[0]}|{pos.linked_position_key[1]}" if pos.linked_position_key else None,
            }
        for symbol, buf in self._resample_30m.items():
            state["resample_30m_counts"][symbol] = len(buf)
        for symbol, buf in self._resample_1h.items():
            state["resample_1h_counts"][symbol] = len(buf)
        for symbol, buf in self._resample_4h.items():
            state["resample_4h_counts"][symbol] = len(buf)

        state_dir = os.path.join(self._get_project_root(), "logs")
        os.makedirs(state_dir, exist_ok=True)
        state_path = os.path.join(state_dir, "engine_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logger.debug("엔진 상태 저장 완료: %s", state_path)

    def load_state(self) -> bool:
        """JSON 파일에서 엔진 상태를 복원한다.

        Returns:
            복원 성공 여부
        """
        state_path = os.path.join(
            self._get_project_root(), "logs", "engine_state.json"
        )
        if not os.path.exists(state_path):
            logger.info("복원할 엔진 상태 파일 없음: %s", state_path)
            return False

        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            self._total_bars = state.get("total_bars", 0)

            for key_str, pos_data in state.get("positions", {}).items():
                parts = key_str.split("|", 1)
                if len(parts) != 2:
                    continue
                pos_key = (parts[0], parts[1])
                pos = _PositionInfo(
                    trade_id=pos_data["trade_id"],
                    strategy=pos_data["strategy"],
                    symbol=pos_data["symbol"],
                    direction=pos_data["direction"],
                    entry_price=pos_data["entry_price"],
                    stop_loss=pos_data["stop_loss"],
                    take_profit=pos_data.get("take_profit"),
                    quantity=pos_data["quantity"],
                    leverage=pos_data["leverage"],
                    margin_used=pos_data["margin_used"],
                    atr=pos_data.get("atr", 0.0),
                    entry_time=pos_data["entry_time"],
                    entry_fee=pos_data.get("entry_fee", 0.0),
                )
                pos.bar_count = pos_data.get("bar_count", 0)
                pos.max_favorable = pos_data.get("max_favorable", 0.0)
                pos.max_adverse = pos_data.get("max_adverse", 0.0)
                # linked_position_key 복원 (PairsTrading A/B-leg 쌍 연결)
                lpk = pos_data.get("linked_position_key")
                if lpk:
                    lpk_parts = lpk.split("|", 1)
                    if len(lpk_parts) == 2:
                        pos.linked_position_key = (lpk_parts[0], lpk_parts[1])
                self._positions[pos_key] = pos

            # 쿨다운 상태 복원
            for cd_str, cd_bar in state.get("cooldown_until", {}).items():
                parts_cd = cd_str.split("|", 1)
                if len(parts_cd) == 2:
                    self._cooldown_until[(parts_cd[0], parts_cd[1])] = cd_bar

            logger.info(
                "엔진 상태 복원 완료: 포지션 %d개, 처리 봉 %d개, 쿨다운 %d개",
                len(self._positions), self._total_bars, len(self._cooldown_until),
            )
            return True
        except Exception as exc:
            logger.warning("엔진 상태 복원 실패: %s", exc)
            return False

    @staticmethod
    def _normalize_bar(bar: dict) -> dict:
        """봉 dict를 표준 float 타입으로 정규화한다.

        Args:
            bar: 원시 봉 데이터

        Returns:
            정규화된 봉 dict (open_time int, ohlcv float)

        Raises:
            KeyError: 필수 키 누락
            ValueError: 숫자 변환 실패
        """
        return {
            "open_time": int(bar["open_time"]),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume", 0) or 0),
        }

    @staticmethod
    def _merge_bars(bars: List[dict]) -> dict:
        """여러 15m 봉을 하나의 상위 봉으로 합친다 (OHLCV 리샘플링).

        Args:
            bars: 15m 봉 list (시간 오름차순 가정)

        Returns:
            합쳐진 봉 dict
        """
        if not bars:
            raise ValueError("bars가 비어 있습니다")
        return {
            "open_time": bars[0]["open_time"],
            "open": bars[0]["open"],
            "high": max(b["high"] for b in bars),
            "low": min(b["low"] for b in bars),
            "close": bars[-1]["close"],
            "volume": sum(b["volume"] for b in bars),
        }

    @staticmethod
    def _buf_to_df(buf: List[dict]) -> pd.DataFrame:
        """봉 버퍼를 DataFrame으로 변환한다.

        Args:
            buf: 봉 dict 리스트

        Returns:
            datetime 인덱스가 설정된 OHLCV DataFrame
        """
        df = pd.DataFrame(buf)
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["close"], inplace=True)
        return df


__all__ = ["TradingEngine"]
