"""백테스트 엔진 모듈.

봉 단위 이벤트 루프로 전략 시그널을 평가하고 PnL을 누적한다.
Lookahead bias 방지: 현재 봉 close로 시그널 생성 → 다음 봉 open으로 체결.
거래비용: taker 수수료 + 슬리피지를 진입/청산 시 차감한다.
포지션 사이징: RiskManager의 ATR 기반 동적 사이징을 사용한다.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Signal, TradeRecord
from config.settings import BacktestConfig, RiskParams
from risk.risk_manager import RiskManager, PositionSizeResult
from engine.annualization import daily_sharpe

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """백테스트 결과 데이터 클래스.

    Attributes:
        strategy_name: 전략명
        symbol: 심볼
        total_trades: 총 거래 수
        win_rate: 승률 (0.0~1.0)
        total_pnl: 누적 손익 (USDT)
        sharpe_ratio: Sharpe Ratio (연환산)
        max_drawdown: 최대 낙폭 (0.0~1.0)
        profit_factor: Profit Factor (총이익 / 총손실)
        calmar_ratio: Calmar Ratio (연수익률 / 최대낙폭)
        expectancy: 기대값 (거래당 평균 USDT 손익)
        avg_trade_pnl: 거래당 평균 PnL
        equity_curve: 자본 곡선 (초기 자본 기준)
        trades: 거래 기록 목록
    """

    strategy_name: str
    symbol: str
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    expectancy: float = 0.0
    avg_trade_pnl: float = 0.0
    equity_curve: List[float] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)


class BacktestEngine:
    """봉 단위 이벤트 루프 백테스트 엔진.

    Lookahead bias 방지 원칙:
        - i봉에서 시그널 생성
        - i+1봉 open가로 체결

    거래비용:
        - 진입/청산 각 1회씩 taker_fee + slippage 차감
        - 총 비용 = (taker_fee_pct + slippage_pct) × 2 × 진입금액

    포지션 관리:
        - 단일 포지션 (새 시그널은 기존 포지션이 있으면 무시)
        - 스톱/TP는 봉의 high/low로 체크 (intra-bar)
        - 시그널 반전 시 청산 후 재진입 (현재 봉 close 기준)
    """

    def run(
        self,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        config: Optional[BacktestConfig] = None,
        symbol: str = "UNKNOWN",
        risk_params: Optional[RiskParams] = None,
    ) -> BacktestResult:
        """전략을 DataFrame에 대해 백테스트한다.

        Args:
            strategy: 평가할 전략 인스턴스
            df: OHLCV DataFrame (open_time 인덱스, open/high/low/close/volume 컬럼)
            config: 백테스트 설정. None이면 기본값 사용.
            symbol: 심볼 (결과에 기록용)
            risk_params: 리스크 파라미터. None이면 기본값 사용.
                         ATR 기반 동적 포지션 사이징에 활용된다.

        Returns:
            BacktestResult 객체
        """
        if config is None:
            config = BacktestConfig()

        if risk_params is None:
            risk_params = RiskParams()

        result = BacktestResult(strategy_name=strategy.name, symbol=symbol)

        # RiskManager 초기화 (ATR 기반 동적 포지션 사이징용)
        risk_mgr = RiskManager(
            params=risk_params,
            initial_capital=config.initial_capital,
            leverage=3,
        )

        if df.empty or len(df) < strategy.required_warmup() + 2:
            logger.warning(
                "%s: 데이터 부족 (필요 %d봉, 보유 %d봉)",
                strategy.name, strategy.required_warmup() + 2, len(df)
            )
            return result

        # 날짜 필터 적용
        df = self._apply_date_filter(df, config)
        if df.empty:
            return result

        # 날짜 인덱스가 없으면 정수 인덱스 사용
        df = df.reset_index(drop=False)
        if "datetime" in df.columns:
            time_col = "datetime"
        elif "open_time" in df.columns:
            time_col = "open_time"
        else:
            time_col = None

        capital = config.initial_capital
        equity = capital
        equity_curve: List[float] = [equity]
        trades: List[TradeRecord] = []

        # 현재 포지션 상태
        in_position = False
        pos_direction: Optional[str] = None
        pos_entry_price: float = 0.0
        pos_stop: float = 0.0
        pos_tp: Optional[float] = None
        pos_entry_time = None
        pos_qty: float = 0.0
        pos_strategy_name: str = ""

        warmup = strategy.required_warmup()
        n = len(df)

        for i in range(warmup, n):
            # 현재 봉 데이터
            cur_row = df.iloc[i]
            cur_high = float(cur_row["high"])
            cur_low = float(cur_row["low"])
            cur_close = float(cur_row["close"])
            cur_open = float(cur_row["open"])
            cur_time = cur_row[time_col] if time_col else i

            # ── 기존 포지션 스톱/TP 체크 (intra-bar) ──────────────
            if in_position:
                exit_price, exit_reason = self._check_exit(
                    pos_direction, pos_stop, pos_tp,
                    cur_open, cur_high, cur_low, cur_close
                )

                if exit_reason is not None:
                    # 청산 처리
                    fee = self._calc_fee(pos_qty, exit_price, config)
                    pnl = self._calc_pnl(pos_direction, pos_entry_price, exit_price, pos_qty) - fee

                    trade = TradeRecord(
                        symbol=symbol,
                        strategy_name=pos_strategy_name,
                        direction=pos_direction,
                        entry_time=pos_entry_time,
                        exit_time=cur_time,
                        entry_price=pos_entry_price,
                        exit_price=exit_price,
                        qty=pos_qty,
                        pnl=pnl,
                        fee=fee,
                        exit_reason=exit_reason,
                    )
                    trades.append(trade)
                    equity += pnl
                    equity_curve.append(equity)
                    in_position = False

                    # RiskManager에 거래 결과 기록 → 다음 포지션 사이징에 반영
                    risk_mgr.record_trade_result(pnl=pnl, is_win=(pnl > 0))

            # ── 시그널 생성 (현재까지의 slice로) ──────────────────
            # i+1봉이 없으면 시그널만 생성하고 체결 안 함
            if i + 1 >= n:
                break

            df_slice = df.iloc[: i + 1].copy()
            # reset_index로 인해 원본 인덱스가 컬럼으로 들어와 있을 수 있으므로
            # open/high/low/close/volume 컬럼만 확인
            try:
                signal = strategy.generate_signal(df_slice, symbol)
            except Exception as exc:
                logger.debug("시그널 생성 예외 %s[%d]: %s", strategy.name, i, exc)
                signal = None

            if signal is None:
                continue

            # 기존 포지션이 있으면 무시 (단일 포지션 원칙)
            if in_position:
                continue

            # ── 다음 봉 open으로 체결 (Lookahead bias 방지) ────────
            next_row = df.iloc[i + 1]
            entry_price = float(next_row["open"])
            next_time = next_row[time_col] if time_col else i + 1

            # ── ATR 기반 동적 포지션 사이징 (RiskManager 연동) ──────
            # 시그널에 ATR 값이 있으면 RiskManager 사용, 없으면 자본 2% 폴백
            atr_val = signal.atr if signal.atr and signal.atr > 0 else None

            if atr_val is not None:
                # 전략 유형 추론: 전략명에 MR 포함이면 MR, 아니면 TF
                strategy_type = "MR" if "MR" in strategy.name.upper() else "TF"
                stop_mult = (
                    risk_params.mr_atr_multiplier
                    if strategy_type == "MR"
                    else risk_params.tf_atr_multiplier
                )
                try:
                    size_result: PositionSizeResult = risk_mgr.calculate_position_size(
                        capital=equity,
                        price=entry_price,
                        atr=atr_val,
                        stop_multiplier=stop_mult,
                        symbol=symbol,
                        current_positions=[],
                    )
                    pos_qty = size_result.contracts
                except (ValueError, ZeroDivisionError) as exc:
                    logger.debug("ATR 사이징 실패(%s), 폴백 사용: %s", symbol, exc)
                    pos_qty = (equity * risk_params.risk_per_trade_pct) / entry_price
            else:
                # ATR 없으면 risk_per_trade_pct(2%) 기반 폴백
                pos_qty = (equity * risk_params.risk_per_trade_pct) / entry_price

            if pos_qty <= 0:
                continue

            # 진입 수수료
            entry_fee = self._calc_fee(pos_qty, entry_price, config)
            equity -= entry_fee  # 진입 시 수수료 선차감

            # 슬리피지 반영한 실제 체결가
            slippage = config.slippage_pct * entry_price
            if signal.direction == "LONG":
                actual_entry = entry_price + slippage
            else:
                actual_entry = entry_price - slippage

            in_position = True
            pos_direction = signal.direction
            pos_entry_price = actual_entry
            pos_stop = signal.stop_loss
            pos_tp = signal.take_profit
            pos_entry_time = next_time
            pos_strategy_name = signal.strategy_name
            # 슬리피지 반영 가격 기준으로 수량 재계산
            if atr_val is not None and pos_qty > 0:
                # 가격 차이가 크지 않으므로 비율 보정만 적용
                pos_qty = pos_qty * (entry_price / actual_entry) if actual_entry > 0 else pos_qty
            else:
                pos_qty = (equity * risk_params.risk_per_trade_pct) / actual_entry if actual_entry > 0 else pos_qty

            # 거래 결과를 RiskManager에 기록 (다음 포지션 사이징에 반영)
            # (청산 시점이 아닌 진입 시점이므로 여기서는 생략; 청산 후 처리)

        # ── 백테스트 종료 시 미청산 포지션 강제 청산 ──────────────
        if in_position and len(df) > 0:
            last_row = df.iloc[-1]
            exit_price = float(last_row["close"])
            exit_time = last_row[time_col] if time_col else len(df) - 1

            fee = self._calc_fee(pos_qty, exit_price, config)
            pnl = self._calc_pnl(pos_direction, pos_entry_price, exit_price, pos_qty) - fee

            trade = TradeRecord(
                symbol=symbol,
                strategy_name=pos_strategy_name,
                direction=pos_direction,
                entry_time=pos_entry_time,
                exit_time=exit_time,
                entry_price=pos_entry_price,
                exit_price=exit_price,
                qty=pos_qty,
                pnl=pnl,
                fee=fee,
                exit_reason="END",
            )
            trades.append(trade)
            equity += pnl
            equity_curve.append(equity)

        # ── 성과 지표 계산 ──────────────────────────────────────────
        result = self._calc_metrics(
            strategy_name=strategy.name,
            symbol=symbol,
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=config.initial_capital,
        )
        return result

    # ── 내부 헬퍼 메서드 ──────────────────────────────────────────

    @staticmethod
    def _apply_date_filter(df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
        """백테스트 날짜 범위 필터를 적용한다.

        Args:
            df: OHLCV DataFrame
            config: BacktestConfig (start_date, end_date)

        Returns:
            필터된 DataFrame
        """
        if config.start_date is None and config.end_date is None:
            return df

        idx = df.index
        # datetime 인덱스인 경우
        try:
            if hasattr(idx, "tz"):
                if config.start_date:
                    start = pd.Timestamp(config.start_date, tz="UTC")
                    df = df[idx >= start]
                if config.end_date:
                    end = pd.Timestamp(config.end_date, tz="UTC")
                    df = df[df.index <= end]
            else:
                if config.start_date:
                    df = df[idx >= config.start_date]
                if config.end_date:
                    df = df[df.index <= config.end_date]
        except Exception as exc:
            logger.warning("날짜 필터 적용 실패 (인덱스 타입 불일치): %s", exc)

        return df

    @staticmethod
    def _check_exit(
        direction: str,
        stop: float,
        tp: Optional[float],
        cur_open: float,
        cur_high: float,
        cur_low: float,
        cur_close: float,
    ) -> tuple:
        """봉 내 스톱/TP 히트 여부를 체크한다.

        갭 오픈 처리: open이 이미 stop/tp를 초과하면 open가 기준.

        Args:
            direction: 포지션 방향 ("LONG" | "SHORT")
            stop: 손절 가격
            tp: 익절 가격 (None이면 무시)
            cur_open: 현재 봉 open
            cur_high: 현재 봉 high
            cur_low: 현재 봉 low
            cur_close: 현재 봉 close

        Returns:
            (exit_price, exit_reason) 또는 (None, None) 튜플
        """
        if direction == "LONG":
            # 갭다운 오픈이 이미 스톱 아래
            if cur_open <= stop:
                return cur_open, "STOP"
            # intra-bar 스톱
            if cur_low <= stop:
                return stop, "STOP"
            # TP
            if tp is not None:
                if cur_open >= tp:
                    return cur_open, "TP"
                if cur_high >= tp:
                    return tp, "TP"
        else:  # SHORT
            # 갭업 오픈이 이미 스톱 위
            if cur_open >= stop:
                return cur_open, "STOP"
            if cur_high >= stop:
                return stop, "STOP"
            if tp is not None:
                if cur_open <= tp:
                    return cur_open, "TP"
                if cur_low <= tp:
                    return tp, "TP"

        return None, None

    @staticmethod
    def _calc_fee(qty: float, price: float, config: BacktestConfig) -> float:
        """거래비용(수수료 + 슬리피지)을 계산한다.

        Args:
            qty: 수량
            price: 체결가
            config: BacktestConfig

        Returns:
            총 수수료 (USDT)
        """
        notional = qty * price
        fee = notional * (config.taker_fee_pct + config.slippage_pct)
        return fee

    @staticmethod
    def _calc_pnl(
        direction: str, entry_price: float, exit_price: float, qty: float
    ) -> float:
        """포지션 손익을 계산한다 (수수료 미포함).

        Args:
            direction: 방향 ("LONG" | "SHORT")
            entry_price: 진입가
            exit_price: 청산가
            qty: 수량

        Returns:
            손익 (USDT)
        """
        if direction == "LONG":
            return (exit_price - entry_price) * qty
        else:
            return (entry_price - exit_price) * qty

    @staticmethod
    def _calc_metrics(
        strategy_name: str,
        symbol: str,
        trades: List[TradeRecord],
        equity_curve: List[float],
        initial_capital: float,
        timeframe: str = "15m",
    ) -> BacktestResult:
        """거래 기록으로 성과 지표를 계산한다.

        Args:
            strategy_name: 전략명
            symbol: 심볼
            trades: 거래 기록 목록
            equity_curve: 자본 곡선
            initial_capital: 초기 자본
            timeframe: 입력 봉 주기 ('15m', '1h' 등). Sharpe 계산에 사용.

        Returns:
            BacktestResult 객체
        """
        result = BacktestResult(
            strategy_name=strategy_name,
            symbol=symbol,
            equity_curve=equity_curve,
            trades=trades,
        )

        if not trades:
            return result

        pnl_list = [t.pnl for t in trades]
        total_trades = len(trades)
        wins = [p for p in pnl_list if p > 0]
        losses = [p for p in pnl_list if p <= 0]

        result.total_trades = total_trades
        result.win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        result.total_pnl = sum(pnl_list)
        result.avg_trade_pnl = result.total_pnl / total_trades if total_trades > 0 else 0.0
        result.expectancy = result.avg_trade_pnl

        # Profit Factor
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        result.profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
        )

        # Sharpe Ratio (daily 집계 → sqrt(365) 연환산, timeframe-agnostic)
        if len(equity_curve) > 1:
            # equity_curve는 거래 이벤트 단위 리스트 → 1D freq 합성 인덱스로 daily_sharpe 호출
            eq_idx = pd.date_range(
                start="2020-01-01",
                periods=len(equity_curve),
                freq="1D",
                tz="UTC",
            )
            eq_df = pd.DataFrame({"equity": equity_curve}, index=eq_idx)
            result.sharpe_ratio = daily_sharpe(eq_df)

        # Max Drawdown
        if len(equity_curve) > 1:
            equity_arr = np.array(equity_curve)
            peak = np.maximum.accumulate(equity_arr)
            drawdown = (peak - equity_arr) / (peak + 1e-9)
            result.max_drawdown = float(drawdown.max())

        # Calmar Ratio
        if result.max_drawdown > 0:
            final_equity = equity_curve[-1] if equity_curve else initial_capital
            annual_return = (final_equity - initial_capital) / initial_capital
            result.calmar_ratio = annual_return / result.max_drawdown
        else:
            result.calmar_ratio = 0.0

        return result


__all__ = ["BacktestEngine", "BacktestResult"]
