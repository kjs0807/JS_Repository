"""OverseasFutures Configuration."""

import os

# 프로젝트 루트 경로
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DB 경로
DB_PATH = os.path.join(PROJECT_ROOT, "db", "futures.db")

# 로그 경로
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

# 상태 저장 경로
STATE_FILE = os.path.join(LOGS_DIR, "state.json")

# .env 경로
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

# 폴링 간격 (ms)
POLL_INTERVAL_MS = 30_000
