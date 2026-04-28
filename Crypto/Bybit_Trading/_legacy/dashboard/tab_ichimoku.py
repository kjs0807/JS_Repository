"""대시보드 Tab 2: IchimokuCloud 차트 탭 (1H).

코인 선택 드롭다운 + [새로고침] 버튼 + matplotlib 구름대 차트.
ohlcv_1h 테이블에서 최근 200봉 직접 조회.
이벤트 구동: mark_dirty() 호출 시 dirty 플래그 세움.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
import tkinter as tk
from tkinter import ttk

import numpy as np
import pandas as pd

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


class IchimokuTab(BaseDirtyTab):
    """Ichimoku Cloud 차트 탭 - 1시간봉 일목균형표.

    구성:
        - 코인 선택 드롭다운 + [새로고침] 버튼
        - matplotlib 구름대 + 전환선/기준선 + 가격선
    """

    def __init__(self, parent: tk.Widget) -> None:
        """초기화.

        Args:
            parent: 부모 위젯 (Notebook)
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
        """상단 컨트롤 바 - 심볼 선택 + 새로고침."""
        bar = tk.Frame(self, bg=Colors.BG_CARD, pady=6)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(
            bar,
            text="심볼:",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG_DIM,
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
            bar,
            text="타임프레임: 1H",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG_DIM,
        ).pack(side=tk.LEFT, padx=16)

        tk.Button(
            bar,
            text="새로고침",
            font=Fonts.MONO_SMALL,
            bg=Colors.BTN_GRAY,
            fg=Colors.FG,
            activebackground=Colors.ACCENT,
            activeforeground=Colors.FG,
            relief=tk.FLAT,
            cursor="hand2",
            command=self.refresh,
        ).pack(side=tk.RIGHT, padx=12)

    def _build_chart_area(self) -> None:
        """matplotlib 차트 영역."""
        chart_frame = tk.Frame(self, bg=Colors.BG)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._fig = make_dark_figure(figsize=(12, 6), dpi=96)
        ax = self._fig.add_subplot(111)
        apply_dark_axes(ax)
        ax.set_title(
            "일목균형표 - 데이터를 불러오려면 [새로고침]을 누르세요",
            color=Colors.FG_DIM, fontsize=9,
        )
        self._fig.tight_layout(pad=1.5)
        self._canvas = embed_figure(self._fig, chart_frame)

    # ── 이벤트 구동 ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """DB에서 1H 봉 조회, 일목균형표 계산, 차트 갱신."""
        self._dirty = False
        if self._db is None:
            self._set_no_data_message("DB가 연결되지 않았습니다.")
            return

        symbol = self._symbol_var.get()
        try:
            df = self._db.get_ohlcv(symbol, "60", limit=200)
        except Exception as exc:
            self._set_no_data_message(f"데이터 조회 오류: {exc}")
            return

        if df is None or df.empty or len(df) < 50:
            cnt = len(df) if df is not None and not df.empty else 0
            self._set_no_data_message(f"{symbol} 1H 데이터 부족 ({cnt}개)")
            return

        # datetime 인덱스 정비
        if "open_time" in df.columns:
            df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.set_index("datetime", inplace=True)
        elif not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)

        try:
            from indicators.ichimoku import calc_ichimoku
            df_ich = calc_ichimoku(df, tenkan=10, kijun=26, senkou=52)
        except Exception as exc:
            self._set_no_data_message(f"지표 계산 오류: {exc}")
            return

        self._draw_chart(df_ich, symbol)

    # ── 차트 그리기 ──────────────────────────────────────────────────────

    def _draw_chart(self, df: pd.DataFrame, symbol: str) -> None:
        """일목균형표 차트를 그린다.

        Args:
            df: 일목균형표 컬럼이 포함된 DataFrame
            symbol: 심볼명
        """
        self._fig.clf()
        ax = self._fig.add_subplot(111)
        apply_dark_axes(ax)

        display_n = min(120, len(df))
        df_show = df.iloc[-display_n:]
        x_arr = list(range(len(df_show)))

        close_s = df_show["close"].values
        tenkan = df_show.get("tenkan")
        kijun = df_show.get("kijun")
        senkou_a = df_show.get("senkou_a")
        senkou_b = df_show.get("senkou_b")
        chikou = df_show.get("chikou")

        # 구름대 fill_between
        if senkou_a is not None and senkou_b is not None:
            sa = senkou_a.values
            sb = senkou_b.values
            try:
                ax.fill_between(
                    x_arr, sa, sb,
                    where=(sa >= sb),
                    color=Colors.CLOUD_BULL,
                    label="양운",
                    interpolate=True,
                )
                ax.fill_between(
                    x_arr, sa, sb,
                    where=(sa < sb),
                    color=Colors.CLOUD_BEAR,
                    label="음운",
                    interpolate=True,
                )
                ax.plot(x_arr, sa, color=Colors.PROFIT, linewidth=0.8, alpha=0.7)
                ax.plot(x_arr, sb, color=Colors.LOSS, linewidth=0.8, alpha=0.7)
            except Exception as exc:
                logger.warning("구름대 렌더링 실패: %s", exc)

        # 전환선 / 기준선 / 가격선
        if tenkan is not None:
            ax.plot(
                x_arr, tenkan.values, color=Colors.TENKAN,
                linewidth=1.2, label="전환선", alpha=0.9,
            )
        if kijun is not None:
            ax.plot(
                x_arr, kijun.values, color=Colors.KIJUN,
                linewidth=1.2, label="기준선", alpha=0.9,
            )
        ax.plot(
            x_arr, close_s, color=Colors.PRICE_LINE,
            linewidth=1.5, label="가격", alpha=1.0,
        )

        # 후행스팬 (점선)
        if chikou is not None:
            ax.plot(
                x_arr, chikou.values, color=Colors.CHIKOU,
                linewidth=0.8, linestyle="--", label="후행스팬", alpha=0.7,
            )

        # X축 레이블
        step = max(1, len(df_show) // 8)
        tick_pos = list(range(0, len(df_show), step))
        tick_labels = [
            str(df_show.index[i])[:16]
            for i in tick_pos
            if i < len(df_show)
        ]
        ax.set_xticks(tick_pos[:len(tick_labels)])
        ax.set_xticklabels(
            tick_labels, rotation=20, ha="right",
            fontsize=7, color=Colors.FG_DIM,
        )

        ax.set_title(
            f"Ichimoku Cloud - {symbol} (1H)",
            color=Colors.FG, fontsize=10,
        )
        ax.set_ylabel("가격", color=Colors.FG_DIM, fontsize=8)
        make_dark_legend(ax)

        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

    def _set_no_data_message(self, msg: str) -> None:
        """차트 영역에 메시지를 표시한다.

        Args:
            msg: 표시할 메시지
        """
        self._fig.clf()
        ax = self._fig.add_subplot(111)
        apply_dark_axes(ax)
        ax.text(
            0.5, 0.5, msg,
            transform=ax.transAxes,
            ha="center", va="center",
            color=Colors.FG_DIM, fontsize=11,
        )
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

