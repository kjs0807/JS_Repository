"""core/logger.py 단위 테스트."""
import logging
from pathlib import Path
from src.core.logger import setup_logger


class TestSetupLogger:
    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logger(log_dir=str(log_dir), level="DEBUG")
        assert log_dir.exists()

    def test_returns_root_logger(self, tmp_path):
        log_dir = tmp_path / "logs"
        logger = setup_logger(log_dir=str(log_dir))
        assert isinstance(logger, logging.Logger)

    def test_creates_log_files(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logger(log_dir=str(log_dir))
        assert (log_dir / "main.log").exists()
        assert (log_dir / "trades.log").exists()
        assert (log_dir / "errors.log").exists()

    def test_log_level_setting(self, tmp_path):
        log_dir = tmp_path / "logs"
        logger = setup_logger(log_dir=str(log_dir), level="WARNING")
        assert logger.level == logging.WARNING

    def test_trade_logger_writes_to_trades_log(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logger(log_dir=str(log_dir), level="DEBUG")
        trade_logger = logging.getLogger("trades")
        trade_logger.info("[SIGNAL] test signal")
        for handler in trade_logger.handlers:
            handler.flush()
        content = (log_dir / "trades.log").read_text(encoding="utf-8")
        assert "[SIGNAL] test signal" in content

    def test_error_logger_filters_below_warning(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logger(log_dir=str(log_dir), level="DEBUG")
        logger = logging.getLogger("test_error_filter")
        logger.info("this is info")
        logger.warning("this is warning")
        for handler in logging.getLogger().handlers:
            handler.flush()
        content = (log_dir / "errors.log").read_text(encoding="utf-8")
        assert "this is info" not in content
        assert "this is warning" in content
