"""대시보드 Tab 5: PairsTrading 탭 (15m).

Top 3 페어(SOL-ETC, DOT-TIA, ADA-LINK)의 Z-Score 차트 +
전체 유효 페어 테이블.
이벤트 구동: mark_dirty() 호출 시 dirty 플래그 세움.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
import tkinter as tk
from tkinter import ttk
import numpy as np

from dashboard.base_tab import BaseDirtyTab
from dashboard.theme import Colors, Fonts
from dashboard.chart_utils import (
    make_dark_figure,
    apply_dark_axes,
    embed_figure,
)

logger = logging.getLogger(__name__)

# 페어는 엔진에서 동적으로 가져옴. 기본값은 빈 리스트.
_TOP3_PAIRS: list = []
_PAIR_LABELS: list = []

# Z-Score 임계값
_ENTRY_Z = 1.75
_EXIT_Z = 0.0


class PairsTab(BaseDirtyTab):
    """PairsTrading 탭 - Top 3 Z-Score 차트 + 전체 페어 테이블.

    구성:
        - 상단: Top 3 페어 Z-Score 차트 (3개 subplot)
        - 하단: 전체 유효 페어 테이블 (페어명, Z-Score, 상태)
        - [새로고침] 버튼
    """

    def __init__(self, parent: tk.Widget) -> None:
        """초기화.

        Args:
            parent: 부모 위젯
        """
        super().__init__(parent)
        self._fig: Optional[Any] = None
        self._canvas: Optional[Any] = None

        self._build_ui()

    # ── UI 구성 ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """전체 UI 구성."""
        self._build_control_bar()
        self._build_chart_area()
        self._build_pairs_table()

    def _build_control_bar(self) -> None:
        """상단 컨트롤 바."""
        bar = tk.Frame(self, bg=Colors.BG_CARD, pady=6)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(
            bar,
            text="PairsTrading - Top 3 페어 Z-Score (15m)",
            font=Fonts.BODY_BOLD,
            bg=Colors.BG_CARD,
            fg=Colors.FG,
        ).pack(side=tk.LEFT, padx=12)

        tk.Button(
            bar, text="새로고침",
            font=Fonts.MONO_SMALL,
            bg=Colors.BTN_GRAY, fg=Colors.FG,
            activebackground=Colors.ACCENT, activeforeground=Colors.FG,
            relief=tk.FLAT, cursor="hand2",
            command=self.refresh,
        ).pack(side=tk.RIGHT, padx=12)

    def _build_chart_area(self) -> None:
        """Top 3 페어 Z-Score 차트 (3개 subplot)."""
        chart_frame = tk.Frame(self, bg=Colors.BG)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._fig = make_dark_figure(figsize=(12, 6), dpi=96)
        axes = self._fig.subplots(1, 3)
        for ax in axes:
            apply_dark_axes(ax)
            ax.set_title("--", color=Colors.FG_DIM, fontsize=9)

        self._fig.tight_layout(pad=1.5)
        self._canvas = embed_figure(self._fig, chart_frame)

    def _build_pairs_table(self) -> None:
        """하단 전체 페어 테이블."""
        frame = tk.Frame(self, bg=Colors.BG)
        frame.pack(fill=tk.X, padx=8, pady=(4, 8))

        tk.Label(
            frame,
            text="유효 페어 현황",
            font=Fonts.BODY_BOLD,
            bg=Colors.BG,
            fg=Colors.FG,
        ).pack(anchor="w", padx=4, pady=(0, 4))

        style = ttk.Style()
        style.configure(
            "Pairs.Treeview",
            background=Colors.BG_CARD,
            foreground=Colors.FG,
            fieldbackground=Colors.BG_CARD,
            rowheight=20,
            font=Fonts.MONO_SMALL,
        )
        style.configure(
            "Pairs.Treeview.Heading",
            background=Colors.ACCENT,
            foreground=Colors.FG,
            font=Fonts.MONO_SMALL,
        )
        style.map("Pairs.Treeview", background=[("selected", Colors.ACCENT)])

        cols = ("pair", "zscore", "coint", "cooldown", "status")
        self._pairs_tree = ttk.Treeview(
            frame,
            columns=cols,
            show="headings",
            height=4,
            style="Pairs.Treeview",
        )
        self._pairs_tree.heading("pair", text="페어")
        self._pairs_tree.heading("zscore", text="Z-Score")
        self._pairs_tree.heading("coint", text="공적분")
        self._pairs_tree.heading("cooldown", text="쿨다운")
        self._pairs_tree.heading("status", text="상태")

        self._pairs_tree.column("pair", width=120, anchor="w")
        self._pairs_tree.column("zscore", width=80, anchor="e")
        self._pairs_tree.column("coint", width=70, anchor="center")
        self._pairs_tree.column("cooldown", width=70, anchor="center")
        self._pairs_tree.column("status", width=90, anchor="center")

        self._pairs_tree.pack(fill=tk.X)

        self._pairs_tree.tag_configure("long", foreground=Colors.PROFIT)
        self._pairs_tree.tag_configure("short", foreground=Colors.LOSS)
        self._pairs_tree.tag_configure("neutral", foreground=Colors.FG_DIM)

    # ── 이벤트 구동 ──────────────────────────────────────────────────────

    def _get_active_pairs(self) -> list:
        """엔진에서 동적 페어를 가져온다. 없으면 빈 리스트."""
        if self._engine is not None:
            try:
                pairs = self._engine._pairs_strategy.pairs
                return pairs[:10]  # 최대 10페어
            except Exception:
                pass
        return _TOP3_PAIRS if _TOP3_PAIRS else []

    def refresh(self) -> None:
        """DB에서 1h 봉 조회, Z-Score 계산, 차트/테이블 갱신."""
        self._dirty = False
        if self._db is None:
            self._set_no_data("DB가 연결되지 않았습니다.")
            return

        active_pairs = self._get_active_pairs()
        pair_labels = [f"{a[:3]}-{b[:3]}" for a, b in active_pairs]

        results: List[tuple] = []
        for (sym_a, sym_b), label in zip(active_pairs, pair_labels):
            try:
                df_a = self._db.get_ohlcv(sym_a, "1h", limit=500)
                df_b = self._db.get_ohlcv(sym_b, "1h", limit=500)
            except Exception as exc:
                logger.debug(
                    "Z-Score 데이터 조회 오류 %s/%s: %s", sym_a, sym_b, exc,
                )
                results.append((label, None))
                continue

            if df_a is None or df_b is None or df_a.empty or df_b.empty:
                results.append((label, None))
                continue

            try:
                from indicators.zscore import calc_pair_zscore
                zdf = calc_pair_zscore(df_a, df_b, window=250)
                results.append((label, zdf))
            except Exception as exc:
                logger.debug(
                    "Z-Score 계산 오류 %s/%s: %s", sym_a, sym_b, exc,
                )
                results.append((label, None))

        self._draw_charts(results)
        self._update_table(results)

    # ── 차트 그리기 ──────────────────────────────────────────────────────

    def _draw_charts(self, results: list) -> None:
        """Z-Score 차트를 그린다 (동적 페어 수).

        Args:
            results: [(label, zdf_or_None), ...]
        """
        self._fig.clf()
        n_plots = max(len(results), 1)
        # 최대 5개까지 가로 배치, 그 이상은 2행
        if n_plots <= 5:
            axes = self._fig.subplots(1, n_plots, squeeze=False)[0]
        else:
            rows = (n_plots + 4) // 5
            axes_2d = self._fig.subplots(rows, 5, squeeze=False)
            axes = [axes_2d[r][c] for r in range(rows) for c in range(5)]

        for i, (label, zdf) in enumerate(results):
            if i >= len(axes):
                break
            ax = axes[i]
            apply_dark_axes(ax)

            if zdf is None or zdf.empty:
                ax.text(
                    0.5, 0.5, f"{label}\n데이터 없음",
                    transform=ax.transAxes, ha="center", va="center",
                    color=Colors.FG_DIM, fontsize=9,
                )
                ax.set_title(label, color=Colors.FG_DIM, fontsize=9)
                continue

            display_n = min(100, len(zdf))
            zdf_show = zdf.iloc[-display_n:]
            x = list(range(len(zdf_show)))
            zscore_vals = zdf_show["zscore"].values

            ax.plot(
                x, zscore_vals, color=Colors.BTN_BLUE,
                linewidth=1.3, label="Z-Score",
            )

            ax.axhline(
                _ENTRY_Z, color=Colors.ZSCORE_ENTRY, linewidth=1.0,
                linestyle="--", alpha=0.8, label=f"+{_ENTRY_Z}",
            )
            ax.axhline(
                -_ENTRY_Z, color=Colors.ZSCORE_ENTRY, linewidth=1.0,
                linestyle="--", alpha=0.8, label=f"-{_ENTRY_Z}",
            )
            ax.axhline(
                _EXIT_Z, color=Colors.ZSCORE_EXIT, linewidth=0.8,
                linestyle="-", alpha=0.6, label="0",
            )

            # fill_between 진입 영역
            ax.fill_between(
                x, zscore_vals, _ENTRY_Z,
                where=(zscore_vals >= _ENTRY_Z),
                color=Colors.LOSS, alpha=0.12,
            )
            ax.fill_between(
                x, zscore_vals, -_ENTRY_Z,
                where=(zscore_vals <= -_ENTRY_Z),
                color=Colors.PROFIT, alpha=0.12,
            )

            ax.set_title(label, color=Colors.FG, fontsize=9)
            ax.set_ylabel("Z-Score", color=Colors.FG_DIM, fontsize=7)

            # 최신 Z-Score 값 표시
            last_z = zscore_vals[~np.isnan(zscore_vals)]
            if len(last_z) > 0:
                ax.text(
                    0.98, 0.95, f"Z={last_z[-1]:.2f}",
                    transform=ax.transAxes, ha="right", va="top",
                    color=Colors.FG, fontsize=9, fontweight="bold",
                )

        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

    def _update_table(self, results: list) -> None:
        """페어 테이블 갱신 (공적분/쿨다운 포함).

        Args:
            results: [(label, zdf_or_None), ...]
        """
        for item in self._pairs_tree.get_children():
            self._pairs_tree.delete(item)

        # 엔진에서 공적분/쿨다운 상태 가져오기
        coint_status = {}
        cooldowns = {}
        if self._engine is not None:
            try:
                engine_st = self._engine.get_status()
                coint_status = engine_st.get("pair_cointegration", {})
                cooldowns = engine_st.get("cooldowns", {})
            except Exception:
                pass

        active_pairs = self._get_active_pairs()
        pair_labels = [f"{a[:3]}-{b[:3]}" for a, b in active_pairs]

        for (sym_a, sym_b), label in zip(active_pairs, pair_labels):
            # 결과에서 해당 label의 zdf 찾기
            zdf = None
            for r_label, r_zdf in results:
                if r_label == label:
                    zdf = r_zdf
                    break

            # 공적분 상태
            pair_key = f"{sym_a}-{sym_b}"
            coint_valid = coint_status.get(pair_key)
            coint_text = "유효" if coint_valid else ("붕괴" if coint_valid is False else "--")

            # 쿨다운 상태
            cd_key = f"PairsTrading|{sym_a}"
            cd_remaining = cooldowns.get(cd_key, 0)
            cd_text = f"{cd_remaining}봉" if cd_remaining > 0 else "-"

            if zdf is None or zdf.empty:
                self._pairs_tree.insert(
                    "", "end",
                    values=(label, "--", coint_text, cd_text, "데이터 없음"),
                    tags=("neutral",),
                )
                continue

            zvals = zdf["zscore"].dropna()
            if zvals.empty:
                self._pairs_tree.insert(
                    "", "end",
                    values=(label, "--", coint_text, cd_text, "계산 중"),
                    tags=("neutral",),
                )
                continue

            last_z = zvals.iloc[-1]
            if last_z >= _ENTRY_Z:
                status = "SHORT 구간"
                tag = "short"
            elif last_z <= -_ENTRY_Z:
                status = "LONG 구간"
                tag = "long"
            else:
                status = "중립"
                tag = "neutral"

            self._pairs_tree.insert(
                "", "end",
                values=(label, f"{last_z:.3f}", coint_text, cd_text, status),
                tags=(tag,),
            )

    def _set_no_data(self, msg: str) -> None:
        """메시지 표시.

        Args:
            msg: 표시할 메시지
        """
        self._fig.clf()
        ax = self._fig.add_subplot(111)
        apply_dark_axes(ax)
        ax.text(
            0.5, 0.5, msg, transform=ax.transAxes,
            ha="center", va="center", color=Colors.FG_DIM, fontsize=11,
        )
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

