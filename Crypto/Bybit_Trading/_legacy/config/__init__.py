"""config 패키지 -- 경로 상수 및 설정 공개 인터페이스."""

import sys
from pathlib import Path

# 프로젝트 루트 (Bybit_Trading/)
if getattr(sys, "frozen", False):
    # PyInstaller exe: exe가 있는 폴더를 프로젝트 루트로 사용
    BASE_DIR: Path = Path(sys.executable).resolve().parent
    # 번들 내부 경로 (sys._MEIPASS): .env, schema.sql 등 번들된 파일 참조
    _BUNDLE_DIR: Path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    _BUNDLE_DIR: Path = BASE_DIR

# 주요 경로 상수
DB_PATH: str = str(BASE_DIR / "db" / "bybit_data.db")
LOGS_DIR: str = str(BASE_DIR / "logs")
SCHEMA_FILE: str = str(_BUNDLE_DIR / "db" / "schema.sql")

# .env: exe 옆에 있으면 우선, 없으면 번들 내부 참조
_env_beside_exe = BASE_DIR / ".env"
_env_in_bundle = _BUNDLE_DIR / ".env"
ENV_FILE: str = str(_env_beside_exe if _env_beside_exe.exists() else _env_in_bundle)

__all__ = ["BASE_DIR", "DB_PATH", "LOGS_DIR", "ENV_FILE", "SCHEMA_FILE"]
