"""Keltner Channel — `period`-기간 mid (SMA 또는 EMA) ± `multiplier`·ATR 밴드.

Phase 1은 두 모드 모두 지원:
- `use_ema=True` (기본, legacy 호환): mid는 EMA(span=period), ATR은 **Wilder smoothing**
  (`alpha=1/atr_period`, `min_periods=atr_period`, `adjust=False`) — legacy
  `momentum.py`의 `ewm(alpha=1/period, min_periods=period, adjust=False)` 수식과 일치.
  span 기반 EWM(alpha=2/(N+1))과는 다른 평활 강도.
- `use_ema=False`: SMA mid + SMA(TR) ATR. 단위 테스트·교차검증용 단순 모드.

`atr_period`는 KC `period`와 분리 (legacy: `kc_period=20` + `atr_period=14`).

Warmup:
- SMA 모드: max(period - 1, atr_period). SMA mid 첫 유효 인덱스 = period - 1,
  ATR 첫 유효 = atr_period.
- EMA 모드: max(period, atr_period). mid EWM은 close에 null이 없어 인덱스 0부터 값을 내지만
  안정화에 ~period 봉 필요해 보수적으로 period 사용. Wilder ATR은 `min_periods=atr_period`
  + TR_0 null 때문에 인덱스 `atr_period`부터 첫 유효값.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class KeltnerChannel:
    """Keltner Channel 지표 (SMA/EMA 모드 선택 가능).

    출력 컬럼: `{name}_mid`, `{name}_upper`, `{name}_lower`.
    `name` = `kc_{period}_{multiplier}_{atr_period}_{sma|ema}`.

    legacy 호환 기본값: period=20, multiplier=1.0, atr_period=14, use_ema=True.
    """

    period: int = 20
    multiplier: float = 1.0
    atr_period: int = 14
    use_ema: bool = True

    def __post_init__(self) -> None:
        if self.period < 2:
            raise ValueError(f"period must be >= 2, got {self.period}")
        if self.atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {self.atr_period}")
        if self.multiplier <= 0:
            raise ValueError(f"multiplier must be > 0, got {self.multiplier}")

    @property
    def name(self) -> str:
        mode = "ema" if self.use_ema else "sma"
        return f"kc_{self.period}_{self.multiplier}_{self.atr_period}_{mode}"

    def required_warmup_bars(self) -> int:
        if self.use_ema:
            # EMA는 bar 0부터 값을 내지만 안정화 위해 보수적으로 period 사용.
            return max(self.period, self.atr_period)
        # SMA: mid 첫 유효 = period - 1, ATR(TR_0 null) 첫 유효 = atr_period
        return max(self.period - 1, self.atr_period)

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        prefix = self.name

        # TR 계산 (atr.py와 동일 — TR_0 null 마스킹)
        prev_close = pl.col("close").shift(1)
        tr_raw = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )
        tr = pl.when(prev_close.is_null()).then(None).otherwise(tr_raw)

        if self.use_ema:
            mid = pl.col("close").ewm_mean(span=self.period, adjust=False)
            # Wilder smoothing — legacy momentum.py와 동일한 수식.
            # `min_samples=atr_period`로 인덱스 < atr_period 구간을 null로 강제.
            atr = tr.ewm_mean(
                alpha=1.0 / self.atr_period,
                adjust=False,
                min_samples=self.atr_period,
            )
        else:
            mid = pl.col("close").rolling_mean(self.period)
            atr = tr.rolling_mean(self.atr_period)

        return bars.select(
            [
                mid.alias(f"{prefix}_mid"),
                (mid + self.multiplier * atr).alias(f"{prefix}_upper"),
                (mid - self.multiplier * atr).alias(f"{prefix}_lower"),
            ]
        )
