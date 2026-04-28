"""Bybit 모의거래 대시보드 메인 애플리케이션.

7개 탭: 개요, IchimokuCloud, BBKCSqueeze, RSI+MACD, PairsTrading, 포지션/PnL, 시그널 로그
하단 상태바: 연결 상태 + 활성 전략 수 + 일일 PnL

갱신 방식: 이벤트 구동 + 활성 탭 lazy refresh
  - 봉 확정/시그널/거래 발생 시 dirty flag 설정
  - 사용자가 탭 전환 시 dirty 탭이면 자동 갱신
  - 상태바만 10초 주기 자동 갱신 (경량)
  - 각 탭에 [새로고침] 버튼도 유지 (수동)
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk
from typing import Any, Dict, List, Optional

from config.settings import backtest_config
from dashboard.theme import Colors, Fonts, apply_dark_notebook_style
from dashboard.tab_overview import OverviewTab
from dashboard.tab_ichimoku import IchimokuTab
from dashboard.tab_bbkc import BBKCTab
from dashboard.tab_rsi_macd import RSIMACDTab
from dashboard.tab_pairs import PairsTab
from dashboard.tab_positions import PositionsTab
from dashboard.tab_signals import SignalLogTab

logger = logging.getLogger(__name__)


class BybitDashboard:
    """Bybit 모의거래 통합 대시보드.

    7개 탭을 관리하고, 이벤트 구동 + lazy refresh 방식으로 갱신한다.
    """

    def __init__(self, master: tk.Tk) -> None:
        """초기화.

        Args:
            master: Tk 루트 윈도우 또는 Toplevel
        """
        self.master = master
        self.master.title("Bybit Crypto Futures Paper Trading System")
        self.master.configure(bg=Colors.BG)
        self.master.geometry("1400x880")
        self.master.minsize(1100, 720)

        # 외부 컴포넌트 참조
        self._db: Optional[Any] = None
        self._ws_client: Optional[Any] = None
        self._engine: Optional[Any] = None

        # 대시보드 열자마자 rest_client 생성 (모의거래 시작 전에도 잔고/포지션 조회 가능)
        try:
            from api.rest_client import BybitRestClient
            from config.settings import settings
            self._rest_client: Optional[Any] = BybitRestClient(base_url=settings.base_url)
        except Exception:
            self._rest_client = None

        # 모든 탭 참조 리스트 (인덱스 순)
        self._all_tabs: List[Any] = []

        self._build_ui()
        self._update_clock()

    # ── UI 구성 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """전체 UI 구성."""
        self._build_top_bar()
        self._build_notebook()
        self._build_status_bar()

    def _build_top_bar(self) -> None:
        """상단 타이틀 바."""
        top = tk.Frame(self.master, bg=Colors.BG_CARD, height=44)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        tk.Label(
            top,
            text="Bybit Crypto Futures  |  Paper Trading",
            font=Fonts.TITLE,
            bg=Colors.BG_CARD,
            fg=Colors.FG,
        ).pack(side=tk.LEFT, padx=16, pady=10)

        # 우측: 시계 + 네트워크 상태
        self._clock_label = tk.Label(
            top, text="",
            font=Fonts.MONO,
            bg=Colors.BG_CARD,
            fg=Colors.PROFIT,
        )
        self._clock_label.pack(side=tk.RIGHT, padx=16)

        self._net_status_label = tk.Label(
            top, text="● WS: --",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.NEUTRAL,
        )
        self._net_status_label.pack(side=tk.RIGHT, padx=8)

    def _build_notebook(self) -> None:
        """메인 탭 Notebook - 7개 탭."""
        style = ttk.Style()
        apply_dark_notebook_style(style)

        self._notebook = ttk.Notebook(self.master, style="Dark.TNotebook")
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 0))

        # 탭 전환 이벤트 바인딩
        self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # 탭 1: 개요
        self.tab_overview = OverviewTab(self._notebook)
        self._notebook.add(self.tab_overview, text="  개요  ")

        # 탭 2: IchimokuCloud
        self.tab_ichimoku = IchimokuTab(self._notebook)
        self._notebook.add(self.tab_ichimoku, text="  IchimokuCloud  ")

        # 탭 3: BBKCSqueeze
        self.tab_bbkc = BBKCTab(self._notebook)
        self._notebook.add(self.tab_bbkc, text="  BBKCSqueeze  ")

        # 탭 4: RSI+MACD
        self.tab_rsi_macd = RSIMACDTab(self._notebook)
        self._notebook.add(self.tab_rsi_macd, text="  RSI+MACD  ")

        # 탭 5: PairsTrading
        self.tab_pairs = PairsTab(self._notebook)
        self._notebook.add(self.tab_pairs, text="  PairsTrading  ")

        # 탭 6: 포지션/PnL
        self.tab_positions = PositionsTab(self._notebook)
        self._notebook.add(self.tab_positions, text="  포지션/PnL  ")

        # 탭 7: 시그널 로그
        self.tab_signals = SignalLogTab(self._notebook)
        self._notebook.add(self.tab_signals, text="  시그널 로그  ")

        # 탭 참조 리스트 (인덱스 순)
        self._all_tabs = [
            self.tab_overview,
            self.tab_ichimoku,
            self.tab_bbkc,
            self.tab_rsi_macd,
            self.tab_pairs,
            self.tab_positions,
            self.tab_signals,
        ]

    def _build_status_bar(self) -> None:
        """하단 상태바 (연결 상태 + 활성 전략 수 + 일일 PnL)."""
        # 구분선
        tk.Frame(self.master, bg=Colors.BORDER, height=1).pack(
            fill=tk.X, side=tk.BOTTOM,
        )

        bar = tk.Frame(self.master, bg=Colors.BG_CARD, height=28)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)

        # 상태 항목들
        self._status_items: Dict[str, tk.Label] = {}

        items = [
            ("connection", "● 미연결", Colors.DISCONNECTED),
            ("strategies", "활성 전략: --", Colors.FG),
            ("equity", "에퀴티: --", Colors.FG),
            ("daily_pnl", "일일PnL: --", Colors.FG),
            ("positions", "포지션: 0건", Colors.FG),
            ("mode", "모드: Demo", Colors.WARNING),
        ]

        for key, text, color in items:
            lbl = tk.Label(
                bar, text=text,
                font=Fonts.MONO_SMALL,
                bg=Colors.BG_CARD,
                fg=color,
            )
            lbl.pack(side=tk.LEFT, padx=14, pady=4)
            self._status_items[key] = lbl

            # 구분자
            tk.Label(
                bar, text="|",
                font=Fonts.MONO_SMALL,
                bg=Colors.BG_CARD,
                fg=Colors.BORDER,
            ).pack(side=tk.LEFT)

        # 우측: 마지막 업데이트 시각
        self._last_update_label = tk.Label(
            bar, text="마지막 업데이트: --",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG_DIM,
        )
        self._last_update_label.pack(side=tk.RIGHT, padx=14)

    # ── 탭 전환 이벤트 ────────────────────────────────────────────────────

    def _on_tab_changed(self, event: Any) -> None:
        """탭 전환 시 dirty 탭이면 refresh()를 호출한다.

        Args:
            event: Notebook 탭 변경 이벤트
        """
        try:
            idx = self._notebook.index(self._notebook.select())
            if 0 <= idx < len(self._all_tabs):
                tab = self._all_tabs[idx]
                if hasattr(tab, "is_dirty") and tab.is_dirty():
                    if hasattr(tab, "refresh"):
                        tab.refresh()
        except Exception as exc:
            logger.debug("탭 전환 갱신 오류: %s", exc)

    # ── 시계 갱신 ──────────────────────────────────────────────────────────

    def _update_clock(self) -> None:
        """시계 1초 주기 갱신 (UTC + KST)."""
        now_utc = datetime.now(timezone.utc)
        utc_str = now_utc.strftime("UTC %H:%M:%S")
        kst_hour = (now_utc.hour + 9) % 24
        kst_str = f"KST {kst_hour:02d}:{now_utc.strftime('%M:%S')}"
        self._clock_label.config(text=f"{utc_str}  |  {kst_str}")
        self.master.after(1000, self._update_clock)

    # ── dirty 일괄 설정 (이벤트 발생 시 호출) ──────────────────────────────

    def mark_all_dirty(self) -> None:
        """모든 탭에 dirty 플래그를 설정한다.

        봉 확정/시그널/거래 발생 시 호출하여 모든 탭을 갱신 대상으로 만든다.
        """
        for tab in self._all_tabs:
            if hasattr(tab, "mark_dirty"):
                tab.mark_dirty()

    # ── DB 주입 ────────────────────────────────────────────────────────────

    def set_db(self, db: Any) -> None:
        """DBManager 인스턴스를 전체 탭에 주입하고 콜백을 연결한다.

        Args:
            db: DBManager 인스턴스
        """
        self._db = db
        for tab in self._all_tabs:
            if hasattr(tab, "refresh_symbol_list"):
                try:
                    tab.refresh_symbol_list()
                except Exception:
                    pass
            if hasattr(tab, "set_db"):
                try:
                    tab.set_db(db)
                except Exception as exc:
                    logger.warning(
                        "탭 set_db 실패: %s - %s",
                        tab.__class__.__name__, exc,
                    )

        # rest_client 주입 (이미 __init__에서 생성됨)
        if self._rest_client is not None:
            for tab in self._all_tabs:
                if hasattr(tab, "set_rest_client"):
                    try:
                        tab.set_rest_client(self._rest_client)
                    except Exception as exc:
                        logger.warning(
                            "탭 set_rest_client 실패: %s - %s",
                            tab.__class__.__name__, exc,
                        )

        # 개요 탭에 모의거래 콜백 연결
        self.tab_overview.set_callbacks(
            trade_start=self._do_trade_start,
            trade_stop=self._do_trade_stop,
        )

    # ── 모의거래 콜백 ─────────────────────────────────────────────────────

    def _do_trade_start(self) -> None:
        """모의거래 시작: DB 초기화 -> gap 수집 -> TradingEngine -> WebSocket 연결.

        순서 보장: gap 수집 완료 -> TradingEngine 생성(_prefill_buffers 포함) -> WS 시작
        """
        try:
            from config.symbol_manager import init_symbol_manager
            from config.products import fetch_products_from_api
            init_symbol_manager(top_n=100, pairs_n=30)
            fetch_products_from_api()

            # 싱글턴 settings를 symbol_manager 반영된 상태로 재생성 (1회만)
            import config.settings as _cfg
            _cfg.settings = _cfg.AppSettings()
            settings = _cfg.settings

            from config.settings import RiskParams
            from config import DB_PATH
            from api.rest_client import BybitRestClient
            from api.ws_client import BybitWebSocketClient
            from db.db_manager import DBManager
            from risk.risk_manager import RiskManager
            from paper_engine.trading_engine import TradingEngine

            # 1. DB 초기화
            if self._db is None:
                self._db = DBManager(DB_PATH)
            self._db.initialize()

            # DB를 탭에 재주입 (메인 스레드에서 UI 갱신)
            self.master.after(0, lambda: self.set_db(self._db))

            # 2. gap 수집 (WS 시작 전에 반드시 완료)
            from utils.data_gap import fill_data_gap
            from config.symbol_manager import get_symbol_manager
            all_syms = get_symbol_manager().all_symbols
            self.master.after(0, lambda: self.update_status_bar({"mode": "gap 수집 중..."}))
            logger.info("데이터 gap 수집 중 (%d개 심볼)...", len(all_syms))
            fill_data_gap(self._db, all_syms)
            logger.info("gap 수집 완료")

            # 3. TradingEngine 생성 (_prefill_buffers 포함, gap 수집 후 실행)
            rest_client = BybitRestClient(base_url=settings.base_url)
            self._rest_client = rest_client
            risk_mgr = RiskManager(
                RiskParams(),
                initial_capital=backtest_config.initial_capital,
                leverage=settings.leverage,
            )
            self._engine = TradingEngine(
                db=self._db, rest_client=rest_client,
                risk_manager=risk_mgr, leverage=settings.leverage,
            )
            logger.info("TradingEngine 초기화 완료 (버퍼 사전 로드 포함)")

            # 포지션/개요 탭에 엔진 주입 (메인 스레드에서)
            self.master.after(0, lambda: (
                self.tab_positions.set_engine(self._engine) if hasattr(self.tab_positions, "set_engine") else None,
                self.tab_overview.set_engine(self._engine) if hasattr(self.tab_overview, "set_engine") else None,
            ))

            # 4. WebSocket 연결 (TradingEngine 준비 완료 후 시작)
            def _on_ws_permanent_failure() -> None:
                """WS 영구 실패 시 상태바 표시 + 10초 후 자동 재시작."""
                logger.critical("WebSocket 영구 실패 -- 10초 후 자동 재시작 시도")
                self.master.after(0, lambda: self.update_status_bar({
                    "ws_connected": False,
                    "active_strategies": 0,
                }))
                self.master.after(0, lambda: self._status_items["connection"].config(
                    text="WS 연결 실패", fg=Colors.DISCONNECTED,
                ))
                # 10초 후 자동 재시작
                self.master.after(10000, self._restart_ws)

            self._ws_client = BybitWebSocketClient(
                ws_url=settings.ws_url,
                on_permanent_failure=_on_ws_permanent_failure,
            )

            def on_kline_confirmed(symbol: str, interval: str, kline: dict) -> None:
                """봉 확정 콜백. 엔진에 전달 + dirty flag 설정."""
                try:
                    bar = {
                        "open_time": int(kline.get("start", 0)),
                        "open": float(kline.get("open", 0)),
                        "high": float(kline.get("high", 0)),
                        "low": float(kline.get("low", 0)),
                        "close": float(kline.get("close", 0)),
                        "volume": float(kline.get("volume", 0)),
                    }
                    self._engine.on_new_bar_15m(symbol, bar)
                    self.master.after(0, self.mark_all_dirty)
                except Exception as exc:
                    logger.warning("on_new_bar 에러 %s: %s", symbol, exc)

            def on_kline_tick(symbol: str, interval: str, kline: dict) -> None:
                """미확정 봉 틱 콜백. on_tick으로 스톱/TP 실시간 체크."""
                try:
                    price = float(kline.get("close", 0))
                    if price > 0 and self._engine is not None:
                        self._engine.on_tick(symbol, price)
                except Exception as exc:
                    logger.debug("on_tick 에러 %s: %s", symbol, exc)

            self._ws_client.on_kline_closed = on_kline_confirmed
            self._ws_client.on_kline_update = on_kline_tick
            self._ws_client.start(
                symbols=settings.symbols,
                intervals=["15"],
            )
            logger.info(
                "WebSocket 연결 + 모의거래 시작: %d개 심볼",
                len(settings.symbols),
            )

            # 개요 탭에 엔진/WS 참조 주입
            self.master.after(0, lambda: self.tab_overview.set_references(
                db=self._db, ws_client=self._ws_client, engine=self._engine,
            ))

            # 상태바 즉시 갱신 (API에서 실제 잔고 조회)
            _init_cap = backtest_config.initial_capital
            try:
                bal_data = self._rest_client.get_wallet_balance()
                usdt_bal = _init_cap
                if bal_data:
                    for coin in bal_data.get("coin", []):
                        if coin.get("coin") == "USDT":
                            usdt_bal = float(coin.get("walletBalance", _init_cap))
                            break
            except Exception as exc:
                logger.warning("잔고 조회 실패, 초기자본 사용: %s", exc)
                usdt_bal = _init_cap

            _equity = usdt_bal
            self.master.after(500, lambda: self.update_status_bar({
                "ws_connected": True,
                "active_strategies": 4,
                "equity": _equity,
                "daily_pnl": 0.0,
                "position_count": 0,
                "mode": "demo",
            }))

            # 주기적 상태 갱신 시작 (10초)
            self._start_periodic_refresh()

        except Exception as exc:
            logger.error("모의거래 시작 실패: %s", exc)

    def _restart_ws(self) -> None:
        """WS 영구 실패 후 자동 재시작을 시도한다."""
        if self._ws_client is not None:
            try:
                self._ws_client.stop()
            except Exception as exc:
                logger.warning("WS 재시작 중 정지 오류: %s", exc)

        if self._engine is None:
            return

        try:
            from config.settings import AppSettings
            from api.ws_client import BybitWebSocketClient

            settings = AppSettings()
            self._ws_client = BybitWebSocketClient(ws_url=settings.ws_url)

            # 기존 콜백 재연결
            def on_kline_confirmed(symbol: str, interval: str, kline: dict) -> None:
                try:
                    bar = {
                        "open_time": int(kline.get("start", 0)),
                        "open": float(kline.get("open", 0)),
                        "high": float(kline.get("high", 0)),
                        "low": float(kline.get("low", 0)),
                        "close": float(kline.get("close", 0)),
                        "volume": float(kline.get("volume", 0)),
                    }
                    self._engine.on_new_bar_15m(symbol, bar)
                    self.master.after(0, self.mark_all_dirty)
                except Exception as exc:
                    logger.warning("on_new_bar 에러 %s: %s", symbol, exc)

            def on_kline_tick(symbol: str, interval: str, kline: dict) -> None:
                try:
                    price = float(kline.get("close", 0))
                    if price > 0 and self._engine is not None:
                        self._engine.on_tick(symbol, price)
                except Exception as exc:
                    logger.debug("on_tick 에러 %s: %s", symbol, exc)

            self._ws_client.on_kline_closed = on_kline_confirmed
            self._ws_client.on_kline_update = on_kline_tick
            self._ws_client.start(
                symbols=settings.symbols,
                intervals=["15"],
            )
            logger.info("WS 자동 재시작 완료")

            self.master.after(0, lambda: self.tab_overview.set_references(
                db=self._db, ws_client=self._ws_client, engine=self._engine,
            ))
            self.master.after(500, lambda: self.update_status_bar({
                "ws_connected": True,
            }))
        except Exception as exc:
            logger.error("WS 자동 재시작 실패: %s", exc)

    def _do_trade_stop(self) -> None:
        """모의거래 중지: WS 해제 + 엔진 제거 + 상태 갱신."""
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception as exc:
                logger.warning("WS 정지 중 오류: %s", exc)
            self._ws_client = None
        self._engine = None

        # 개요 탭 참조 초기화
        self.master.after(0, lambda: self.tab_overview.set_references(
            db=self._db, ws_client=None, engine=None,
        ))

        # 포지션 탭 엔진 초기화
        if hasattr(self.tab_positions, "set_engine"):
            self.tab_positions.set_engine(None)

        # 상태바 갱신
        self.master.after(0, lambda: self.update_status_bar({
            "ws_connected": False,
            "active_strategies": 0,
            "daily_pnl": 0.0,
            "position_count": 0,
            "mode": "demo",
        }))

        logger.info("모의거래 중지")

    def _start_periodic_refresh(self) -> None:
        """엔진 상태를 10초마다 상태바에 반영한다."""
        if self._engine is None or self._ws_client is None:
            return
        try:
            status = self._engine.get_status()
            risk_st = status.get("risk_status", {})
            ws_running = (
                self._ws_client is not None
                and hasattr(self._ws_client, "is_running")
                and self._ws_client.is_running
            )
            # API에서 실제 잔고 조회
            _fallback = risk_st.get("equity", backtest_config.initial_capital)
            try:
                bal_data = self._rest_client.get_wallet_balance()
                usdt_equity = _fallback
                if bal_data:
                    for coin in bal_data.get("coin", []):
                        if coin.get("coin") == "USDT":
                            usdt_equity = float(coin.get("walletBalance", usdt_equity))
                            break
            except Exception as exc:
                logger.warning("주기 잔고 조회 실패: %s", exc)
                usdt_equity = _fallback

            self.update_status_bar({
                "ws_connected": ws_running,
                "active_strategies": 4,
                "equity": usdt_equity,
                "daily_pnl": status.get("daily_pnl", 0.0),
                "position_count": status.get("position_count", 0),
                "mode": "demo",
            })
        except Exception as exc:
            logger.warning("주기 상태 갱신 실패: %s", exc)
        # 다음 갱신 예약
        if self._engine is not None:
            self.master.after(10000, self._start_periodic_refresh)

    # ── 상태바 갱신 ────────────────────────────────────────────────────────

    def update_status_bar(self, data: dict) -> None:
        """하단 상태바 갱신.

        Args:
            data: 상태 데이터 딕셔너리
        """
        ws_ok = data.get("ws_connected", False)
        self._status_items["connection"].config(
            text="● 연결됨" if ws_ok else "● 미연결",
            fg=Colors.CONNECTED if ws_ok else Colors.DISCONNECTED,
        )
        self._net_status_label.config(
            text=f"● WS: {'ON' if ws_ok else 'OFF'}",
            fg=Colors.CONNECTED if ws_ok else Colors.NEUTRAL,
        )

        active = data.get("active_strategies")
        if active is not None:
            self._status_items["strategies"].config(
                text=f"활성 전략: {active}개",
                fg=Colors.PROFIT if active > 0 else Colors.FG,
            )

        equity = data.get("equity")
        if equity is not None:
            self._status_items["equity"].config(
                text=f"에퀴티: {equity:,.0f} USDT",
                fg=Colors.FG,
            )

        daily_pnl = data.get("daily_pnl")
        if daily_pnl is not None:
            sign = "+" if daily_pnl >= 0 else ""
            self._status_items["daily_pnl"].config(
                text=f"일일PnL: {sign}{daily_pnl:,.2f} USDT",
                fg=Colors.PROFIT if daily_pnl >= 0 else Colors.LOSS,
            )

        pos_count = data.get("position_count", 0)
        self._status_items["positions"].config(
            text=f"포지션: {pos_count}건",
            fg=Colors.WARNING if pos_count > 0 else Colors.FG,
        )

        mode = data.get("mode", "demo")
        self._status_items["mode"].config(
            text=f"모드: {'Demo' if mode == 'demo' else '실거래'}",
            fg=Colors.WARNING if mode == "demo" else Colors.LOSS,
        )

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        self._last_update_label.config(text=f"마지막 업데이트: {now_str}")


def create_dashboard() -> tuple:
    """대시보드 생성 및 반환.

    Returns:
        (root, dashboard) 튜플
    """
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception as exc:
        logger.debug("아이콘 설정 실패 (무시): %s", exc)
    dashboard = BybitDashboard(root)
    return root, dashboard
