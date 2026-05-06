# SATS Backtester Implementation Plan

## 1. 목적

TradingView Pine 지표 `Self-Aware Trend System [WillyAlgoTrader]`를 백테스터에서 사용할 수 있는 형태로 포팅한다. UI 요소인 `plot`, `label`, `line`, `table`, `alert`, `watermark`는 제외하고, 백테스터가 사용할 수 있는 지표값, 신호, 진입 계획, SL/TP 가격만 생성한다.

핵심 방향은 다음과 같다.

- SATS는 indicator/signal module로 구현한다.
- 체결, 수수료, 슬리피지, 분할익절, 손절, 동일 봉 TP/SL 충돌 처리는 BacktestEngine 또는 Strategy가 담당한다.
- Pine의 내부 성과 대시보드는 백테스터 결과 분석으로 대체한다.
- 1차 구현은 현재 BacktestEngine과 바로 결합되는 범위로 제한한다. multi-leg TP는 별도 엔진 확장으로 분리한다.

## 2. 적합한 구조

권장 데이터 흐름:

```text
OHLCV bars
  -> SATSIndicator.compute()
  -> sats_* columns
  -> SATSStrategy.on_bar()
  -> OrderIntent
  -> BacktestEngine execution/orderbook/ledger
```

SATS가 담당할 것:

- ATR, ER, RSI, Volume Z-score 계산
- TQI 계산
- adaptive/asymmetric multiplier 계산
- asymmetric SuperTrend band와 trend flip 계산
- BUY/SELL signal 계산
- signal 발생 시 entry, SL, TP1/TP2/TP3 가격 산출
- 참고 메타값 출력: TQI, ER, ATR, trend, st_line, live TP R

백테스터가 담당할 것:

- 진입 체결 방식: signal close, next bar open, limit/market 등
- 주문 수량 산정
- 수수료, 슬리피지
- TP1/TP2/TP3 분할익절
- SL 발생 시 잔여 수량 전량 청산
- 한 봉 안에서 TP와 SL이 모두 닿은 경우의 우선순위
- 반대 신호 발생 시 기존 포지션 처리
- 성과 통계

현재 코드 기준 결합 포인트:

- `SATSIndicator`는 `backtester.indicators.base.Indicator` 프로토콜을 만족해야 한다.
- `SATSStrategy`는 `backtester.strategies.base.BaseStrategy`를 상속하고 `required_indicators()`에서 내부 SATS indicator 인스턴스를 반환해야 한다.
- YAML/CLI registry는 `strategy_params`를 `StrategyClass(**params)`로 전달하므로, strategy 생성자는 `SATSIndicator` 객체가 아니라 숫자/문자열/bool 같은 primitive params를 받아야 한다.
- 현재 `BracketSpec`는 TP 하나와 SL 하나만 지원한다. TP1/TP2/TP3 분할익절은 1차 구현 범위가 아니라 Phase 3 엔진 확장 범위다.

## 3. 출력 컬럼 스키마

`SATSIndicator.compute(bars: pl.DataFrame) -> pl.DataFrame`는 입력 bars와 같은 height의 Polars DataFrame을 반환한다.

권장 컬럼:

| column | type | 의미 |
| --- | --- | --- |
| `sats_atr` | float | effective ATR. 기본은 efficiency-weighted ATR 적용 후 값 |
| `sats_raw_atr` | float | Wilder ATR 원본 |
| `sats_er` | float | Efficiency Ratio |
| `sats_vol_ratio` | float | raw ATR / ATR baseline |
| `sats_tqi` | float | Trend Quality Index, 0..1 |
| `sats_tqi_er` | float | TQI efficiency component |
| `sats_tqi_vol` | float | TQI volatility component |
| `sats_tqi_struct` | float | TQI structure component |
| `sats_tqi_mom` | float | TQI momentum persistence component |
| `sats_active_mult` | float | active-side band multiplier |
| `sats_passive_mult` | float | passive-side band multiplier |
| `sats_lower_band` | float | adaptive lower band |
| `sats_upper_band` | float | adaptive upper band |
| `sats_trend` | int | 1 bull, -1 bear |
| `sats_st_line` | float | trend 방향에 따른 SuperTrend line |
| `sats_signal` | int | 1 buy, -1 sell, 0 none |
| `sats_entry_price` | float | signal candle close 기준 계획 진입가 |
| `sats_sl_price` | float | signal 발생 시 산출한 SL |
| `sats_tp1_price` | float | TP1 가격 |
| `sats_tp2_price` | float | TP2 가격 |
| `sats_tp3_price` | float | TP3 가격 |
| `sats_tp1_r` | float | signal 발생 시 TP1 R multiple |
| `sats_tp2_r` | float | signal 발생 시 TP2 R multiple |
| `sats_tp3_r` | float | signal 발생 시 TP3 R multiple |
| `sats_ready` | bool | warmup 이후 사용 가능 여부 |

초기 warmup 구간은 `null` 또는 `0 signal`로 둔다. 전략은 반드시 `sats_ready == true`인 행만 거래한다.

컬럼명은 1차 구현에서 고정 prefix `sats_`를 사용한다. 현재 `IndicatorEngine`은 indicator output을 horizontal concat하므로 같은 `(symbol, timeframe)`에 서로 다른 SATS 설정을 2개 이상 등록하면 컬럼 충돌이 날 수 있다. 따라서 1차 구현에서는 전략당 SATS 인스턴스 1개만 등록한다. 여러 SATS 변형을 동시에 비교하려면 Phase 2 이후 `column_prefix` 또는 parameterized suffix를 추가한다.

## 4. 설정 객체 예시

Pine input을 Python dataclass로 옮긴다. 1차 구현에서는 UI 전용 설정을 제외한다.

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SATSConfig:
    preset: Literal["Auto", "Custom", "Scalping", "Default", "Swing", "Crypto 24/7"] = "Auto"
    timeframe_minutes: int = 60

    # The raw input values below are used only when preset == "Custom".
    # Auto/Scalping/Default/Swing/Crypto 24/7 resolve effective values from the preset table.
    atr_len: int = 13
    base_mult: float = 2.0
    source_col: str = "close"

    use_adaptive: bool = True
    er_length: int = 20
    adapt_strength: float = 0.5
    atr_baseline_len: int = 100

    use_tqi: bool = True
    quality_strength: float = 0.4
    quality_curve: float = 1.5
    mult_smooth: bool = True

    use_asym_bands: bool = True
    asym_strength: float = 0.5
    use_eff_atr: bool = True

    use_char_flip: bool = True
    char_flip_min_age: int = 5
    char_flip_high: float = 0.55
    char_flip_low: float = 0.25

    tqi_weight_er: float = 0.35
    tqi_weight_vol: float = 0.20
    tqi_weight_struct: float = 0.25
    tqi_weight_mom: float = 0.20
    tqi_struct_len: int = 20
    tqi_mom_len: int = 10

    pivot_len: int = 3
    rsi_len: int = 14
    vol_len: int = 20

    sl_atr_mult: float = 1.5
    tp_mode: Literal["Fixed", "Dynamic"] = "Fixed"
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    tp3_r: float = 3.0

    dyn_tp_tqi_weight: float = 0.6
    dyn_tp_vol_weight: float = 0.4
    dyn_tp_min_scale: float = 0.5
    dyn_tp_max_scale: float = 2.0
    dyn_tp_floor_r1: float = 0.5
    dyn_tp_ceil_r3: float = 8.0

    trade_max_age_bars: int = 100
```

Preset resolution은 Pine과 동일하게 별도 함수로 둔다.

```python
def resolve_sats_preset(cfg: SATSConfig) -> tuple[int, float, int, int, float]:
    preset = cfg.preset
    if preset == "Auto":
        if cfg.timeframe_minutes <= 5:
            preset = "Scalping"
        elif cfg.timeframe_minutes <= 240:
            preset = "Default"
        else:
            preset = "Swing"

    atr_len = {
        "Scalping": 10,
        "Default": 14,
        "Swing": 21,
        "Crypto 24/7": 14,
        "Custom": cfg.atr_len,
    }.get(preset, cfg.atr_len)

    base_mult = {
        "Scalping": 1.5,
        "Default": 2.0,
        "Swing": 2.5,
        "Crypto 24/7": 2.8,
        "Custom": cfg.base_mult,
    }.get(preset, cfg.base_mult)

    er_len = {
        "Scalping": 14,
        "Default": 20,
        "Swing": 30,
        "Crypto 24/7": 20,
        "Custom": cfg.er_length,
    }.get(preset, cfg.er_length)

    rsi_len = {
        "Scalping": 9,
        "Default": 14,
        "Swing": 21,
        "Crypto 24/7": 14,
        "Custom": cfg.rsi_len,
    }.get(preset, cfg.rsi_len)

    sl_mult = {
        "Scalping": 1.0,
        "Default": 1.5,
        "Swing": 2.0,
        "Crypto 24/7": 2.5,
        "Custom": cfg.sl_atr_mult,
    }.get(preset, cfg.sl_atr_mult)

    return atr_len, base_mult, er_len, rsi_len, sl_mult
```

## 5. Indicator 구현 예시

현재 백테스터의 `Indicator` 프로토콜은 Polars DataFrame을 받는다. SATS는 이전 bar 상태에 강하게 의존하므로 내부에서는 numpy array로 뽑아 순차 계산하는 방식이 적합하다. 기존 `FRAMAChannel`처럼 `to_numpy().astype(np.float64, copy=False)` 후 `_compute_sats_recursive(...)` helper에서 상태 루프를 돌리는 컨벤션을 따른다.

```python
import math
from dataclasses import dataclass

import numpy as np
import polars as pl


def safe_div(num: float | None, den: float | None, fallback: float = 0.0) -> float:
    if num is None or den is None or den == 0:
        return fallback
    if math.isnan(num) or math.isnan(den):
        return fallback
    return num / den


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def map_clamp(v: float, in_lo: float, in_hi: float, out_lo: float, out_hi: float) -> float:
    t = clamp(safe_div(v - in_lo, in_hi - in_lo, 0.0), 0.0, 1.0)
    return out_lo + t * (out_hi - out_lo)


@dataclass(frozen=True)
class SATSIndicator:
    cfg: SATSConfig

    def required_warmup_bars(self) -> int:
        atr_len, _, er_len, rsi_len, _ = resolve_sats_preset(self.cfg)
        return max(
            50,
            atr_len,
            er_len,
            rsi_len,
            self.cfg.vol_len,
            self.cfg.pivot_len * 2 + 1,
            self.cfg.tqi_mom_len,
            self.cfg.tqi_struct_len,
        ) + 10

    def compute(self, bars: pl.DataFrame) -> pl.DataFrame:
        high = bars["high"].to_numpy().astype(np.float64, copy=False)
        low = bars["low"].to_numpy().astype(np.float64, copy=False)
        close = bars["close"].to_numpy().astype(np.float64, copy=False)
        volume = (
            bars["volume"].to_numpy().astype(np.float64, copy=False)
            if "volume" in bars.columns
            else np.zeros(bars.height, dtype=np.float64)
        )
        source = bars[self.cfg.source_col].to_numpy().astype(np.float64, copy=False)

        n = bars.height
        out = {
            "sats_atr": [None] * n,
            "sats_raw_atr": [None] * n,
            "sats_er": [None] * n,
            "sats_vol_ratio": [None] * n,
            "sats_tqi": [None] * n,
            "sats_tqi_er": [None] * n,
            "sats_tqi_vol": [None] * n,
            "sats_tqi_struct": [None] * n,
            "sats_tqi_mom": [None] * n,
            "sats_active_mult": [None] * n,
            "sats_passive_mult": [None] * n,
            "sats_lower_band": [None] * n,
            "sats_upper_band": [None] * n,
            "sats_trend": [1] * n,
            "sats_st_line": [None] * n,
            "sats_signal": [0] * n,
            "sats_entry_price": [None] * n,
            "sats_sl_price": [None] * n,
            "sats_tp1_price": [None] * n,
            "sats_tp2_price": [None] * n,
            "sats_tp3_price": [None] * n,
            "sats_tp1_r": [None] * n,
            "sats_tp2_r": [None] * n,
            "sats_tp3_r": [None] * n,
            "sats_ready": [False] * n,
        }

        # TODO Phase 1 helper implementations:
        # - wilder_rsi(close, length)
        # - efficiency_ratio(close, length)
        # - volume_zscore(volume, length)
        # - pivot_high(high, left, right) / pivot_low(low, left, right)
        # - rolling highest/lowest helpers for TQI structure
        # Existing stateless ATR uses SMA, so SATS must keep its own Wilder ATR helper.
        raw_atr = wilder_atr(high, low, close, length=resolve_sats_preset(self.cfg)[0])
        rsi = wilder_rsi(close, length=resolve_sats_preset(self.cfg)[3])

        lower_band = None
        upper_band = None
        st_trend = 1
        trend_start_bar = 0
        active_mult_sm = None
        passive_mult_sm = None
        last_pivot_high = None
        last_pivot_low = None

        warmup = self.required_warmup_bars()

        for i in range(n):
            # 1. ER, vol ratio, TQI 계산
            # 2. adaptive multiplier 계산
            # 3. lower/upper band ratchet 갱신
            # 4. price flip / character flip 판단
            # 5. signal 발생 시 SL/TP 계획 산출
            #
            # Pine의 series[i]와 var 상태를 맞추기 위해 이 루프 안에서 이전 값을 직접 참조한다.
            out["sats_ready"][i] = i >= warmup

        return pl.DataFrame(
            out,
            schema={
                "sats_atr": pl.Float64,
                "sats_raw_atr": pl.Float64,
                "sats_er": pl.Float64,
                "sats_vol_ratio": pl.Float64,
                "sats_tqi": pl.Float64,
                "sats_tqi_er": pl.Float64,
                "sats_tqi_vol": pl.Float64,
                "sats_tqi_struct": pl.Float64,
                "sats_tqi_mom": pl.Float64,
                "sats_active_mult": pl.Float64,
                "sats_passive_mult": pl.Float64,
                "sats_lower_band": pl.Float64,
                "sats_upper_band": pl.Float64,
                "sats_trend": pl.Int8,
                "sats_st_line": pl.Float64,
                "sats_signal": pl.Int8,
                "sats_entry_price": pl.Float64,
                "sats_sl_price": pl.Float64,
                "sats_tp1_price": pl.Float64,
                "sats_tp2_price": pl.Float64,
                "sats_tp3_price": pl.Float64,
                "sats_tp1_r": pl.Float64,
                "sats_tp2_r": pl.Float64,
                "sats_tp3_r": pl.Float64,
                "sats_ready": pl.Boolean,
            },
        )
```

보조 지표는 TradingView parity를 위해 Pine과 같은 smoothing을 사용한다. 아래는 일부 helper 예시이며, Phase 1 구현에는 위 TODO helper 전체가 필요하다.

```python
def wilder_rma(values: np.ndarray, length: int) -> np.ndarray:
    """Wilder RMA tolerant of NaN gaps in ``values``.

    Tracks the most recent valid output as ``prev_valid`` instead of reading
    ``out[i - 1]``. This matters when the input is itself a derived series
    (e.g. efficiency ratio, true range during warmup) that has NaN holes:
    naive ``out[i - 1]`` would propagate NaN forever once the previous bar
    was skipped, even if Pine's stateful ``ta.rma`` would have kept its
    internal value across the gap.
    """
    out = np.full(len(values), np.nan, dtype=np.float64)
    acc = 0.0
    count = 0
    prev_valid: float | None = None

    for i, value in enumerate(values):
        if math.isnan(value):
            continue
        if prev_valid is None:
            acc += value
            count += 1
            if count == length:
                out[i] = acc / length
                prev_valid = out[i]
            continue
        out[i] = (prev_valid * (length - 1) + value) / length
        prev_valid = out[i]

    return out


def wilder_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    length: int,
) -> np.ndarray:
    tr = np.full(len(close), np.nan, dtype=np.float64)
    for i in range(len(close)):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
    return wilder_rma(tr, length)
```

## 6. 다중 TP 처리 정책

Pine 원본의 실현 R 계산은 TP1/TP2/TP3를 각각 1/3 청산으로 가정한다.

```text
TP1: 33.33%
TP2: 33.33%
TP3: 33.33%
SL: 남은 수량 전부
```

예시:

```text
TP1만 찍고 SL: 1/3 * 1R - 2/3 * 1R = -0.333R
TP1, TP2 찍고 SL: 1/3 * 1R + 1/3 * 2R - 1/3 * 1R = +0.667R
TP1, TP2, TP3 모두 도달: (1R + 2R + 3R) / 3 = +2.0R
```

다만 현재 `BracketSpec`는 `take_profit_price` 하나와 `stop_loss_price` 하나만 가진다. 따라서 구현 옵션은 두 가지다.

### Option A: 1차 구현, 단일 TP bracket

초기 구현에서는 `tp_execution_mode="single_tp"`를 두고 TP3 또는 TP1 중 하나만 백테스터 bracket에 연결한다. 이 방법은 현재 `BracketSpec`와 바로 호환되지만 Pine의 1/3 분할익절과 다르다.

권장 기본값:

```text
single_tp_level = "tp3"
```

주의: `single_tp_level="tp3"`는 신호와 SL/TP 산출 smoke test용 기본값이다. 전체 수량을 TP3에서 청산하므로 Pine의 `(TP1 + TP2 + TP3) / 3` 분할익절 모델보다 수익/손실 분포가 훨씬 공격적으로 바뀐다. 성과 비교에는 사용하지 말고, Pine 유사 성과 비교는 Phase 3 multi-leg TP 또는 별도 post-hoc 분석기로 수행한다.

이 경우 전략은 하나의 `BracketSpec`만 붙인다. 단, 현재 엔진은 `BracketSpec.time_stop_bars`를 자동 처리하지 않는다. timeout 청산은 strategy `on_bar()`에서 `ctx.bars_held()`를 검사해 직접 `ClosePosition()` intent를 발행해야 한다. **권장: SATS의 `BracketSpec`에는 `time_stop_bars`를 채우지 마라.** 일부 기존 전략(`BBKCLegacyCompatStrategy`)은 호환성 메타데이터로 필드를 채워두지만, 엔진이 무시하는 값이 spec에 살아 있으면 이중 진실 소스가 된다. SATS는 timeout 책임을 strategy 단일 경로(`ctx.bars_held()` + `ClosePosition()`)로 통일한다.

```python
from decimal import Decimal

from backtester.core.orders import BracketSpec, OrderIntent, TargetNotionalPct


intent = OrderIntent(
    symbol=symbol,
    side="buy",
    type="market",
    size_spec=TargetNotionalPct(Decimal("0.25")),
    reason="sats_buy",
    bracket=BracketSpec(
        take_profit_price=Decimal(str(row["sats_tp3_price"])),
        stop_loss_price=Decimal(str(row["sats_sl_price"])),
    ),
)
```

### Option B: 권장 최종형, multi-leg exit 확장

SATS 원본에 가깝게 하려면 백테스터가 multi-leg bracket을 지원해야 한다.

추가 타입 예시:

```python
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TakeProfitLeg:
    price: Decimal
    size_fraction: Decimal
    label: str = ""


@dataclass(frozen=True)
class MultiBracketSpec:
    take_profits: tuple[TakeProfitLeg, ...]
    stop_loss_price: Decimal | None = None
    time_stop_bars: int | None = None
```

Entry fill 후 엔진은 다음 child order를 생성해야 한다.

```text
TP1 reduce-only limit, size = parent_qty * 0.3333
TP2 reduce-only limit, size = parent_qty * 0.3333
TP3 reduce-only limit, size = parent_qty * 0.3334
SL reduce-only stop, size = remaining position
```

주의할 점:

- 현재 OCO 구현은 같은 `oco_group_id` 안에서 한 주문이 체결되면 sibling을 모두 취소한다. TP1/TP2/TP3/SL을 단순히 같은 OCO group에 넣으면 TP1 체결 직후 TP2/TP3/SL까지 취소되는 잘못된 동작이 된다.
- Multi-leg TP에는 기존 OCO와 다른 grouping 모델이 필요하다. 예를 들어 TP legs는 서로 sibling cancel 대상이 아니고, SL만 남은 포지션 전체를 보호해야 한다.
- TP가 체결될 때마다 SL 주문 수량을 남은 포지션 크기로 줄여야 한다.
- SL이 체결되면 남은 TP 주문을 모두 cancel해야 한다.
- TP3까지 모두 체결되면 SL 주문을 cancel해야 한다.
- 같은 봉에서 TP와 SL이 모두 닿으면 `BarPathModel` 정책을 따른다. 기본은 `PESSIMISTIC`, 즉 SL 우선이다.

실현 R 계산은 분석 레이어에서 아래처럼 재구성할 수 있다.

```python
def realized_r_from_legs(
    tp_hits: list[bool],
    tp_rs: list[float],
    weights: list[float],
    sl_hit: bool,
) -> float:
    taken = 0.0
    remaining = 1.0
    for hit, r, weight in zip(tp_hits, tp_rs, weights, strict=True):
        if hit:
            taken += weight * r
            remaining -= weight
    if sl_hit:
        return taken - remaining
    return taken
```

## 7. Strategy 구현 예시

SATS 전략은 precomputed indicator를 읽고 signal 발생 시 주문을 낸다. 1차 구현에서는 current engine과 맞추기 위해 single TP bracket을 사용한다.

중요: registry/YAML 경로에서는 `strategy_params`가 그대로 `SATSStrategy(**params)`에 들어간다. 따라서 생성자 인자로 `SATSIndicator` 객체를 받지 말고, primitive params를 받아 내부에서 `SATSConfig`와 `SATSIndicator`를 생성해야 한다.

```python
from decimal import Decimal
from typing import Literal

from backtester.core.context import StrategyContext
from backtester.core.orders import BracketSpec, ClosePosition, OrderIntent, TargetNotionalPct
from backtester.indicators.base import Indicator
from backtester.strategies.base import BaseStrategy


class SATSStrategy(BaseStrategy):
    def __init__(
        self,
        *,
        preset: str = "Auto",
        timeframe_minutes: int = 60,
        notional_pct: Decimal | float | str = Decimal("0.05"),
        single_tp_level: Literal["tp1", "tp2", "tp3"] = "tp3",
        allow_short: bool = True,
        trade_max_age_bars: int | None = 100,
        atr_len: int = 13,
        base_mult: float = 2.0,
        er_length: int = 20,
        quality_strength: float = 0.4,
        sl_atr_mult: float = 1.5,
        tp1_r: float = 1.0,
        tp2_r: float = 2.0,
        tp3_r: float = 3.0,
        # Implementation rule: expose every SATSConfig tuning field as a primitive
        # kwarg here, or group them under one validated dict before constructing SATSConfig.
    ) -> None:
        self.notional_pct = Decimal(str(notional_pct))
        self.single_tp_level = single_tp_level
        self.allow_short = allow_short
        self.trade_max_age_bars = trade_max_age_bars
        self._sats = SATSIndicator(
            SATSConfig(
                preset=preset,  # type: ignore[arg-type] if Literal narrowing is not added
                timeframe_minutes=timeframe_minutes,
                atr_len=atr_len,
                base_mult=base_mult,
                er_length=er_length,
                quality_strength=quality_strength,
                sl_atr_mult=sl_atr_mult,
                tp1_r=tp1_r,
                tp2_r=tp2_r,
                tp3_r=tp3_r,
                trade_max_age_bars=trade_max_age_bars or 0,
            )
        )

    def required_indicators(self) -> list[Indicator]:
        return [self._sats]

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        symbol = ctx.primary_symbol
        tf = ctx.primary_timeframe

        if self.trade_max_age_bars is not None and ctx.has_position(symbol):
            held = ctx.bars_held(symbol)
            if held is not None and held >= self.trade_max_age_bars:
                pos = ctx.position(symbol)
                if pos is not None and not pos.is_flat:
                    return [
                        OrderIntent(
                            symbol=symbol,
                            side="sell" if pos.size > 0 else "buy",
                            type="market",
                            size_spec=ClosePosition(),
                            reason="sats_time_stop",
                            reduce_only=True,
                        )
                    ]

        ind = ctx.indicators[symbol][tf]
        if ind.is_empty():
            return []

        row = ind.tail(1).to_dicts()[0]
        if not row.get("sats_ready") or row.get("sats_signal", 0) == 0:
            return []

        signal = int(row["sats_signal"])
        has_pos = ctx.has_position(symbol)
        if has_pos:
            return []

        if signal == -1 and not self.allow_short:
            return []

        side = "buy" if signal == 1 else "sell"
        tp_col = {
            "tp1": "sats_tp1_price",
            "tp2": "sats_tp2_price",
            "tp3": "sats_tp3_price",
        }[self.single_tp_level]

        sl_price = row["sats_sl_price"]
        tp_price = row[tp_col]
        if sl_price is None or tp_price is None:
            return []

        return [
            OrderIntent(
                symbol=symbol,
                side=side,
                type="market",
                size_spec=TargetNotionalPct(self.notional_pct),
                reason=f"sats_{'buy' if signal == 1 else 'sell'}",
                bracket=BracketSpec(
                    take_profit_price=Decimal(str(tp_price)),
                    stop_loss_price=Decimal(str(sl_price)),
                ),
            )
        ]
```

Multi-leg 최종형에서는 `BracketSpec` 대신 `MultiBracketSpec`를 쓰거나, 엔진 확장 전 임시로 전략이 포지션 확인 후 reduce-only limit/stop을 직접 생성하는 방식을 선택할 수 있다. 다만 후자는 OCO 그룹, SL 수량 축소, TP sibling cancel을 전략이 직접 관리해야 하므로 엔진 확장보다 복잡해지기 쉽다.

위 예시는 구조 설명용이다. 실제 구현에서는 `SATSConfig`의 모든 튜닝 필드를 primitive kwarg로 노출하거나, `sats_params: dict[str, Any]`를 받아 validator로 검증한 뒤 `SATSConfig(**validated)`로 넘긴다. 일부 필드만 forward하면 YAML에서 튜닝할 수 없는 숨은 기본값이 생긴다. Phase 1은 `ignore_while_position`만 지원한다. `close_then_reverse`는 기존 포지션 close intent와 신규 entry intent의 순서, `allow_flip` 설정, 같은 봉 처리 정책을 함께 확정한 뒤 Phase 2 이후 추가한다. `preset` Literal 타입 처리는 `SATSConfig` 생성 전에 별도 validator로 정리하는 편이 좋다.

## 8. Pine parity 체크리스트

TradingView와 신호를 최대한 맞추려면 다음 항목을 고정한다.

- ATR은 Wilder RMA 방식으로 계산한다.
- RSI도 Wilder smoothing을 사용한다.
- `ta.pivothigh`, `ta.pivotlow`는 right bars가 지난 뒤 확정되는 값으로 구현한다.
- `nz(x, fallback)` 처리를 명확히 한다.
- `var` 변수는 루프 상태 변수로 유지한다.
- `barstate.isconfirmed`는 백테스터가 닫힌 봉만 전달한다고 가정한다.
- signal entry price는 우선 Pine과 동일하게 signal candle close로 산출한다.
- 실제 체결은 BacktestEngine 정책을 따른다. 기본 `next_bar_open`이면 Pine label 가격과 체결 가격이 다를 수 있다.
- 현재 엔진은 entry가 체결된 같은 봉의 high/low로 bracket TP/SL 체결을 즉시 시도한다. 타이트한 SL/TP, 5m 이하 timeframe에서는 진입 봉에서 바로 stop-out 또는 TP 체결이 발생할 수 있으며, TradingView 시각화와 체감상 차이가 날 수 있다.
- 동일 봉 TP/SL 충돌은 `BarPathModel.PESSIMISTIC`를 기본값으로 둔다.

## 9. 구현 단계

### Phase 1: 지표와 신호

- `backtester/src/backtester/indicators/stateful/sats.py` 추가
- `SATSConfig`, `SATSIndicator` 구현
- ATR, RSI, ER, volume z-score, pivot helper 구현
- 출력 컬럼과 dtype은 Section 3 스키마와 Section 5 `pl.DataFrame(schema=...)`를 일치시킨다.
- TQI, adaptive multiplier, asymmetric SuperTrend 구현
- `sats_signal`, `sats_sl_price`, `sats_tp*_price` 출력
- 단위 테스트: known small OHLCV에서 warmup, trend flip, TP/SL 산출 검증

### Phase 2: 전략 연결

- `backtester/src/backtester/strategies/sats.py` 추가
- `SATSStrategy` 구현
- strategy 생성자는 YAML/registry 호환을 위해 primitive params만 받는다.
- `required_indicators()`에서 내부 `self._sats`를 반환한다.
- `backtester/src/backtester/strategies/registry.py`에 `"sats"` 등록
- current engine 호환을 위해 single TP bracket 먼저 지원
- timeout은 `BracketSpec.time_stop_bars`가 아니라 `ctx.bars_held()` + `ClosePosition()`으로 strategy에서 직접 처리
- config/registry 연결
- smoke backtest 추가

### Phase 3: multi-leg TP

- `MultiBracketSpec` 또는 별도 exit plan 타입 설계
- 기존 OCO sibling cancel 모델과 분리된 partial TP grouping 설계
- Entry fill 후 TP1/TP2/TP3 reduce-only limit 생성
- shared SL stop 생성
- TP 체결 시 SL 수량 축소
- SL 체결 시 잔여 TP cancel
- 동일 봉 충돌은 기존 `BarPathModel` 우선순위 사용
- event export에 TP leg label과 parent relationship 보존

### Phase 4: 검증

- TradingView에서 같은 symbol/timeframe/exported OHLCV로 signal 날짜 비교
- Pine과 Python의 `sats_tqi`, `sats_trend`, `sats_st_line`, `sats_signal` 샘플 비교
- single TP와 multi-leg TP의 성과 차이 리포트 작성
- 수수료/슬리피지 포함 결과와 Pine 대시보드식 R 결과를 분리해서 기록

## 10. 우선 결정할 옵션

구현 전에 아래 기본값을 확정한다.

```text
entry_fill_policy: next_bar_open
signal_price_for_plan: signal_close
tp_weights: [0.3333, 0.3333, 0.3334]
same_bar_conflict: pessimistic_sl_first
reverse_signal_policy: ignore_while_position only in Phase 1
initial_tp_execution_mode: single_tp 또는 multi_leg_engine_extension
single_tp_level: tp3
```

추천 기본값:

```text
entry_fill_policy = next_bar_open
signal_price_for_plan = signal_close
tp_weights = [0.3333, 0.3333, 0.3334]
same_bar_conflict = pessimistic_sl_first
reverse_signal_policy = ignore_while_position only in Phase 1
initial_tp_execution_mode = single_tp
single_tp_level = tp3
```

이렇게 시작하면 current BacktestEngine과 충돌 없이 빠르게 SATS 신호를 검증할 수 있다. 단, 이 모드는 Pine의 분할익절 성과와 다르므로 성과 검증의 기준으로 쓰지 않는다. Pine의 분할익절 모델까지 정확히 반영하는 것은 Phase 3에서 엔진 기능으로 올리는 편이 가장 깔끔하다.
