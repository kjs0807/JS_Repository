"""대시보드 탭 공통 베이스 클래스."""
import tkinter as tk
from tkinter import ttk
from typing import Optional
from dashboard.theme import Colors, Fonts


class BaseDirtyTab(tk.Frame):
    """dirty flag + set_db 패턴을 제공하는 탭 베이스."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=Colors.BG, **kwargs)
        self._db = None
        self._engine = None
        self._rest_client = None
        self._dirty: bool = True

    def mark_dirty(self) -> None:
        self._dirty = True

    def is_dirty(self) -> bool:
        return self._dirty

    def set_db(self, db) -> None:
        self._db = db

    def set_engine(self, engine) -> None:
        self._engine = engine

    def set_rest_client(self, client) -> None:
        """BybitRestClient 인스턴스 설정. API 직접 조회용."""
        self._rest_client = client

    def refresh(self) -> None:
        """서브클래스에서 오버라이드. 데이터 갱신."""
        self._dirty = False

    def _build_symbol_control_bar(self, parent_frame, tf_label: str = "15m"):
        """심볼 선택 + 새로고침 버튼 공통 컨트롤바."""
        from config.settings import settings

        bar = tk.Frame(parent_frame, bg=Colors.BG)
        bar.pack(fill=tk.X, padx=8, pady=4)

        tk.Label(bar, text="심볼:", font=Fonts.SMALL, bg=Colors.BG, fg=Colors.FG).pack(side=tk.LEFT)

        combo = ttk.Combobox(bar, values=settings.symbols, state="readonly", width=12)
        combo.set(settings.symbols[0] if settings.symbols else "")
        combo.pack(side=tk.LEFT, padx=4)
        self._symbol_combo = combo

        tk.Label(bar, text=f"({tf_label})", font=Fonts.SMALL, bg=Colors.BG, fg=Colors.FG_DIM).pack(side=tk.LEFT, padx=4)

        tk.Button(
            bar, text="새로고침", font=Fonts.SMALL,
            bg=Colors.ACCENT, fg=Colors.FG,
            activebackground=Colors.ACCENT, activeforeground=Colors.FG,
            relief=tk.FLAT, cursor="hand2",
            command=self.refresh,
        ).pack(side=tk.RIGHT, padx=4)

        return combo

    def refresh_symbol_list(self) -> None:
        """심볼 유니버스 갱신 후 콤보박스 values를 재구성한다."""
        from config.settings import settings
        if hasattr(self, '_symbol_combo') and self._symbol_combo:
            self._symbol_combo["values"] = settings.symbols
