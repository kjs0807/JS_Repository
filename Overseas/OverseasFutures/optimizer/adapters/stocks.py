"""주식 어댑터 — OHLCV DataFrame → Asset 변환."""

from __future__ import annotations

import math

import pandas as pd

from optimizer.types import Asset, AssetMetrics, SizingMode


def _compute_basic_metrics(df: pd.DataFrame) -> AssetMetrics:
    """OHLCV DataFrame에서 기본 메트릭을 계산한다 (buy-and-hold 기준).

    Args:
        df: columns=[open, high, low, close, volume], index=DatetimeIndex

    Returns:
        AssetMetrics
    """
    if df.empty or len(df) < 20:
        return AssetMetrics(
            sharpe=0.0, win_rate=0.0, return_pct=0.0,
            mdd=0.0, calmar=0.0, trades=0,
        )

    close = df["close"]
    returns = close.pct_change().dropna()

    # 수익률
    total_return = (close.iloc[-1] / close.iloc[0] - 1) * 100

    # Sharpe (연환산)
    if returns.std() > 0:
        sharpe = (returns.mean() / returns.std()) * math.sqrt(252)
    else:
        sharpe = 0.0

    # MDD
    peak = close.expanding().max()
    dd = (close - peak) / peak * 100
    mdd = abs(dd.min()) if len(dd) > 0 else 0.0

    # Calmar
    n_days = len(close)
    annual_return = total_return * (252 / n_days) if n_days > 0 else 0.0
    calmar = annual_return / mdd if mdd > 0 else 0.0

    # Win rate (일간 양수 수익률 비율)
    win_rate = (returns > 0).mean() if len(returns) > 0 else 0.0

    return AssetMetrics(
        sharpe=sharpe,
        win_rate=float(win_rate),
        return_pct=total_return,
        mdd=mdd,
        calmar=calmar,
        trades=0,
    )


def load_stock_assets(
    data: dict[str, pd.DataFrame],
    sector_map: dict[str, str] | None = None,
    sizing_mode: SizingMode = SizingMode.INTEGER_SHARES,
) -> list[Asset]:
    """주식 OHLCV 딕셔너리에서 Asset 리스트를 생성한다.

    Args:
        data: {symbol: DataFrame} — DataFrame은 columns=[open, high, low, close, volume]
        sector_map: {symbol: sector} — 자산군 매핑 (None이면 "Equity")
        sizing_mode: 사이징 모드

    Returns:
        Asset 리스트
    """
    sectors = sector_map or {}
    assets: list[Asset] = []

    for symbol, df in data.items():
        if df.empty or len(df) < 20:
            continue

        close = df["close"]
        returns = close.pct_change().dropna()
        metrics = _compute_basic_metrics(df)

        # ATR 기반 변동성
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        cls = df["close"].astype(float)
        tr1 = high - low
        tr2 = (high - cls.shift(1)).abs()
        tr3 = (low - cls.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        avg_atr = float(atr.iloc[-20:].mean()) if len(atr) >= 20 else float(atr.mean())
        if pd.isna(avg_atr) or avg_atr <= 0:
            avg_atr = 1.0

        assets.append(Asset(
            symbol=symbol,
            name=symbol,
            asset_class=sectors.get(symbol, "Equity"),
            group=None,
            cost_per_unit=float(close.iloc[-1]),  # 최근 종가
            sizing_mode=sizing_mode,
            point_value=1.0,
            daily_returns=returns,
            volatility_usd=avg_atr,  # 주식: ATR × 1.0
            metrics=metrics,
            meta={},
        ))

    return assets
