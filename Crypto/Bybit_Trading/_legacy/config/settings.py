"""앱 전역 설정 모듈.

Bybit 암호화폐 선물 모의거래 시스템의 설정값을 dataclass로 정의한다.
.env 파일에서 민감 정보(API 키 등)를 오버라이드한다.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False


def _load_env() -> None:
    """프로젝트 루트의 .env 파일을 로드한다."""
    if not _DOTENV_AVAILABLE:
        return
    from config import ENV_FILE
    load_dotenv(ENV_FILE, override=False)


@dataclass
class AppSettings:
    """앱 전역 설정.

    Attributes:
        base_url: Bybit API 서버 URL (Demo: api-demo.bybit.com, Real: api.bybit.com)
        ws_url: Bybit WebSocket URL
        db_path: SQLite DB 파일 경로
        leverage: 레버리지 배수
        symbols: 거래 대상 심볼 목록 (symbol_manager에서 동적 주입)
        recv_window: API 요청 허용 시간 오프셋 (밀리초)
        log_level: 로깅 레벨
    """
    base_url: str = "https://api-demo.bybit.com"
    ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    db_path: str = "db/bybit_data.db"
    leverage: int = 3
    symbols: List[str] = field(default_factory=list)
    recv_window: int = 5000
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        """환경변수에서 설정값을 오버라이드한다."""
        _load_env()
        env_url = os.getenv("BYBIT_BASE_URL")
        if env_url:
            self.base_url = env_url
        env_ws = os.getenv("BYBIT_WS_URL")
        if env_ws:
            self.ws_url = env_ws
        env_db = os.getenv("BYBIT_DB_PATH")
        if env_db:
            self.db_path = env_db
        env_lev = os.getenv("BYBIT_LEVERAGE")
        if env_lev:
            try:
                self.leverage = int(env_lev)
            except ValueError:
                import logging as _log
                _log.getLogger(__name__).warning(
                    "BYBIT_LEVERAGE 환경변수 정수 변환 실패: %s", env_lev,
                )
        # symbols는 init_symbol_manager() 호출 후 명시적으로 주입됨
        # import 시점에 네트워크 호출을 방지하기 위해 여기서는 채우지 않음
        if not self.symbols:
            from config.symbol_manager import STRATEGY_SYMBOLS
            self.symbols = list(STRATEGY_SYMBOLS)

    @property
    def api_key(self) -> str:
        """환경변수에서 API 키 반환."""
        _load_env()
        return os.getenv("BYBIT_API_KEY", "")

    @property
    def api_secret(self) -> str:
        """환경변수에서 API 시크릿 반환."""
        _load_env()
        return os.getenv("BYBIT_API_SECRET", "")

    def __repr__(self) -> str:
        return (
            f"AppSettings(base_url='{self.base_url}', "
            f"leverage={self.leverage}, symbols={len(self.symbols)}개)"
        )


@dataclass
class StrategyParams:
    """전략 파라미터 설정.

    grid_optimization.json 확정값 기준. 모든 전략 파라미터의 Single Source of Truth.

    Attributes:
        bb_period: 볼린저밴드 기간
        bb_std: 볼린저밴드 표준편차 배수
        kc_period: 켈트너채널 EMA 기간
        kc_atr_mult: 켈트너채널 ATR 배수
        atr_period: ATR 기간
        adx_period: ADX 기간
        rsi_period: RSI 기간
        macd_fast: MACD 빠른 EMA 기간
        macd_slow: MACD 느린 EMA 기간
        macd_signal: MACD 시그널 EMA 기간
        ichimoku_tenkan: 일목 전환선 기간
        ichimoku_kijun: 일목 기준선 기간
        ichimoku_senkou: 일목 선행스팬B 기간
        kama_period: KAMA Efficiency Ratio 기간
        kama_fast: KAMA 빠른 EMA 기간
        kama_slow: KAMA 느린 EMA 기간
        zscore_window: 페어 Z-Score 롤링 윈도우
        ichimoku_stop_atr: Ichimoku ATR 스톱 배수
        ichimoku_tp_atr: Ichimoku ATR 익절 배수
        bbkc_stop_atr: BBKCSqueeze ATR 스톱 배수
        bbkc_tp_atr: BBKCSqueeze ATR 익절 배수
        rsi_oversold: RSI 과매도 기준
        rsi_overbought: RSI 과매수 기준
        rsimacd_adx_min: RSI+MACD ADX 최소 기준
        rsimacd_stop_atr: RSI+MACD ATR 스톱 배수
        rsimacd_tp_atr: RSI+MACD ATR 익절 배수
        pairs_zscore_window: 페어 Z-Score 윈도우
        pairs_entry_z: 페어 진입 Z-Score
        pairs_exit_z: 페어 청산 Z-Score
        pairs_stop_z: 페어 손절 Z-Score
    """
    # 볼린저밴드 / BBKCSqueeze (2026-03-30 그리드 최적화 확정)
    bb_period: int = 20
    bb_std: float = 1.5
    # 켈트너채널
    kc_period: int = 20
    kc_atr_mult: float = 1.0
    # BBKCSqueeze 고정 TP/SL (기본 청산)
    bbkc_tp_pct: float = 0.06       # 고정 TP 6%
    bbkc_sl_pct: float = 0.07       # 고정 SL 7%
    # BBKCSqueeze ATR 청산 (대안, 선택 가능)
    bbkc_stop_atr: float = 2.0
    bbkc_tp_atr: float = 8.0
    bbkc_exit_mode: str = "fixed"   # "fixed" 또는 "atr"
    # ATR / ADX
    atr_period: int = 14
    adx_period: int = 14
    # RSI
    rsi_period: int = 14
    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    # 일목균형표 (grid_optimization.json 확정값)
    ichimoku_tenkan: int = 10
    ichimoku_kijun: int = 26
    ichimoku_senkou: int = 40
    ichimoku_stop_atr: float = 2.0
    ichimoku_tp_atr: float = 6.0
    # KAMA
    kama_period: int = 10
    kama_fast: int = 2
    kama_slow: int = 30
    # RSI+MACD 평균회귀 (2026-03-30 그리드 최적화 확정)
    rsi_oversold: float = 20.0       # 과매도 (기존 30 -> 20)
    rsi_overbought: float = 70.0
    rsimacd_adx_min: float = 15.0
    rsimacd_tp_pct: float = 0.06     # 고정 TP 6%
    rsimacd_sl_pct: float = 0.05     # 고정 SL 5%
    rsimacd_stop_atr: float = 2.5    # ATR 대안 (미사용)
    rsimacd_tp_atr: float = 3.0      # ATR 대안 (미사용)
    # 페어 Z-Score (grid_optimization.json 확정값)
    zscore_window: int = 250
    # PairsTrading (2026-03-30 그리드 최적화 확정)
    pairs_zscore_window: int = 250   # Z-Score 롤링 윈도우 (기존 350 -> 250)
    pairs_entry_z: float = 1.75      # 진입 Z-Score
    pairs_exit_z: float = 0.0        # 청산 Z-Score (Z@0.0 복귀)
    pairs_stop_z: float = 4.0        # 스톱 Z-Score (기존 3.5 -> 4.0)
    pairs_stop_pct: float = 0.03     # 페어 A-leg 스톱 비율 (고정% 대안)
    pairs_tp_pct: float = 0.02       # 페어 A-leg 익절 비율 (고정% 대안)
    pairs_adf_pvalue: float = 0.03   # 동적 페어 선별 ADF p-value 컷오프
    pairs_max_concurrent: int = 10   # 최대 동시 페어 수 (기존 3 -> 10)
    pairs_cooldown_bars: int = 96    # STOP 청산 후 재진입 금지 봉 수 (96봉 = 24시간 @15m)
    # 전략 자동 활성화/비활성화 기준
    auto_disable_calmar: float = 0.5   # Calmar < 이 값이면 비활성화
    auto_enable_calmar: float = 1.0    # Calmar >= 이 값이면 재활성화
    min_trades_for_eval: int = 20      # 최소 거래 수 (미달 시 자동 판단 안 함)
    # 레짐 감지
    adx_mr_threshold: float = 20.0   # ADX < 20: 평균회귀 레짐
    adx_tf_threshold: float = 25.0   # ADX > 25: 추세추종 레짐
    # BBKCSqueeze 세부 파라미터 (F-04: _get_bbkc_params 주입용)
    bbkc_kc_period: int = 20
    bbkc_atr_period: int = 14
    bbkc_rsi_period: int = 14
    bbkc_rsi_filter: float = 70.0
    # RSIMACDStrategy 세부 파라미터 (F-04: _get_rsimacd_params 주입용)
    rsimacd_rsi_period: int = 14
    rsimacd_macd_fast: int = 12
    rsimacd_macd_slow: int = 26
    rsimacd_macd_signal: int = 9
    rsimacd_adx_period: int = 14
    rsimacd_atr_period: int = 14
    rsimacd_exit_mode: str = "fixed"


@dataclass
class RiskParams:
    """리스크 관리 파라미터.

    Attributes:
        risk_per_trade_pct: 거래당 허용 손실 비율 (자본 대비)
        max_position_pct: 단일 포지션 최대 자본 비율
        max_concurrent: 최대 동시 포지션 수
        daily_loss_limit_pct: 일일 최대 손실 한도 비율
        max_drawdown_pct: 최대 낙폭 한도 비율
        mr_atr_multiplier: 평균회귀 전략 ATR 스톱 배수
        tf_atr_multiplier: 추세추종 전략 ATR 스톱 배수
        trailing_activation_atr: 트레일링 스톱 활성화 ATR 배수
        trailing_distance_atr: 트레일링 스톱 거리 ATR 배수
        correlation_adjustment: 상관관계 높은 자산 포지션 사이즈 조정 계수
        weekend_size_factor: 주말 포지션 사이즈 축소 계수
        consecutive_loss_threshold: 연속 손실 허용 횟수
        consecutive_loss_reduction: 연속 손실 초과 시 포지션 사이즈 축소 계수
    """
    risk_per_trade_pct: float = 0.02
    max_position_pct: float = 0.05       # 단일 포지션 최대 5% (기존 20%)
    pairs_position_pct: float = 0.03     # PairsTrading 포지션 3%
    max_concurrent: int = 26             # BBKC 3 + RSI 3 + Pairs 10x2 = 26
    daily_loss_limit_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    mr_atr_multiplier: float = 1.5
    tf_atr_multiplier: float = 2.5
    trailing_activation_atr: float = 2.5  # ATR 2.5배 수익 후 활성화 (조기 활성화 방지)
    trailing_distance_atr: float = 1.5   # ATR 1.5배 거리로 추적 (되돌림 여유 확보)
    correlation_adjustment: float = 0.7
    weekend_size_factor: float = 0.5
    consecutive_loss_threshold: int = 3
    consecutive_loss_reduction: float = 0.5


@dataclass
class BacktestConfig:
    """백테스트 전용 설정.

    Attributes:
        initial_capital: 초기 자본 (USDT)
        taker_fee_pct: 테이커 수수료 비율
        maker_fee_pct: 메이커 수수료 비율
        slippage_pct: 슬리피지 비율
        start_date: 백테스트 시작일 (YYYY-MM-DD, None이면 전체)
        end_date: 백테스트 종료일 (YYYY-MM-DD, None이면 전체)
    """
    initial_capital: float = 50000.0
    taker_fee_pct: float = 0.00055   # Bybit taker 0.055%
    maker_fee_pct: float = 0.0002    # Bybit maker 0.02%
    slippage_pct: float = 0.0003     # 시장가 주문 슬리피지 0.03%
    start_date: Optional[str] = None
    end_date: Optional[str] = None


# 기본 싱글턴 인스턴스 (임포트 시 바로 사용 가능)
settings = AppSettings()
strategy_params = StrategyParams()
risk_params = RiskParams()
backtest_config = BacktestConfig()

__all__ = [
    "AppSettings",
    "StrategyParams",
    "RiskParams",
    "BacktestConfig",
    "settings",
    "strategy_params",
    "risk_params",
    "backtest_config",
    "PAIRS_LIST",
]

# 페어 트레이딩 페어 목록 (단일 소스)
PAIRS_LIST = [
    ("SOLUSDT", "ETCUSDT"),
    ("DOTUSDT", "TIAUSDT"),
    ("ADAUSDT", "LINKUSDT"),
]
