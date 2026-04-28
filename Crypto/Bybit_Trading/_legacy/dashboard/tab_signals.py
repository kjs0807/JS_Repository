"""대시보드 Tab 7: 시그널 로그 탭.

DB signal_log 테이블에서 최근 50건 시그널을 조회하여 표시한다.
이벤트 구동: mark_dirty() 호출 시 dirty 플래그 세움.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Optional

from dashboard.base_tab import BaseDirtyTab
from dashboard.theme import Colors, Fonts

logger = logging.getLogger(__name__)

SIGNAL_COLUMNS = [
    ("시각", 140),
    ("전략", 120),
    ("심볼", 90),
    ("방향", 60),
    ("강도", 60),
    ("진입가", 90),
    ("스톱", 90),
    ("TP", 90),
    ("ATR", 70),
    ("사유", 200),
]


class SignalLogTab(BaseDirtyTab):
    """시그널 로그 탭.

    DB signal_log 테이블에서 최근 50건을 Treeview에 표시한다.
    """

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        """SignalLogTab 초기화.

        Args:
            parent: 부모 위젯
            **kwargs: Frame 추가 인수
        """
        super().__init__(parent, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        """UI 구성."""
        # 상단: 제목 + 새로고침
        top = tk.Frame(self, bg=Colors.BG)
        top.pack(fill=tk.X, padx=8, pady=4)

        tk.Label(
            top, text="시그널 로그", font=Fonts.TITLE,
            bg=Colors.BG, fg=Colors.FG,
        ).pack(side=tk.LEFT)

        tk.Button(
            top, text="새로고침", font=Fonts.SMALL,
            bg=Colors.ACCENT, fg=Colors.FG,
            activebackground=Colors.ACCENT,
            activeforeground=Colors.FG,
            relief=tk.FLAT, cursor="hand2",
            command=self.refresh,
        ).pack(side=tk.RIGHT, padx=4)

        # 시그널 테이블
        tree_frame = tk.Frame(self, bg=Colors.BG)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        style = ttk.Style()
        style.configure(
            "SigLog.Treeview",
            background=Colors.BG_CARD,
            foreground=Colors.FG,
            fieldbackground=Colors.BG_CARD,
            rowheight=22,
            font=Fonts.MONO_SMALL,
        )
        style.configure(
            "SigLog.Treeview.Heading",
            background=Colors.ACCENT,
            foreground=Colors.FG,
            font=Fonts.MONO_SMALL,
        )

        cols = [c[0] for c in SIGNAL_COLUMNS]
        self._tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings", height=20,
            style="SigLog.Treeview",
        )
        for name, width in SIGNAL_COLUMNS:
            self._tree.heading(name, text=name)
            self._tree.column(name, width=width, minwidth=40)

        scrollbar = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self._tree.yview,
        )
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 색상 태그
        self._tree.tag_configure("long", foreground=Colors.PROFIT)
        self._tree.tag_configure("short", foreground=Colors.LOSS)
        self._tree.tag_configure("exit", foreground=Colors.WARNING)

    # ── 이벤트 구동 ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """DB에서 최근 시그널 50건 조회하여 테이블 갱신."""
        self._dirty = False

        # 기존 행 삭제
        for item in self._tree.get_children():
            self._tree.delete(item)

        if self._db is None:
            return

        try:
            signals = self._db.get_recent_signals(50)
            for sig in signals:
                direction = sig.get("direction", "")
                if direction == "LONG":
                    tag = "long"
                elif direction == "SHORT":
                    tag = "short"
                else:
                    tag = "exit"

                self._tree.insert("", tk.END, values=(
                    sig.get("timestamp", ""),
                    sig.get("strategy", ""),
                    sig.get("symbol", ""),
                    direction,
                    (
                        f"{sig.get('signal_strength', 0):.2f}"
                        if sig.get("signal_strength") else ""
                    ),
                    (
                        f"{sig.get('entry_price', 0):,.2f}"
                        if sig.get("entry_price") else ""
                    ),
                    (
                        f"{sig.get('stop_loss', 0):,.2f}"
                        if sig.get("stop_loss") else ""
                    ),
                    (
                        f"{sig.get('take_profit', 0):,.2f}"
                        if sig.get("take_profit") else ""
                    ),
                    (
                        f"{sig.get('atr', 0):,.2f}"
                        if sig.get("atr") else ""
                    ),
                    str(sig.get("reason", ""))[:60],
                ), tags=(tag,))
        except Exception as exc:
            logger.warning("시그널 로그 조회 실패: %s", exc)

