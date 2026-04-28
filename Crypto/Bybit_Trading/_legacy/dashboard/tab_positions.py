"""대시보드 Tab 6: 포지션/PnL 탭.

현재 포지션 테이블, 거래 이력 (최근 20건, trade_log DB),
PnL 요약 라벨을 제공한다.
이벤트 구동: mark_dirty() 호출 시 dirty 플래그 세움.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Dict, List, Optional

from config.settings import backtest_config
from dashboard.base_tab import BaseDirtyTab
from dashboard.theme import Colors, Fonts

logger = logging.getLogger(__name__)

# 포지션 테이블 컬럼
POSITION_COLUMNS = [
    ("심볼", 90),
    ("방향", 60),
    ("계약", 70),
    ("진입가", 90),
    ("현재가", 90),
    ("스톱", 90),
    ("미실현PnL", 100),
    ("마진", 80),
    ("전략", 120),
]

# 거래 이력 컬럼
TRADE_COLUMNS = [
    ("시각", 140),
    ("심볼", 80),
    ("방향", 55),
    ("전략", 110),
    ("진입가", 85),
    ("청산가", 85),
    ("수량", 65),
    ("PnL", 80),
    ("사유", 65),
]


class PositionsTab(BaseDirtyTab):
    """포지션/PnL 탭.

    현재 포지션, PnL 요약, 거래 이력을 표시한다.
    DB에서 trade_log 최근 20건 + 엔진 포지션 목록을 조회한다.
    """

    def __init__(self, parent: tk.Widget) -> None:
        """초기화.

        Args:
            parent: 부모 위젯
        """
        super().__init__(parent)

        self._build_ui()

    # ── UI 구성 ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """탭 전체 UI 구성."""
        self._build_control_bar()
        self._build_summary_panel()
        self._build_positions_panel()
        self._build_history_panel()

    def _build_control_bar(self) -> None:
        """상단 컨트롤 바 - 새로고침/선택청산/전체청산 버튼."""
        bar = tk.Frame(self, bg=Colors.BG_CARD, pady=6)
        bar.pack(fill=tk.X, padx=10, pady=(8, 4))

        tk.Label(
            bar,
            text="포지션/PnL",
            font=Fonts.BODY_BOLD,
            bg=Colors.BG_CARD,
            fg=Colors.FG,
        ).pack(side=tk.LEFT, padx=12)

        tk.Button(
            bar, text="새로고침",
            font=Fonts.MONO_SMALL,
            bg=Colors.BTN_GRAY, fg=Colors.FG,
            activebackground=Colors.ACCENT, activeforeground=Colors.FG,
            relief=tk.FLAT, cursor="hand2",
            command=self.refresh,
        ).pack(side=tk.RIGHT, padx=6)

        self._btn_close_all = tk.Button(
            bar, text="전체 청산",
            font=Fonts.MONO_SMALL,
            bg="#5a1a1a", fg=Colors.LOSS,
            activebackground="#7a2a2a", activeforeground=Colors.FG,
            relief=tk.FLAT, cursor="hand2",
            command=self._on_close_all,
        )
        self._btn_close_all.pack(side=tk.RIGHT, padx=6)

        self._btn_close_selected = tk.Button(
            bar, text="선택 청산",
            font=Fonts.MONO_SMALL,
            bg="#3a2a1a", fg=Colors.WARNING,
            activebackground="#5a3a2a", activeforeground=Colors.FG,
            relief=tk.FLAT, cursor="hand2",
            command=self._on_close_selected,
        )
        self._btn_close_selected.pack(side=tk.RIGHT, padx=6)

    def _build_summary_panel(self) -> None:
        """PnL 요약 패널 (일일/누적/에퀴티)."""
        frame = tk.Frame(self, bg=Colors.BG_CARD)
        frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        summary_items = [
            ("초기자본", "initial_capital", "USDT"),
            ("에퀴티", "equity", "USDT"),
            ("누적PnL", "total_pnl", "USDT"),
            ("일일PnL", "daily_pnl", "USDT"),
            ("미실현PnL", "unrealized_pnl", "USDT"),
            ("사용마진", "margin_used", "USDT"),
            ("승률", "win_rate", "%"),
            ("총거래", "total_trades", "건"),
        ]

        self._summary_labels: Dict[str, tk.Label] = {}
        for col_idx, (label_text, key, unit) in enumerate(summary_items):
            cell = tk.Frame(frame, bg=Colors.BG_CARD)
            cell.pack(side=tk.LEFT, padx=14, pady=8)

            tk.Label(
                cell, text=label_text, font=Fonts.SMALL,
                bg=Colors.BG_CARD, fg=Colors.FG_DIM,
            ).pack()

            value_lbl = tk.Label(
                cell, text=f"-- {unit}", font=Fonts.MONO_BOLD,
                bg=Colors.BG_CARD, fg=Colors.FG,
            )
            value_lbl.pack()
            self._summary_labels[key] = value_lbl

    def _build_positions_panel(self) -> None:
        """현재 포지션 테이블 패널."""
        header = tk.Frame(self, bg=Colors.BG)
        header.pack(fill=tk.X, padx=10, pady=(0, 2))
        tk.Label(
            header, text="현재 포지션", font=Fonts.HEADER,
            bg=Colors.BG, fg=Colors.FG,
        ).pack(side=tk.LEFT, padx=4)

        frame = tk.Frame(self, bg=Colors.BG)
        frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 5))

        col_ids = [col for col, _ in POSITION_COLUMNS]
        self._pos_tree = ttk.Treeview(
            frame, columns=col_ids, show="headings", height=7,
        )

        style = ttk.Style()
        style.configure(
            "Pos.Treeview",
            background=Colors.BG_CARD, foreground=Colors.FG,
            fieldbackground=Colors.BG_CARD, rowheight=23,
            font=Fonts.MONO_SMALL,
        )
        style.configure(
            "Pos.Treeview.Heading",
            background=Colors.ACCENT, foreground=Colors.FG,
            font=Fonts.SMALL,
        )
        self._pos_tree.configure(style="Pos.Treeview")
        self._pos_tree.tag_configure("long", foreground=Colors.PROFIT)
        self._pos_tree.tag_configure("short", foreground=Colors.LOSS)

        for col, width in POSITION_COLUMNS:
            self._pos_tree.heading(col, text=col, anchor="center")
            self._pos_tree.column(col, width=width, anchor="center", minwidth=40)

        sb = tk.Scrollbar(frame, orient=tk.VERTICAL, command=self._pos_tree.yview)
        self._pos_tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._pos_tree.pack(fill=tk.BOTH, expand=True)

    def _build_history_panel(self) -> None:
        """거래 이력 테이블 패널."""
        header = tk.Frame(self, bg=Colors.BG)
        header.pack(fill=tk.X, padx=10, pady=(5, 2))
        tk.Label(
            header, text="거래 이력 (최근 20건)", font=Fonts.HEADER,
            bg=Colors.BG, fg=Colors.FG,
        ).pack(side=tk.LEFT, padx=4)

        frame = tk.Frame(self, bg=Colors.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        col_ids = [col for col, _ in TRADE_COLUMNS]
        self._hist_tree = ttk.Treeview(
            frame, columns=col_ids, show="headings", height=10,
        )

        style = ttk.Style()
        style.configure(
            "Hist.Treeview",
            background=Colors.BG_CARD, foreground=Colors.FG,
            fieldbackground=Colors.BG_CARD, rowheight=22,
            font=Fonts.MONO_SMALL,
        )
        style.configure(
            "Hist.Treeview.Heading",
            background=Colors.ACCENT, foreground=Colors.FG,
            font=Fonts.SMALL,
        )
        self._hist_tree.configure(style="Hist.Treeview")
        self._hist_tree.tag_configure("win", foreground=Colors.PROFIT)
        self._hist_tree.tag_configure("loss", foreground=Colors.LOSS)

        for col, width in TRADE_COLUMNS:
            self._hist_tree.heading(col, text=col, anchor="center")
            self._hist_tree.column(col, width=width, anchor="center", minwidth=40)

        sb_y = tk.Scrollbar(
            frame, orient=tk.VERTICAL, command=self._hist_tree.yview,
        )
        sb_x = tk.Scrollbar(
            frame, orient=tk.HORIZONTAL, command=self._hist_tree.xview,
        )
        self._hist_tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._hist_tree.pack(fill=tk.BOTH, expand=True)

    # ── 이벤트 구동 ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """DB에서 포지션 + 거래 이력 + PnL 요약을 갱신한다."""
        self._dirty = False
        self._refresh_positions()
        self._refresh_trades()
        self._refresh_summary()

    def _refresh_positions(self) -> None:
        """API에서 현재 포지션을 직접 조회하여 테이블에 표시한다.

        각 treeview item의 iid에 'symbol|side|size|posIdx' 형태로
        청산에 필요한 정보를 저장한다.
        """
        for item in self._pos_tree.get_children():
            self._pos_tree.delete(item)

        # 엔진 포지션 매핑: symbol -> strategy (전략명 표시용)
        engine_strategy_map: Dict[str, str] = {}
        if self._engine is not None:
            try:
                status = self._engine.get_status()
                for pos in status.get("open_positions", []):
                    engine_strategy_map[pos.get("symbol", "")] = pos.get("strategy", "")
            except Exception:
                pass

        # API 우선 조회
        if self._rest_client is not None:
            try:
                positions = self._rest_client.get_positions()
                for pos in positions:
                    symbol = pos.get("symbol", "")
                    side = pos.get("side", "")
                    size = pos.get("size", "0")
                    entry = pos.get("avgPrice", "0")
                    pnl = pos.get("unrealisedPnl", "0")
                    pos_idx = pos.get("positionIdx", "0")
                    margin = pos.get("positionIM", "0")

                    pnl_val = float(pnl) if pnl else 0.0
                    tag = "long" if side == "Buy" else "short"
                    strategy = engine_strategy_map.get(symbol, "")
                    values = (
                        symbol,
                        side,
                        size,
                        f"{float(entry):,.4f}" if entry else "",
                        "",  # 현재가
                        "",  # 스톱
                        f"{pnl_val:+,.2f}",
                        f"{float(margin):,.2f}" if margin else "",
                        strategy,
                    )
                    # iid에 청산용 메타데이터 저장
                    iid = f"{symbol}|{side}|{size}|{pos_idx}"
                    self._pos_tree.insert(
                        "", tk.END, iid=iid, values=values, tags=(tag,),
                    )
                return
            except Exception as exc:
                logger.warning("API 포지션 조회 실패: %s", exc)

        # API 실패 시 엔진 fallback
        if self._engine is None:
            return
        try:
            status = self._engine.get_status()
            positions = status.get("open_positions", [])
            for pos in positions:
                symbol = pos.get("symbol", "")
                direction = pos.get("direction", "")
                upnl = pos.get("unrealized_pnl", 0.0)
                tag = "long" if direction == "LONG" else "short"
                api_side = "Buy" if direction == "LONG" else "Sell"
                qty_str = f"{pos.get('quantity', 0.0):.4f}"
                pos_idx = "1" if direction == "LONG" else "2"
                values = (
                    symbol,
                    direction,
                    qty_str,
                    f"{pos.get('entry_price', 0.0):,.4f}",
                    f"{pos.get('current_price', pos.get('entry_price', 0.0)):,.4f}",
                    f"{pos.get('stop_loss', 0.0):,.4f}",
                    f"{upnl:+,.2f}",
                    f"{pos.get('margin', 0.0):,.2f}",
                    pos.get("strategy", ""),
                )
                iid = f"{symbol}|{api_side}|{pos.get('quantity', 0.0)}|{pos_idx}"
                self._pos_tree.insert(
                    "", tk.END, iid=iid, values=values, tags=(tag,),
                )
        except Exception as exc:
            logger.debug("포지션 갱신 오류 (엔진 fallback): %s", exc)

    def _refresh_trades(self) -> None:
        """DB에서 최근 거래 이력 20건을 조회하여 테이블에 표시한다."""
        for item in self._hist_tree.get_children():
            self._hist_tree.delete(item)

        if self._db is None:
            return

        try:
            trades = self._db.get_recent_trades(limit=20)
            for trade in trades:
                pnl = trade.get("net_pnl", 0.0) or 0.0
                tag = "win" if pnl > 0 else "loss"

                exit_time = trade.get("exit_time", "")
                if exit_time and "T" in str(exit_time):
                    exit_time = str(exit_time).split("T")[1][:8]

                values = (
                    exit_time,
                    trade.get("symbol", ""),
                    trade.get("direction", ""),
                    trade.get("strategy", ""),
                    f"{trade.get('entry_price', 0.0):,.4f}",
                    f"{trade.get('exit_price', 0.0):,.4f}",
                    f"{trade.get('quantity', 0.0):.4f}",
                    f"{pnl:+,.2f}",
                    trade.get("exit_reason", ""),
                )
                self._hist_tree.insert("", 0, values=values, tags=(tag,))
        except Exception as exc:
            logger.debug("거래 이력 갱신 오류: %s", exc)

    def _refresh_summary(self) -> None:
        """PnL 요약 수치를 갱신한다. 에퀴티/미실현PnL은 API에서 직접 조회."""
        stats = {
            "initial_capital": backtest_config.initial_capital,
            "equity": backtest_config.initial_capital,
            "total_pnl": 0.0,
            "daily_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "margin_used": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
        }

        # 1. API에서 에퀴티 조회
        if self._rest_client is not None:
            try:
                bal = self._rest_client.get_wallet_balance()
                if bal:
                    for coin in bal.get("coin", []):
                        if coin.get("coin") == "USDT":
                            stats["equity"] = float(coin.get("walletBalance", stats["equity"]))
                            break
            except Exception as exc:
                logger.debug("잔고 API 조회 실패: %s", exc)

            # 2. API에서 미실현PnL 합산
            try:
                positions = self._rest_client.get_positions()
                unrealized = sum(
                    float(p.get("unrealisedPnl", 0) or 0) for p in positions
                )
                stats["unrealized_pnl"] = unrealized
            except Exception as exc:
                logger.debug("포지션 API 조회 실패: %s", exc)

        # 3. DB에서 일일PnL + 거래 통계
        if self._db is not None:
            try:
                stats["daily_pnl"] = self._db.get_daily_pnl()

                trades = self._db.get_recent_trades(limit=1000)
                closed = [t for t in trades if t.get("exit_time")]
                total = len(closed)
                wins = sum(1 for t in closed if (t.get("net_pnl") or 0) > 0)
                stats["win_rate"] = wins / total if total > 0 else 0.0
                stats["total_pnl"] = sum(t.get("net_pnl", 0) or 0 for t in closed)
                stats["total_trades"] = total
            except Exception as exc:
                logger.debug("DB 거래 통계 조회 실패: %s", exc)

        # 4. 엔진에서 사용마진 보완 (가용 시)
        if self._engine is not None:
            try:
                risk_st = self._engine.get_status().get("risk_status", {})
                stats["margin_used"] = risk_st.get("margin_used", 0.0)
            except Exception as exc:
                logger.debug("엔진 마진 조회 실패: %s", exc)

        self._update_summary_labels(stats)

    def _update_summary_labels(self, stats: dict) -> None:
        """요약 라벨을 갱신한다.

        Args:
            stats: 요약 딕셔너리
        """
        unit_map = {
            "initial_capital": "USDT", "equity": "USDT",
            "total_pnl": "USDT", "daily_pnl": "USDT",
            "unrealized_pnl": "USDT", "margin_used": "USDT",
            "win_rate": "%", "total_trades": "건",
        }
        for key, lbl in self._summary_labels.items():
            val = stats.get(key)
            if val is None:
                continue
            unit = unit_map.get(key, "")

            if key == "win_rate":
                text = f"{val * 100:.1f} {unit}"
            elif key == "total_trades":
                text = f"{int(val)} {unit}"
            else:
                sign = (
                    "+"
                    if key in ("total_pnl", "daily_pnl", "unrealized_pnl")
                    and val > 0
                    else ""
                )
                text = f"{sign}{val:,.2f} {unit}"

            if key in ("total_pnl", "daily_pnl", "unrealized_pnl"):
                color = (
                    Colors.PROFIT if val > 0
                    else (Colors.LOSS if val < 0 else Colors.FG)
                )
            else:
                color = Colors.FG

            lbl.config(text=text, fg=color)

    # ── 수동 청산 ──────────────────────────────────────────────────────

    def _parse_pos_iid(self, iid: str) -> Optional[Dict[str, Any]]:
        """treeview iid에서 청산용 메타데이터를 파싱한다.

        Args:
            iid: 'symbol|side|size|posIdx' 형태 문자열

        Returns:
            파싱된 딕셔너리 또는 None
        """
        parts = iid.split("|")
        if len(parts) != 4:
            return None
        try:
            return {
                "symbol": parts[0],
                "side": parts[1],
                "size": float(parts[2]),
                "position_idx": int(parts[3]),
            }
        except (ValueError, IndexError):
            return None

    def _close_position_api(self, pos_info: Dict[str, Any]) -> bool:
        """API를 통해 단일 포지션을 시장가 청산한다.

        Args:
            pos_info: _parse_pos_iid의 반환 딕셔너리

        Returns:
            청산 성공 여부
        """
        if self._rest_client is None:
            logger.warning("REST client 없음: 청산 불가")
            return False

        symbol = pos_info["symbol"]
        side = pos_info["side"]
        size = pos_info["size"]
        pos_idx = pos_info["position_idx"]
        close_side = "Sell" if side == "Buy" else "Buy"

        try:
            self._rest_client.place_order(
                symbol=symbol,
                side=close_side,
                qty=size,
                order_type="Market",
                position_idx=pos_idx,
            )
            logger.info("수동 청산 성공: %s %s size=%s", symbol, close_side, size)
            return True
        except Exception as exc:
            logger.error("수동 청산 실패 %s: %s", symbol, exc)
            return False

    def _update_engine_on_close(self, symbol: str, side: str) -> None:
        """엔진 내부 포지션을 수동 청산 처리한다.

        엔진의 _close_position을 직접 호출하여 DB trade_log도 갱신한다.

        Args:
            symbol: 심볼
            side: API side ('Buy' or 'Sell')
        """
        if self._engine is None:
            return

        direction = "LONG" if side == "Buy" else "SHORT"
        # 엔진 _positions에서 해당 심볼 포지션 찾기
        keys_to_close = [
            k for k, v in self._engine._positions.items()
            if v.symbol == symbol and v.direction == direction
        ]
        for pos_key in keys_to_close:
            pos = self._engine._positions.get(pos_key)
            if pos is None:
                continue
            # 현재가: 15m 버퍼 마지막 봉
            buf = self._engine._buf_15m.get(symbol)
            current_price = float(buf[-1]["close"]) if buf else pos.entry_price
            try:
                self._engine._close_position(pos_key, current_price, "MANUAL")
            except Exception as exc:
                logger.warning("엔진 포지션 제거 실패 %s: %s", pos_key, exc)

    def _on_close_selected(self) -> None:
        """선택된 포지션을 수동 청산한다."""
        selected = self._pos_tree.selection()
        if not selected:
            messagebox.showwarning("선택 없음", "청산할 포지션을 선택하세요.")
            return

        # 선택된 포지션 정보 수집
        targets: List[Dict[str, Any]] = []
        names: List[str] = []
        for iid in selected:
            info = self._parse_pos_iid(iid)
            if info and info["size"] > 0:
                targets.append(info)
                names.append(f"{info['symbol']} {info['side']} x{info['size']}")

        if not targets:
            return

        confirm = messagebox.askyesno(
            "포지션 청산 확인",
            f"다음 {len(targets)}건을 시장가 청산합니다:\n\n"
            + "\n".join(names)
            + "\n\n진행하시겠습니까?",
        )
        if not confirm:
            return

        self._btn_close_selected.config(state=tk.DISABLED)
        self._btn_close_all.config(state=tk.DISABLED)

        def _worker() -> None:
            success = 0
            for info in targets:
                if self._close_position_api(info):
                    self._update_engine_on_close(info["symbol"], info["side"])
                    success += 1
            self.master.after(0, lambda: self._on_close_done(success, len(targets)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_close_all(self) -> None:
        """전체 포지션을 시장가 청산한다."""
        items = self._pos_tree.get_children()
        if not items:
            messagebox.showinfo("포지션 없음", "청산할 포지션이 없습니다.")
            return

        targets: List[Dict[str, Any]] = []
        for iid in items:
            info = self._parse_pos_iid(iid)
            if info and info["size"] > 0:
                targets.append(info)

        if not targets:
            return

        confirm = messagebox.askyesno(
            "전체 청산 확인",
            f"열린 포지션 {len(targets)}건을 전부 시장가 청산합니다.\n\n"
            "진행하시겠습니까?",
        )
        if not confirm:
            return

        self._btn_close_selected.config(state=tk.DISABLED)
        self._btn_close_all.config(state=tk.DISABLED)

        def _worker() -> None:
            success = 0
            for info in targets:
                if self._close_position_api(info):
                    self._update_engine_on_close(info["symbol"], info["side"])
                    success += 1
            self.master.after(0, lambda: self._on_close_done(success, len(targets)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_close_done(self, success: int, total: int) -> None:
        """청산 작업 완료 후 UI 갱신.

        Args:
            success: 성공 건수
            total: 시도 건수
        """
        self._btn_close_selected.config(state=tk.NORMAL)
        self._btn_close_all.config(state=tk.NORMAL)

        if success == total:
            messagebox.showinfo("청산 완료", f"{success}건 청산 성공")
        else:
            messagebox.showwarning(
                "청산 결과",
                f"{total}건 중 {success}건 성공, {total - success}건 실패\n"
                "로그를 확인하세요.",
            )

        # 테이블 갱신
        self.refresh()

