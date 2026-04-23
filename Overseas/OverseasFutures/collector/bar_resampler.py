"""틱 데이터 → OHLCV 봉 리샘플러.

멀티 거래소 지원. 봉 경계는 UTC 기준 timeframe 단위로 계산.
완성된 봉은 ohlcv_intraday 테이블에 저장하고 콜백을 호출한다.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, Optional, Tuple

from config import DB_PATH
from db.init_db import get_db_connection

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """완성 또는 집계 중인 OHLCV 봉 + VWAP.

    Attributes:
        symbol: 루트 심볼 (예: "VG")
        open: 시가
        high: 고가
        low: 저가
        close: 현재 종가 (집계 중) 또는 최종 종가 (완성)
        volume: 거래량 누적
        start_time: 봉 시작 시각 (UTC)
        end_time: 봉 종료 시각 (UTC, exclusive)
        timeframe: 봉 단위 문자열 (예: "60m")
        is_complete: 봉 완성 여부
        vwap: Volume Weighted Average Price
        _cum_pv: 누적 price*volume (VWAP 계산용, 내부)
        _cum_vol: 누적 volume (VWAP 계산용, 내부)
    """
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    start_time: datetime
    end_time: datetime
    timeframe: str
    is_complete: bool = False
    vwap: float = 0.0
    _cum_pv: float = field(default=0.0, repr=False)
    _cum_vol: int = field(default=0, repr=False)


class BarResampler:
    """틱 → 분봉 리샘플링 (멀티 거래소).

    on_tick()으로 틱을 입력받아 timeframe 단위 봉을 집계한다.
    봉 경계를 넘으면 이전 봉을 완성 처리하고 콜백을 호출한다.

    Attributes:
        symbol: 루트 심볼
        timeframe_minutes: 봉 단위 (분)
        on_bar_complete: 봉 완성 시 호출될 콜백 callback(bar: Bar)
        _current_bar: 현재 집계 중인 봉
        _tick_count: 수신된 틱 수 (디버그용)
    """

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int = 60,
        on_bar_complete: Optional[Callable[["Bar"], None]] = None,
    ) -> None:
        """
        Args:
            symbol: 루트 심볼 (예: "VG")
            timeframe_minutes: 봉 단위 분 (1, 5, 15, 60 등)
            on_bar_complete: 봉 완성 시 콜백. signature: callback(bar: Bar) -> None
        """
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.on_bar_complete = on_bar_complete
        self._current_bar: Optional[Bar] = None
        self._tick_count: int = 0

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────

    def _get_bar_boundary(
        self, timestamp: datetime
    ) -> Tuple[datetime, datetime]:
        """타임스탬프의 봉 시작/종료 시각 계산.

        epoch 기준으로 timeframe_minutes 단위로 내림하여 봉 시작 시각을 구한다.

        Args:
            timestamp: 틱 수신 시각 (timezone-aware 권장)

        Returns:
            (start_time, end_time) 튜플. 모두 UTC timezone-aware datetime.
        """
        # UTC로 정규화
        if timestamp.tzinfo is None:
            ts = timestamp.replace(tzinfo=timezone.utc)
        else:
            ts = timestamp.astimezone(timezone.utc)

        tf_secs = self.timeframe_minutes * 60
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        elapsed = int((ts - epoch).total_seconds())
        bar_start_secs = (elapsed // tf_secs) * tf_secs

        start = epoch + timedelta(seconds=bar_start_secs)
        end = start + timedelta(seconds=tf_secs)
        return start, end

    def _complete_bar(self, bar: Bar) -> None:
        """봉을 완성 처리하고 콜백을 호출한다.

        Args:
            bar: 완성할 Bar 인스턴스 (is_complete=True로 설정됨)
        """
        bar.is_complete = True
        logger.debug(
            "봉 완성: %s %s O=%.4f H=%.4f L=%.4f C=%.4f V=%d",
            bar.symbol, bar.start_time.isoformat(),
            bar.open, bar.high, bar.low, bar.close, bar.volume,
        )
        if self.on_bar_complete is not None:
            try:
                self.on_bar_complete(bar)
            except Exception as exc:
                logger.error("on_bar_complete 콜백 예외 [%s]: %s", self.symbol, exc)

    # ── 공개 메서드 ──────────────────────────────────────────────────────

    def on_tick(
        self,
        price: float,
        volume: int,
        timestamp: datetime,
    ) -> Optional[Bar]:
        """새 틱 수신 → 봉 업데이트.

        봉 경계를 넘으면 이전 봉을 완성하고 새 봉을 시작한다.

        Args:
            price: 현재가
            volume: 거래량 (틱 단위)
            timestamp: 틱 수신 시각

        Returns:
            봉이 완성된 경우 완성된 Bar. 아직 집계 중이면 None.
        """
        if price is None:
            return None

        self._tick_count += 1
        start, end = self._get_bar_boundary(timestamp)

        completed_bar: Optional[Bar] = None

        vol = volume or 0
        pv = price * vol

        if self._current_bar is None:
            # 첫 틱 — 새 봉 시작
            self._current_bar = Bar(
                symbol=self.symbol,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=vol,
                start_time=start,
                end_time=end,
                timeframe=f"{self.timeframe_minutes}m",
                vwap=price,
                _cum_pv=pv,
                _cum_vol=vol,
            )
        elif start != self._current_bar.start_time:
            # 봉 경계 초과 — 이전 봉 완성
            completed_bar = self._current_bar
            self._complete_bar(completed_bar)

            # 새 봉 시작
            self._current_bar = Bar(
                symbol=self.symbol,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=vol,
                start_time=start,
                end_time=end,
                timeframe=f"{self.timeframe_minutes}m",
                vwap=price,
                _cum_pv=pv,
                _cum_vol=vol,
            )
        else:
            # 같은 봉 내 집계
            bar = self._current_bar
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.volume += vol
            bar._cum_pv += pv
            bar._cum_vol += vol
            bar.vwap = (
                bar._cum_pv / bar._cum_vol if bar._cum_vol > 0 else price
            )

        return completed_bar

    def force_close(self, timestamp: datetime) -> Optional[Bar]:
        """현재 봉 강제 완성 (장 종료 시 호출).

        Args:
            timestamp: 강제 완성 시각 (end_time 갱신에 사용)

        Returns:
            강제 완성된 Bar. 집계 중인 봉이 없으면 None.
        """
        if self._current_bar is None:
            return None

        bar = self._current_bar
        bar.end_time = timestamp if timestamp.tzinfo else timestamp.replace(
            tzinfo=timezone.utc
        )
        self._complete_bar(bar)
        self._current_bar = None
        return bar

    def get_building_bar(self) -> Optional[Bar]:
        """현재 집계 중인 봉 반환 (사본 아님, 직접 참조).

        Returns:
            집계 중인 Bar. 없으면 None.
        """
        return self._current_bar

    def save_bar_to_db(self, bar: Bar, db_path: str = None) -> None:
        """완성 봉 → ohlcv_intraday 테이블 저장.

        Args:
            bar: 저장할 Bar 인스턴스
            db_path: SQLite DB 경로. None이면 config.DB_PATH 사용.
        """
        path = db_path or DB_PATH
        sql = """
            INSERT OR REPLACE INTO ohlcv_intraday
                (symbol, datetime, timeframe, open, high, low, close, volume)
            VALUES
                (:symbol, :datetime, :timeframe, :open, :high, :low, :close, :volume)
        """
        row = {
            "symbol":    bar.symbol,
            "datetime":  bar.start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeframe": bar.timeframe,
            "open":      bar.open,
            "high":      bar.high,
            "low":       bar.low,
            "close":     bar.close,
            "volume":    bar.volume,
        }
        conn = get_db_connection(path)
        try:
            conn.execute(sql, row)
            conn.commit()
        except Exception as exc:
            logger.error("봉 저장 실패 [%s %s]: %s", bar.symbol, bar.start_time, exc)
        finally:
            conn.close()

    def restore(self, bar_data: dict) -> None:
        """직렬화된 상태에서 봉 복원.

        Args:
            bar_data: to_dict()가 반환한 dict
        """
        if not bar_data:
            self._current_bar = None
            return

        def _parse_dt(s: str) -> datetime:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) \
                if s and "+" not in s and "Z" not in s \
                else datetime.fromisoformat(s.replace("Z", "+00:00"))

        self._current_bar = Bar(
            symbol=bar_data.get("symbol", self.symbol),
            open=float(bar_data["open"]),
            high=float(bar_data["high"]),
            low=float(bar_data["low"]),
            close=float(bar_data["close"]),
            volume=int(bar_data.get("volume", 0)),
            start_time=_parse_dt(bar_data["start_time"]),
            end_time=_parse_dt(bar_data["end_time"]),
            timeframe=bar_data.get("timeframe", f"{self.timeframe_minutes}m"),
            is_complete=False,
            vwap=float(bar_data.get("vwap", bar_data["close"])),
            _cum_pv=float(bar_data.get("_cum_pv", 0.0)),
            _cum_vol=int(bar_data.get("_cum_vol", 0)),
        )
        logger.info("봉 상태 복원: %s %s", self.symbol, self._current_bar.start_time)

    def to_dict(self) -> Optional[dict]:
        """현재 집계 중인 봉 상태를 직렬화.

        Returns:
            봉 상태 dict. 집계 중인 봉이 없으면 None.
        """
        if self._current_bar is None:
            return None
        bar = self._current_bar
        return {
            "symbol":     bar.symbol,
            "open":       bar.open,
            "high":       bar.high,
            "low":        bar.low,
            "close":      bar.close,
            "volume":     bar.volume,
            "start_time": bar.start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time":   bar.end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeframe":  bar.timeframe,
            "vwap":       bar.vwap,
            "_cum_pv":    bar._cum_pv,
            "_cum_vol":   bar._cum_vol,
        }
