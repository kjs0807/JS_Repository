"""Dual Bollinger Band Breakout Strategy — Configuration."""

from dataclasses import dataclass


class DualBBConfigError(Exception):
    """Raised when configuration validation fails."""
    pass


@dataclass
class DualBBConfig:
    """Configuration for Inner Band Breakout strategy.

    Attributes:
        candle_minutes: Candle timeframe in minutes
        ma_period: Moving average period for Bollinger Bands
        sigma_inner: Inner band standard deviation multiplier
        base_qty: First entry quantity
        scale_qty: Second entry (scale-in) quantity
        partial_exit_ratio: Partial exit ratio
    """
    candle_minutes: int = 60
    ma_period: int = 20
    sigma_inner: float = 1.5
    sigma_outer: float = 3.0
    breakout_pct: float = 0.0  # inner band 돌파 최소 %
    rsi_period: int = 14
    rsi_overbought: float = 80.0
    rsi_oversold: float = 20.0
    # ATR 기반 동적 스탑로스
    atr_stop_period: int = 14
    atr_stop_multiplier: float = 2.0  # 스탑 = entry ± ATR * multiplier
    # 트레일링 스탑
    use_trailing_stop: bool = True
    trailing_activation_atr: float = 1.0  # ATR 1배 수익 후 활성화
    trailing_distance_atr: float = 1.5  # ATR 1.5배 거리로 추적
    # 변동성 필터
    vol_filter_enabled: bool = True
    max_bandwidth_pct: float = 8.0  # 밴드폭 > 가격의 X% 시 진입 금지
    base_qty: int = 1
    scale_qty: int = 1
    partial_exit_ratio: float = 0.5

    def validate(self) -> None:
        """Validate configuration parameters."""
        errors = []
        if self.candle_minutes not in (1, 3, 5, 15, 30, 60, 240, 1440):
            errors.append(f"candle_minutes must be one of (1, 3, 5, 15, 30, 60, 240, 1440), got {self.candle_minutes}")
        if self.ma_period < 2:
            errors.append(f"ma_period must be >= 2, got {self.ma_period}")
        if self.sigma_inner < 0.5:
            errors.append(f"sigma_inner must be >= 0.5, got {self.sigma_inner}")
        if self.breakout_pct < 0:
            errors.append(f"breakout_pct must be >= 0, got {self.breakout_pct}")
        if self.sigma_outer <= self.sigma_inner:
            errors.append(f"sigma_outer must be > sigma_inner, got sigma_outer={self.sigma_outer}, sigma_inner={self.sigma_inner}")
        if self.rsi_period < 2:
            errors.append(f"rsi_period must be >= 2, got {self.rsi_period}")
        if not (0 < self.rsi_overbought <= 100):
            errors.append(f"rsi_overbought must be in (0, 100], got {self.rsi_overbought}")
        if not (0 <= self.rsi_oversold < 100):
            errors.append(f"rsi_oversold must be in [0, 100), got {self.rsi_oversold}")
        if self.rsi_oversold >= self.rsi_overbought:
            errors.append(f"rsi_oversold must be < rsi_overbought, got oversold={self.rsi_oversold}, overbought={self.rsi_overbought}")
        if self.atr_stop_period < 2:
            errors.append(f"atr_stop_period must be >= 2, got {self.atr_stop_period}")
        if self.atr_stop_multiplier <= 0:
            errors.append(f"atr_stop_multiplier must be > 0, got {self.atr_stop_multiplier}")
        if self.trailing_activation_atr < 0:
            errors.append(f"trailing_activation_atr must be >= 0, got {self.trailing_activation_atr}")
        if self.trailing_distance_atr <= 0:
            errors.append(f"trailing_distance_atr must be > 0, got {self.trailing_distance_atr}")
        if self.max_bandwidth_pct <= 0:
            errors.append(f"max_bandwidth_pct must be > 0, got {self.max_bandwidth_pct}")
        if self.base_qty <= 0:
            errors.append(f"base_qty must be > 0, got {self.base_qty}")
        if self.scale_qty <= 0:
            errors.append(f"scale_qty must be > 0, got {self.scale_qty}")
        if errors:
            raise DualBBConfigError("; ".join(errors))
