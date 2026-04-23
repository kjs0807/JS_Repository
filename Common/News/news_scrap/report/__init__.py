"""
뉴스 보고서 생성 모듈
=====================
report 패키지 초기화 + 공통 유틸리티 함수.
"""

import re
import html
import io
import base64
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import matplotlib.figure


def clean_html(text: str) -> str:
    """HTML 태그 및 엔티티 제거.

    Args:
        text: HTML이 포함된 원본 문자열.

    Returns:
        태그/엔티티가 제거된 순수 텍스트.
    """
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def fig_to_base64(fig: "matplotlib.figure.Figure", dpi: int = 100) -> str:
    """matplotlib Figure를 base64 PNG data-URI 문자열로 변환.

    Args:
        fig: matplotlib Figure 객체.
        dpi: PNG 해상도 (기본 100).

    Returns:
        'data:image/png;base64,...' 형식의 문자열.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    return f"data:image/png;base64,{encoded}"


__all__ = [
    "clean_html",
    "fig_to_base64",
]
