"""거래소별 장시간 정의 및 판별 유틸리티.

DESIGN.md 8.1절 기반. 모든 시간은 KST(Asia/Seoul) 기준.
crosses_midnight=True인 세션은 close_time이 다음 날 새벽에 해당한다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


@dataclass
class TradingSession:
    """단일 거래 세션 정보.

    Attributes:
        open_time: 세션 시작 시각 (KST)
        close_time: 세션 종료 시각 (KST)
        crosses_midnight: True이면 close_time이 다음 날임
    """
    open_time: dt_time    # KST
    close_time: dt_time   # KST
    crosses_midnight: bool  # True if close is next calendar day


# DESIGN.md 8.1절 거래소별 장시간 (KST)
EXCHANGE_HOURS: Dict[str, List[TradingSession]] = {
    "EUREX": [
        TradingSession(dt_time(16, 0), dt_time(6, 0), crosses_midnight=True),
    ],
    "OSE": [
        TradingSession(dt_time(8, 45), dt_time(15, 30), crosses_midnight=False),
        TradingSession(dt_time(16, 30), dt_time(6, 0), crosses_midnight=True),
    ],
    "HKEx": [
        TradingSession(dt_time(10, 15), dt_time(12, 0), crosses_midnight=False),
        TradingSession(dt_time(13, 0), dt_time(16, 15), crosses_midnight=False),
        TradingSession(dt_time(17, 0), dt_time(1, 0), crosses_midnight=True),
    ],
    "ASX": [
        TradingSession(dt_time(7, 10), dt_time(18, 30), crosses_midnight=False),
        TradingSession(dt_time(18, 40), dt_time(6, 0), crosses_midnight=True),
    ],
    "FTX": [
        TradingSession(dt_time(8, 45), dt_time(13, 45), crosses_midnight=False),
        TradingSession(dt_time(15, 0), dt_time(5, 0), crosses_midnight=True),
    ],
}


def _session_contains(session: TradingSession, now_kst: datetime) -> bool:
    """주어진 KST datetime이 세션 내에 있는지 판별.

    crosses_midnight=True인 세션은 두 가지 경우로 처리:
    - 당일 open_time <= now: 다음 날 close_time까지 포함
    - 전날 open_time <= now: 당일 close_time까지 포함 (현재 시각이 새벽인 경우)

    Args:
        session: 판별할 세션
        now_kst: 현재 KST datetime (timezone-aware)

    Returns:
        세션 내에 있으면 True
    """
    now_time = now_kst.time()

    if not session.crosses_midnight:
        return session.open_time <= now_time < session.close_time
    else:
        # 자정 넘기는 세션:
        # 경우 1: 지금이 open_time 이후 (당일 저녁)
        if now_time >= session.open_time:
            return True
        # 경우 2: 지금이 close_time 이전 (다음날 새벽)
        if now_time < session.close_time:
            return True
        return False


def is_exchange_open(
    exchange: str,
    now: Optional[datetime] = None,
) -> bool:
    """거래소가 현재 열려 있는지 판별.

    Args:
        exchange: 거래소 코드 (예: "EUREX", "OSE", "HKEx", "ASX", "FTX")
        now: 기준 시각. None이면 현재 시각 사용 (KST로 변환).

    Returns:
        장이 열려 있으면 True. 알 수 없는 거래소는 False.
    """
    sessions = EXCHANGE_HOURS.get(exchange)
    if not sessions:
        logger.warning("알 수 없는 거래소: %s", exchange)
        return False

    if now is None:
        now = datetime.now(KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)

    return any(_session_contains(s, now) for s in sessions)


def get_open_exchanges(now: Optional[datetime] = None) -> List[str]:
    """현재 열려 있는 거래소 목록 반환.

    Args:
        now: 기준 시각. None이면 현재 시각 사용.

    Returns:
        열린 거래소 코드 리스트 (정렬됨).
    """
    if now is None:
        now = datetime.now(KST)

    return sorted(
        exc for exc in EXCHANGE_HOURS if is_exchange_open(exc, now)
    )


def get_next_open_time(
    exchange: str,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """거래소의 다음 개장 시각 반환 (KST timezone-aware).

    현재 열려 있으면 None 반환.
    닫혀 있으면 오늘 또는 내일 중 가장 가까운 개장 시각을 반환.

    Args:
        exchange: 거래소 코드
        now: 기준 시각. None이면 현재 시각 사용.

    Returns:
        다음 개장 KST datetime. 이미 열려 있거나 거래소 미확인이면 None.
    """
    sessions = EXCHANGE_HOURS.get(exchange)
    if not sessions:
        return None

    if now is None:
        now = datetime.now(KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)

    if is_exchange_open(exchange, now):
        return None

    now_time = now.time()
    today = now.date()

    candidates: List[datetime] = []
    for session in sessions:
        # 오늘 개장 시각 후보
        candidate_today = datetime(
            today.year, today.month, today.day,
            session.open_time.hour, session.open_time.minute,
            tzinfo=KST,
        )
        if candidate_today > now:
            candidates.append(candidate_today)

        # 내일 개장 시각 후보 (오늘 개장 시각이 이미 지난 경우)
        tomorrow = today + timedelta(days=1)
        candidate_tomorrow = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day,
            session.open_time.hour, session.open_time.minute,
            tzinfo=KST,
        )
        candidates.append(candidate_tomorrow)

    if not candidates:
        return None

    return min(candidates)
