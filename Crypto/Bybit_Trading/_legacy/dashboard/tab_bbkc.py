"""대시보드 Tab 3: BBKCSqueeze 차트 탭 (1H).

BB + KC Squeeze 상단 subplot (가격/밴드) +
하단 subplot (모멘텀 히스토그램 + Squeeze ON/OFF 도트).
ohlcv_1h 테이블에서 최근 200봉 직접 조회.
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


class BBKCTab(BaseDirtyTab):
    """BB-KC Squeeze 차트 탭 - 1시간봉.

    구성:
        - 코인 선택 드롭다운 + [새로고침]
        - 상단 subplot: 가격 + BB 밴드 + KC 밴드
        - 하단 subplot: 모멘텀 히스토그램 + Squeeze ON/OFF 도트
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
        """matplotlib 차트 영역 (2개 subplot)."""
        chart_frame = tk.Frame(self, bg=Colors.BG)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._fig = make_dark_figure(figsize=(12, 7), dpi=96)
        ax1, ax2 = self._fig.subplots(
            2, 1,
            gridspec_kw={"height_ratios": [3, 1]},
            sharex=True,
        )
        apply_dark_axes(ax1)
        apply_dark_axes(ax2)
        ax1.set_title(
            "BB/KC Squeeze - 데이터를 불러오려면 [새로고침]을 누르세요",
            color=Colors.FG_DIM, fontsize=9,
        )
        self._fig.tight_layout(pad=1.5)
        self._canvas = embed_figure(self._fig, chart_frame)

    # ── 이벤트 구동 ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """DB에서 1H 봉 조회, BB/KC 계산, 차트 갱신."""
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

        if df is None or df.empty or len(df) < 30:
            cnt = len(df) if df is not None and not df.empty else 0
            self._set_no_data(f"{symbol} 1H 데이터 부족 ({cnt}개)")
            return

        try:
            from indicators.bollinger import calc_bollinger_bands
            from indicators.keltner import calc_keltner_channel
            df = calc_bollinger_bands(df, period=20, std=2.0)
            df = calc_keltner_channel(df, period=20, atr_mult=1.5)
        except Exception as exc:
            self._set_no_data(f"지표 계산 오류: {exc}")
            return

        self._draw_chart(df, symbol)

    # ── 차트 그리기 ──────────────────────────────────────────────────────

    def _draw_chart(self, df: Any, symbol: str) -> None:
        """BB/KC Squeeze 차트를 그린다.

        Args:
            df: BB/KC 컬럼이 포함된 DataFrame
            symbol: 심볼명
        """
        self._fig.clf()
        ax1, ax2 = self._fig.subplots(
            2, 1,
            gridspec_kw={"height_ratios": [3, 1]},
            sharex=True,
        )
        apply_dark_axes(ax1)
        apply_dark_axes(ax2)

        display_n = min(80, len(df))
        df_show = df.iloc[-display_n:]
        x = list(range(len(df_show)))

        close = df_show["close"].values
        bb_upper = df_show.get("bb_upper", df_show["close"]).values
        bb_lower = df_show.get("bb_lower", df_show["close"]).values
        bb_mid = df_show.get("bb_mid", df_show["close"]).values
        kc_upper = df_show.get("kc_upper", df_show["close"]).values
        kc_lower = df_show.get("kc_lower", df_show["close"]).values
        squeeze = df_show.get("squeeze_on", None)

        # ax1: 가격 + BB fill + KC fill
        ax1.fill_between(
            x, bb_upper, bb_lower,
            color=Colors.BB_UPPER, alpha=0.08, label="BB 범위",
        )
        ax1.fill_between(
            x, kc_upper, kc_lower,
            color=Colors.KC_UPPER, alpha=0.06, label="KC 범위",
        )

        ax1.plot(
            x, bb_upper, color=Colors.BB_UPPER,
            linewidth=1.0, linestyle="--", alpha=0.8, label="BB 상단",
        )
        ax1.plot(
            x, bb_lower, color=Colors.BB_LOWER,
            linewidth=1.0, linestyle="--", alpha=0.8, label="BB 하단",
        )
        ax1.plot(
            x, kc_upper, color=Colors.KC_UPPER,
            linewidth=1.0, linestyle=":", alpha=0.7, label="KC 상단",
        )
        ax1.plot(
            x, kc_lower, color=Colors.KC_LOWER,
            linewidth=1.0, linestyle=":", alpha=0.7, label="KC 하단",
        )
        ax1.plot(
            x, close, color=Colors.PRICE_LINE,
            linewidth=1.5, label="가격",
        )

        # Squeeze ON 구간 배경
        if squeeze is not None:
            sq_vals = squeeze.values if hasattr(squeeze, "values") else squeeze
            in_sq = False
            sq_start = 0
            for i_sq, sq_val in enumerate(sq_vals):
                if sq_val and not in_sq:
                    sq_start = i_sq
                    in_sq = True
                elif not sq_val and in_sq:
                    ax1.axvspan(
                        sq_start, i_sq, alpha=0.08,
                        color=Colors.LOSS, zorder=0,
                    )
                    in_sq = False
            if in_sq:
                ax1.axvspan(
                    sq_start, len(sq_vals), alpha=0.08,
                    color=Colors.LOSS, zorder=0,
                )

        ax1.set_title(
            f"BB/KC Squeeze - {symbol} (1H)",
            color=Colors.FG, fontsize=10,
        )
        ax1.set_ylabel("가격", color=Colors.FG_DIM, fontsize=8)
        make_dark_legend(ax1)

        # ax2: 모멘텀 히스토그램 + Squeeze 도트
        momentum = close - bb_mid
        prev_mom = np.roll(momentum, 1)
        prev_mom[0] = momentum[0]

        bar_colors = []
        for i_m in range(len(momentum)):
            m = momentum[i_m]
            pm = prev_mom[i_m]
            if m >= 0:
                bar_colors.append("#00d09c" if m >= pm else "#007a5a")
            else:
                bar_colors.append("#ff4757" if m <= pm else "#aa2233")

        ax2.bar(x, momentum, color=bar_colors, width=0.8, label="모멘텀")

        # Squeeze ON/OFF 도트
        if squeeze is not None:
            sq_vals = squeeze.values if hasattr(squeeze, "values") else squeeze
            valid_mom = momentum[~np.isnan(momentum)]
            y_base = (
                np.nanmin(momentum) * 1.1
                if len(valid_mom) > 0 else -0.01
            )
            y_dot = np.full(len(sq_vals), y_base)
            dot_colors = [
                Colors.LOSS if sq else Colors.PROFIT for sq in sq_vals
            ]
            ax2.scatter(x, y_dot, color=dot_colors, s=12, zorder=5)

        ax2.axhline(0, color=Colors.BORDER, linewidth=0.8, linestyle="--")
        ax2.set_ylabel("모멘텀", color=Colors.FG_DIM, fontsize=8)

        # X축 레이블
        step = max(1, len(df_show) // 8)
        tick_pos = list(range(0, len(df_show), step))
        tick_labels = [
            str(df_show.index[i])[:13]
            for i in tick_pos
            if i < len(df_show)
        ]
        ax2.set_xticks(tick_pos[:len(tick_labels)])
        ax2.set_xticklabels(
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

