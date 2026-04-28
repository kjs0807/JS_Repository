"""대시보드 Tab 4: RSI+MACD 차트 탭 (1H).

3단 subplot: 가격 / RSI(30/70 수평선) / MACD+히스토그램.
DB에서 1H 최근 200봉 조회.
이벤트 구동: mark_dirty() 호출 시 dirty 플래그 세움.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
import tkinter as tk
from tkinter import ttk
import numpy as np

from config.settings import settings
from dashboard.base_tab import BaseDirtyTab
from dashboard.theme import Colors, Fonts
from dashboard.chart_utils import (
    make_dark_figure,
    apply_dark_axes,
    embed_figure,
    make_dark_legend,
)

logger = logging.getLogger(__name__)


class RSIMACDTab(BaseDirtyTab):
    """RSI + MACD 차트 탭 - 1시간봉.

    구성:
        - 코인 선택 드롭다운 + [새로고침]
        - ax1 (40%): 가격선
        - ax2 (25%): RSI + 과매수/과매도 라인 + fill
        - ax3 (35%): MACD line + signal line + histogram
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

    def _build_control_bar(self) -> None:
        """상단 컨트롤 바."""
        bar = tk.Frame(self, bg=Colors.BG_CARD, pady=6)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(
            bar, text="심볼:", font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD, fg=Colors.FG_DIM,
        ).pack(side=tk.LEFT, padx=(12, 4))

        self._symbol_var = tk.StringVar(value=settings.symbols[0])
        ttk.Combobox(
            bar,
            textvariable=self._symbol_var,
            values=settings.symbols,
            state="readonly",
            width=12,
            font=Fonts.MONO_SMALL,
        ).pack(side=tk.LEFT, padx=4)

        tk.Label(
            bar, text="타임프레임: 1H", font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD, fg=Colors.FG_DIM,
        ).pack(side=tk.LEFT, padx=16)

        tk.Button(
            bar, text="새로고침",
            font=Fonts.MONO_SMALL,
            bg=Colors.BTN_GRAY, fg=Colors.FG,
            activebackground=Colors.ACCENT, activeforeground=Colors.FG,
            relief=tk.FLAT, cursor="hand2",
            command=self.refresh,
        ).pack(side=tk.RIGHT, padx=12)

    def _build_chart_area(self) -> None:
        """matplotlib 차트 영역 (3개 subplot)."""
        chart_frame = tk.Frame(self, bg=Colors.BG)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._fig = make_dark_figure(figsize=(12, 7), dpi=96)
        axes = self._fig.subplots(
            3, 1,
            gridspec_kw={"height_ratios": [4, 2.5, 3.5]},
            sharex=True,
        )
        for ax in axes:
            apply_dark_axes(ax)
        axes[0].set_title(
            "RSI + MACD - 데이터를 불러오려면 [새로고침]을 누르세요",
            color=Colors.FG_DIM, fontsize=9,
        )
        self._fig.tight_layout(pad=1.2)
        self._canvas = embed_figure(self._fig, chart_frame)

    # ── 이벤트 구동 ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """DB에서 1H 봉 조회, RSI/MACD 계산, 차트 갱신."""
        self._dirty = False
        if self._db is None:
            self._set_no_data("DB가 연결되지 않았습니다.")
            return

        symbol = self._symbol_var.get()
        try:
            df = self._db.get_ohlcv(symbol, "60", limit=200)
        except Exception as exc:
            self._set_no_data(f"데이터 조회 오류: {exc}")
            return

        if df is None or df.empty or len(df) < 40:
            self._set_no_data(f"{symbol} 1H 데이터 부족")
            return

        try:
            from indicators.rsi import calc_rsi
            from indicators.macd import calc_macd
            df = calc_rsi(df, period=14)
            df = calc_macd(df, fast=12, slow=26, signal=9)
        except Exception as exc:
            self._set_no_data(f"지표 계산 오류: {exc}")
            return

        self._draw_chart(df, symbol)

    # ── 차트 그리기 ──────────────────────────────────────────────────────

    def _draw_chart(self, df: Any, symbol: str) -> None:
        """RSI + MACD 3단 차트를 그린다.

        Args:
            df: RSI/MACD 컬럼이 포함된 DataFrame
            symbol: 심볼명
        """
        self._fig.clf()
        axes = self._fig.subplots(
            3, 1,
            gridspec_kw={"height_ratios": [4, 2.5, 3.5]},
            sharex=True,
        )
        ax1, ax2, ax3 = axes
        for ax in axes:
            apply_dark_axes(ax)

        display_n = min(100, len(df))
        df_show = df.iloc[-display_n:]
        x = list(range(len(df_show)))

        close = df_show["close"].values
        rsi = df_show.get("rsi", None)
        macd = df_show.get("macd_line", None)
        sig = df_show.get("signal_line", None)
        hist = df_show.get("histogram", None)

        # ax1: 가격선
        ax1.plot(
            x, close, color=Colors.PRICE_LINE,
            linewidth=1.4, label="가격",
        )
        ax1.set_title(
            f"RSI + MACD - {symbol} (1H)",
            color=Colors.FG, fontsize=10,
        )
        ax1.set_ylabel("가격", color=Colors.FG_DIM, fontsize=8)
        make_dark_legend(ax1)

        # ax2: RSI
        if rsi is not None:
            rsi_vals = rsi.values if hasattr(rsi, "values") else rsi
            ax2.plot(
                x, rsi_vals, color=Colors.RSI_LINE,
                linewidth=1.2, label="RSI",
            )
            ax2.axhline(
                70, color=Colors.LOSS, linewidth=0.8,
                linestyle="--", alpha=0.7,
            )
            ax2.axhline(
                30, color=Colors.PROFIT, linewidth=0.8,
                linestyle="--", alpha=0.7,
            )
            ax2.axhline(
                50, color=Colors.BORDER, linewidth=0.6,
                linestyle="--", alpha=0.5,
            )
            ax2.fill_between(
                x, rsi_vals, 70,
                where=(rsi_vals >= 70),
                color=Colors.LOSS, alpha=0.12,
            )
            ax2.fill_between(
                x, rsi_vals, 30,
                where=(rsi_vals <= 30),
                color=Colors.PROFIT, alpha=0.12,
            )
            ax2.set_ylim(0, 100)

        ax2.set_ylabel("RSI", color=Colors.FG_DIM, fontsize=8)
        make_dark_legend(ax2)

        # ax3: MACD
        if macd is not None and sig is not None and hist is not None:
            macd_vals = macd.values if hasattr(macd, "values") else macd
            sig_vals = sig.values if hasattr(sig, "values") else sig
            hist_vals = hist.values if hasattr(hist, "values") else hist

            hist_colors = [
                Colors.PROFIT if v >= 0 else Colors.LOSS
                for v in hist_vals
            ]
            ax3.bar(
                x, hist_vals, color=hist_colors, width=0.8,
                alpha=0.7, label="히스토그램",
            )
            ax3.plot(
                x, macd_vals, color=Colors.MACD_LINE,
                linewidth=1.2, label="MACD",
            )
            ax3.plot(
                x, sig_vals, color=Colors.SIGNAL_LINE,
                linewidth=1.0, label="Signal", linestyle="--",
            )
            ax3.axhline(0, color=Colors.BORDER, linewidth=0.7, linestyle="--")

        ax3.set_ylabel("MACD", color=Colors.FG_DIM, fontsize=8)
        make_dark_legend(ax3)

        # X축 레이블
        step = max(1, len(df_show) // 8)
        tick_pos = list(range(0, len(df_show), step))
        tick_labels = [
            str(df_show.index[i])[11:16]
            for i in tick_pos
            if i < len(df_show)
        ]
        ax3.set_xticks(tick_pos[:len(tick_labels)])
        ax3.set_xticklabels(
            tick_labels, rotation=20, ha="right",
            fontsize=7, color=Colors.FG_DIM,
        )

        self._fig.tight_layout(pad=1.2)
        self._canvas.draw_idle()

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

