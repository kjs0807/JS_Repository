"""로깅 설정 모듈. 콘솔 + 파일(main/trades/errors) 동시 출력."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logger(log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """로깅을 설정하고 루트 로거를 반환한다.

    Args:
        log_dir: 로그 디렉토리 경로
        level: 루트 로거 레벨

    Returns:
        설정된 루트 로거
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    (log_path / "research").mkdir(exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # 1. 콘솔 (level+)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. main.log (DEBUG+)
    main_handler = logging.FileHandler(log_path / "main.log", encoding="utf-8")
    main_handler.setLevel(logging.DEBUG)
    main_handler.setFormatter(formatter)
    root_logger.addHandler(main_handler)

    # 3. errors.log (WARNING+)
    error_handler = logging.FileHandler(log_path / "errors.log", encoding="utf-8")
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)

    # 4. trades.log (trades 로거 전용)
    trades_logger = logging.getLogger("trades")
    trades_logger.setLevel(logging.DEBUG)
    trades_logger.propagate = True
    trade_handler = logging.FileHandler(log_path / "trades.log", encoding="utf-8")
    trade_handler.setLevel(logging.DEBUG)
    trade_handler.setFormatter(formatter)
    trades_logger.addHandler(trade_handler)

    return root_logger


__all__ = ["setup_logger"]
