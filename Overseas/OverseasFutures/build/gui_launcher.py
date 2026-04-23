"""해외선물 실시간 데이터 수집기 GUI 런처.

PyInstaller --onefile 호환.
"""

import sys
import os

# ── 경로 설정 (PyInstaller 호환) ─────────────────────────────────────────
if hasattr(sys, '_MEIPASS'):
    BASE_DIR = sys._MEIPASS
    PROJECT_ROOT = BASE_DIR
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(BASE_DIR)  # build/ 상위 = 프로젝트 루트

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── .env 탐색: exe 위치 또는 프로젝트 루트 ───────────────────────────────
def _locate_env() -> str:
    """실행 파일과 같은 디렉터리 또는 프로젝트 루트에서 .env 탐색."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), ".env"),
        os.path.join(PROJECT_ROOT, ".env"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return candidates[-1]  # 없으면 기본값


import asyncio
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import threading
import sqlite3
import time
import io
import logging
from datetime import datetime
from typing import Dict, List, Optional

# ── 색상 팔레트 ──────────────────────────────────────────────────────────
COLORS = {
    'bg':      '#1a1a2e',
    'bg2':     '#16213e',
    'bg3':     '#0f3460',
    'accent':  '#e94560',
    'accent2': '#ff6b6b',
    'text':    '#eaeaea',
    'text2':   '#a0a0a0',
    'success': '#2ecc71',
    'warning': '#f39c12',
    'error':   '#e74c3c',
    'border':  '#2a2a4a',
    'btn_bg':  '#0f3460',
    'btn_start': '#1a7a4a',
    'btn_stop':  '#7a1a1a',
    'check_on':  '#e94560',
}

# ── 종목 데이터 (analysis.json에서 하드코딩) ────────────────────────────
SYMBOLS_BY_EXCHANGE = {
    "EUREX": [
        {"symbol": "VG",  "kis_code": "VGM26",   "name": "Euro Stoxx 50"},
        {"symbol": "BON", "kis_code": "BONM26",  "name": "Euro-Bund 10Y"},
        {"symbol": "OAT", "kis_code": "OATM26",  "name": "Euro-OAT"},
        {"symbol": "GX",  "kis_code": "GXM26",   "name": "DAX"},
    ],
    "OSE": [
        {"symbol": "JGB", "kis_code": "JGBM26",  "name": "JGB 10Y"},
        {"symbol": "TPX", "kis_code": "TPXM26",  "name": "TOPIX"},
    ],
    "HKEx": [
        {"symbol": "HSI", "kis_code": "HSIM26",  "name": "Hang Seng"},
        {"symbol": "MHI", "kis_code": "MHIM26",  "name": "Mini Hang Seng"},
        {"symbol": "HHI", "kis_code": "HHIM26",  "name": "H-Shares"},
    ],
    "ASX": [
        {"symbol": "YT",  "kis_code": "YTM26",   "name": "AUS 3Y Bond"},
        {"symbol": "XT",  "kis_code": "XTM26",   "name": "AUS 10Y Bond"},
        {"symbol": "SPI", "kis_code": "SPIM26",  "name": "SPI 200"},
    ],
    "FTX": [
        {"symbol": "TX",  "kis_code": "TXM26",   "name": "TAIEX"},
        {"symbol": "MTX", "kis_code": "MTXM26",  "name": "Mini TAIEX"},
    ],
}


# ── stdout/stderr → 로그 패널 리다이렉터 ────────────────────────────────
class _TextRedirector(io.TextIOBase):
    """stdout/stderr를 tkinter ScrolledText로 리다이렉트."""

    def __init__(self, widget: ScrolledText, tag: str = "normal") -> None:
        super().__init__()
        self._widget = widget
        self._tag = tag

    def write(self, s: str) -> int:
        if not s:
            return 0
        # 타임스탬프 없이 들어온 텍스트는 그대로 삽입
        self._widget.after(0, self._insert, s)
        return len(s)

    def _insert(self, s: str) -> None:
        self._widget.configure(state="normal")
        self._widget.insert("end", s, self._tag)
        # 최대 500줄 유지
        line_count = int(self._widget.index("end-1c").split(".")[0])
        if line_count > 500:
            self._widget.delete("1.0", f"{line_count - 500}.0")
        self._widget.see("end")
        self._widget.configure(state="disabled")

    def flush(self) -> None:
        pass


# ── 로그 핸들러 ──────────────────────────────────────────────────────────
class _GUILogHandler(logging.Handler):
    """logging 모듈 → GUI 로그 패널."""

    def __init__(self, widget: ScrolledText) -> None:
        super().__init__()
        self._widget = widget
        fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        self.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        tag = "normal"
        if record.levelno >= logging.ERROR:
            tag = "error"
        elif record.levelno >= logging.WARNING:
            tag = "warning"
        elif "체결" in record.getMessage():
            tag = "trade"
        elif "호가" in record.getMessage():
            tag = "orderbook"
        self._widget.after(0, self._insert, msg, tag)

    def _insert(self, msg: str, tag: str) -> None:
        self._widget.configure(state="normal")
        self._widget.insert("end", msg, tag)
        line_count = int(self._widget.index("end-1c").split(".")[0])
        if line_count > 500:
            self._widget.delete("1.0", f"{line_count - 500}.0")
        self._widget.see("end")
        self._widget.configure(state="disabled")


# ── 메인 앱 ──────────────────────────────────────────────────────────────
class DataCollectorApp:
    """해외선물 실시간 데이터 수집기 메인 GUI."""

    VERSION = "v1.1"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.ws_client = None
        self._running = False
        self._start_time: Optional[datetime] = None
        self._db_path: Optional[str] = None

        # 통계 캐시
        self._trade_count = 0
        self._orderbook_count = 0
        self._sub_count = 0
        self._db_trade_rows = 0
        self._db_rt_rows = 0
        self._last_db_check = 0.0

        # 체크박스 변수: kis_code → BooleanVar
        self._check_vars: Dict[str, tk.BooleanVar] = {}

        self._setup_window()
        self._build_ui()
        self._setup_logging()
        self._tick()

    # ── 윈도우 기본 설정 ─────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.root.title("해외선물 실시간 데이터 수집기")
        self.root.configure(bg=COLORS['bg'])
        self.root.resizable(True, True)
        self.root.minsize(600, 700)

        # 화면 중앙 배치
        w, h = 720, 820
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI 빌드 ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 타이틀 바
        self._build_titlebar()

        # 메인 스크롤 영역
        outer = tk.Frame(self.root, bg=COLORS['bg'])
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        main = tk.Frame(outer, bg=COLORS['bg'])
        main.pack(fill="both", expand=True, padx=12, pady=8)

        # 종목 선택
        self._build_symbol_panel(main)

        # 구분선
        sep1 = tk.Frame(main, height=1, bg=COLORS['border'])
        sep1.pack(fill="x", pady=(10, 4))

        # 시작/중지 버튼
        self._build_control_buttons(main)

        # 실시간 현황
        self._build_status_panel(main)

        # 로그 패널
        self._build_log_panel(main)

        # 상태바
        self._build_statusbar()

    def _build_titlebar(self) -> None:
        bar = tk.Frame(self.root, bg=COLORS['bg3'], height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        accent_line = tk.Frame(self.root, bg=COLORS['accent'], height=2)
        accent_line.pack(fill="x")

        title_lbl = tk.Label(
            bar,
            text="  해외선물 실시간 데이터 수집기",
            font=("Malgun Gothic", 13, "bold"),
            bg=COLORS['bg3'],
            fg=COLORS['text'],
            anchor="w",
        )
        title_lbl.pack(side="left", fill="y", padx=8)

        ver_lbl = tk.Label(
            bar,
            text=self.VERSION,
            font=("Consolas", 9),
            bg=COLORS['bg3'],
            fg=COLORS['text2'],
        )
        ver_lbl.pack(side="right", padx=12)

    def _build_symbol_panel(self, parent: tk.Frame) -> None:
        # 섹션 헤더
        hdr = tk.Frame(parent, bg=COLORS['bg'])
        hdr.pack(fill="x", pady=(8, 4))

        tk.Label(
            hdr,
            text="종목 선택",
            font=("Malgun Gothic", 10, "bold"),
            bg=COLORS['bg'],
            fg=COLORS['accent'],
        ).pack(side="left")

        btn_frame = tk.Frame(hdr, bg=COLORS['bg'])
        btn_frame.pack(side="right")

        self._make_small_btn(btn_frame, "전체선택", self._select_all).pack(side="left", padx=2)
        self._make_small_btn(btn_frame, "전체해제", self._deselect_all).pack(side="left", padx=2)

        # 체크박스 컨테이너
        container = tk.Frame(
            parent,
            bg=COLORS['bg2'],
            bd=1,
            relief="flat",
            highlightbackground=COLORS['border'],
            highlightthickness=1,
        )
        container.pack(fill="x", pady=(0, 4))

        inner = tk.Frame(container, bg=COLORS['bg2'])
        inner.pack(fill="x", padx=10, pady=8)

        for exch, symbols in SYMBOLS_BY_EXCHANGE.items():
            # 거래소 레이블
            exch_lbl = tk.Label(
                inner,
                text=f"── {exch} ──",
                font=("Consolas", 8),
                bg=COLORS['bg2'],
                fg=COLORS['text2'],
                anchor="w",
            )
            exch_lbl.pack(fill="x", pady=(6, 2))

            # 종목 체크박스 (2열 레이아웃)
            row_frame = tk.Frame(inner, bg=COLORS['bg2'])
            row_frame.pack(fill="x")

            for i, sym in enumerate(symbols):
                var = tk.BooleanVar(value=True)
                self._check_vars[sym["kis_code"]] = var

                label_text = f"{sym['symbol']:4s} {sym['name']}"
                cb = tk.Checkbutton(
                    row_frame,
                    text=label_text,
                    variable=var,
                    font=("Consolas", 9),
                    bg=COLORS['bg2'],
                    fg=COLORS['text'],
                    selectcolor=COLORS['bg3'],
                    activebackground=COLORS['bg2'],
                    activeforeground=COLORS['accent2'],
                    cursor="hand2",
                    anchor="w",
                    width=22,
                )
                col = i % 2
                row = i // 2
                cb.grid(row=row, column=col, sticky="w", padx=(0, 8))

            row_frame.columnconfigure(0, weight=1)
            row_frame.columnconfigure(1, weight=1)

    def _build_control_buttons(self, parent: tk.Frame) -> None:
        frame = tk.Frame(parent, bg=COLORS['bg'])
        frame.pack(pady=6)

        self._btn_start = tk.Button(
            frame,
            text="  ▶  수집 시작  ",
            font=("Malgun Gothic", 11, "bold"),
            bg=COLORS['btn_start'],
            fg=COLORS['text'],
            activebackground="#22a060",
            activeforeground=COLORS['text'],
            relief="flat",
            cursor="hand2",
            bd=0,
            padx=16,
            pady=8,
            command=self._on_start,
        )
        self._btn_start.pack(side="left", padx=8)

        self._btn_stop = tk.Button(
            frame,
            text="  ■  수집 중지  ",
            font=("Malgun Gothic", 11, "bold"),
            bg=COLORS['btn_stop'],
            fg=COLORS['text2'],
            activebackground="#a02222",
            activeforeground=COLORS['text'],
            relief="flat",
            cursor="hand2",
            bd=0,
            padx=16,
            pady=8,
            state="disabled",
            command=self._on_stop,
        )
        self._btn_stop.pack(side="left", padx=8)

    def _build_status_panel(self, parent: tk.Frame) -> None:
        # 헤더
        tk.Label(
            parent,
            text="실시간 현황",
            font=("Malgun Gothic", 10, "bold"),
            bg=COLORS['bg'],
            fg=COLORS['accent'],
            anchor="w",
        ).pack(fill="x", pady=(10, 4))

        container = tk.Frame(
            parent,
            bg=COLORS['bg2'],
            highlightbackground=COLORS['border'],
            highlightthickness=1,
        )
        container.pack(fill="x")

        inner = tk.Frame(container, bg=COLORS['bg2'])
        inner.pack(fill="x", padx=12, pady=8)

        font_mono = ("Consolas", 9)
        fg = COLORS['text']

        # 상태 줄
        row0 = tk.Frame(inner, bg=COLORS['bg2'])
        row0.pack(fill="x", pady=1)
        tk.Label(row0, text="상태:  ", font=font_mono, bg=COLORS['bg2'], fg=COLORS['text2']).pack(side="left")
        self._lbl_status = tk.Label(row0, text="대기 중", font=font_mono, bg=COLORS['bg2'], fg=COLORS['text2'])
        self._lbl_status.pack(side="left")

        # 카운트 줄
        row1 = tk.Frame(inner, bg=COLORS['bg2'])
        row1.pack(fill="x", pady=1)
        tk.Label(row1, text="체결: ", font=font_mono, bg=COLORS['bg2'], fg=COLORS['text2']).pack(side="left")
        self._lbl_trade = tk.Label(row1, text="0건", font=font_mono, bg=COLORS['bg2'], fg=COLORS['success'])
        self._lbl_trade.pack(side="left")
        tk.Label(row1, text="  호가: ", font=font_mono, bg=COLORS['bg2'], fg=COLORS['text2']).pack(side="left")
        self._lbl_orderbook = tk.Label(row1, text="0건", font=font_mono, bg=COLORS['bg2'], fg=COLORS['success'])
        self._lbl_orderbook.pack(side="left")
        tk.Label(row1, text="  구독: ", font=font_mono, bg=COLORS['bg2'], fg=COLORS['text2']).pack(side="left")
        self._lbl_sub = tk.Label(row1, text="0건", font=font_mono, bg=COLORS['bg2'], fg=fg)
        self._lbl_sub.pack(side="left")

        # DB 행 수
        row2 = tk.Frame(inner, bg=COLORS['bg2'])
        row2.pack(fill="x", pady=1)
        tk.Label(row2, text="DB:    ", font=font_mono, bg=COLORS['bg2'], fg=COLORS['text2']).pack(side="left")
        self._lbl_db = tk.Label(row2, text="trade_ticks 0행 / realtime_ticks 0행",
                                 font=font_mono, bg=COLORS['bg2'], fg=COLORS['text2'])
        self._lbl_db.pack(side="left")

    def _build_log_panel(self, parent: tk.Frame) -> None:
        # 헤더
        hdr = tk.Frame(parent, bg=COLORS['bg'])
        hdr.pack(fill="x", pady=(10, 4))

        tk.Label(
            hdr,
            text="로그",
            font=("Malgun Gothic", 10, "bold"),
            bg=COLORS['bg'],
            fg=COLORS['accent'],
        ).pack(side="left")

        self._btn_clear_log = self._make_small_btn(hdr, "지우기", self._clear_log)
        self._btn_clear_log.pack(side="right")

        self._log_text = ScrolledText(
            parent,
            height=14,
            font=("Consolas", 8),
            bg=COLORS['bg2'],
            fg=COLORS['text'],
            insertbackground=COLORS['text'],
            selectbackground=COLORS['bg3'],
            relief="flat",
            bd=0,
            highlightbackground=COLORS['border'],
            highlightthickness=1,
            state="disabled",
            wrap="word",
        )
        self._log_text.pack(fill="both", expand=True, pady=(0, 4))

        # 태그 색상 설정
        self._log_text.tag_config("error",     foreground=COLORS['error'])
        self._log_text.tag_config("warning",   foreground=COLORS['warning'])
        self._log_text.tag_config("trade",     foreground=COLORS['success'])
        self._log_text.tag_config("orderbook", foreground="#5dade2")
        self._log_text.tag_config("normal",    foreground=COLORS['text'])

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=COLORS['bg3'], height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        tk.Frame(bar, bg=COLORS['border'], width=1).pack(side="left", fill="y")

        self._dot = tk.Label(
            bar,
            text="●",
            font=("Consolas", 10),
            bg=COLORS['bg3'],
            fg=COLORS['text2'],
        )
        self._dot.pack(side="left", padx=(8, 2))

        self._lbl_conn = tk.Label(
            bar,
            text="대기 중",
            font=("Consolas", 8),
            bg=COLORS['bg3'],
            fg=COLORS['text2'],
        )
        self._lbl_conn.pack(side="left")

        tk.Label(
            bar,
            text=self.VERSION,
            font=("Consolas", 8),
            bg=COLORS['bg3'],
            fg=COLORS['text2'],
        ).pack(side="right", padx=10)

    # ── 유틸 위젯 ───────────────────────────────────────────────────

    def _make_small_btn(self, parent: tk.Widget, text: str, cmd) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            font=("Malgun Gothic", 8),
            bg=COLORS['bg3'],
            fg=COLORS['text'],
            activebackground=COLORS['accent'],
            activeforeground=COLORS['text'],
            relief="flat",
            cursor="hand2",
            bd=0,
            padx=8,
            pady=3,
            command=cmd,
        )

    # ── 종목 선택 ────────────────────────────────────────────────────

    def _select_all(self) -> None:
        for var in self._check_vars.values():
            var.set(True)

    def _deselect_all(self) -> None:
        for var in self._check_vars.values():
            var.set(False)

    def _get_selected_kis_codes(self) -> List[str]:
        return [k for k, v in self._check_vars.items() if v.get()]

    def _get_code_to_root_map(self) -> Dict[str, str]:
        """kis_code → symbol 역매핑 생성."""
        mapping: Dict[str, str] = {}
        for syms in SYMBOLS_BY_EXCHANGE.values():
            for s in syms:
                mapping[s["kis_code"]] = s["symbol"]
        return mapping

    # ── 로깅 설정 ────────────────────────────────────────────────────

    def _setup_logging(self) -> None:
        handler = _GUILogHandler(self._log_text)
        handler.setLevel(logging.INFO)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)

        # ws_client 내부 로그는 WARNING 이상만
        logging.getLogger("api.ws_client").setLevel(logging.WARNING)

        # stdout 리다이렉트
        sys.stdout = _TextRedirector(self._log_text, "normal")
        sys.stderr = _TextRedirector(self._log_text, "error")

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    # ── 시작/중지 ────────────────────────────────────────────────────

    def _on_start(self) -> None:
        selected = self._get_selected_kis_codes()
        if not selected:
            messagebox.showwarning("종목 없음", "구독할 종목을 하나 이상 선택하세요.")
            return

        # .env 검증
        env_path = _locate_env()
        if not os.path.isfile(env_path):
            messagebox.showerror(
                ".env 없음",
                f".env 파일을 찾을 수 없습니다.\n탐색 위치: {env_path}\n\nAPI 키를 설정한 뒤 다시 시도하세요."
            )
            return

        # GUI 프리징 방지: 초기화를 별도 스레드에서 실행
        self._btn_start.config(state="disabled")
        self._lbl_status.config(text="초기화 중...", fg=COLORS.get('text2', '#a0a0a0'))
        threading.Thread(
            target=self._do_start_background,
            args=(selected, env_path),
            daemon=True,
            name="WS-Init",
        ).start()

    def _do_start_background(self, selected: list, env_path: str) -> None:
        """별도 스레드에서 WS 초기화 + 시작 (GUI 블로킹 방지)."""
        try:
            self._do_start_impl(selected, env_path)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logging.getLogger(__name__).error("시작 실패: %s\n%s", e, tb)
            self.root.after(0, lambda: self._on_start_failed(f"{e}\n\n{tb}"))

    def _on_start_failed(self, msg: str) -> None:
        """시작 실패 시 GUI 스레드에서 복원."""
        messagebox.showerror("시작 실패", f"WebSocket 시작 실패:\n{msg}")
        self._btn_start.config(state="normal")
        self._lbl_status.config(text="시작 실패", fg=COLORS.get('error', '#e74c3c'))

    def _do_start_impl(self, selected: list, env_path: str) -> None:
        """별도 스레드에서 실행되는 초기화 + WS 시작 로직."""
        logger = logging.getLogger(__name__)

        # 환경변수 override
        os.environ["_OVF_ENV_PATH"] = env_path
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=True)
        except ImportError:
            pass

        # 설정 검증
        from config.settings import KISConfig
        config = KISConfig()

        # DB 경로 결정 — exe면 프로젝트 원본 DB를 직접 사용
        import config as _cfg
        if hasattr(sys, '_MEIPASS'):
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            correct_db = os.path.normpath(
                os.path.join(exe_dir, "..", "..", "db", "futures.db")
            )
            # config.DB_PATH를 덮어써서 모든 내부 모듈이 올바른 경로 사용
            _cfg.DB_PATH = correct_db
        self._db_path = _cfg.DB_PATH
        logger.info("DB 경로: %s", self._db_path)

        # DB 초기화
        from db.init_db import initialize_database

        schema_candidates = [
            os.path.join(PROJECT_ROOT, "db", "schema.sql"),
        ]
        if hasattr(sys, '_MEIPASS'):
            schema_candidates.insert(0, os.path.join(sys._MEIPASS, "db", "schema.sql"))

        schema_path = None
        for sp in schema_candidates:
            if os.path.isfile(sp):
                schema_path = sp
                break

        initialize_database(db_path=self._db_path, schema_path=schema_path)
        logger.info("DB 연결 완료: %s", self._db_path)

        # TickArchiver: 오래된 틱 데이터 백그라운드 아카이브 (keep_days=3)
        threading.Thread(
            target=self._run_tick_archive,
            args=(self._db_path,),
            daemon=True,
            name="TickArchiver",
        ).start()

        # WebSocket 클라이언트 생성 + 시작
        from api.ws_client import KISWebSocketClient

        self.ws_client = KISWebSocketClient(
            config=config,
            db_path=self._db_path,
            save_ticks_to_db=True,
        )
        self.ws_client.set_code_mapping(self._get_code_to_root_map())
        self.ws_client.start(selected)
        logger.info("수집 시작: %d개 종목", len(selected))

        # GUI 갱신은 메인 스레드에서 실행
        self.root.after(0, lambda: self._on_start_success(selected))

    def _on_start_success(self, selected: list) -> None:
        """시작 성공 시 GUI 갱신 (메인 스레드에서 호출)."""
        self._running = True
        self._start_time = datetime.now()
        self._trade_count = 0
        self._orderbook_count = 0

        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal", fg=COLORS.get('text', '#eaeaea'))

        self._lbl_status.config(
            text=f"수집 중 ({self._start_time:%H:%M:%S}부터)",
            fg=COLORS.get('success', '#2ecc71'),
        )
        self._dot.config(fg=COLORS.get('success', '#2ecc71'))

        logging.getLogger(__name__).info(
            "수집 시작: %d개 종목 [%s]",
            len(selected),
            ", ".join(selected),
        )

    @staticmethod
    def _run_tick_archive(db_path: str, keep_days: int = 3) -> None:
        """백그라운드 스레드에서 TickArchiver 실행 (시작 시 자동 호출)."""
        logger = logging.getLogger(__name__)
        try:
            from db.tick_archiver import TickArchiver
            archiver = TickArchiver(db_path=db_path)
            result = archiver.archive_old_ticks(keep_days=keep_days)
            for table, info in result.items():
                if info.get("archived", 0) > 0:
                    logger.info(
                        "[TickArchiver] %s %d건 → %s",
                        table, info["archived"], info.get("file", ""),
                    )
                else:
                    logger.info("[TickArchiver] %s 아카이브 대상 없음", table)
        except ImportError:
            logger.warning("[TickArchiver] db.tick_archiver 모듈 없음 — 건너뜀")
        except Exception as e:
            logger.warning("[TickArchiver] 아카이브 중 오류 (무시): %s", e)

    def _on_stop(self) -> None:
        logger = logging.getLogger(__name__)
        logger.info("수집 중지 요청")

        # 즉시 GUI 갱신
        self._btn_stop.config(state="disabled")
        self._btn_start.config(state="normal")
        self._running = False
        self._start_time = None
        self._lbl_status.config(text="중지됨", fg=COLORS['text2'])
        self._lbl_conn.config(text="중지됨")
        self._dot.config(fg=COLORS['text2'])

        # WS 강제 종료 (join 없이 fire-and-forget)
        if self.ws_client is not None:
            ws = self.ws_client
            self.ws_client = None
            def _kill():
                try:
                    ws._running = False
                    if ws._ws and ws._loop and ws._loop.is_running():
                        asyncio.run_coroutine_threadsafe(ws._ws.close(), ws._loop)
                except Exception:
                    pass
                logger.info("WebSocket 정지 완료")
            threading.Thread(target=_kill, daemon=True).start()

    # ── 주기적 갱신 (1초) ────────────────────────────────────────────

    def _tick(self) -> None:
        """1초마다 현황 패널 갱신."""
        try:
            self._update_status()
        except Exception:
            pass
        self.root.after(1000, self._tick)

    def _update_status(self) -> None:
        if not self._running or self.ws_client is None:
            return

        stats = self.ws_client.get_stats()
        trade_count = stats.get("trade_count", 0)
        ob_count = stats.get("orderbook_count", 0)
        sub_count = stats.get("subscriptions", 0)
        is_running = stats.get("running", False)

        # 연결 상태
        if is_running:
            self._dot.config(fg=COLORS['success'])
            self._lbl_conn.config(text="연결됨", fg=COLORS['success'])
        else:
            self._dot.config(fg=COLORS['warning'])
            self._lbl_conn.config(text="재연결 중...", fg=COLORS['warning'])

        # 수집 시간
        elapsed = ""
        if self._start_time:
            elapsed = f" ({self._start_time.strftime('%H:%M:%S')}부터)"
        self._lbl_status.config(
            text=f"수집 중{elapsed}",
            fg=COLORS['success'],
        )

        self._lbl_trade.config(text=f"{trade_count:,}건")
        self._lbl_orderbook.config(text=f"{ob_count:,}건")
        self._lbl_sub.config(text=f"{sub_count}건")

        # DB 행 수: 10초마다 갱신
        now = time.monotonic()
        if self._db_path and (now - self._last_db_check) >= 10.0:
            self._last_db_check = now
            threading.Thread(
                target=self._fetch_db_counts,
                daemon=True,
            ).start()

    def _fetch_db_counts(self) -> None:
        """DB 행 수 조회 (백그라운드 스레드, timeout 포함)."""
        if not self._db_path:
            return
        try:
            conn = sqlite3.connect(self._db_path, timeout=2)
            conn.execute("PRAGMA journal_mode=WAL")
            trade_rows = conn.execute(
                "SELECT COUNT(*) FROM trade_ticks"
            ).fetchone()[0]
            rt_rows = conn.execute(
                "SELECT COUNT(*) FROM realtime_ticks"
            ).fetchone()[0]
            conn.close()
            self._db_trade_rows = trade_rows
            self._db_rt_rows = rt_rows
            self.root.after(0, self._update_db_label)
        except Exception:
            pass

    def _update_db_label(self) -> None:
        self._lbl_db.config(
            text=(
                f"trade_ticks {self._db_trade_rows:,}행 / "
                f"realtime_ticks {self._db_rt_rows:,}행"
            )
        )

    # ── 종료 ─────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        """윈도우 닫기 — 즉시 종료."""
        if self._running and self.ws_client is not None:
            if not messagebox.askyesno("종료 확인", "수집이 진행 중입니다.\n종료하시겠습니까?"):
                return
            self._running = False
            if self.ws_client:
                try:
                    self.ws_client._running = False
                except Exception:
                    pass
                self.ws_client = None
        self._do_destroy()

    def _do_destroy(self) -> None:
        """실제 윈도우 파괴."""
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        self.root.destroy()


# ── 엔트리포인트 ─────────────────────────────────────────────────────────

def main() -> None:
    root = tk.Tk()

    # ttk 스타일 (다크)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "TScrollbar",
        background=COLORS['bg3'],
        troughcolor=COLORS['bg2'],
        arrowcolor=COLORS['text2'],
        bordercolor=COLORS['border'],
    )

    app = DataCollectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
