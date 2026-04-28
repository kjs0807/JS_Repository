"""차트 공통 유틸리티 - matplotlib 다크 테마 Figure 생성 및 공통 스타일.

모든 탭 차트가 공유하는 Figure 생성, 다크 테마 적용, 리샘플링 함수를 제공한다.
pyplot 상태 머신을 우회하고 Figure를 직접 생성한다.
"""

from __future__ import annotations

from typing import Tuple, Union

import matplotlib
matplotlib.use("TkAgg")

import matplotlib.font_manager as fm
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
import tkinter as tk

from dashboard.theme import Colors

# matplotlib 한글 폰트 설정 (Windows: Malgun Gothic)
matplotlib.rcParams["axes.unicode_minus"] = False
for _fname in ("Malgun Gothic", "NanumGothic", "AppleGothic"):
    _matches = [f for f in fm.fontManager.ttflist if _fname in f.name]
    if _matches:
        matplotlib.rcParams["font.family"] = _fname
        break
else:
    matplotlib.rcParams["font.family"] = "sans-serif"


def make_dark_figure(
    figsize: Tuple[float, float] = (10, 6),
    dpi: int = 100,
) -> Figure:
    """다크 테마 matplotlib Figure를 생성한다.

    Args:
        figsize: Figure 크기 (width, height) 인치 단위
        dpi: 해상도

    Returns:
        다크 테마가 적용된 Figure 인스턴스
    """
    fig = Figure(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor(Colors.CHART_BG)
    return fig


def apply_dark_axes(ax: object) -> None:
    """Axes에 다크 테마 스타일을 적용한다.

    Args:
        ax: 스타일을 적용할 Axes 인스턴스
    """
    ax.set_facecolor(Colors.CHART_CARD)
    ax.tick_params(colors=Colors.FG, labelsize=8)
    ax.xaxis.label.set_color(Colors.FG)
    ax.yaxis.label.set_color(Colors.FG)
    ax.spines["bottom"].set_color(Colors.BORDER)
    ax.spines["top"].set_color(Colors.BORDER)
    ax.spines["left"].set_color(Colors.BORDER)
    ax.spines["right"].set_color(Colors.BORDER)
    ax.grid(color=Colors.CHART_GRID, alpha=0.5, linewidth=0.5)


def embed_figure(
    fig: Figure,
    parent: tk.Widget,
) -> FigureCanvasTkAgg:
    """Figure를 tkinter 위젯에 임베드한다.

    Args:
        fig: 임베드할 Figure
        parent: 부모 tkinter 위젯

    Returns:
        FigureCanvasTkAgg 인스턴스
    """
    canvas = FigureCanvasTkAgg(fig, master=parent)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    canvas.draw()
    return canvas


def make_dark_legend(ax: object) -> None:
    """Axes에 다크 테마 범례를 추가한다.

    Args:
        ax: 범례를 추가할 Axes
    """
    legend = ax.legend(
        facecolor=Colors.CHART_CARD,
        edgecolor=Colors.BORDER,
        labelcolor=Colors.FG,
        fontsize=7,
        loc="upper left",
    )
    if legend:
        legend.get_frame().set_alpha(0.8)


def clear_axes(fig: Figure) -> None:
    """Figure의 모든 Axes를 초기화한다.

    Args:
        fig: 초기화할 Figure
    """
    for ax in fig.get_axes():
        ax.cla()


def resample_15m_to_30m(df: pd.DataFrame) -> pd.DataFrame:
    """15분봉 DataFrame을 30분봉으로 리샘플링한다.

    인덱스 기반이 아닌 행 순서 기반으로 2봉씩 묶어 리샘플링한다.
    홀수 행이면 마지막 1봉은 버린다.

    Args:
        df: 15분봉 OHLCV DataFrame. open_time, open, high, low, close,
            volume 컬럼이 필요하다.

    Returns:
        30분봉으로 리샘플링된 DataFrame
    """
    n = len(df)
    trim = n - (n % 2)
    if trim < 2:
        return df.copy()

    df_trimmed = df.iloc[:trim].copy()
    g = np.arange(trim) // 2

    result = pd.DataFrame({
        "open_time": df_trimmed.groupby(g)["open_time"].first().values,
        "open": df_trimmed.groupby(g)["open"].first().values,
        "high": df_trimmed.groupby(g)["high"].max().values,
        "low": df_trimmed.groupby(g)["low"].min().values,
        "close": df_trimmed.groupby(g)["close"].last().values,
        "volume": df_trimmed.groupby(g)["volume"].sum().values,
    }).reset_index(drop=True)

    return result
