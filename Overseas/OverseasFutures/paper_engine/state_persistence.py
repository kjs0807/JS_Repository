"""상태 영속성 — 앱 재시작 시 가상 매매 상태 완전 복원."""

from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Any, Dict, Optional

from paper_engine.virtual_account import VirtualAccount
from paper_engine.order_manager import OrderManager
from paper_engine.position_tracker import PositionTracker

logger = logging.getLogger(__name__)


class StatePersistence:
    """가상 매매 엔진 상태 저장/복원.

    원자적 쓰기(임시 파일 → rename)와 백업 파일을 통해
    앱 비정상 종료 시에도 상태를 안전하게 복원한다.

    Attributes:
        state_file: 메인 상태 파일 경로 (JSON)
        backup_file: 백업 상태 파일 경로
    """

    def __init__(self, state_file: str) -> None:
        self.state_file = state_file
        self.backup_file = state_file + ".bak"

    # ── 저장 ─────────────────────────────────────────────────────

    def save(
        self,
        account: VirtualAccount,
        order_manager: OrderManager,
        position_tracker: PositionTracker,
        fsm_states: Dict[str, str],
        bar_states: Dict[str, dict],
        extra: Optional[dict] = None,
    ) -> None:
        """전체 엔진 상태를 JSON 파일로 저장.

        원자적 쓰기를 위해 임시 파일에 먼저 기록 후 rename한다.
        기존 파일이 있으면 백업으로 복사한다.

        Args:
            account: VirtualAccount 인스턴스
            order_manager: OrderManager 인스턴스
            position_tracker: PositionTracker 인스턴스
            fsm_states: {symbol: fsm_state_name} 딕셔너리
            bar_states: {symbol: bar_state_dict} 딕셔너리
            extra: 추가로 저장할 임의 데이터 (선택)
        """
        state: Dict[str, Any] = {
            "account": account.to_dict(),
            "order_manager": order_manager.to_dict(),
            "position_tracker": position_tracker.to_dict(),
            "fsm_states": dict(fsm_states),
            "bar_states": dict(bar_states),
        }
        if extra:
            state["extra"] = extra

        os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)

        # 기존 파일 백업
        if os.path.exists(self.state_file):
            self._create_backup()

        # 임시 파일에 쓰고 원자적 rename
        tmp_file = self.state_file + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, self.state_file)
            logger.debug("State saved to %s", self.state_file)
        except Exception:
            # 임시 파일 정리
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
            raise

    # ── 복원 ─────────────────────────────────────────────────────

    def load(self) -> Optional[dict]:
        """저장된 상태를 딕셔너리로 반환.

        메인 파일 읽기 실패 시 백업 파일로 재시도한다.

        Returns:
            상태 딕셔너리. 저장 파일이 없거나 읽기 실패 시 None.
        """
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug("State loaded from %s", self.state_file)
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "Failed to load state from %s: %s. Trying backup...",
                    self.state_file, e,
                )
                return self._restore_from_backup()

        if os.path.exists(self.backup_file):
            logger.info("Main state file not found, loading from backup.")
            return self._restore_from_backup()

        return None

    def restore_objects(
        self, state: dict
    ) -> tuple[VirtualAccount, OrderManager, PositionTracker, dict, dict, dict]:
        """load()로 읽은 딕셔너리에서 엔진 객체 전체 복원.

        Args:
            state: load()가 반환한 상태 딕셔너리

        Returns:
            (account, order_manager, position_tracker, fsm_states, bar_states, extra) 튜플.
        """
        account = VirtualAccount.from_dict(state["account"])
        order_manager = OrderManager.from_dict(state["order_manager"])
        position_tracker = PositionTracker.from_dict(state["position_tracker"])

        # 포지션 참조를 account에 연결
        account.positions = position_tracker.positions

        fsm_states: Dict[str, str] = state.get("fsm_states", {})
        bar_states: Dict[str, dict] = state.get("bar_states", {})
        extra: dict = state.get("extra", {})

        return account, order_manager, position_tracker, fsm_states, bar_states, extra

    # ── 내부 헬퍼 ─────────────────────────────────────────────────

    def _create_backup(self) -> None:
        """메인 상태 파일을 백업 파일로 복사."""
        try:
            shutil.copy2(self.state_file, self.backup_file)
            logger.debug("Backup created: %s", self.backup_file)
        except OSError as e:
            logger.warning("Failed to create backup: %s", e)

    def _restore_from_backup(self) -> Optional[dict]:
        """백업 파일에서 상태 복원.

        Returns:
            상태 딕셔너리. 백업 파일이 없거나 읽기 실패 시 None.
        """
        if not os.path.exists(self.backup_file):
            return None
        try:
            with open(self.backup_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("State restored from backup: %s", self.backup_file)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to restore from backup: %s", e)
            return None
