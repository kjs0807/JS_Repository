"""선물 어댑터 — summary.json + DB → Asset 변환."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from optimizer.types import Asset, AssetMetrics, SizingMode


# ── 중복 그룹 정의 ──────────────────────────────────────────
DUPLICATE_GROUPS: dict[str, list[str]] = {
    "ES": ["MES", "SIN"],
    "NQ": ["MNQ"],
    "YM": ["MYM"],
    "GC": ["MGC"],
}

# 데이터 품질 문제로 제외하는 종목
DATA_QUALITY_EXCLUSIONS: set[str] = {"FGBL"}

# 종목별 개시증거금 (USD) — 거래소 공시 기준 근사치
MARGIN_TABLE: dict[str, float] = {
    "ZF":  1_300,
    "MGC": 1_500,
    "HSI": 1_600,
    "6A":  1_900,
    "6B":  2_400,
    "6E":  2_600,
    "CL":  5_700,
    "HG":  6_000,
    "RTY": 5_700,
    "YM": 11_200,
    "ES": 14_000,
    "NQ": 18_500,
    "GC": 10_500,
    "SI":  9_000,
    "ZN":  2_200,
    "ZB":  4_400,
    "NG":  6_000,
    "MES": 1_400,
    "MNQ": 1_850,
    "MYM": 1_100,
    # KRX 국채선물 (KRW, 별도 통화)
    "KTBF_3Y":  900_000,
    "KTBF_10Y": 1_800_000,
}


def _find_group(symbol: str) -> str | None:
    """종목의 중복 그룹 대표를 찾는다."""
    for rep, dupes in DUPLICATE_GROUPS.items():
        if symbol == rep or symbol in dupes:
            return rep
    return None


def _get_asset_class(symbol: str, db_path: str) -> str:
    """DB products_master에서 asset_class를 읽는다."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT asset_class FROM products_master WHERE symbol=?",
        (symbol,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "Unknown"


def _get_product_name(symbol: str, db_path: str) -> str:
    """DB products_master에서 이름을 읽는다."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT name_kr FROM products_master WHERE symbol=?",
        (symbol,),
    )
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return symbol


def _get_point_value(symbol: str, db_path: str) -> float:
    """DB products_master에서 point_value를 계산한다."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT tick_size, tick_value FROM products_master WHERE symbol=?",
        (symbol,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return 1.0
    tick_size, tick_value = float(row[0]), float(row[1])
    return tick_value / tick_size if tick_size > 0 else 1.0


def _load_daily_returns(
    symbol: str,
    db_path: str,
    start: str = "2023-01-03",
    end: str = "2025-02-13",
) -> pd.Series:
    """DB에서 일간 수익률을 로드한다."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, close FROM ohlcv_daily "
        "WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(symbol, start, end),
    )
    conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    returns = df["close"].pct_change().dropna()
    return returns


def _calculate_volatility_usd(
    symbol: str,
    db_path: str,
    point_value: float,
    start: str = "2023-01-03",
    end: str = "2025-02-13",
    atr_period: int = 14,
) -> float:
    """ATR × point_value로 달러 변동성을 계산한다."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, high, low, close FROM ohlcv_daily "
        "WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(symbol, start, end),
    )
    conn.close()
    if df.empty or len(df) < atr_period + 1:
        return 1.0

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    avg_atr = atr.iloc[-20:].mean()

    if pd.isna(avg_atr) or avg_atr <= 0:
        return 1.0
    return float(avg_atr * point_value)


def load_futures_assets(
    summary_path: str | Path = "logs/optimization_report/summary.json",
    db_path: str = "db/futures.db",
    returns_start: str = "2023-01-03",
    returns_end: str = "2025-02-13",
    margin_table: dict[str, float] | None = None,
) -> list[Asset]:
    """summary.json + DB에서 선물 Asset 리스트를 생성한다.

    Args:
        summary_path: 최적화 summary.json 경로
        db_path: SQLite DB 경로
        returns_start: 수익률 계산 시작일
        returns_end: 수익률 계산 종료일
        margin_table: 종목별 마진 오버라이드 (None이면 기본 MARGIN_TABLE)

    Returns:
        Asset 리스트
    """
    margins = margin_table or MARGIN_TABLE

    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assets: list[Asset] = []
    for entry in data:
        symbol = entry["symbol"]

        if symbol in DATA_QUALITY_EXCLUSIONS:
            continue

        # 마진이 없으면 후보에서 제외
        margin = margins.get(symbol)
        if margin is None:
            continue

        fwd_sharpe = entry.get("fwd_sharpe", 0.0)
        fwd_return = entry.get("fwd_return", 0.0)
        fwd_mdd = entry.get("fwd_mdd", 0.0)
        fwd_wr = entry.get("fwd_win_rate", 0.0)
        fwd_trades = entry.get("fwd_trades", 0)
        fwd_calmar = fwd_return / fwd_mdd if fwd_mdd > 0 else 0.0

        point_value = _get_point_value(symbol, db_path)
        daily_returns = _load_daily_returns(symbol, db_path, returns_start, returns_end)
        vol_usd = _calculate_volatility_usd(
            symbol, db_path, point_value, returns_start, returns_end,
        )

        asset_class = _get_asset_class(symbol, db_path)
        name = _get_product_name(symbol, db_path)
        group = _find_group(symbol)

        assets.append(Asset(
            symbol=symbol,
            name=name,
            asset_class=asset_class,
            group=group,
            cost_per_unit=margin,
            sizing_mode=SizingMode.INTEGER_CONTRACTS,
            point_value=point_value,
            daily_returns=daily_returns,
            volatility_usd=vol_usd,
            metrics=AssetMetrics(
                sharpe=fwd_sharpe,
                win_rate=fwd_wr,
                return_pct=fwd_return,
                mdd=fwd_mdd,
                calmar=fwd_calmar,
                trades=fwd_trades,
            ),
            meta={
                "best_config": entry.get("best_config", {}),
                "point_value_raw": entry.get("point_value", point_value),
            },
        ))

    return assets
