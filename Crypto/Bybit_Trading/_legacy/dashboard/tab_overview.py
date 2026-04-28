"""대시보드 Tab 1: 개요 탭.

모의거래 시작/중지, 13코인 가격 테이블, PnL 요약 카드,
최근 시그널 목록을 제공한다.
이벤트 구동: mark_dirty() 호출 시 dirty 플래그를 세우고,
탭 전환 시 dirty면 refresh()를 자동 실행한다.
"""

from __future__ import annotations

import logging
import threading
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional
import tkinter as tk

from config.settings import backtest_config
from dashboard.base_tab import BaseDirtyTab
from dashboard.theme import Colors, Fonts

logger = logging.getLogger(__name__)


class OverviewTab(BaseDirtyTab):
    """개요 탭 - 전체 현황 한눈에 보기.

    구성:
        - 상단: 모의거래 시작/중지 버튼 + 상태 표시
        - 중단: 13코인 가격 테이블 (새로고침 버튼)
        - 하단 좌: PnL 요약 카드
        - 하단 우: 최근 시그널 5개
    """

    def __init__(self, parent: tk.Widget) -> None:
        """초기화.

        Args:
            parent: 부모 위젯 (Notebook)
        """
        super().__init__(parent)

        # 외부에서 주입되는 콜백
        self._trade_start_cb: Optional[Callable] = None
        self._trade_stop_cb: Optional[Callable] = None
        self._ws_client: Optional[Any] = None

        self._build_ui()

    # ── UI 구성 ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """전체 UI 구성."""
        self._build_control_panel()
        self._build_price_table()
        self._build_bottom_row()

    def _build_control_panel(self) -> None:
        """상단 컨트롤 패널 - 모의거래 시작/중지 + 상태 표시."""
        panel = tk.Frame(self, bg=Colors.BG_CARD, pady=8)
        panel.pack(fill=tk.X, padx=8, pady=(8, 4))

        # 왼쪽: 버튼 영역
        btn_frame = tk.Frame(panel, bg=Colors.BG_CARD)
        btn_frame.pack(side=tk.LEFT, padx=12)

        self._start_btn = tk.Button(
            btn_frame,
            text="모의거래 시작",
            font=Fonts.MONO_SMALL,
            bg=Colors.BTN_START,
            fg="#000000",
            activebackground="#00a87a",
            activeforeground="#000000",
            relief=tk.FLAT,
            cursor="hand2",
            width=16,
            command=self._on_trade_start,
        )
        self._start_btn.pack(side=tk.LEFT, padx=4)

        self._stop_btn = tk.Button(
            btn_frame,
            text="모의거래 중지",
            font=Fonts.MONO_SMALL,
            bg="#3a1a1a",
            fg=Colors.LOSS,
            activebackground="#5a2a2a",
            activeforeground=Colors.FG,
            relief=tk.FLAT,
            cursor="hand2",
            width=16,
            state=tk.DISABLED,
            command=self._on_trade_stop,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=4)

        # 오른쪽: 상태 표시
        status_frame = tk.Frame(panel, bg=Colors.BG_CARD)
        status_frame.pack(side=tk.RIGHT, padx=12)

        tk.Label(
            status_frame,
            text="WS 상태:",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG_DIM,
        ).pack(side=tk.LEFT)

        self._ws_status_label = tk.Label(
            status_frame,
            text="● 미연결",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.DISCONNECTED,
        )
        self._ws_status_label.pack(side=tk.LEFT, padx=6)

        tk.Label(
            status_frame,
            text="  거래 상태:",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG_DIM,
        ).pack(side=tk.LEFT)

        self._engine_status_label = tk.Label(
            status_frame,
            text="대기 중",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG_DIM,
        )
        self._engine_status_label.pack(side=tk.LEFT, padx=6)

    def _build_price_table(self) -> None:
        """13코인 가격 테이블."""
        frame = tk.Frame(self, bg=Colors.BG)
        frame.pack(fill=tk.X, padx=8, pady=4)

        # 헤더 행
        header_frame = tk.Frame(frame, bg=Colors.BG)
        header_frame.pack(fill=tk.X)

        tk.Label(
            header_frame,
            text="실시간 가격",
            font=Fonts.BODY_BOLD,
            bg=Colors.BG,
            fg=Colors.FG,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            header_frame,
            text="새로고침",
            font=Fonts.MONO_SMALL,
            bg=Colors.BTN_GRAY,
            fg=Colors.FG,
            activebackground=Colors.ACCENT,
            activeforeground=Colors.FG,
            relief=tk.FLAT,
            cursor="hand2",
            command=self.refresh,
        ).pack(side=tk.RIGHT, padx=4)

        # Treeview
        tree_frame = tk.Frame(frame, bg=Colors.BORDER, pady=1)
        tree_frame.pack(fill=tk.X, pady=(4, 0))

        style = ttk.Style()
        style.configure(
            "Price.Treeview",
            background=Colors.BG_CARD,
            foreground=Colors.FG,
            fieldbackground=Colors.BG_CARD,
            rowheight=22,
            font=Fonts.MONO_SMALL,
        )
        style.configure(
            "Price.Treeview.Heading",
            background=Colors.ACCENT,
            foreground=Colors.FG,
            font=Fonts.MONO_SMALL,
        )
        style.map("Price.Treeview", background=[("selected", Colors.ACCENT)])

        cols = ("symbol", "price", "change_pct")
        self._price_tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="headings",
            height=13,
            style="Price.Treeview",
        )

        self._price_tree.heading("symbol", text="심볼")
        self._price_tree.heading("price", text="현재가")
        self._price_tree.heading("change_pct", text="변동%")

        self._price_tree.column("symbol", width=120, anchor="w")
        self._price_tree.column("price", width=140, anchor="e")
        self._price_tree.column("change_pct", width=100, anchor="e")

        self._price_tree.pack(fill=tk.X)

        # 색상 태그
        self._price_tree.tag_configure("up", foreground=Colors.PROFIT)
        self._price_tree.tag_configure("down", foreground=Colors.LOSS)
        self._price_tree.tag_configure("neutral", foreground=Colors.FG)

    def _build_bottom_row(self) -> None:
        """하단 좌우 분할 - PnL 요약(좌) + 최근 시그널(우)."""
        row = tk.Frame(self, bg=Colors.BG)
        row.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        row.columnconfigure(0, weight=3)
        row.columnconfigure(1, weight=2)

        self._build_pnl_summary(row)
        self._build_recent_signals(row)

        # 전략 토글 (PnL/시그널 아래)
        self._build_strategy_toggles(self)

        # 전략 성과 메트릭 (Calmar/승률)
        self._build_strategy_metrics(self)

    def _build_pnl_summary(self, parent: tk.Frame) -> None:
        """PnL 요약 카드.

        Args:
            parent: 부모 grid 프레임
        """
        card = tk.Frame(parent, bg=Colors.BG_CARD, padx=12, pady=8)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        tk.Label(
            card,
            text="PnL 요약",
            font=Fonts.BODY_BOLD,
            bg=Colors.BG_CARD,
            fg=Colors.FG,
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        # 라벨 정의: (키, 화면 라벨)
        fields = [
            ("equity", "에퀴티"),
            ("unrealized_pnl", "미실현PnL"),
            ("daily_pnl", "일일PnL"),
            ("total_pnl", "누적PnL"),
            ("position_cnt", "포지션수"),
        ]
        self._pnl_labels: Dict[str, tk.Label] = {}

        for i, (key, lbl) in enumerate(fields):
            col = (i % 2) * 2
            r = 1 + (i // 2)
            tk.Label(
                card,
                text=lbl,
                font=Fonts.MONO_SMALL,
                bg=Colors.BG_CARD,
                fg=Colors.FG_DIM,
            ).grid(row=r, column=col, sticky="w", padx=4)

            val_lbl = tk.Label(
                card,
                text="--",
                font=Fonts.MONO_BOLD,
                bg=Colors.BG_CARD,
                fg=Colors.FG,
            )
            val_lbl.grid(row=r, column=col + 1, sticky="w", padx=(2, 16))
            self._pnl_labels[key] = val_lbl

    def _build_strategy_toggles(self, parent: tk.Frame) -> None:
        """전략별 활성화/비활성화 토글.

        Args:
            parent: 부모 프레임
        """
        card = tk.Frame(parent, bg=Colors.BG_CARD, padx=12, pady=8)
        card.pack(fill=tk.X, padx=8, pady=(0, 4))

        tk.Label(
            card, text="전략 ON/OFF", font=Fonts.BODY_BOLD,
            bg=Colors.BG_CARD, fg=Colors.FG,
        ).pack(anchor="w", pady=(0, 4))

        toggle_frame = tk.Frame(card, bg=Colors.BG_CARD)
        toggle_frame.pack(fill=tk.X)

        self._strategy_vars: Dict[str, tk.BooleanVar] = {}
        strategies = [
            ("BBKCSqueeze", "BBKCSqueeze (1h)", True),
            ("RSIMACDStrategy", "RSI+MACD (1h)", True),
            ("PairsTrading", "PairsTrading (1h)", True),
            ("IchimokuCloud", "IchimokuCloud (OFF)", False),
        ]
        for i, (key, label, default) in enumerate(strategies):
            var = tk.BooleanVar(value=default)
            self._strategy_vars[key] = var
            cb = tk.Checkbutton(
                toggle_frame, text=label, variable=var,
                bg=Colors.BG_CARD, fg=Colors.FG,
                selectcolor=Colors.BG, activebackground=Colors.BG_CARD,
                activeforeground=Colors.FG, font=Fonts.SMALL,
                command=lambda k=key, v=var: self._on_strategy_toggle(k, v.get()),
            )
            cb.grid(row=0, column=i, padx=8, sticky="w")

    def _build_strategy_metrics(self, parent: tk.Frame) -> None:
        """전략별 성과 메트릭 카드 (Calmar, 승률, 거래수).

        Args:
            parent: 부모 프레임
        """
        card = tk.Frame(parent, bg=Colors.BG_CARD, padx=12, pady=8)
        card.pack(fill=tk.X, padx=8, pady=(0, 4))

        tk.Label(
            card, text="전략 성과", font=Fonts.BODY_BOLD,
            bg=Colors.BG_CARD, fg=Colors.FG,
        ).pack(anchor="w", pady=(0, 4))

        cols = ("strategy", "calmar", "winrate", "wr_std", "trades", "status")
        style = ttk.Style()
        style.configure(
            "Metrics.Treeview",
            background=Colors.BG_CARD,
            foreground=Colors.FG,
            fieldbackground=Colors.BG_CARD,
            rowheight=22,
            font=Fonts.MONO_SMALL,
        )
        style.configure(
            "Metrics.Treeview.Heading",
            background=Colors.ACCENT,
            foreground=Colors.FG,
            font=Fonts.MONO_SMALL,
        )
        self._metrics_tree = ttk.Treeview(
            card, columns=cols, show="headings", height=4,
            style="Metrics.Treeview",
        )
        self._metrics_tree.heading("strategy", text="전략")
        self._metrics_tree.heading("calmar", text="Calmar")
        self._metrics_tree.heading("winrate", text="승률")
        self._metrics_tree.heading("wr_std", text="월별WR편차")
        self._metrics_tree.heading("trades", text="거래수")
        self._metrics_tree.heading("status", text="상태")

        self._metrics_tree.column("strategy", width=120, anchor="w")
        self._metrics_tree.column("calmar", width=70, anchor="e")
        self._metrics_tree.column("winrate", width=60, anchor="e")
        self._metrics_tree.column("wr_std", width=80, anchor="e")
        self._metrics_tree.column("trades", width=60, anchor="e")
        self._metrics_tree.column("status", width=60, anchor="center")

        self._metrics_tree.pack(fill=tk.X, pady=(0, 4))

    def _on_strategy_toggle(self, strategy_name: str, enabled: bool) -> None:
        """전략 토글 콜백."""
        if self._engine is not None:
            self._engine.set_strategy_enabled(strategy_name, enabled)
            status = "ON" if enabled else "OFF"
            logger.info("전략 %s -> %s", strategy_name, status)

    def _build_recent_signals(self, parent: tk.Frame) -> None:
        """최근 시그널 목록 (최대 5건).

        Args:
            parent: 부모 grid 프레임
        """
        card = tk.Frame(parent, bg=Colors.BG_CARD, padx=12, pady=8)
        card.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        header_row = tk.Frame(card, bg=Colors.BG_CARD)
        header_row.pack(fill=tk.X)

        tk.Label(
            header_row,
            text="최근 시그널",
            font=Fonts.BODY_BOLD,
            bg=Colors.BG_CARD,
            fg=Colors.FG,
        ).pack(side=tk.LEFT)

        style = ttk.Style()
        style.configure(
            "Signal.Treeview",
            background=Colors.BG_CARD,
            foreground=Colors.FG,
            fieldbackground=Colors.BG_CARD,
            rowheight=20,
            font=Fonts.MONO_SMALL,
        )
        style.configure(
            "Signal.Treeview.Heading",
            background=Colors.ACCENT,
            foreground=Colors.FG,
            font=Fonts.MONO_SMALL,
        )

        cols = ("time", "strategy", "symbol", "direction")
        self._signal_tree = ttk.Treeview(
            card,
            columns=cols,
            show="headings",
            height=5,
            style="Signal.Treeview",
        )
        self._signal_tree.heading("time", text="시각")
        self._signal_tree.heading("strategy", text="전략")
        self._signal_tree.heading("symbol", text="심볼")
        self._signal_tree.heading("direction", text="방향")

        self._signal_tree.column("time", width=75, anchor="w")
        self._signal_tree.column("strategy", width=80, anchor="w")
        self._signal_tree.column("symbol", width=90, anchor="w")
        self._signal_tree.column("direction", width=55, anchor="center")

        self._signal_tree.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        self._signal_tree.tag_configure("long", foreground=Colors.PROFIT)
        self._signal_tree.tag_configure("short", foreground=Colors.LOSS)
        self._signal_tree.tag_configure("exit", foreground=Colors.WARNING)

    # ── 버튼 핸들러 ──────────────────────────────────────────────────────

    def _on_trade_start(self) -> None:
        """모의거래 시작."""
        if self._trade_start_cb:
            threading.Thread(target=self._trade_start_cb, daemon=True).start()
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._engine_status_label.config(text="거래 실행 중", fg=Colors.PROFIT)

    def _on_trade_stop(self) -> None:
        """모의거래 중지."""
        if self._trade_stop_cb:
            self._trade_stop_cb()
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._engine_status_label.config(text="중지됨", fg=Colors.WARNING)

    # ── 이벤트 구동 갱신 ─────────────────────────────────────────────────

    def refresh(self) -> None:
        """가격 테이블 + 시그널 목록 + PnL 요약 + 전략 메트릭을 갱신한다."""
        self._dirty = False
        self._refresh_prices()
        self._refresh_signals()
        self._refresh_pnl()
        self._refresh_ws_status()
        self._refresh_strategy_metrics()

    def _refresh_prices(self) -> None:
        """엔진 버퍼 또는 DB에서 각 심볼의 최신 종가를 가져와 가격 테이블을 갱신한다."""
        if self._db is None and self._engine is None:
            return
        try:
            from config.settings import AppSettings
            symbols = AppSettings().symbols
            prices: Dict[str, dict] = {}

            # 엔진 버퍼에서 최신 가격 조회 (우선)
            engine_prices: Dict[str, float] = {}
            if self._engine is not None and hasattr(self._engine, "get_latest_prices"):
                try:
                    engine_prices = self._engine.get_latest_prices()
                except Exception as exc:
                    logger.debug("엔진 가격 조회 실패: %s", exc)

            for symbol in symbols:
                if symbol in engine_prices and engine_prices[symbol] > 0:
                    close_price = engine_prices[symbol]
                    # 변동률은 DB에서 이전 봉 조회
                    change_pct = 0.0
                    if self._db is not None:
                        try:
                            rows = self._db.get_ohlcv(symbol, "15", limit=2)
                            if rows is not None and not rows.empty and len(rows) >= 2:
                                prev_close = float(rows.iloc[-2].get("close", 0))
                                if prev_close > 0:
                                    change_pct = (close_price - prev_close) / prev_close * 100
                        except Exception as exc:
                            logger.debug("변동률 조회 실패 %s: %s", symbol, exc)
                    prices[symbol] = {
                        "price": close_price,
                        "change_pct": change_pct,
                    }
                elif self._db is not None:
                    # DB fallback
                    rows = self._db.get_ohlcv(symbol, "15", limit=2)
                    if rows is not None and not rows.empty and len(rows) >= 1:
                        latest = rows.iloc[-1]
                        close_price = float(latest.get("close", 0))
                        if len(rows) >= 2:
                            prev_close = float(rows.iloc[-2].get("close", 0))
                            change_pct = (
                                ((close_price - prev_close) / prev_close * 100)
                                if prev_close else 0.0
                            )
                        else:
                            change_pct = 0.0
                        prices[symbol] = {
                            "price": close_price,
                            "change_pct": change_pct,
                        }
            self._update_price_tree(prices)
        except Exception as exc:
            logger.debug("가격 갱신 오류: %s", exc)

    def _refresh_signals(self) -> None:
        """시그널 목록 갱신."""
        if self._db is None:
            return
        try:
            signals = self._db.get_recent_signals(limit=5)
            self._update_signal_tree(signals)
        except Exception as exc:
            logger.debug("시그널 갱신 오류: %s", exc)

    def _refresh_pnl(self) -> None:
        """API에서 잔고/포지션을 직접 조회하고, DB에서 일일PnL을 갱신한다."""
        try:
            # 1. API에서 실제 잔고 조회
            if self._rest_client is not None:
                try:
                    bal = self._rest_client.get_wallet_balance()
                    if bal:
                        for coin in bal.get("coin", []):
                            if coin.get("coin") == "USDT":
                                equity = float(coin.get("walletBalance", 0))
                                self._pnl_labels["equity"].config(
                                    text=f"{equity:,.0f} USDT",
                                )
                                break
                except Exception as exc:
                    logger.debug("잔고 API 조회 실패: %s", exc)

                # 2. API에서 실제 포지션 수 + 미실현PnL 조회
                try:
                    positions = self._rest_client.get_positions()
                    self._pnl_labels["position_cnt"].config(
                        text=f"{len(positions)}건",
                    )
                    # 미실현PnL 합산
                    total_unrealized = sum(
                        float(p.get("unrealisedPnl", 0))
                        for p in positions
                    )
                    self._pnl_labels["unrealized_pnl"].config(
                        text=f"{'+' if total_unrealized >= 0 else ''}{total_unrealized:,.2f}",
                        fg=Colors.PROFIT if total_unrealized >= 0 else Colors.LOSS,
                    )
                except Exception as exc:
                    logger.debug("포지션 API 조회 실패: %s", exc)

            # 3. DB에서 일일 PnL
            if self._db is not None:
                try:
                    daily_pnl = self._db.get_daily_pnl()
                    self._pnl_labels["daily_pnl"].config(
                        text=f"{'+' if daily_pnl >= 0 else ''}{daily_pnl:,.2f}",
                        fg=Colors.PROFIT if daily_pnl >= 0 else Colors.LOSS,
                    )
                except Exception as exc:
                    logger.debug("일일PnL DB 조회 실패: %s", exc)
        except Exception as exc:
            logger.warning("PnL 갱신 오류: %s", exc)

    def _refresh_strategy_metrics(self) -> None:
        """전략 성과 메트릭 트리뷰를 갱신한다."""
        if self._engine is None:
            return
        try:
            metrics = self._engine.get_strategy_metrics()
            enabled = self._engine.get_strategy_enabled()

            # 트리뷰 초기화
            for item in self._metrics_tree.get_children():
                self._metrics_tree.delete(item)

            for strat in ["PairsTrading", "BBKCSqueeze", "IchimokuCloud", "RSIMACDStrategy"]:
                m = metrics.get(strat, {})
                calmar = m.get("calmar", 0.0)
                winrate = m.get("winrate", 0.0)
                wr_std = m.get("monthly_wr_std", 0.0)
                trades = m.get("trade_count", 0)
                on_off = "ON" if enabled.get(strat, False) else "OFF"

                self._metrics_tree.insert("", "end", values=(
                    strat,
                    f"{calmar:.2f}",
                    f"{winrate*100:.1f}%",
                    f"{wr_std*100:.1f}%",
                    str(trades),
                    on_off,
                ))

            # 토글 체크박스 동기화 (자동 비활성화 반영)
            for strat, var in self._strategy_vars.items():
                var.set(enabled.get(strat, True))

        except Exception as exc:
            logger.debug("전략 메트릭 갱신 오류: %s", exc)

    def _refresh_ws_status(self) -> None:
        """WS 연결 상태를 갱신한다."""
        connected = (
            self._ws_client is not None
            and hasattr(self._ws_client, "is_running")
            and self._ws_client.is_running
        )
        if connected:
            self._ws_status_label.config(text="● 연결됨", fg=Colors.CONNECTED)
        else:
            self._ws_status_label.config(text="● 미연결", fg=Colors.DISCONNECTED)

    # ── 내부 UI 갱신 ─────────────────────────────────────────────────────

    def _update_price_tree(self, prices: Dict[str, dict]) -> None:
        """가격 Treeview 갱신.

        Args:
            prices: {symbol: {"price": float, "change_pct": float}}
        """
        for item in self._price_tree.get_children():
            self._price_tree.delete(item)

        for symbol, data in prices.items():
            price = data.get("price", 0.0)
            change = data.get("change_pct", 0.0)
            sign = "+" if change >= 0 else ""
            tag = "up" if change > 0 else ("down" if change < 0 else "neutral")
            self._price_tree.insert(
                "",
                "end",
                values=(symbol, f"{price:,.4f}", f"{sign}{change:.2f}%"),
                tags=(tag,),
            )

    def _update_signal_tree(self, signals: List[dict]) -> None:
        """시그널 Treeview 갱신.

        Args:
            signals: signal_log 딕셔너리 리스트
        """
        for item in self._signal_tree.get_children():
            self._signal_tree.delete(item)

        for sig in signals[:5]:
            ts = sig.get("timestamp", "")
            if ts and len(str(ts)) >= 16:
                ts_str = str(ts)[11:16]
            else:
                ts_str = str(ts)[:8]
            direction = sig.get("direction", "")
            tag = (
                "long" if direction == "LONG"
                else ("short" if direction == "SHORT" else "exit")
            )
            self._signal_tree.insert(
                "",
                "end",
                values=(
                    ts_str,
                    sig.get("strategy", "")[:10],
                    sig.get("symbol", ""),
                    direction,
                ),
                tags=(tag,),
            )

    # ── 외부 주입 ─────────────────────────────────────────────────────────

    def set_references(
        self,
        db: Any = None,
        ws_client: Any = None,
        engine: Any = None,
    ) -> None:
        """외부 컴포넌트 참조를 주입한다.

        Args:
            db: DBManager 인스턴스
            ws_client: BybitWebSocketClient 인스턴스
            engine: TradingEngine 인스턴스
        """
        if db is not None:
            self._db = db
        self._ws_client = ws_client
        self._engine = engine
        self._refresh_ws_status()

    def set_callbacks(
        self,
        trade_start: Optional[Callable] = None,
        trade_stop: Optional[Callable] = None,
    ) -> None:
        """버튼 콜백 주입.

        Args:
            trade_start: 모의거래 시작 콜백
            trade_stop: 모의거래 중지 콜백
        """
        self._trade_start_cb = trade_start
        self._trade_stop_cb = trade_stop
