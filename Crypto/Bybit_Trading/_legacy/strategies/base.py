"""전략 기본 추상 클래스 모듈.

모든 거래 전략이 구현해야 하는 BaseStrategy ABC와
Signal/TradeRecord 데이터 클래스를 정의한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class Signal:
    """거래 시그널 데이터 클래스.

    Attributes:
        symbol: 거래 심볼 (예: "BTCUSDT")
        direction: 방향 ("LONG" | "SHORT")
        strategy_name: 전략명
        strength: 시그널 강도 (0.0 ~ 1.0)
        entry_price: 진입 가격 (다음 봉 open 체결 예상가)
        stop_loss: 손절 가격
        take_profit: 익절 가격 (None이면 동적 청산)
        atr: ATR 값 (포지션 사이징용)
        reason: 시그널 발생 이유 설명
    """

    symbol: str
    direction: str          # "LONG" | "SHORT"
    strategy_name: str
    strength: float         # 0.0 ~ 1.0
    entry_price: float
    stop_loss: float
    take_profit: Optional[float]
    atr: float              # 포지션 사이징용
    reason: str


@dataclass
class TradeRecord:
    """거래 기록 데이터 클래스.

    Attributes:
        symbol: 거래 심볼
        strategy_name: 전략명
        direction: 방향 ("LONG" | "SHORT")
        entry_time: 진입 시각
        exit_time: 청산 시각
        entry_price: 진입 가격
        exit_price: 청산 가격
        qty: 수량 (USDT 기준)
        pnl: 손익 (수수료 차감 후)
        fee: 수수료
        exit_reason: 청산 사유 ("STOP" | "TP" | "SIGNAL" | "END")
    """

    symbol: str
    strategy_name: str
    direction: str
    entry_time: object      # pd.Timestamp
    exit_time: object       # pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float              # 포지션 수량 (USDT 기준)
    pnl: float              # 수수료 차감 후 손익
    fee: float              # 납부 수수료
    exit_reason: str        # "STOP" | "TP" | "SIGNAL" | "END"


class BaseStrategy(ABC):
    """거래 전략 추상 기본 클래스.

    모든 전략은 이 클래스를 상속하고 generate_signal, get_params,
    set_params를 반드시 구현해야 한다.

    Attributes:
        name: 전략 고유 이름
    """

    name: str = "BaseStrategy"

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[Signal]:
        """현재 봉 기준으로 시그널을 생성한다.

        시그널은 현재 봉의 close가 확정된 시점에 생성되며,
        실제 체결은 다음 봉 open에서 이루어진다 (Lookahead bias 방지).

        워밍업 구간(required_warmup 봉 미만)에서는 None을 반환해야 한다.
        NaN이 포함된 지표 값이 있을 때도 None을 반환해야 한다.

        Args:
            df: 현재까지의 OHLCV + 지표 DataFrame (마지막 행 = 현재 봉).
            symbol: 심볼 (예: "BTCUSDT")

        Returns:
            Signal 객체. 시그널 없으면 None.
        """
        ...

    @abstractmethod
    def get_params(self) -> dict:
        """현재 파라미터를 딕셔너리로 반환한다.

        Returns:
            파라미터 이름-값 딕셔너리
        """
        ...

    @abstractmethod
    def set_params(self, params: dict) -> None:
        """파라미터를 업데이트한다.

        Args:
            params: 업데이트할 파라미터 딕셔너리
        """
        ...

    def required_warmup(self) -> int:
        """최소 워밍업 봉 수를 반환한다.

        지표 계산에 필요한 최소 봉 수. 이 값 미만이면 시그널을 생성하지 않는다.

        Returns:
            최소 워밍업 봉 수 (기본 60)
        """
        return 60


__all__ = ["Signal", "TradeRecord", "BaseStrategy"]
