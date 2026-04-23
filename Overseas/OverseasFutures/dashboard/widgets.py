"""재사용 가능한 tkinter 위젯 컴포넌트."""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Dict

from dashboard.themes import Colors, Fonts


class CardFrame(tk.Frame):
    """다크 테마 카드 프레임."""

    def __init__(self, parent, title: str = "", **kwargs):
        kwargs.setdefault("bg", Colors.BG_CARD)
        kwargs.setdefault("highlightbackground", Colors.BORDER)
        kwargs.setdefault("highlightthickness", 1)
        super().__init__(parent, **kwargs)

        if title:
            tk.Label(
                self,
                text=title,
                font=Fonts.HEADER,
                bg=Colors.BG_CARD,
                fg=Colors.MAUVE,
                anchor="w",
            ).pack(fill=tk.X, padx=10, pady=(8, 2))

            sep = tk.Frame(self, bg=Colors.BORDER, height=1)
            sep.pack(fill=tk.X, padx=5, pady=(0, 4))


class PriceLabel(tk.Label):
    """가격 표시 라벨 — 상승=초록, 하락=빨강."""

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("font", Fonts.MONO)
        kwargs.setdefault("bg", Colors.BG)
        kwargs.setdefault("fg", Colors.FG)
        kwargs.setdefault("text", "--")
        super().__init__(parent, **kwargs)
        self._prev_price: Optional[float] = None

    def update_price(self, price: float, prev_price: Optional[float] = None) -> None:
        """가격 업데이트. prev_price 기준으로 색상 결정."""
        compare = prev_price if prev_price is not None else self._prev_price

        text = f"{price:,.4f}" if price < 10 else f"{price:,.2f}"
        self.config(text=text)

        if compare is None or price == compare:
            self.config(fg=Colors.FG)
        elif price > compare:
            self.config(fg=Colors.GREEN)
        else:
            self.config(fg=Colors.RED)

        self._prev_price = price


class StatusIndicator(tk.Frame):
    """거래소 장 상태 표시 (열림=초록, 닫힘=회색)."""

    def __init__(self, parent, exchange: str, **kwargs):
        kwargs.setdefault("bg", Colors.BG)
        super().__init__(parent, **kwargs)
        self.exchange = exchange

        self._dot = tk.Label(
            self,
            text="●",
            font=Fonts.MONO_SMALL,
            bg=Colors.BG,
            fg=Colors.FG_DIM,
        )
        self._dot.pack(side=tk.LEFT)

        self._label = tk.Label(
            self,
            text=exchange,
            font=Fonts.MONO_SMALL,
            bg=Colors.BG,
            fg=Colors.FG_DIM,
        )
        self._label.pack(side=tk.LEFT, padx=(1, 4))

    def set_open(self, is_open: bool) -> None:
        """장 상태에 따라 색상 갱신."""
        color = Colors.GREEN if is_open else Colors.FG_DIM
        self._dot.config(fg=color)
        self._label.config(fg=color)


class PositionRow(tk.Frame):
    """포지션 한 줄 표시."""

    def __init__(
        self,
        parent,
        symbol: str,
        side: str,
        qty: float,
        avg_price: float,
        unrealized_pnl: float,
        currency: str,
        **kwargs,
    ):
        kwargs.setdefault("bg", Colors.BG_CARD)
        kwargs.setdefault("highlightbackground", Colors.BORDER)
        kwargs.setdefault("highlightthickness", 1)
        super().__init__(parent, **kwargs)

        side_color = Colors.GREEN if side == "LONG" else Colors.RED

        # 심볼 + 방향
        tk.Label(
            self,
            text=symbol,
            width=6,
            font=Fonts.MONO,
            bg=Colors.BG_CARD,
            fg=Colors.FG,
            anchor="w",
        ).pack(side=tk.LEFT, padx=(8, 2))

        self._side_lbl = tk.Label(
            self,
            text=side,
            width=5,
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=side_color,
            anchor="center",
        )
        self._side_lbl.pack(side=tk.LEFT, padx=2)

        # 수량
        self._qty_lbl = tk.Label(
            self,
            text=str(qty),
            width=6,
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG,
            anchor="e",
        )
        self._qty_lbl.pack(side=tk.LEFT, padx=2)

        # 평균단가
        self._avg_lbl = tk.Label(
            self,
            text=f"{avg_price:,.2f}",
            width=12,
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=Colors.FG_DIM,
            anchor="e",
        )
        self._avg_lbl.pack(side=tk.LEFT, padx=2)

        # 미실현 PnL
        pnl_color = Colors.GREEN if unrealized_pnl >= 0 else Colors.RED
        self._pnl_lbl = tk.Label(
            self,
            text=f"{unrealized_pnl:+,.2f} {currency}",
            width=18,
            font=Fonts.MONO_SMALL,
            bg=Colors.BG_CARD,
            fg=pnl_color,
            anchor="e",
        )
        self._pnl_lbl.pack(side=tk.LEFT, padx=(2, 8))

    def update(self, qty: float, avg_price: float, unrealized_pnl: float) -> None:
        """수량, 평균단가, 미실현 PnL 갱신."""
        self._qty_lbl.config(text=str(qty))
        self._avg_lbl.config(text=f"{avg_price:,.2f}")
        pnl_color = Colors.GREEN if unrealized_pnl >= 0 else Colors.RED
        self._pnl_lbl.config(
            text=f"{unrealized_pnl:+,.2f}",
            fg=pnl_color,
        )


class PnLSummary(CardFrame):
    """통화별 PnL 요약 패널."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, title="PnL 요약", **kwargs)
        self._rows: Dict[str, Dict[str, tk.Label]] = {}

        # 헤더 행
        header = tk.Frame(self, bg=Colors.BG_CARD)
        header.pack(fill=tk.X, padx=10, pady=(2, 0))
        for text, width in [("통화", 6), ("실현", 14), ("미실현", 14), ("합계", 14)]:
            tk.Label(
                header,
                text=text,
                width=width,
                font=Fonts.MONO_SMALL,
                bg=Colors.BG_CARD,
                fg=Colors.FG_DIM,
                anchor="e" if text != "통화" else "w",
            ).pack(side=tk.LEFT, padx=2)

        self._body = tk.Frame(self, bg=Colors.BG_CARD)
        self._body.pack(fill=tk.X, padx=10, pady=(0, 6))

    def update(self, pnl_data: Dict[str, dict]) -> None:
        """통화별 PnL 데이터 반영.

        Args:
            pnl_data: {currency: {"realized": float, "unrealized": float, "total": float}}
        """
        # 새 통화 행 추가 또는 기존 행 갱신
        existing = set(self._rows.keys())
        incoming = set(pnl_data.keys())

        # 사라진 통화 행 제거
        for ccy in existing - incoming:
            for lbl in self._rows[ccy].values():
                lbl.destroy()
            del self._rows[ccy]

        for ccy in sorted(incoming):
            data = pnl_data[ccy]
            realized = data.get("realized", 0.0)
            unrealized = data.get("unrealized", 0.0)
            total = data.get("total", realized + unrealized)

            if ccy not in self._rows:
                row = tk.Frame(self._body, bg=Colors.BG_CARD)
                row.pack(fill=tk.X, pady=1)
                labels: Dict[str, tk.Label] = {}

                labels["ccy"] = tk.Label(
                    row, text=ccy, width=6, font=Fonts.MONO_SMALL,
                    bg=Colors.BG_CARD, fg=Colors.FG, anchor="w",
                )
                labels["ccy"].pack(side=tk.LEFT, padx=2)

                for key in ("realized", "unrealized", "total"):
                    lbl = tk.Label(
                        row, text="--", width=14, font=Fonts.MONO_SMALL,
                        bg=Colors.BG_CARD, fg=Colors.FG, anchor="e",
                    )
                    lbl.pack(side=tk.LEFT, padx=2)
                    labels[key] = lbl

                self._rows[ccy] = labels

            # 값 갱신
            lbls = self._rows[ccy]
            for key, val in [("realized", realized), ("unrealized", unrealized), ("total", total)]:
                color = Colors.GREEN if val >= 0 else Colors.RED
                lbls[key].config(text=f"{val:+,.2f}", fg=color)
