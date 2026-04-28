"""대시보드 다크 테마 색상 및 폰트 상수.

Bybit 트레이딩 대시보드용 다크 테마 (스펙 지정 색상 기반).
"""


class Colors:
    """다크 테마 색상 팔레트."""

    # 배경
    BG = "#1a1a2e"           # 메인 배경 (진한 네이비)
    BG_CARD = "#16213e"      # 카드/패널 배경
    BG_INPUT = "#2a2a4a"     # 입력 필드 배경

    # 전경
    FG = "#e0e0e0"           # 기본 텍스트
    FG_DIM = "#808080"       # 보조 텍스트 (흐리게)

    # 강조
    ACCENT = "#0f3460"       # 강조 색상 (버튼 등)
    HEADER = "#16213e"       # 헤더 배경

    # 손익
    PROFIT = "#00d09c"       # 수익 (녹색)
    LOSS = "#ff4757"         # 손실 (빨간)
    WARNING = "#ffa502"      # 경고 (주황)
    NEUTRAL = "#a0a0b0"      # 중립 (회색)

    # 구조
    BORDER = "#2a2a4a"       # 테두리
    SEPARATOR = "#1e2a4a"    # 구분선

    # 버튼
    BTN_START = "#00d09c"    # 시작 버튼
    BTN_STOP = "#ff4757"     # 중지 버튼
    BTN_BLUE = "#4a9eff"     # 파란 버튼
    BTN_GRAY = "#444466"     # 회색 버튼

    # 상태
    CONNECTED = "#00d09c"    # 연결됨
    DISCONNECTED = "#ff4757" # 연결 끊김
    PENDING = "#ffa502"      # 대기 중

    # 탭
    TAB_ACTIVE = "#0f3460"   # 활성 탭
    TAB_INACTIVE = "#16213e" # 비활성 탭

    # 차트
    CHART_BG = "#1a1a2e"     # 차트 배경
    CHART_CARD = "#16213e"   # 차트 카드 배경
    CHART_GRID = "#2a2a4a"   # 차트 그리드
    BB_UPPER = "#ff6b6b"     # 볼린저밴드 상단
    BB_LOWER = "#4ecdc4"     # 볼린저밴드 하단
    KC_UPPER = "#ffa502"     # 켈트너채널 상단
    KC_LOWER = "#ffa502"     # 켈트너채널 하단
    CLOUD_BULL = "#00d09c33" # 양운 (반투명 녹색)
    CLOUD_BEAR = "#ff475733" # 음운 (반투명 적색)
    TENKAN = "#ff6b6b"       # 전환선
    KIJUN = "#4ecdc4"        # 기준선
    ZSCORE_ENTRY = "#ff6b6b" # Z-Score 진입선
    ZSCORE_EXIT = "#00d09c"  # Z-Score 청산선
    PRICE_LINE = "#e0e0e0"   # 가격선
    RSI_LINE = "#b388ff"     # RSI 선
    MACD_LINE = "#4a9eff"    # MACD 선
    SIGNAL_LINE = "#ffa502"  # MACD 시그널 선
    CHIKOU = "#ffa502"       # 후행스팬


class Fonts:
    """폰트 설정."""

    TITLE = ("맑은 고딕", 13, "bold")
    HEADER = ("맑은 고딕", 11, "bold")
    BODY = ("맑은 고딕", 10)
    BODY_BOLD = ("맑은 고딕", 10, "bold")
    SMALL = ("맑은 고딕", 9)

    MONO = ("Consolas", 10)
    MONO_LARGE = ("Consolas", 14, "bold")
    MONO_SMALL = ("Consolas", 9)
    MONO_BOLD = ("Consolas", 10, "bold")


def apply_dark_notebook_style(style: object) -> None:
    """ttk.Style에 다크 테마 Notebook 스타일 적용.

    Args:
        style: ttk.Style 인스턴스
    """
    style.theme_use("default")  # type: ignore[union-attr]
    style.configure(  # type: ignore[union-attr]
        "Dark.TNotebook",
        background=Colors.BG,
        borderwidth=0,
    )
    style.configure(  # type: ignore[union-attr]
        "Dark.TNotebook.Tab",
        background=Colors.TAB_INACTIVE,
        foreground=Colors.FG_DIM,
        padding=[14, 5],
        font=Fonts.BODY,
    )
    style.map(  # type: ignore[union-attr]
        "Dark.TNotebook.Tab",
        background=[("selected", Colors.TAB_ACTIVE)],
        foreground=[("selected", Colors.FG)],
    )
