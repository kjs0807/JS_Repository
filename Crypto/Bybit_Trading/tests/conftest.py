"""공통 테스트 fixture."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# src/ 패키지를 import 가능하도록 프로젝트 루트를 path에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """임시 SQLite DB 경로를 반환한다."""
    return str(tmp_path / "test.db")


@pytest.fixture
def sample_bar_data() -> list[dict]:
    """테스트용 OHLCV 봉 데이터 10개를 반환한다."""
    base_ts = 1700000000000  # 2023-11-14 approx
    bars = []
    for i in range(10):
        bars.append({
            "symbol": "BTCUSDT",
            "open_time": base_ts + i * 3600000,  # 1시간 간격
            "open": 40000.0 + i * 100,
            "high": 40150.0 + i * 100,
            "low": 39900.0 + i * 100,
            "close": 40050.0 + i * 100,
            "volume": 1000.0 + i * 10,
            "turnover": 40000000.0,
        })
    return bars


@pytest.fixture
def schema_path() -> str:
    """schema.sql 파일 경로를 반환한다."""
    return str(PROJECT_ROOT / "db" / "schema.sql")
