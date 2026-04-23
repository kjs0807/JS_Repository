"""해외선물 통합 대시보드 메인 애플리케이션."""

import logging
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Dict, Optional

from dashboard.themes import Colors, Fonts
from dashboard.widgets import CardFrame, PnLSummary, PositionRow, StatusIndicator
from config.products import PRODUCTS
from scheduler.exchange_hours import is_exchange_open

logger = logging.getLogger(__name__)


class OverseasFuturesDashboard:
    """해외선물 통합 대시보드."""

    def __init__(self, master: tk.Tk):
        self.master = master
        self.master.title("OverseasFutures - 해외선물 Paper Trading")
        self.master.configure(bg=Colors.BG)
        self.master.geometry("1400x900")

        # 외부 객체 참조 (main.py에서 설정)
        self.trade_manager = None
        self.virtual_account = None
        self.poll_scheduler = None
        self._state_persistence = None

        self._build_ui()
        self._update_clock()

    # ------------------------------------------------------------------ #
    # UI 구성
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        """전체 UI 구성."""
        self._build_top_bar()

        main_paned = tk.PanedWindow(
            self.master,
            orient=tk.HORIZONTAL,
            bg=Colors.BG,
            sashwidth=3,
            sashrelief=tk.FLAT,
        )
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._build_price_panel(main_paned)
        self._build_right_panel(main_paned)

    def _build_top_bar(self) -> None:
        """상단 바: 제목, 시계, 거래소 상태."""
        top = tk.Frame(self.master, bg=Colors.BG, height=50)
        top.pack(fill=tk.X, padx=10, pady=(10, 0))

        tk.Label(
            top,
            text="해외선물 Paper Trading",
            font=Fonts.TITLE,
            bg=Colors.BG,
            fg=Colors.FG,
        ).pack(side=tk.LEFT)

        self.clock_label = tk.Label(top, font=Fonts.MONO, bg=Colors.BG, fg=Colors.TEAL)
        self.clock_label.pack(side=tk.RIGHT, padx=20)

        self.exchange_indicators: Dict[str, StatusIndicator] = {}
        exchanges_frame = tk.Frame(top, bg=Colors.BG)
        exchanges_frame.pack(side=tk.RIGHT)
        for exch in ["EUREX", "OSE", "HKEx", "ASX", "FTX"]:
            indicator = StatusIndicator(exchanges_frame, exch)
            indicator.pack(side=tk.LEFT, padx=3)
            self.exchange_indicators[exch] = indicator

    def _build_price_panel(self, parent: tk.PanedWindow) -> None:
        """가격 그리드 — 거래소별 탭."""
        left_frame = tk.Frame(parent, bg=Colors.BG)
        parent.add(left_frame, width=850)

        # 다크 테마 Notebook 스타일
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Dark.TNotebook",
            background=Colors.BG,
            borderwidth=0,
        )
        style.configure(
            "Dark.TNotebook.Tab",
            background=Colors.BG_CARD,
            foreground=Colors.FG,
            padding=[12, 4],
        )
        style.map(
            "Dark.TNotebook.Tab",
            background=[("selected", Colors.BG_INPUT)],
            foreground=[("selected", Colors.MAUVE)],
        )

        notebook = ttk.Notebook(left_frame, style="Dark.TNotebook")
        notebook.pack(fill=tk.BOTH, expand=True)

        self.price_labels: Dict[str, Dict[str, tk.Label]] = {}
        self.state_labels: Dict[str, tk.Label] = {}

        # 거래소별 상품 분류
        exchanges: Dict[str, list] = {
            "EUREX": [], "OSE": [], "HKEx": [], "ASX": [], "FTX": []
        }
        for sym, prod in PRODUCTS.items():
            if prod.exch_cd in exchanges:
                exchanges[prod.exch_cd].append((sym, prod))

        col_headers = ["종목", "KIS코드", "현재가", "전일비", "상태", "포지션", "미실현PnL"]
        col_widths = [12, 10, 12, 10, 10, 8, 14]

        for exch, products in exchanges.items():
            tab = tk.Frame(notebook, bg=Colors.BG)
            notebook.add(tab, text=f" {exch} ({len(products)}) ")

            # 헤더 행
            header = tk.Frame(tab, bg=Colors.BG_CARD)
            header.pack(fill=tk.X, padx=5, pady=(5, 0))
            for h, w in zip(col_headers, col_widths):
                tk.Label(
                    header,
                    text=h,
                    width=w,
                    font=Fonts.HEADER,
                    bg=Colors.BG_CARD,
                    fg=Colors.FG_DIM,
                    anchor="center",
                ).pack(side=tk.LEFT, padx=2, pady=4)

            # 구분선
            tk.Frame(tab, bg=Colors.BORDER, height=1).pack(
                fill=tk.X, padx=5, pady=(0, 2)
            )

            # 상품 행
            for sym, prod in products:
                row = tk.Frame(
                    tab,
                    bg=Colors.BG,
                    highlightbackground=Colors.BORDER,
                    highlightthickness=1,
                )
                row.pack(fill=tk.X, padx=5, pady=1)

                self.price_labels[sym] = {}

                tk.Label(
                    row, text=prod.name_kr, width=12, font=Fonts.BODY,
                    bg=Colors.BG, fg=Colors.FG, anchor="w",
                ).pack(side=tk.LEFT, padx=2, pady=3)

                tk.Label(
                    row, text=prod.kis_code, width=10, font=Fonts.MONO_SMALL,
                    bg=Colors.BG, fg=Colors.FG_DIM, anchor="center",
                ).pack(side=tk.LEFT, padx=2)

                price_lbl = tk.Label(
                    row, text="--", width=12, font=Fonts.MONO,
                    bg=Colors.BG, fg=Colors.FG, anchor="e",
                )
                price_lbl.pack(side=tk.LEFT, padx=2)
                self.price_labels[sym]["price"] = price_lbl

                change_lbl = tk.Label(
                    row, text="--", width=10, font=Fonts.MONO_SMALL,
                    bg=Colors.BG, fg=Colors.FG_DIM, anchor="e",
                )
                change_lbl.pack(side=tk.LEFT, padx=2)
                self.price_labels[sym]["change"] = change_lbl

                state_lbl = tk.Label(
                    row, text="FLAT", width=10, font=Fonts.MONO_SMALL,
                    bg=Colors.BG, fg=Colors.FG_DIM, anchor="center",
                )
                state_lbl.pack(side=tk.LEFT, padx=2)
                self.state_labels[sym] = state_lbl

                pos_lbl = tk.Label(
                    row, text="--", width=8, font=Fonts.MONO_SMALL,
                    bg=Colors.BG, fg=Colors.FG_DIM, anchor="center",
                )
                pos_lbl.pack(side=tk.LEFT, padx=2)
                self.price_labels[sym]["position"] = pos_lbl

                pnl_lbl = tk.Label(
                    row, text="--", width=14, font=Fonts.MONO_SMALL,
                    bg=Colors.BG, fg=Colors.FG_DIM, anchor="e",
                )
                pnl_lbl.pack(side=tk.LEFT, padx=2)
                self.price_labels[sym]["pnl"] = pnl_lbl

    def _build_right_panel(self, parent: tk.PanedWindow) -> None:
        """우측 패널: PnL 요약 + 포지션 + 컨트롤."""
        right_frame = tk.Frame(parent, bg=Colors.BG)
        parent.add(right_frame, width=500)

        # PnL 요약
        self.pnl_summary = PnLSummary(right_frame)
        self.pnl_summary.pack(fill=tk.X, padx=5, pady=5)

        # 활성 포지션 카드
        pos_card = CardFrame(right_frame, title="활성 포지션")
        pos_card.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 포지션 헤더
        pos_header = tk.Frame(pos_card, bg=Colors.BG_CARD)
        pos_header.pack(fill=tk.X, padx=10, pady=(0, 2))
        for text, width in [("심볼", 6), ("방향", 5), ("수량", 6), ("평균단가", 12), ("미실현PnL", 18)]:
            tk.Label(
                pos_header,
                text=text,
                width=width,
                font=Fonts.MONO_SMALL,
                bg=Colors.BG_CARD,
                fg=Colors.FG_DIM,
                anchor="e" if text not in ("심볼", "방향") else "w",
            ).pack(side=tk.LEFT, padx=2)

        # 포지션 스크롤 영역
        pos_scroll_frame = tk.Frame(pos_card, bg=Colors.BG_CARD)
        pos_scroll_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        pos_canvas = tk.Canvas(
            pos_scroll_frame, bg=Colors.BG_CARD, highlightthickness=0
        )
        pos_scrollbar = tk.Scrollbar(
            pos_scroll_frame, orient=tk.VERTICAL, command=pos_canvas.yview
        )
        pos_canvas.configure(yscrollcommand=pos_scrollbar.set)

        pos_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        pos_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.positions_frame = tk.Frame(pos_canvas, bg=Colors.BG_CARD)
        self._pos_canvas_window = pos_canvas.create_window(
            (0, 0), window=self.positions_frame, anchor="nw"
        )

        def _on_pos_configure(event: tk.Event) -> None:
            pos_canvas.configure(scrollregion=pos_canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event) -> None:
            pos_canvas.itemconfig(self._pos_canvas_window, width=event.width)

        self.positions_frame.bind("<Configure>", _on_pos_configure)
        pos_canvas.bind("<Configure>", _on_canvas_configure)

        self.position_widgets: Dict[str, PositionRow] = {}

        # 컨트롤 카드
        ctrl_card = CardFrame(right_frame, title="제어")
        ctrl_card.pack(fill=tk.X, padx=5, pady=5)

        btn_frame = tk.Frame(ctrl_card, bg=Colors.BG_CARD)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        self.start_btn = tk.Button(
            btn_frame,
            text="폴링 시작",
            font=Fonts.BODY,
            bg=Colors.GREEN,
            fg=Colors.BG,
            activebackground=Colors.TEAL,
            activeforeground=Colors.BG,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_start,
        )
        self.start_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self.stop_btn = tk.Button(
            btn_frame,
            text="폴링 중지",
            font=Fonts.BODY,
            bg=Colors.RED,
            fg=Colors.BG,
            activebackground=Colors.ORANGE,
            activeforeground=Colors.BG,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_stop,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self.save_btn = tk.Button(
            btn_frame,
            text="상태 저장",
            font=Fonts.BODY,
            bg=Colors.BLUE,
            fg=Colors.BG,
            activebackground=Colors.MAUVE,
            activeforeground=Colors.BG,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_save,
        )
        self.save_btn.pack(side=tk.LEFT, padx=5, pady=5)

        # 상태 표시줄
        self.status_var = tk.StringVar(value="대기 중")
        tk.Label(
            ctrl_card,
            textvariable=self.status_var,
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.TEAL,
            anchor="w",
        ).pack(padx=10, pady=(0, 8), anchor="w")

    # ------------------------------------------------------------------ #
    # 시계 및 거래소 상태 갱신
    # ------------------------------------------------------------------ #

    def _update_clock(self) -> None:
        """시계 업데이트 (1초 주기)."""
        now = datetime.now()
        self.clock_label.config(text=now.strftime("KST %Y-%m-%d %H:%M:%S"))

        for exch, indicator in self.exchange_indicators.items():
            indicator.set_open(is_exchange_open(exch, now))

        self.master.after(1000, self._update_clock)

    # ------------------------------------------------------------------ #
    # 데이터 업데이트 (외부에서 호출)
    # ------------------------------------------------------------------ #

    def update_prices(self, price_data: Dict[str, dict]) -> None:
        """가격 데이터 업데이트.

        Args:
            price_data: {symbol: {"price": float, "change": float}}
        """
        for sym, data in price_data.items():
            if sym not in self.price_labels:
                continue
            labels = self.price_labels[sym]
            price = data.get("price")
            if price is None:
                continue

            labels["price"].config(text=f"{price:,.2f}")
            change = data.get("change", 0.0)
            if change > 0:
                labels["price"].config(fg=Colors.GREEN)
                labels["change"].config(text=f"+{change:,.2f}", fg=Colors.GREEN)
            elif change < 0:
                labels["price"].config(fg=Colors.RED)
                labels["change"].config(text=f"{change:,.2f}", fg=Colors.RED)
            else:
                labels["price"].config(fg=Colors.FG)
                labels["change"].config(text="0.00", fg=Colors.FG_DIM)

    def update_states(self, state_data: Dict[str, str]) -> None:
        """FSM 상태 텍스트 업데이트.

        Args:
            state_data: {symbol: state_string}
        """
        for sym, state in state_data.items():
            if sym not in self.state_labels:
                continue
            lbl = self.state_labels[sym]
            lbl.config(text=state)
            if state == "FLAT":
                lbl.config(fg=Colors.FG_DIM)
            elif "LONG" in state:
                lbl.config(fg=Colors.GREEN)
            elif "SHORT" in state:
                lbl.config(fg=Colors.RED)
            else:
                lbl.config(fg=Colors.YELLOW)

    def update_positions(self, positions: Dict[str, Optional[dict]]) -> None:
        """포지션 패널 전체 갱신.

        Args:
            positions: {symbol: {"side": str, "qty": float, "avg_price": float,
                                  "unrealized_pnl": float, "currency": str} | None}
        """
        # 기존 위젯 제거
        for w in self.position_widgets.values():
            w.destroy()
        self.position_widgets.clear()

        # 포지션이 없는 항목은 가격 그리드 초기화
        for sym in self.price_labels:
            labels = self.price_labels[sym]
            if sym not in positions or positions[sym] is None:
                labels["position"].config(text="--", fg=Colors.FG_DIM)
                labels["pnl"].config(text="--", fg=Colors.FG_DIM)

        for sym, pos in positions.items():
            if pos is None:
                continue

            row = PositionRow(
                self.positions_frame,
                sym,
                pos["side"],
                pos["qty"],
                pos["avg_price"],
                pos.get("unrealized_pnl", 0.0),
                pos.get("currency", ""),
            )
            row.pack(fill=tk.X, padx=5, pady=1)
            self.position_widgets[sym] = row

            # 가격 그리드 포지션 컬럼 갱신
            if sym in self.price_labels:
                side_char = "L" if pos["side"] == "LONG" else "S"
                self.price_labels[sym]["position"].config(
                    text=f"{side_char}{pos['qty']}",
                    fg=Colors.GREEN if pos["side"] == "LONG" else Colors.RED,
                )
                pnl = pos.get("unrealized_pnl", 0.0)
                ccy = pos.get("currency", "")
                self.price_labels[sym]["pnl"].config(
                    text=f"{pnl:+,.2f} {ccy}",
                    fg=Colors.GREEN if pnl >= 0 else Colors.RED,
                )

    # ------------------------------------------------------------------ #
    # 버튼 핸들러
    # ------------------------------------------------------------------ #

    def _on_start(self) -> None:
        """폴링 시작 버튼."""
        if self.poll_scheduler is not None:
            self.poll_scheduler.start()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("폴링 진행 중...")
        logger.info("폴링 시작")

    def _on_stop(self) -> None:
        """폴링 중지 버튼."""
        if self.poll_scheduler is not None:
            self.poll_scheduler.stop()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_var.set("폴링 중지됨")
        logger.info("폴링 중지")

    def _on_save(self) -> None:
        """상태 저장 버튼."""
        self.status_var.set("상태 저장 중...")
        try:
            if self._state_persistence is not None:
                self._state_persistence.save()
            self.status_var.set("상태 저장 완료")
            logger.info("상태 저장 완료")
        except Exception as exc:
            self.status_var.set(f"저장 실패: {exc}")
            logger.error("상태 저장 실패: %s", exc)

    # ------------------------------------------------------------------ #
    # 외부 참조 설정
    # ------------------------------------------------------------------ #

    def set_references(
        self,
        trade_manager=None,
        virtual_account=None,
        poll_scheduler=None,
        state_persistence=None,
    ) -> None:
        """외부 객체 참조 설정 (main.py에서 호출).

        Args:
            trade_manager: TradeManager 인스턴스
            virtual_account: VirtualAccount 인스턴스
            poll_scheduler: PollScheduler 인스턴스
            state_persistence: StatePersistence 인스턴스
        """
        self.trade_manager = trade_manager
        self.virtual_account = virtual_account
        self.poll_scheduler = poll_scheduler
        self._state_persistence = state_persistence


def create_dashboard() -> tuple:
    """대시보드 생성 및 반환.

    Returns:
        (root, dashboard) 튜플
    """
    root = tk.Tk()
    dashboard = OverseasFuturesDashboard(root)
    return root, dashboard
