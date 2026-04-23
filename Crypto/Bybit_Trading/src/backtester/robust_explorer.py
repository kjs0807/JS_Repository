"""RobustExplorer — 일반화된 전략 탐색 프레임워크.

기존 explore_donchian.py의 JSONL resume, heartbeat, signal 핸들링 로직을
재사용 가능한 클래스로 추출. 각 전략별 `explore_*.py` 스크립트가 이 클래스를
재사용한다.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


logger = logging.getLogger("robust_explorer")


def params_key(params: Dict[str, Any]) -> str:
    """Canonical string representation of params dict."""
    return json.dumps(params, sort_keys=True)


def combo_key(variant: str, symbol: str, tf: str, params: Dict[str, Any]) -> str:
    """Unique key for a (variant, symbol, tf, params) combination."""
    return f"{variant}|{symbol}|{tf}|{params_key(params)}"


def load_existing_jsonl(file: Path) -> Tuple[List[Dict], Set[str]]:
    """Load existing JSONL file and return (results, done_keys)."""
    if not file.exists():
        return [], set()
    results = []
    done_keys = set()
    with open(file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            results.append(r)
            done_keys.add(combo_key(
                r.get("variant", ""),
                r.get("symbol", ""),
                r.get("tf", ""),
                r.get("params", {}),
            ))
    return results, done_keys


def append_jsonl(file: Path, record: Dict) -> None:
    """Append a single JSON record to file."""
    file.parent.mkdir(parents=True, exist_ok=True)
    with open(file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
        f.flush()


class RobustExplorer:
    """재사용 가능한 전략 탐색 오케스트레이터.

    Attributes:
        name: 탐색 이름 (예: "bbkc_squeeze")
        output_dir: 결과 디렉토리
        stop_requested: SIGINT/SIGTERM 시 True로 설정
    """

    def __init__(self, name: str, output_dir: Path) -> None:
        self.name = name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stop_requested = False
        self._heartbeat_thread: Optional[threading.Thread] = None

    def jsonl_path(self, stage: str) -> Path:
        return self.output_dir / f"{stage}_results.jsonl"

    def pid_path(self) -> Path:
        return self.output_dir / "explore.pid"

    def heartbeat_path(self) -> Path:
        return self.output_dir / "heartbeat.txt"

    def log_path(self) -> Path:
        return self.output_dir / "explore.log"

    def write_pid(self) -> None:
        self.pid_path().write_text(str(os.getpid()), encoding="utf-8")

    def remove_pid(self) -> None:
        p = self.pid_path()
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    def start_heartbeat(self, interval: float = 10.0) -> None:
        def _loop():
            while not self.stop_requested:
                try:
                    self.heartbeat_path().write_text(str(time.time()), encoding="utf-8")
                except OSError:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        self._heartbeat_thread = t

    def install_signal_handler(self) -> None:
        def _handler(signum, frame):
            logger.info(f"Signal {signum} received, requesting stop")
            self.stop_requested = True
        signal.signal(signal.SIGINT, _handler)
        try:
            signal.signal(signal.SIGTERM, _handler)
        except (AttributeError, ValueError):
            pass

    def request_stop(self) -> None:
        self.stop_requested = True

    def load_done_keys(self, stage: str) -> Set[str]:
        _, done_keys = load_existing_jsonl(self.jsonl_path(stage))
        return done_keys

    def append_result(self, stage: str, record: Dict) -> None:
        append_jsonl(self.jsonl_path(stage), record)

    def load_all_results(self, stage: str) -> List[Dict]:
        results, _ = load_existing_jsonl(self.jsonl_path(stage))
        return results


def setup_logger(log_file: Path, name: str = "robust_explorer") -> logging.Logger:
    """Setup logger with file + stderr handlers."""
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(formatter)
    log.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(formatter)
    log.addHandler(sh)

    return log


def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is alive."""
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


__all__ = [
    "RobustExplorer",
    "params_key",
    "combo_key",
    "load_existing_jsonl",
    "append_jsonl",
    "setup_logger",
    "is_process_alive",
]
