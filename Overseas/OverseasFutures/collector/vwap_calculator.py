"""일중 VWAP / TWAP / VWAP SD Band 계산기.

KTB_VWAP 전략의 지표 계산 로직을 해외선물에 맞게 구현.
- VWAP: Typical Price(H+L+C)/3 x Volume 누적, 일간 리셋
- TWAP: Volume=0 구간 fallback (expanding mean of close)
- VWAP SD: Volume-weighted 표준편차 (rolling window)

Usage:
    calc = IntradayVWAPCalculator("VG")
    calc.on_bar(bar)  # 1분봉 입력
    print(calc.vwap, calc.twap, calc.vwap_sd)
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class VWAPState:
    """단일 심볼의 일중 VWAP 상태.

    Attributes:
        symbol: 루트 심볼
        date: 현재 영업일 (YYYYMMDD)
        cum_tp_vol: 누적 (TP x volume)
        cum_vol: 누적 volume
        vwap: 현재 VWAP
        twap: 현재 TWAP (volume=0 fallback)
        close_sum: close 누적합 (TWAP 계산용)
        bar_count: 입력된 봉 수
        vwap_sd: VWAP 표준편차 (volume-weighted)
        upper_1sd: VWAP + 1 SD
        lower_1sd: VWAP - 1 SD
        upper_2sd: VWAP + 2 SD
        lower_2sd: VWAP - 2 SD
        _tp_list: TP 히스토리 (SD 계산용)
        _vol_list: Volume 히스토리 (SD 계산용)
    """
    symbol: str = ""
    date: str = ""
    cum_tp_vol: float = 0.0
    cum_vol: int = 0
    vwap: float = 0.0
    twap: float = 0.0
    close_sum: float = 0.0
    bar_count: int = 0
    vwap_sd: float = 0.0
    upper_1sd: float = 0.0
    lower_1sd: float = 0.0
    upper_2sd: float = 0.0
    lower_2sd: float = 0.0
    _tp_list: List[float] = field(default_factory=list)
    _vol_list: List[int] = field(default_factory=list)


class IntradayVWAPCalculator:
    """일중 VWAP/TWAP/SD Band 계산기.

    매 봉 입력 시 VWAP 관련 지표를 갱신한다.
    일간 리셋 자동 처리 (날짜 변경 감지).

    Attributes:
        symbol: 루트 심볼
        sd_period: VWAP SD 계산 윈도우 (봉 수, 기본 20)
        state: 현재 계산 상태
    """

    def __init__(self, symbol: str, sd_period: int = 20) -> None:
        """
        Args:
            symbol: 루트 심볼 (예: "VG")
            sd_period: SD rolling window (기본 20봉)
        """
        self.symbol = symbol
        self.sd_period = sd_period
        self.state = VWAPState(symbol=symbol)

    def reset(self, new_date: str = "") -> None:
        """일간 리셋 — 새 영업일 시작.

        Args:
            new_date: 새 영업일 (YYYYMMDD)
        """
        self.state = VWAPState(symbol=self.symbol, date=new_date)
        logger.debug("[%s] VWAP 일간 리셋: %s", self.symbol, new_date)

    def on_bar(
        self,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        bar_date: str = "",
    ) -> VWAPState:
        """새 봉 입력 → VWAP/TWAP/SD 갱신.

        Args:
            open_price: 시가
            high: 고가
            low: 저가
            close: 종가
            volume: 거래량
            bar_date: 봉 날짜 YYYYMMDD (일간 리셋 감지용)

        Returns:
            갱신된 VWAPState
        """
        s = self.state

        # 일간 리셋 감지
        if bar_date and bar_date != s.date and s.date != "":
            self.reset(bar_date)
            s = self.state
        elif bar_date and s.date == "":
            s.date = bar_date

        # Typical Price = (H + L + C) / 3
        tp = (high + low + close) / 3.0

        # VWAP 누적
        vol = max(volume, 0)
        s.cum_tp_vol += tp * vol
        s.cum_vol += vol
        s.bar_count += 1

        # VWAP 계산
        if s.cum_vol > 0:
            s.vwap = s.cum_tp_vol / s.cum_vol
        else:
            s.vwap = close  # volume=0이면 close fallback

        # TWAP 계산 (expanding mean of close)
        s.close_sum += close
        s.twap = s.close_sum / s.bar_count

        # SD 히스토리 누적
        s._tp_list.append(tp)
        s._vol_list.append(vol)

        # VWAP SD Band 계산 (volume-weighted std dev)
        s.vwap_sd = self._calc_vwap_sd(s)
        s.upper_1sd = s.vwap + s.vwap_sd
        s.lower_1sd = s.vwap - s.vwap_sd
        s.upper_2sd = s.vwap + 2.0 * s.vwap_sd
        s.lower_2sd = s.vwap - 2.0 * s.vwap_sd

        # 메모리 제한: SD 계산에 필요한 범위의 2배만 보관
        if len(s._tp_list) > self.sd_period * 2:
            s._tp_list = s._tp_list[-self.sd_period:]
            s._vol_list = s._vol_list[-self.sd_period:]

        return s

    def _calc_vwap_sd(self, s: VWAPState) -> float:
        """Volume-weighted 표준편차 계산.

        공식: sigma = sqrt( sum((TP_i - VWAP)^2 * V_i) / sum(V_i) )
        최근 sd_period 봉만 사용.

        Args:
            s: 현재 VWAPState

        Returns:
            VWAP SD 값. 데이터 부족 시 0.0.
        """
        n = len(s._tp_list)
        if n < 2:
            return 0.0

        # rolling window 적용
        start = max(0, n - self.sd_period)
        tp_window = s._tp_list[start:]
        vol_window = s._vol_list[start:]

        total_vol = sum(vol_window)
        if total_vol <= 0:
            # volume 없으면 단순 std 사용
            if len(tp_window) < 2:
                return 0.0
            mean = sum(tp_window) / len(tp_window)
            variance = sum((tp - mean) ** 2 for tp in tp_window) / len(tp_window)
            return math.sqrt(variance) if variance > 0 else 0.0

        # volume-weighted variance
        weighted_var = sum(
            (tp - s.vwap) ** 2 * vol
            for tp, vol in zip(tp_window, vol_window)
        ) / total_vol

        return math.sqrt(weighted_var) if weighted_var > 0 else 0.0

    # ── 직렬화 (상태 저장/복원) ───────────────────────────────────────

    def to_dict(self) -> dict:
        """상태를 dict로 직렬화."""
        s = self.state
        return {
            "symbol": s.symbol,
            "date": s.date,
            "cum_tp_vol": s.cum_tp_vol,
            "cum_vol": s.cum_vol,
            "vwap": s.vwap,
            "twap": s.twap,
            "close_sum": s.close_sum,
            "bar_count": s.bar_count,
            "vwap_sd": s.vwap_sd,
            "_tp_list": s._tp_list[-self.sd_period:],
            "_vol_list": s._vol_list[-self.sd_period:],
        }

    def restore(self, data: dict) -> None:
        """dict에서 상태 복원."""
        if not data:
            return
        s = self.state
        s.symbol = data.get("symbol", self.symbol)
        s.date = data.get("date", "")
        s.cum_tp_vol = float(data.get("cum_tp_vol", 0.0))
        s.cum_vol = int(data.get("cum_vol", 0))
        s.vwap = float(data.get("vwap", 0.0))
        s.twap = float(data.get("twap", 0.0))
        s.close_sum = float(data.get("close_sum", 0.0))
        s.bar_count = int(data.get("bar_count", 0))
        s.vwap_sd = float(data.get("vwap_sd", 0.0))
        s._tp_list = list(data.get("_tp_list", []))
        s._vol_list = list(data.get("_vol_list", []))
        # Band 재계산
        s.upper_1sd = s.vwap + s.vwap_sd
        s.lower_1sd = s.vwap - s.vwap_sd
        s.upper_2sd = s.vwap + 2.0 * s.vwap_sd
        s.lower_2sd = s.vwap - 2.0 * s.vwap_sd
        logger.info("[%s] VWAP 상태 복원: date=%s vwap=%.4f bars=%d",
                    self.symbol, s.date, s.vwap, s.bar_count)

    # ── 프로퍼티 ─────────────────────────────────────────────────────

    @property
    def vwap(self) -> float:
        return self.state.vwap

    @property
    def twap(self) -> float:
        return self.state.twap

    @property
    def vwap_sd(self) -> float:
        return self.state.vwap_sd

    @property
    def effective_vwap(self) -> float:
        """유효 VWAP — volume 있으면 VWAP, 없으면 TWAP fallback."""
        if self.state.cum_vol > 0:
            return self.state.vwap
        return self.state.twap
