"""리스크 관리 모듈.

ATR 기반 포지션 사이징, 동적 스톱로스, 일일 손실 한도,
최대 낙폭 추적, 연속 손실 관리, 상관관계 조정, 세션 관리를
통합적으로 제공한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from config.settings import RiskParams

logger = logging.getLogger(__name__)


# =============================================================================
# 열거형 및 결과 데이터 클래스
# =============================================================================

class RiskAction(Enum):
    """리스크 관리 액션 코드."""
    ALLOW = "allow"          # 거래 허용
    REDUCE_SIZE = "reduce_size"  # 사이즈 축소
    BLOCK_NEW = "block_new"  # 신규 진입 차단
    CLOSE_ALL = "close_all"  # 전 포지션 청산


@dataclass
class PositionSizeResult:
    """포지션 사이징 결과.

    Attributes:
        contracts: 계약 수 (소수점 포함)
        margin_required: 필요 마진 (USDT)
        notional_value: 명목 가치 (USDT)
        risk_amount: 위험 금액 (USDT)
        capped: 최대 한도에 의해 축소되었는지 여부
    """
    contracts: float
    margin_required: float
    notional_value: float
    risk_amount: float
    capped: bool


# =============================================================================
# 1. PositionSizer — ATR 기반 포지션 사이징
# =============================================================================

class PositionSizer:
    """ATR 기반 포지션 사이징.

    레버리지 3x 반영. Risk Plan 공식 기반:
      계약수 = Dollar_Risk / (k × ATR × 진입가)
      마진 = 명목가치 / 레버리지
    """

    def __init__(self, params: RiskParams, leverage: int = 3) -> None:
        """초기화.

        Args:
            params: 리스크 파라미터
            leverage: 레버리지 배수 (기본 3)
        """
        self.params = params
        self.leverage = leverage

    def calculate(
        self,
        capital: float,
        price: float,
        atr: float,
        stop_multiplier: float,
        symbol: str,
    ) -> PositionSizeResult:
        """ATR 기반 포지션 사이즈 계산.

        Args:
            capital: 총 자본 (USDT)
            price: 현재 가격 (USDT)
            atr: ATR 값 (USDT)
            stop_multiplier: ATR 스톱 배수 (MR=1.5, TF=2.5)
            symbol: 거래 심볼

        Returns:
            PositionSizeResult

        Raises:
            ValueError: price, atr, stop_multiplier가 0 이하일 때
        """
        if price <= 0 or atr <= 0 or stop_multiplier <= 0:
            raise ValueError(
                f"price({price}), atr({atr}), stop_multiplier({stop_multiplier}) must be positive"
            )

        # [1단계] 거래당 허용 손실액
        dollar_risk = capital * self.params.risk_per_trade_pct

        # [2단계] ATR 스톱 거리 (USDT 환산)
        stop_distance_usdt = stop_multiplier * atr

        # [3단계] 계약 수 계산
        contracts = dollar_risk / stop_distance_usdt

        # [4단계] 명목가치 및 마진 계산
        notional_value = contracts * price
        margin_required = notional_value / self.leverage

        # [5단계] 최대 포지션 한도 체크
        max_notional = capital * self.params.max_position_pct
        capped = False
        if notional_value > max_notional:
            contracts = (max_notional * self.leverage) / price
            notional_value = contracts * price
            margin_required = notional_value / self.leverage
            capped = True
            logger.debug(
                "[%s] 포지션 한도 캡핑: %.4f계약 → max_notional=%.0f USDT",
                symbol, contracts, max_notional
            )

        # 계약수 0 이하 → 진입 금지 (호출부에서 스킵)
        if contracts <= 0:
            return PositionSizeResult(
                contracts=0.0,
                margin_required=0.0,
                notional_value=0.0,
                risk_amount=dollar_risk,
                capped=False,
            )

        return PositionSizeResult(
            contracts=contracts,
            margin_required=margin_required,
            notional_value=notional_value,
            risk_amount=dollar_risk,
            capped=capped,
        )


# =============================================================================
# 2. StopLossManager — ATR 동적 스톱 + 트레일링 스톱
# =============================================================================

class StopLossManager:
    """ATR 동적 스톱로스 및 트레일링 스톱 관리.

    MR 전략: k=1.5, TF 전략: k=2.5
    트레일링 스톱: 1×ATR 수익 도달 시 활성화, 0.75×ATR 거리 추적
    """

    def __init__(self, params: RiskParams) -> None:
        """초기화.

        Args:
            params: 리스크 파라미터
        """
        self.params = params

    def calc_stop(
        self,
        entry_price: float,
        direction: str,
        atr: float,
        strategy_type: str,
    ) -> float:
        """ATR 기반 초기 스톱로스 계산.

        Args:
            entry_price: 진입 가격
            direction: "LONG" 또는 "SHORT"
            atr: ATR 값
            strategy_type: "MR" (평균회귀) 또는 "TF" (추세추종)

        Returns:
            스톱로스 가격

        Raises:
            ValueError: direction 또는 strategy_type이 유효하지 않을 때
        """
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be 'LONG' or 'SHORT', got {direction!r}")

        # 전략 유형별 ATR 배수 선택
        if strategy_type.upper() == "MR":
            k = self.params.mr_atr_multiplier
        elif strategy_type.upper() == "TF":
            k = self.params.tf_atr_multiplier
        else:
            # 알 수 없는 전략은 MR 배수 사용
            k = self.params.mr_atr_multiplier
            logger.warning("알 수 없는 strategy_type=%r, MR 배수(%.1f) 사용", strategy_type, k)

        stop_distance = k * atr

        if direction == "LONG":
            return entry_price - stop_distance
        else:  # SHORT
            return entry_price + stop_distance

    def update_trailing(
        self,
        current_price: float,
        current_stop: float,
        direction: str,
        atr: float,
    ) -> float:
        """트레일링 스톱 가격 갱신.

        롱: trail_stop = max(current_stop, current_price - 0.75×ATR)
        숏: trail_stop = min(current_stop, current_price + 0.75×ATR)

        Args:
            current_price: 현재 가격
            current_stop: 현재 스톱로스 가격
            direction: "LONG" 또는 "SHORT"
            atr: ATR 값

        Returns:
            갱신된 스톱로스 가격 (기존보다 유리한 방향으로만 이동)
        """
        trail_dist = self.params.trailing_distance_atr * atr

        if direction == "LONG":
            new_stop = current_price - trail_dist
            return max(current_stop, new_stop)
        else:  # SHORT
            new_stop = current_price + trail_dist
            return min(current_stop, new_stop)


# =============================================================================
# 3. DailyLossTracker — 일일 손실 한도 추적
# =============================================================================

class DailyLossTracker:
    """일일 손실 한도 추적.

    UTC 00:00 기준으로 일일 손익을 추적하고,
    5% 손실 한도 도달 시 신규 진입을 차단한다.
    """

    def __init__(self) -> None:
        """초기화."""
        self._daily_pnl: float = 0.0
        self._last_reset_date: Optional[str] = None  # "YYYY-MM-DD" (UTC)

    def reset_if_new_day(self) -> None:
        """UTC 00:00 기준으로 새 날이면 일일 손익을 리셋한다."""
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_reset_date != today_utc:
            if self._last_reset_date is not None:
                logger.info(
                    "일일 손익 리셋: %s → %s (이전 일일 PnL: %.2f USDT)",
                    self._last_reset_date, today_utc, self._daily_pnl
                )
            self._daily_pnl = 0.0
            self._last_reset_date = today_utc

    def record_pnl(self, pnl: float) -> None:
        """손익 기록.

        Args:
            pnl: 실현 손익 (USDT). 손실은 음수.
        """
        self.reset_if_new_day()
        self._daily_pnl += pnl
        logger.debug("일일 손익 기록: %.2f USDT (누계: %.2f USDT)", pnl, self._daily_pnl)

    def check_limit(self, capital: float, params: RiskParams) -> RiskAction:
        """일일 손실 한도 초과 여부 확인.

        Args:
            capital: 총 자본 (USDT)
            params: 리스크 파라미터

        Returns:
            RiskAction (BLOCK_NEW 또는 ALLOW)
        """
        self.reset_if_new_day()
        daily_loss_limit = capital * params.daily_loss_limit_pct
        if self._daily_pnl <= -daily_loss_limit:
            logger.warning(
                "일일 손실 한도 도달: %.2f USDT (한도: %.2f USDT)",
                self._daily_pnl, -daily_loss_limit
            )
            return RiskAction.BLOCK_NEW
        return RiskAction.ALLOW

    @property
    def daily_pnl(self) -> float:
        """현재 일일 손익 반환."""
        return self._daily_pnl


# =============================================================================
# 4. DrawdownTracker — MDD 추적 + 구간별 리스크 축소
# =============================================================================

class DrawdownTracker:
    """최대 낙폭(MDD) 추적 및 리스크 단계별 축소.

    구간별 리스크:
      - 0~5%: 정상 (risk=2%)
      - 5~10%: 주의 (risk×0.75)
      - 10~15%: 경고 (risk×0.5)
      - 15%+: 거래 중단
    """

    def __init__(self, initial_capital: float = 50000.0) -> None:
        """초기화.

        Args:
            initial_capital: 초기 자본 (USDT)
        """
        self._peak_equity: float = initial_capital
        self._current_equity: float = initial_capital
        self._max_drawdown: float = 0.0

    def update(self, equity: float) -> None:
        """에퀴티 갱신 및 최고점/MDD 업데이트.

        Args:
            equity: 현재 에퀴티 (USDT)
        """
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        drawdown = (self._peak_equity - equity) / self._peak_equity
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown
            logger.debug("MDD 갱신: %.2f%%", drawdown * 100)

    def get_drawdown_pct(self) -> float:
        """현재 낙폭 비율 반환 (0~1)."""
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - self._current_equity) / self._peak_equity

    def get_risk_multiplier(self) -> float:
        """낙폭 구간에 따른 리스크 배수 반환.

        Returns:
            리스크 배수 (0.0 ~ 1.0). 0.0이면 거래 중단.
        """
        dd = self.get_drawdown_pct()
        if dd >= 0.15:
            return 0.0   # 거래 중단
        elif dd >= 0.10:
            return 0.5   # 경고: 리스크 50%
        elif dd >= 0.05:
            return 0.75  # 주의: 리스크 75%
        else:
            return 1.0   # 정상

    def check_limit(self, params: RiskParams) -> RiskAction:
        """MDD 한도 초과 여부 확인.

        Args:
            params: 리스크 파라미터

        Returns:
            RiskAction
        """
        dd = self.get_drawdown_pct()
        if dd >= params.max_drawdown_pct:
            logger.error(
                "MDD 한도 도달: %.2f%% (한도: %.2f%%) → 전 포지션 청산",
                dd * 100, params.max_drawdown_pct * 100
            )
            return RiskAction.CLOSE_ALL
        elif dd >= params.max_drawdown_pct * 0.667:
            return RiskAction.BLOCK_NEW
        return RiskAction.ALLOW

    @property
    def max_drawdown_pct(self) -> float:
        """최대 낙폭 비율 반환."""
        return self._max_drawdown


# =============================================================================
# 5. ConsecutiveLossManager — 연속 손실 추적 + 자동 사이즈 축소
# =============================================================================

class ConsecutiveLossManager:
    """연속 손실 추적 및 자동 포지션 사이즈 축소.

    연속 3패 발생 시 사이즈를 50% 축소.
    이후 2연승 또는 3거래 기대값 양(+) 시 정상 복귀.
    """

    def __init__(self, params: RiskParams) -> None:
        """초기화.

        Args:
            params: 리스크 파라미터
        """
        self.params = params
        self._consecutive_losses: int = 0
        self._is_reduced: bool = False
        self._recovery_wins: int = 0
        self._recovery_pnl_sum: float = 0.0
        self._recovery_trades: int = 0

    def record_trade(self, is_win: bool, pnl: float = 0.0) -> None:
        """거래 결과 기록.

        Args:
            is_win: 수익 거래 여부
            pnl: 손익 금액 (복귀 조건 계산용)
        """
        if is_win:
            self._consecutive_losses = 0
            if self._is_reduced:
                # 복귀 조건 추적
                self._recovery_wins += 1
                self._recovery_pnl_sum += pnl
                self._recovery_trades += 1
                # 2연승 시 정상 복귀
                if self._recovery_wins >= 2:
                    self._is_reduced = False
                    self._reset_recovery_counters()
                    logger.info("연속 손실 축소 해제: 2연승 달성")
        else:
            self._consecutive_losses += 1
            if self._is_reduced:
                self._recovery_trades += 1
                self._recovery_pnl_sum += pnl

            if self._consecutive_losses >= self.params.consecutive_loss_threshold and not self._is_reduced:
                self._is_reduced = True
                self._reset_recovery_counters()
                logger.warning(
                    "연속 %d패 → 포지션 사이즈 %.0f%% 축소",
                    self._consecutive_losses,
                    (1 - self.params.consecutive_loss_reduction) * 100
                )

        # 복귀 조건: 3거래 기대값 양(+)
        if self._is_reduced and self._recovery_trades >= 3 and self._recovery_pnl_sum > 0:
            self._is_reduced = False
            self._reset_recovery_counters()
            logger.info("연속 손실 축소 해제: 3거래 기대값 양(+)")

    def _reset_recovery_counters(self) -> None:
        """복귀 카운터 초기화."""
        self._recovery_wins = 0
        self._recovery_pnl_sum = 0.0
        self._recovery_trades = 0

    def get_size_multiplier(self) -> float:
        """현재 사이즈 배수 반환.

        Returns:
            1.0 (정상) 또는 0.5 (축소됨)
        """
        if self._is_reduced:
            return self.params.consecutive_loss_reduction
        return 1.0

    @property
    def consecutive_losses(self) -> int:
        """현재 연속 손실 횟수."""
        return self._consecutive_losses

    @property
    def is_reduced(self) -> bool:
        """사이즈 축소 상태 여부."""
        return self._is_reduced


# =============================================================================
# 6. CorrelationAdjuster — 상관 코인 동시 진입 시 사이즈 조정
# =============================================================================

class CorrelationAdjuster:
    """상관 코인 동시 진입 시 포지션 사이즈 조정.

    ETH 그룹(높은 상관): ETHUSDT, BNBUSDT, SOLUSDT, AVAXUSDT, DOTUSDT
    ALT 그룹(중간 상관): XRPUSDT, ADAUSDT, DOGEUSDT, TRXUSDT
    """

    # 상관 그룹 정의
    CORRELATION_GROUPS: Dict[str, List[str]] = {
        "BTC": ["BTCUSDT"],
        "ETH_HIGH": [
            "ETHUSDT", "BNBUSDT", "SOLUSDT", "AVAXUSDT", "DOTUSDT"
        ],
        "ALT_MID": [
            "XRPUSDT", "ADAUSDT", "DOGEUSDT", "TRXUSDT"
        ],
    }

    # 그룹별 동시 진입 시 사이즈 조정 계수
    GROUP_WEIGHTS: Dict[str, float] = {
        "BTC": 1.0,
        "ETH_HIGH": 0.7,   # 동일 그룹 2개 이상 시 30% 축소
        "ALT_MID": 0.8,    # 동일 그룹 2개 이상 시 20% 축소
    }

    def _get_group(self, symbol: str) -> Optional[str]:
        """심볼이 속한 상관 그룹 반환."""
        for group_name, members in self.CORRELATION_GROUPS.items():
            if symbol in members:
                return group_name
        return None

    def adjust_size(self, symbol: str, current_positions: List[str]) -> float:
        """상관관계 기반 사이즈 조정 계수 반환.

        Args:
            symbol: 신규 진입할 심볼
            current_positions: 현재 보유 중인 심볼 목록

        Returns:
            사이즈 조정 계수 (0.0 ~ 1.0)
        """
        group = self._get_group(symbol)
        if group is None:
            return 1.0

        # 동일 그룹 내 현재 보유 포지션 수 확인
        group_members = self.CORRELATION_GROUPS[group]
        same_group_positions = [p for p in current_positions if p in group_members]

        if len(same_group_positions) == 0:
            return 1.0

        # BTC + ETH 그룹 동시 진입 시 ETH 그룹 추가 축소
        if group == "ETH_HIGH" and "BTCUSDT" in current_positions:
            base_weight = self.GROUP_WEIGHTS[group]
            return base_weight * 0.8  # 추가 20% 축소

        return self.GROUP_WEIGHTS[group]


# =============================================================================
# 7. SessionManager — 24/7 시장 세션 관리
# =============================================================================

class SessionManager:
    """암호화폐 24/7 시장 세션 관리.

    주말 사이즈 축소, 펀딩 정산 구간 감지.
    """

    # 펀딩 정산 시각 (UTC 시간, 분): 00:00, 08:00, 16:00
    FUNDING_HOURS_UTC = [0, 8, 16]
    FUNDING_WINDOW_MINUTES = 30  # 정산 30분 전 구간

    def is_weekend(self) -> bool:
        """현재 시각이 주말(UTC 기준)인지 확인.

        Returns:
            True이면 토요일 또는 일요일
        """
        now_utc = datetime.now(timezone.utc)
        # 5=토요일, 6=일요일
        return now_utc.weekday() >= 5

    def get_session_multiplier(self) -> float:
        """세션별 포지션 사이즈 배수 반환.

        주말: 0.5 (유동성 감소, 변동성 증가 위험)
        평일: 1.0

        Returns:
            사이즈 배수
        """
        if self.is_weekend():
            return 0.5
        return 1.0

    def is_funding_window(self) -> bool:
        """현재 시각이 펀딩 정산 30분 전 구간인지 확인.

        Returns:
            True이면 펀딩 정산 구간 (신규 진입 보류 권장)
        """
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        current_minute = now_utc.minute

        for funding_hour in self.FUNDING_HOURS_UTC:
            # 정산 30분 전 구간 체크
            window_start_hour = (funding_hour - 1) % 24
            window_start_minute = 30

            if current_hour == window_start_hour and current_minute >= window_start_minute:
                return True
            if current_hour == funding_hour and current_minute == 0:
                return True  # 정시 포함

        return False


# =============================================================================
# 8. RiskManager — 통합 리스크 매니저
# =============================================================================

class RiskManager:
    """통합 리스크 매니저.

    PositionSizer, StopLossManager, DailyLossTracker,
    DrawdownTracker, ConsecutiveLossManager, CorrelationAdjuster,
    SessionManager를 조율하여 통합 리스크 관리를 제공한다.
    """

    def __init__(
        self,
        params: RiskParams,
        initial_capital: float = 50000.0,
        leverage: int = 3,
    ) -> None:
        """초기화.

        Args:
            params: 리스크 파라미터
            initial_capital: 초기 자본 (USDT)
            leverage: 레버리지 배수
        """
        self.params = params
        self.initial_capital = initial_capital
        self.leverage = leverage

        # 하위 컴포넌트 초기화
        self._sizer = PositionSizer(params, leverage)
        self._stop_mgr = StopLossManager(params)
        self._daily_tracker = DailyLossTracker()
        self._drawdown_tracker = DrawdownTracker(initial_capital)
        self._consec_mgr = ConsecutiveLossManager(params)
        self._corr_adjuster = CorrelationAdjuster()
        self._session_mgr = SessionManager()

        # 내부 상태
        self._current_equity: float = initial_capital

        logger.info(
            "RiskManager 초기화: 자본=%.0f USDT, 레버리지=%dx, "
            "Risk/Trade=%.1f%%, 일일한도=%.1f%%, MDD한도=%.1f%%",
            initial_capital, leverage,
            params.risk_per_trade_pct * 100,
            params.daily_loss_limit_pct * 100,
            params.max_drawdown_pct * 100,
        )

    # ── 진입 허용 여부 ──────────────────────────────────────────────

    def check_entry_allowed(
        self,
        capital: float,
        current_positions: List[str],
    ) -> RiskAction:
        """신규 진입 허용 여부 확인.

        Args:
            capital: 현재 자본 (USDT)
            current_positions: 현재 보유 심볼 목록

        Returns:
            RiskAction
        """
        # MDD 한도 확인
        mdd_action = self._drawdown_tracker.check_limit(self.params)
        if mdd_action in (RiskAction.CLOSE_ALL, RiskAction.BLOCK_NEW):
            return mdd_action

        # 일일 손실 한도 확인
        daily_action = self._daily_tracker.check_limit(capital, self.params)
        if daily_action == RiskAction.BLOCK_NEW:
            return RiskAction.BLOCK_NEW

        # 최대 동시 포지션 수 확인
        if len(current_positions) >= self.params.max_concurrent:
            logger.debug(
                "최대 동시 포지션 수 초과: %d/%d",
                len(current_positions), self.params.max_concurrent
            )
            return RiskAction.BLOCK_NEW

        # 펀딩 정산 구간
        if self._session_mgr.is_funding_window():
            logger.debug("펀딩 정산 구간: 신규 진입 차단")
            return RiskAction.BLOCK_NEW

        return RiskAction.ALLOW

    # ── 포지션 사이징 ───────────────────────────────────────────────

    def calculate_position_size(
        self,
        capital: float,
        price: float,
        atr: float,
        stop_multiplier: float,
        symbol: str,
        current_positions: List[str],
    ) -> PositionSizeResult:
        """포지션 사이즈 계산 (모든 조정 계수 반영).

        Args:
            capital: 현재 자본 (USDT)
            price: 현재 가격
            atr: ATR 값
            stop_multiplier: ATR 스톱 배수
            symbol: 거래 심볼
            current_positions: 현재 보유 심볼 목록

        Returns:
            PositionSizeResult (모든 조정 계수 반영)
        """
        # 기본 사이징
        base_result = self._sizer.calculate(capital, price, atr, stop_multiplier, symbol)

        # 드로다운 리스크 배수
        dd_multiplier = self._drawdown_tracker.get_risk_multiplier()

        # 연속 손실 배수
        consec_multiplier = self._consec_mgr.get_size_multiplier()

        # 상관관계 조정
        corr_multiplier = self._corr_adjuster.adjust_size(symbol, current_positions)

        # 세션 배수 (주말 축소)
        session_multiplier = self._session_mgr.get_session_multiplier()

        # 전체 조정 계수
        total_multiplier = (
            dd_multiplier * consec_multiplier * corr_multiplier * session_multiplier
        )

        if total_multiplier < 1.0:
            logger.debug(
                "[%s] 사이즈 조정: DD=%.2f, 연손=%.2f, 상관=%.2f, 세션=%.2f → 합계=%.2f",
                symbol, dd_multiplier, consec_multiplier,
                corr_multiplier, session_multiplier, total_multiplier
            )

        adjusted_contracts = base_result.contracts * total_multiplier
        adjusted_notional = adjusted_contracts * price
        adjusted_margin = adjusted_notional / self.leverage

        return PositionSizeResult(
            contracts=adjusted_contracts,
            margin_required=adjusted_margin,
            notional_value=adjusted_notional,
            risk_amount=base_result.risk_amount * total_multiplier,
            capped=base_result.capped,
        )

    # ── 스톱로스 관리 ───────────────────────────────────────────────

    def calculate_stop_loss(
        self,
        entry_price: float,
        direction: str,
        atr: float,
        strategy_type: str,
    ) -> float:
        """초기 스톱로스 가격 계산.

        Args:
            entry_price: 진입 가격
            direction: "LONG" 또는 "SHORT"
            atr: ATR 값
            strategy_type: "MR" 또는 "TF"

        Returns:
            스톱로스 가격
        """
        return self._stop_mgr.calc_stop(entry_price, direction, atr, strategy_type)

    def update_trailing_stop(
        self,
        current_price: float,
        current_stop: float,
        direction: str,
        atr: float,
    ) -> float:
        """트레일링 스톱 가격 갱신.

        Args:
            current_price: 현재 가격
            current_stop: 현재 스톱로스 가격
            direction: "LONG" 또는 "SHORT"
            atr: ATR 값

        Returns:
            갱신된 스톱로스 가격
        """
        return self._stop_mgr.update_trailing(current_price, current_stop, direction, atr)

    # ── 거래 결과 기록 ──────────────────────────────────────────────

    def record_trade_result(self, pnl: float, is_win: bool) -> None:
        """거래 결과를 모든 트래커에 기록.

        Args:
            pnl: 실현 손익 (USDT)
            is_win: 수익 거래 여부
        """
        self._daily_tracker.record_pnl(pnl)
        self._current_equity += pnl
        self._drawdown_tracker.update(self._current_equity)
        self._consec_mgr.record_trade(is_win, pnl)

        logger.debug(
            "거래 결과 기록: PnL=%.2f, 승패=%s, 에퀴티=%.0f, MDD=%.2f%%",
            pnl, "WIN" if is_win else "LOSS",
            self._current_equity,
            self._drawdown_tracker.get_drawdown_pct() * 100,
        )

    def check_daily_loss_limit(self, daily_pnl: float, capital: float) -> bool:
        """일일 손실 한도 초과 여부 확인.

        Args:
            daily_pnl: 일일 손익 합계 (USDT)
            capital: 현재 자본 (USDT)

        Returns:
            True이면 한도 초과 (신규 진입 차단)
        """
        daily_loss_limit = capital * self.params.daily_loss_limit_pct
        return daily_pnl <= -daily_loss_limit

    # ── 분석 유틸리티 ──────────────────────────────────────────────

    def calculate_expectancy(self, trades: list) -> float:
        """거래 기대값 계산.

        Args:
            trades: 거래 기록 리스트. 각 항목은 {"pnl": float} 포함.

        Returns:
            기대값 (USDT). 양수면 유리한 전략.
        """
        if not trades:
            return 0.0
        total_pnl = sum(t.get("pnl", 0.0) for t in trades)
        return total_pnl / len(trades)

    def run_cost_sensitivity(
        self,
        backtest_func: object,
        scenarios: List[float],
    ) -> dict:
        """비용 시나리오별 전략 수익성 분석.

        Args:
            backtest_func: 백테스트 함수. fee_pct를 인수로 받아 결과 dict 반환.
            scenarios: 테스트할 비용 시나리오 리스트 (소수점 비율)

        Returns:
            {fee_pct: backtest_result} 딕셔너리
        """
        results = {}
        for fee_pct in scenarios:
            try:
                result = backtest_func(fee_pct=fee_pct)  # type: ignore[call-arg]
                results[fee_pct] = result
                logger.info("비용 민감도 분석: fee=%.4f → %s", fee_pct, result)
            except Exception as exc:
                logger.error("비용 민감도 분석 실패 (fee=%.4f): %s", fee_pct, exc)
                results[fee_pct] = {"error": str(exc)}
        return results

    # ── 상태 조회 ──────────────────────────────────────────────────

    def get_status(self) -> dict:
        """현재 리스크 상태 요약 반환.

        Returns:
            리스크 상태 딕셔너리
        """
        self._daily_tracker.reset_if_new_day()
        return {
            "equity": self._current_equity,
            "daily_pnl": self._daily_tracker.daily_pnl,
            "drawdown_pct": round(self._drawdown_tracker.get_drawdown_pct() * 100, 2),
            "max_drawdown_pct": round(self._drawdown_tracker.max_drawdown_pct * 100, 2),
            "dd_risk_multiplier": self._drawdown_tracker.get_risk_multiplier(),
            "consecutive_losses": self._consec_mgr.consecutive_losses,
            "size_reduced": self._consec_mgr.is_reduced,
            "consec_size_multiplier": self._consec_mgr.get_size_multiplier(),
            "is_weekend": self._session_mgr.is_weekend(),
            "session_multiplier": self._session_mgr.get_session_multiplier(),
            "is_funding_window": self._session_mgr.is_funding_window(),
        }


__all__ = [
    "RiskAction",
    "PositionSizeResult",
    "PositionSizer",
    "StopLossManager",
    "DailyLossTracker",
    "DrawdownTracker",
    "ConsecutiveLossManager",
    "CorrelationAdjuster",
    "SessionManager",
    "RiskManager",
]
