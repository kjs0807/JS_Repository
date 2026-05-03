"""FRAMA Channel indicator (PR 16, BigBeluga TradingView port).

Pine reference (CC BY-NC-SA 4.0, BigBeluga "FRAMA Channel"):

    N = length, even, >= 2
    price = hl2 = (high + low) / 2
    volatility = SMA(high - low, 200)

    N3 = (highest(high, N) - lowest(low, N)) / N
    N1 = (highest(high, N/2 recent half) - lowest(low, N/2 recent half)) / (N/2)
    N2 = (highest(high, older N/2 half)  - lowest(low, older N/2 half))  / (N/2)

    Dimen = (log(N1 + N2) - log(N3)) / log(2)         when N1, N2, N3 > 0
    alpha = exp(-4.6 * (Dimen - 1)), clamp to [0.01, 1]

    Filt := na(Filt) ? price : alpha * price + (1 - alpha) * Filt[1]
    Filt := ta.sma((bar_index < N + 1) ? price : Filt, 5)

    upper = Filt + volatility * distance
    lower = Filt - volatility * distance

    break_up = ta.crossover(hlc3, upper)
    break_dn = ta.crossunder(hlc3, lower)

The Pine `Filt` is overwritten twice per bar — the next bar's `Filt[1]` reads
the **smoothed** value, not the raw recursive value. This implementation feeds
the SMA(5) buffer with `sma_input[t] = price[t] if t < N + 1 else raw_candidate`
and stores the smoothed result back into ``filt[t]``, exactly mirroring Pine.

Causality: every read at index ``t`` uses values at index ``<= t`` only. The
recursive filter reads ``filt[t-1]``; the crossover compares ``hlc3[t]`` /
``upper[t]`` vs the previous bar; volatility / SMA(5) are right-aligned rolling
windows. Truncating the input from the right does not change earlier outputs —
this is exercised by ``test_pr16_frama_indicator.py``.

Output columns (fixed names — instances with different params share columns,
which means a strategy must register at most one ``FRAMAChannel`` instance):

- ``frama`` — smoothed Filt
- ``frama_upper`` / ``frama_lower`` — channel bands
- ``frama_alpha`` — per-bar alpha actually used (clamped to [0.01, 1])
- ``frama_dimension`` — fractal dimension (NaN when N1/N2/N3 not all positive)
- ``frama_break_up`` / ``frama_break_dn`` — bool, ``hlc3`` crossover/crossunder
  of the bands
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(frozen=True)
class FRAMAChannel:
    """FRAMA Channel indicator (BigBeluga port, recursive Filt + SMA smoothing).

    Args:
        length: ``N`` in Pine. Must be even and >= 2 (the algorithm splits the
            window into two halves of ``N/2``).
        distance: Channel width multiplier. ``upper = Filt + volatility *
            distance``. Must be > 0.
        smoothing: Final SMA window applied on top of the recursive Filt.
            Pine uses 5; exposed for tuning. Must be >= 1.
        volatility_window: SMA window over ``high - low`` used as channel
            volatility. Pine uses 200; configurable for shorter datasets.
            Must be >= 1.
    """

    length: int = 26
    distance: float = 1.5
    smoothing: int = 5
    volatility_window: int = 200

    def __post_init__(self) -> None:
        if self.length < 2:
            raise ValueError(f"length must be >= 2, got {self.length}")
        if self.length % 2 != 0:
            raise ValueError(f"length must be even, got {self.length}")
        if self.distance <= 0:
            raise ValueError(f"distance must be > 0, got {self.distance}")
        if self.smoothing < 1:
            raise ValueError(f"smoothing must be >= 1, got {self.smoothing}")
        if self.volatility_window < 1:
            raise ValueError(
                f"volatility_window must be >= 1, got {self.volatility_window}"
            )

    @property
    def name(self) -> str:
        return f"frama_{self.length}_{self.distance}"

    def required_warmup_bars(self) -> int:
        # FRAMA needs ``length`` bars to compute the first valid dimension; the
        # SMA(smoothing) on top extends that. ``volatility_window`` is independent
        # — bands stay NaN until both warmups complete. Take the conservative max.
        return max(self.length + self.smoothing, self.volatility_window)

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        n_bars = bars.height
        if n_bars == 0:
            return pl.DataFrame(
                schema={
                    "frama": pl.Float64,
                    "frama_upper": pl.Float64,
                    "frama_lower": pl.Float64,
                    "frama_alpha": pl.Float64,
                    "frama_dimension": pl.Float64,
                    "frama_break_up": pl.Boolean,
                    "frama_break_dn": pl.Boolean,
                }
            )

        high = bars["high"].to_numpy().astype(np.float64, copy=False)
        low = bars["low"].to_numpy().astype(np.float64, copy=False)
        close = bars["close"].to_numpy().astype(np.float64, copy=False)

        hl2 = (high + low) / 2.0
        hlc3 = (high + low + close) / 3.0

        # Volatility = SMA(high - low, volatility_window). Use polars rolling_mean
        # with min_samples = window so warmup region is null (matches Pine which
        # returns na until full window).
        vol_series = (
            pl.Series(name="hl_range", values=high - low)
            .rolling_mean(window_size=self.volatility_window, min_samples=self.volatility_window)
        )
        volatility = vol_series.to_numpy().astype(np.float64, copy=False)

        frama, alpha_arr, dim_arr = _compute_frama_recursive(
            hl2,
            high,
            low,
            length=self.length,
            smoothing=self.smoothing,
        )

        upper = frama + volatility * self.distance
        lower = frama - volatility * self.distance

        break_up = _crossover(hlc3, upper)
        break_dn = _crossunder(hlc3, lower)

        return pl.DataFrame(
            {
                "frama": frama,
                "frama_upper": upper,
                "frama_lower": lower,
                "frama_alpha": alpha_arr,
                "frama_dimension": dim_arr,
                "frama_break_up": break_up,
                "frama_break_dn": break_dn,
            }
        )


# ---------- internal helpers (numpy) ----------------------------------------


def _sma_at(values: np.ndarray, idx: int, window: int) -> float:
    """Right-aligned SMA at ``idx``. Returns NaN if window not yet full or if
    any value in the window is NaN — matches Pine ``ta.sma`` na semantics.
    """
    start = idx - window + 1
    if start < 0:
        return float("nan")
    chunk = values[start : idx + 1]
    if np.isnan(chunk).any():
        return float("nan")
    return float(chunk.mean())


def _compute_frama_recursive(
    price: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    length: int,
    smoothing: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-bar recursive Filt + SMA smoothing pass (Pine parity).

    Returns ``(filt, alpha, dimension)`` arrays of length ``len(price)``. The
    ``filt`` array is the *smoothed* value — this is what Pine's ``Filt[1]``
    reads on the next bar, so the recursion uses ``filt[t-1]`` (smoothed).

    Causality: every write at ``t`` reads only ``filt[t-1]`` and values at index
    ``<= t``. No forward references.
    """
    n_bars = len(price)
    half = length // 2
    filt = np.full(n_bars, np.nan, dtype=np.float64)
    alpha_out = np.full(n_bars, np.nan, dtype=np.float64)
    dim_out = np.full(n_bars, np.nan, dtype=np.float64)
    sma_input = np.full(n_bars, np.nan, dtype=np.float64)

    log2 = np.log(2.0)

    for t in range(n_bars):
        alpha = 0.01
        dimen = float("nan")

        if t >= length - 1:
            window_hi = high[t - length + 1 : t + 1]
            window_lo = low[t - length + 1 : t + 1]
            n3 = (window_hi.max() - window_lo.min()) / length

            # Pine: high[0]..high[N/2-1] is the *recent* half (most recent bars).
            recent_hi = high[t - half + 1 : t + 1]
            recent_lo = low[t - half + 1 : t + 1]
            n1 = (recent_hi.max() - recent_lo.min()) / half

            # Pine: high[N/2]..high[N-1] is the *older* half.
            older_hi = high[t - length + 1 : t - half + 1]
            older_lo = low[t - length + 1 : t - half + 1]
            n2 = (older_hi.max() - older_lo.min()) / half

            if n1 > 0 and n2 > 0 and n3 > 0:
                dimen = (np.log(n1 + n2) - np.log(n3)) / log2
                raw_alpha = float(np.exp(-4.6 * (dimen - 1.0)))
                alpha = min(max(raw_alpha, 0.01), 1.0)

        prev_filt = filt[t - 1] if t > 0 else float("nan")
        if np.isnan(prev_filt):
            raw_candidate = price[t]
        else:
            raw_candidate = alpha * price[t] + (1.0 - alpha) * prev_filt

        # Pine: Filt := ta.sma((bar_index < N + 1) ? price : Filt, smoothing).
        # Below the threshold the SMA(smoothing) is fed with raw price; once we
        # have at least ``length + 1`` bars the SMA is fed with the recursive
        # candidate. The smoothed result becomes both ``filt[t]`` and the input
        # to next bar's ``Filt[1]``.
        sma_input[t] = price[t] if t < length + 1 else raw_candidate
        filt[t] = _sma_at(sma_input, t, smoothing)
        alpha_out[t] = alpha
        dim_out[t] = dimen

    return filt, alpha_out, dim_out


def _crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossover(a, b)`` — True at ``t`` iff ``a[t] > b[t]`` and
    ``a[t-1] <= b[t-1]``. Bar 0 and any NaN comparison return False.
    """
    n = len(a)
    if n == 0:
        return np.zeros(0, dtype=bool)
    prev_a = np.empty(n, dtype=np.float64)
    prev_b = np.empty(n, dtype=np.float64)
    prev_a[0] = np.nan
    prev_b[0] = np.nan
    if n > 1:
        prev_a[1:] = a[:-1]
        prev_b[1:] = b[:-1]
    out = (a > b) & (prev_a <= prev_b)
    out[np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)] = False
    return out


def _crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossunder(a, b)`` — mirror of ``_crossover``."""
    n = len(a)
    if n == 0:
        return np.zeros(0, dtype=bool)
    prev_a = np.empty(n, dtype=np.float64)
    prev_b = np.empty(n, dtype=np.float64)
    prev_a[0] = np.nan
    prev_b[0] = np.nan
    if n > 1:
        prev_a[1:] = a[:-1]
        prev_b[1:] = b[:-1]
    out = (a < b) & (prev_a >= prev_b)
    out[np.isnan(prev_a) | np.isnan(prev_b) | np.isnan(a) | np.isnan(b)] = False
    return out


__all__ = ["FRAMAChannel"]
