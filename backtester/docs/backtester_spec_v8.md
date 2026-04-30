# 범용 백테스트 프레임워크 설계 문서 (v8)

## 0. 프로젝트 개요

### 목적
데이터·자산·전략·시간프레임·실행모델이 직교(orthogonal)하게 분리된 개인용 백테스트 엔진. 멀티 자산, 멀티 타임프레임, 멀티 거래시간 지원. 각 백테스트 run을 self-contained 패키지로 관리하며 결과를 시각적으로 검증.

### 사용 시나리오
- BB-KC Squeeze 전략을 BTC/ETH의 1h, 4h, 1d 등에서 백테스트
- 같은 BB-KC Squeeze를 해외주식 지수(SPX, NDX)에 적용
- FRAMA Channel 전략을 끼워넣어 비교
- BTC-ETH pairs trading, 포트폴리오 리밸런싱
- 백테스트 결과를 차트·리포트로 시각적 검증
- run 디렉토리 통째로 압축 공유 / 다른 PC에서 재현

### 범위
- **A안 (개인용)**: 코드 깔끔·확장 가능, 외부 배포 미고려
- **언어**: Python 100%
- **DataFrame**: Polars (시각화 경계만 pandas)
- **시각화**: Plotly + quantstats
- **데이터 소스**: Bybit API + 로컬 Parquet

### v7 → v8 핵심 변경
- **SNAPSHOT 이벤트 주기 명시**: `snapshot_every_bars: int = 1` + FILL/SETTLE/EXPIRE 직후 implicit
- **EventLog vs results 관계 원칙**: EventLog = 원본, results = 캐시
- **`on_run_exists` 정책**: fail (기본) / overwrite / auto_suffix / archive
- **`snapshot_reason` 필드**: SNAPSHOT 이벤트마다 fill/settlement/expire/periodic 구분
- **`resolved_run_id` 추적**: auto_suffix/archive 시 사용자가 실제 디렉토리 인지 가능
- **BacktestConfig `__post_init__` 검증**: 잘못된 값 즉시 ConfigError
- **CLI 명시적 알림**: auto_suffix/archive/overwrite 발생 시 stdout 출력 (`--quiet`로 끌 수 있음)
- **CLI `rebuild-results` 명령**: events.jsonl에서 results 재생성 (Phase 2)
- **EventLog `schema_version` 필드**: 모든 이벤트 라인에 스키마 버전 명시 (rebuild-results, replay 호환성 보호)

### 용어 (Glossary)

| 용어 | 정의 |
|------|------|
| **OHLCV timestamp** | 봉 시작 시각. 1h봉 ts=13:00 → 13:00~14:00 데이터. |
| **ClockEvent.timestamp** | 봉 마감 시각 = 전략 의사결정 가능 시각. 위 봉의 ClockEvent는 14:00에 발생. |
| **last_closed_time** | 주어진 now 기준 가장 최근 마감 봉의 ts. now == 마감 시각이면 그 봉이 last_closed. |
| **decision_ts** | 의사결정이 일어난 ClockEvent.timestamp (= 봉 마감 시각). |
| **bar_timestamp** | 의사결정 시 참조한 가장 최근 마감 봉의 OHLCV timestamp (= 봉 시작 시각). |
| **requested_run_id** | 사용자가 BacktestConfig에 명시한 원본 run_id. |
| **resolved_run_id** | 실제 디렉토리에 사용된 run_id. auto_suffix 시 다를 수 있음. 사용자 노출은 항상 이 값. |
| **EventLog** | events.jsonl. 백테스트의 1차 원본. results/는 여기서 재생성 가능한 캐시. |
| **SNAPSHOT** | Ledger 상태(equity/cash/positions)를 시점에 박제한 이벤트. snapshot_reason 필수. |
| **snapshot_reason** | SNAPSHOT 발행 사유. fill / settlement / expire / periodic 중 하나. |
| **BarPathModel** | 한 봉 안에서 high/low 도달 순서 가정. PESSIMISTIC = 불리한 쪽 먼저 도달. |
| **size_unit** | Instrument의 사이즈 단위. base_asset / contracts / quote_notional. |
| **size_spec** | OrderIntent의 사이즈 명세. TargetWeight / TargetNotional / TargetUnits / FullPosition / ClosePosition / ScaleIn. |
| **persist_run_data** | bars/indicators 영속화 정책. copy / symlink / none. |
| **on_run_exists** | run 디렉토리 충돌 정책. fail / overwrite / auto_suffix / archive. |
| **schema_version** | events.jsonl의 각 라인 헤더에 포함되는 정수. **호환성을 깨는** 스키마 변경(필드 제거/타입 변경/의미 변경) 시 증가. 필드 추가만 있는 변경은 동일 버전 유지. |

---

## 1. 핵심 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│  BacktestEngine (오케스트레이터)                                 │
│                                                                  │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────────┐   │
│  │ DataSource  │→  │ Instrument   │→  │ Strategy             │   │
│  └─────────────┘   └──────────────┘   └──────────────────────┘   │
│        │                  │                       │              │
│        ↓                  ↓                       ↓              │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │ Clock — Phase 1: SimpleClock, Phase 3+: GlobalClock     │     │
│  └─────────────────────────────────────────────────────────┘     │
│        │                                                         │
│        ↓                                                         │
│  ┌──────────────────┐  ┌────────────────┐  ┌────────────────┐    │
│  │ IndicatorEngine  │  │ ExecutionModel │  │ OrderBook      │    │
│  └──────────────────┘  └────────────────┘  └────────────────┘    │
│        │                                                         │
│        ↓                                                         │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Portfolio Subsystem                                     │    │
│  │  Sizer + RiskManager + Ledger                            │    │
│  └──────────────────────────────────────────────────────────┘    │
│        │                                                         │
│        ↓                                                         │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Run Directory (self-contained 패키지)                   │    │
│  │  events.jsonl(원본) + bars/ + indicators/ + config.* +  │    │
│  │  results/(캐시) + charts/                                │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ↓ (post-hoc)
┌──────────────────────────────────────────────────────────────────┐
│  Visualization Layer (run_dir만으로 모든 차트·리포트 재현)       │
└──────────────────────────────────────────────────────────────────┘
```

**핵심 원칙**:
- 각 run은 self-contained 패키지
- **EventLog가 1차 원본, results/는 캐시 산출물** (불일치 시 EventLog 기준)
- 시각화는 run_dir만 입력으로
- run_id 충돌은 명시적 정책으로 해결, **resolved_run_id로 실제 사용 디렉토리 추적**

---

## 2. 시간 모델 (Time Semantics)

### 2.1 두 종류의 timestamp

| 종류 | 의미 | 예시 |
|------|------|------|
| **OHLCV timestamp** | 봉 시작 시각 | 1h봉 timestamp=13:00은 13:00~14:00 데이터 |
| **ClockEvent.timestamp** | **봉 마감 시각 = 의사결정 가능 시각** | 위 봉의 ClockEvent는 14:00에 발생 |

절대 혼용 금지.

### 2.2 시간 흐름 예시

```
실제 시간 ──┬──────────┬──────────┬──────────┬──→
           12:00      13:00      14:00      15:00

OHLCV:    [12:00 봉  ][13:00 봉  ][14:00 봉  ]
          시작=12:00 시작=13:00 시작=14:00
          마감=13:00 마감=14:00 마감=15:00

ClockEvent:            ↑          ↑          ↑
                      13:00      14:00      15:00
```

### 2.3 last_closed_time
경계: now가 마감 시각과 정확히 일치하면 그 봉은 '이미 마감'.

### 2.4 BarsView 노출
ctx.now=14:00 → 1h봉 timestamp≤13:00만 노출.

### 2.5 의사결정 시점
봉 마감 시 결정 → 다음 봉 시가/tick에 체결.

### 2.6 Warmup
`warmup_bars` (0이면 자동 추정). 그 동안 on_bar 미호출.

---

## 3. 컴포넌트 상세 스펙

### 3.1 DataSource

```python
class DataSource(Protocol):
    def fetch(self, symbol, timeframe, start, end) -> tuple[pl.DataFrame, GapReport]: ...
```

**스키마**:
```python
{
    "timestamp": pl.Datetime("us", time_zone="UTC"),
    "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64,
}
```

**Phase별**: Phase 1 ParquetDataSource, Phase 1.5 CSVDataSource, Phase 2 BybitDataSource.

---

### 3.2 데이터 갭 정책

```python
@dataclass
class GapReport:
    symbol: str
    timeframe: str
    expected_interval: timedelta
    gaps: list[tuple[datetime, datetime]]
    total_missing_bars: int

    def is_significant(self, threshold: int = 10) -> bool:
        return self.total_missing_bars > threshold
```

기본: 갭 그대로, 알림. 자동 fill 안 함.

---

### 3.3 Instrument + FeeModel

```python
@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: str
    tick_size: Decimal
    tick_value: Decimal
    contract_multiplier: Decimal
    quote_currency: str
    base_currency: str
    size_unit: Literal["base_asset", "contracts", "quote_notional"]
    fee_model: FeeModel
    funding_model: Optional[FundingModel]
    margin_model: MarginModel
    trading_hours: TradingHours
```

> **⚠ Phase 1 구현 범위**: `funding_model`, `margin_model`, `trading_hours`는 Phase 1에서 **클래스 자체를 정의하지 않는다** (§17.1 미래 필드 정책). Phase 1 Instrument에서는 이 필드들을 **생략**하거나 `Optional[...]` + 기본값 `None`으로만 두고 어디에서도 참조하지 않는다.

**FeeModel Phase 1**: flat taker. ExecutionModel 책임. `maker` 필드는 Phase 2에서 사용.

```python
@dataclass(frozen=True)
class FeeModel:
    type: Literal["flat", "tiered"]
    taker: Decimal
    maker: Decimal = Decimal("0")

    def compute_fee(self, fill_notional, is_maker=False) -> Decimal:
        return abs(fill_notional) * self.taker  # Phase 1
```

---

### 3.4 Clock

```python
@dataclass
class ClockEvent:
    timestamp: datetime
    bar_closes: dict[str, list[str]]
    settlements: list[tuple[str, str]] = field(default_factory=list)  # Phase 1: 항상 []


class SimpleClock:
    def __iter__(self) -> Iterator[ClockEvent]:
        interval = parse_timeframe(self._timeframe)
        for bar_start in self._bars_timestamps:
            close_time = bar_start + interval
            yield ClockEvent(
                timestamp=close_time,
                bar_closes={sym: [self._timeframe] for sym in self._symbols},
                settlements=[],
            )
```

---

### 3.5 Strategy + BaseStrategy

```python
class BaseStrategy:
    def on_init(self, instruments): pass
    def required_indicators(self): return []
    def on_pending_orders(self, ctx, pending): return []
    def on_data_gap(self, symbol, start, end): return []

    def on_bar(self, ctx) -> list[OrderIntent]:
        raise NotImplementedError
```

---

### 3.6 BarsView — O(1) Slicing

```python
class TimeframeView:
    def __getitem__(self, tf):
        idx_map = self._engine.timestamp_index[self._symbol][tf]
        ts_list = self._engine.timestamps[self._symbol][tf]
        last_closed = self._engine.clock_helper.last_closed_time(tf, self._now)

        end_idx = idx_map.get(last_closed)
        if end_idx is None:
            end_idx = bisect_right(ts_list, last_closed) - 1
        if end_idx < 0:
            return self._engine.bars[self._symbol][tf].slice(0, 0)
        return self._engine.bars[self._symbol][tf].slice(0, end_idx + 1)
```

`df.filter()` 매 봉 호출 금지.

---

### 3.7 OrderIntent + SizeSpec

```python
SizeSpec = Union[TargetWeight, TargetNotional, TargetUnits,
                 FullPosition, ClosePosition, ScaleIn]


@dataclass
class OrderIntent:
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop", "stop_limit"]
    size_spec: SizeSpec
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    reason: str = ""
    tif: Literal["GTC", "IOC", "FOK", "DAY"] = "GTC"
    client_order_id: Optional[str] = None
    expires_at: Optional[datetime] = None
```

> **⚠ Phase 1 구현 범위**: `type`은 `"market"`만 처리. `tif="GTC"` + `expires_at=None`만 정상 처리. 그 외 값은 `OrderBook.add`/`ExecutionModel`에서 `NotImplementedError("Phase 2")`. SizeSpec 지원 범위는 §3.13 Sizer 참조.

---

### 3.8 IndicatorEngine + 자동 Persist

```python
class IndicatorEngine:
    def precompute(self, bars, indicators,
                   persist_to: Optional[Path] = None) -> None:
        """persist_to 지정 시 {symbol}_{timeframe}.parquet 자동 저장."""
        ...

    def required_warmup(self, indicators) -> int:
        return max((ind.required_warmup_bars() for ind in indicators), default=0)
```

---

### 3.9 OrderBook

```python
@dataclass
class Order:
    id: str
    intent: OrderIntent
    state: Literal["pending", "partially_filled", "filled",
                   "cancelled", "expired", "rejected"]
    submitted_at: datetime
    sized_quantity: Decimal
    fills: list[Fill]
    remaining: Decimal


class OrderBook:
    def add(self, intent, sized_quantity, ts) -> Order: ...
    def cancel(self, order_id, ts) -> bool: ...
    def modify(self, order_id, **changes) -> bool: ...
    def get_active(self, symbol=None) -> list[Order]: ...
    def expire_pending(self, ts) -> list[Order]: ...
    def fill(self, order_id, fill) -> None: ...
```

---

### 3.10 ExecutionModel + BarPathModel

```python
class BarPathModel(Enum):
    PESSIMISTIC = "pessimistic"
    OPTIMISTIC = "optimistic"
    OPEN_TO_CLOSE = "linear"
    OHLC_ORDER = "ohlc"
```

> **⚠ Phase 1 구현 범위**: `BarPathModel` enum은 정의만 두고 **사용하지 않는다**. ExecutionModel은 `next_bar_open` 단 하나만 구현하며, 다음 봉 open에 즉시 체결한다 (slippage 0). 아래 limit BUY 예시는 **Phase 2 사양**이며, Phase 1에서는 limit/stop 입력 자체가 `NotImplementedError`로 차단된다.

**Limit BUY (PESSIMISTIC) — Phase 2**:
```
if open <= L: fill_price = open
elif low <= L: fill_price = L
else: no_fill
```

```python
@dataclass
class Fill:
    timestamp: datetime
    symbol: str
    price: Decimal
    size: Decimal
    side: Literal["buy", "sell"]
    fee: Decimal
    fee_currency: str
    order_id: str
    intent_reason: str
    indicators_snapshot: dict
```

---

### 3.11 MarketSnapshot

```python
@dataclass
class MarketSnapshot:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    funding_rate: Optional[Decimal] = None
    open_interest: Optional[Decimal] = None
    metadata: dict = field(default_factory=dict)
```

---

### 3.12 Position

```python
@dataclass
class Position:
    symbol: str
    size: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    last_update: Optional[datetime] = None

    @property
    def is_flat(self) -> bool:
        return self.size == 0

    def is_effectively_flat(self, tick_size: Decimal) -> bool:
        return abs(self.size) < tick_size

    @property
    def direction(self) -> Literal["long", "short", "flat"]:
        if self.size > 0: return "long"
        if self.size < 0: return "short"
        return "flat"
```

**Decimal 비교**: `is_effectively_flat()` 또는 `abs(a-b) < tick_size`.

---

### 3.13 Portfolio Subsystem

#### Sizer
```python
class Sizer:
    def resolve(self, intent, instrument, equity, position, market) -> Decimal:
        match intent.size_spec:
            case TargetWeight(weight): ...       # Phase 2
            case TargetNotional(notional): ...   # Phase 1
            case TargetUnits(units): return units                    # Phase 1
            case FullPosition(): return self._max_units(...)         # Phase 2
            case ClosePosition(): return abs(position.size)          # Phase 1
            case ScaleIn(by): ...                # Phase 2
```

> **⚠ Phase 1 구현 범위**: `TargetUnits` / `TargetNotional` / `ClosePosition` 3종만 구현. 나머지(`TargetWeight`, `FullPosition`, `ScaleIn`)는 `case _: raise NotImplementedError("Phase 2")`로 차단.
>
> **short 정책 강제 위치 (Phase 1)**: 결과 size가 적용되었을 때 포지션이 음수가 되면 `Sizer.resolve()`가 `NotImplementedError("short not supported in Phase 1")` raise. Position/OrderBook/Risk/Ledger는 이 정책에 관여하지 않는다.

#### RiskManager
```python
@dataclass
class RiskLimits:
    max_position_size: Optional[Decimal] = None      # Phase 2
    max_total_exposure: Optional[Decimal] = None     # Phase 2
    max_leverage: Optional[Decimal] = None           # Phase 2
    max_drawdown_halt: Optional[float] = None        # Phase 2
    max_orders_per_symbol: int = 5                   # Phase 1
    blacklist_symbols: set[str] = field(default_factory=set)  # Phase 1


class RiskManager:
    def check(self, intent, sized_quantity, instrument, ledger,
              active_orders) -> RiskCheckResult: ...
```

> **⚠ Phase 1 구현 범위**: `blacklist_symbols`(거부 → REJECTED) + `max_orders_per_symbol`(초과 → REJECTED) 2개만 검사. 나머지 필드는 정의만 두고 `RiskManager.check()` 안에서 무시 (Phase 2에서 검사 로직 추가).

#### Ledger
```python
class Ledger:
    @property
    def equity(self) -> Decimal: ...
    @property
    def positions(self) -> dict[str, Position]: ...

    def on_fill(self, fill, instrument): ...        # Phase 1
    def on_market(self, snapshots): ...             # Phase 1
    def on_settle(self, cashflow): ...              # Phase 1.5 (settlement 도입 후)
    def on_expired(self, expired): ...              # Phase 1: noop (cash 영향 없음). Phase 1.5+ 활성
    def equity_curve(self) -> pl.DataFrame: ...     # Phase 1

    def snapshot(self) -> dict:
        """SNAPSHOT 이벤트 payload용. snapshot_reason은 호출자가 추가."""
        return {
            "equity": str(self.equity),
            "cash": str(self.cash),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "positions": {
                sym: {"size": str(p.size),
                      "avg_price": str(p.avg_price),
                      "unrealized_pnl": str(p.unrealized_pnl)}
                for sym, p in self.positions.items()
                if not p.is_flat
            },
        }
```

> **⚠ Phase 1 구현 범위**: `on_fill`, `on_market`, `equity_curve`, `equity` / `cash` / `realized_pnl` / `unrealized_pnl` / `positions` 속성 + `snapshot()` 메서드만 구현. `on_settle`은 정의만 두고 본문 `raise NotImplementedError("Phase 1.5")`. `on_expired`는 noop 본문(`pass`)으로 두고 Phase 1.5+에서 활성.
>
> **short 차단 위치**: `Position` 클래스는 순수 데이터 컨테이너로 두고 정책 강제는 하지 않는다. short 진입 시도(현재 flat에서 sell 주문, 또는 long 보유분 초과 sell)는 **Sizer 단계에서 차단** — `Sizer.resolve()`가 결과 size를 검사해 음수 포지션을 만들 수 있으면 `NotImplementedError("short not supported in Phase 1")` raise. Risk/OrderBook/Ledger는 short 정책에 무관.

**Decimal 가드**:
```python
def to_decimal(x) -> Decimal:
    if isinstance(x, Decimal): return x
    if isinstance(x, (int, str)): return Decimal(str(x))
    if isinstance(x, float): return Decimal(str(x))
    raise TypeError(f"Cannot convert {type(x)} to Decimal")
```

---

### 3.14 FundingModel — Phase 1.5

```python
@dataclass(frozen=True)
class FundingModel:
    interval_hours: int
    rate_source: Literal["constant", "from_data_source"]
    constant_rate: Optional[Decimal] = None


class FundingProcessor:
    def process(self, symbol, ts, instrument, position,
                market, data_source=None) -> Optional[CashFlow]: ...
```

---

### 3.15 EventLog

#### Event Types

```python
class EventType(StrEnum):
    BAR_CLOSE = "bar_close"
    DATA_GAP = "data_gap"
    INTENT_CREATED = "intent_created"
    ORDER_ADDED = "order_added"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_MODIFIED = "order_modified"
    ORDER_EXPIRED = "order_expired"
    ORDER_REJECTED = "order_rejected"
    FILL = "fill"
    SETTLE = "settle"
    SNAPSHOT = "snapshot"


@dataclass
class Event:
    ts: datetime
    type: EventType
    payload: dict


@dataclass
class IntentCreatedPayload:
    intent: OrderIntent
    decision_ts: datetime       # ClockEvent.timestamp (봉 마감)
    bar_timestamp: datetime     # OHLCV timestamp (봉 시작)
    bar_close_price: Decimal
```

#### SNAPSHOT 이벤트 + `snapshot_reason`

**기본 정책**:
- `BacktestConfig.snapshot_every_bars: int = 1`
- 매 N봉마다 정기 SNAPSHOT (primary_timeframe 기준)
- FILL/SETTLE/ORDER_EXPIRED 직후 implicit SNAPSHOT (주기 무관)

**모든 SNAPSHOT 이벤트 payload에 `snapshot_reason` 필드 포함**:

```python
SnapshotReason = Literal["fill", "settlement", "expire", "periodic"]

# payload 예시
{
    "equity": "10523.45",
    "cash": "5000.00",
    "realized_pnl": "523.45",
    "unrealized_pnl": "0",
    "positions": {...},
    "snapshot_reason": "fill",   # 항상 포함
}
```

**이유**:
- 같은 ts에 여러 SNAPSHOT 찍히는 케이스 허용 (FILL 직후 + 같은 봉 마감 정기) — dedup 비용 회피, 단순성 유지
- 디버깅 시 "왜 이 시점에 두 번 찍혔지?" 추적 가능
- 분석 시 reason="fill"만 필터링해서 거래 전후 equity만 추출 가능
- `build_equity_series()`는 같은 ts의 마지막 SNAPSHOT만 사용 (또는 reason 필터)

#### Serialize 유틸

```python
def serialize_event_payload(obj):
    """Decimal/datetime/Enum/dataclass 안전 변환."""
    if obj is None or isinstance(obj, (bool, int, str)): return obj
    if isinstance(obj, Decimal): return str(obj)
    if isinstance(obj, float): return obj
    if isinstance(obj, (datetime, date)): return obj.isoformat()
    if isinstance(obj, Enum): return obj.value
    if is_dataclass(obj): return serialize_event_payload(asdict(obj))
    if isinstance(obj, dict):
        return {k: serialize_event_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [serialize_event_payload(x) for x in obj]
    raise TypeError(f"Cannot serialize {type(obj)}")
```

#### EventLog (writer)

```python
EVENT_SCHEMA_VERSION = 1  # 호환성을 깨는 스키마 변경 시에만 증가 (필드 제거/타입 변경/의미 변경). 필드 추가만 있는 변경은 동일 버전 유지. rebuild-results, replay 호환성 게이트.


class EventLog:
    def __init__(self, run_dir: Path):
        self._path = run_dir / "events.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = None

    def __enter__(self):
        self._file = open(self._path, "a", buffering=1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None
        return False

    def append(self, event: Event) -> None:
        if self._file is None:
            raise RuntimeError("EventLog used outside context manager")
        line = json.dumps({
            "schema_version": EVENT_SCHEMA_VERSION,
            "ts": event.ts.isoformat(),
            "type": event.type.value,
            "payload": serialize_event_payload(event.payload),
        })
        self._file.write(line + "\n")
```

**스키마 버전 정책**:
- 매 이벤트 라인에 포함 (라인 단위 자기 기술 — 부분 읽기/스트리밍 안전).
- 추가만 있는 변경(Backward-compatible additive): 동일 버전 유지, payload에 새 필드만 추가.
- 의미 변경/필드 제거/타입 변경: 버전 증가 + EventLogReader가 마이그레이션 또는 거부.
- Phase 1 시작 = `EVENT_SCHEMA_VERSION = 1`.

#### EventLogReader (Phase 1.5)

```python
class EventLogReader:
    def __init__(self, events_path: Path):
        self._events: list[Event] = []
        self._by_type: dict[EventType, list[int]] = defaultdict(list)
        with open(events_path) as f:
            for i, line in enumerate(f):
                event = self._parse_event(json.loads(line))
                self._events.append(event)
                self._by_type[event.type].append(i)

    def by_type(self, t: EventType) -> Iterator[Event]: ...
    def to_dataframe(self, t: EventType) -> pd.DataFrame: ...
    def by_snapshot_reason(self, reason: SnapshotReason) -> Iterator[Event]:
        """SNAPSHOT 중 특정 reason만."""
        for e in self.by_type(EventType.SNAPSHOT):
            if e.payload.get("snapshot_reason") == reason:
                yield e
```

---

## 4. BacktestEngine + BacktestResult

### 4.1 BacktestResult

```python
@dataclass
class BacktestResult:
    """백테스트 결과. requested vs resolved run_id 모두 포함."""
    requested_run_id: str       # 사용자가 config에 명시한 값
    resolved_run_id: str        # 실제 사용된 디렉토리명 (auto_suffix 시 다름)
    run_dir: Path               # 절대 경로
    final_equity: Decimal
    total_return: Decimal
    num_fills: int
    num_intents: int
    config_path: Path           # 영속화된 config 파일 (Phase 1: config.json, Phase 1.5+: config.yaml)
    events_path: Path           # events.jsonl 경로
```

### 4.2 BacktestEngine

```python
class BacktestEngine:
    def __init__(self, config: BacktestConfig, verbose: bool = True):
        self.config = config
        self.verbose = verbose

        # Run 디렉토리 정책 처리 (resolved_run_id 결정)
        self.run_dir, self.resolved_run_id = self._resolve_run_dir(config)

        # 디렉토리 구조
        (self.run_dir / "bars").mkdir(exist_ok=True)
        (self.run_dir / "indicators").mkdir(exist_ok=True)
        (self.run_dir / "results").mkdir(exist_ok=True)
        (self.run_dir / "charts").mkdir(exist_ok=True)

        # config 영속화 (requested + resolved 모두 포함)
        if config.persist_run_data != "none":
            self._persist_config()

        # 데이터 로드 + bars 영속화
        self.instruments = self._load_instruments()
        self.data_source = self._build_data_source()
        self.bars, self.gap_reports = self._fetch_all_bars()
        if config.persist_run_data != "none":
            self._persist_bars()

        self._build_timestamp_indices()

        self.indicator_engine = IndicatorEngine(cache_dir=config.cache_dir)
        self.strategy = self._build_strategy()
        indicators_persist = (
            self.run_dir / "indicators"
            if config.persist_run_data != "none" else None
        )
        self.indicator_engine.precompute(
            self.bars, self.strategy.required_indicators(),
            persist_to=indicators_persist,
        )

        self.warmup_bars = config.warmup_bars or \
            self.indicator_engine.required_warmup(
                self.strategy.required_indicators())

        self.clock = self._build_clock()
        self.clock_helper = ClockHelper()
        self.orderbook = OrderBook()
        self.ledger = Ledger(config.initial_equity)
        self.sizer = Sizer()
        self.risk = RiskManager(config.risk_limits)
        self.execution = self._build_execution_model()

        self.current_snapshots: dict[str, MarketSnapshot] = {}
        self._bar_count = 0

    def _resolve_run_dir(self, config: BacktestConfig) -> tuple[Path, str]:
        """on_run_exists 정책에 따라 (run_dir, resolved_run_id) 반환.
        verbose=True면 stdout에 명시적 알림."""
        target = config.output_dir / config.run_id

        if not target.exists():
            target.mkdir(parents=True)
            return target, config.run_id

        # 디렉토리 이미 존재
        match config.on_run_exists:
            case "fail":
                raise RunDirectoryError(
                    f"Run directory already exists: {target}. "
                    f"Set on_run_exists to 'overwrite', 'auto_suffix', "
                    f"or 'archive' to handle this."
                )
            case "overwrite":
                shutil.rmtree(target)
                target.mkdir(parents=True)
                self._notify_resolution(
                    requested=config.run_id,
                    resolved=config.run_id,
                    policy="overwrite",
                    run_dir=target,
                )
                return target, config.run_id
            case "auto_suffix":
                suffix = 2
                while True:
                    candidate = config.output_dir / f"{config.run_id}_{suffix}"
                    if not candidate.exists():
                        candidate.mkdir(parents=True)
                        resolved = f"{config.run_id}_{suffix}"
                        self._notify_resolution(
                            requested=config.run_id,
                            resolved=resolved,
                            policy="auto_suffix",
                            run_dir=candidate,
                        )
                        return candidate, resolved
                    suffix += 1
            case "archive":
                ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                archive_path = config.output_dir / f"{config.run_id}_archive_{ts}"
                target.rename(archive_path)
                target.mkdir(parents=True)
                self._notify_resolution(
                    requested=config.run_id,
                    resolved=config.run_id,
                    policy="archive",
                    run_dir=target,
                    archive_path=archive_path,
                )
                return target, config.run_id

    def _notify_resolution(self, requested, resolved, policy, run_dir,
                           archive_path: Optional[Path] = None):
        """auto_suffix/archive/overwrite 발생 시 stdout 알림.
        verbose=False면 조용히 진행."""
        if not self.verbose:
            return
        print(f"[INFO] Run directory already existed, applied '{policy}'")
        print(f"[INFO] Requested run_id: {requested}")
        if requested != resolved:
            print(f"[INFO] Resolved run_id: {resolved}")
        print(f"[INFO] Run directory: {run_dir}")
        if archive_path:
            print(f"[INFO] Previous content archived to: {archive_path}")

    def _persist_config(self):
        """config 저장. requested + resolved + run_dir 모두 포함.
        Phase 1: config.json (json.dump, 표준 라이브러리만).
        Phase 1.5+: config.yaml (양방향 round-trip)."""
        config_dict = asdict(self.config)
        config_dict["resolved_run_id"] = self.resolved_run_id
        config_dict["run_dir"] = str(self.run_dir.absolute())
        # Phase 1: json.dump(config_dict, ..., default=str)
        # Phase 1.5+: yaml.safe_dump
        ...

    def _persist_bars(self):
        """bars/ 복사 또는 symlink. 파일명: {symbol}_{tf}.parquet"""
        bars_dir = self.run_dir / "bars"
        for sym, tfs in self.bars.items():
            for tf, df in tfs.items():
                target = bars_dir / f"{sym}_{tf}.parquet"
                if self.config.persist_run_data == "copy":
                    df.write_parquet(target)
                elif self.config.persist_run_data == "symlink":
                    source = self._original_bars_path(sym, tf)
                    if target.exists():
                        target.unlink()
                    target.symlink_to(source.absolute())

    def _emit_snapshot(self, ts: datetime, reason: SnapshotReason):
        """SNAPSHOT 이벤트 발행 헬퍼. snapshot_reason 자동 부착.
        모든 SNAPSHOT은 이 헬퍼를 통해 발행. 직접 Event 생성 금지."""
        payload = self.ledger.snapshot()
        payload["snapshot_reason"] = reason
        self._event_log.append(Event(ts, EventType.SNAPSHOT, payload))

    def run(self) -> BacktestResult:
        with EventLog(self.run_dir) as event_log:
            self._event_log = event_log
            for event in self.clock:
                self._process_event(event)

        self._persist_results()
        return BacktestResult(
            requested_run_id=self.config.run_id,
            resolved_run_id=self.resolved_run_id,
            run_dir=self.run_dir,
            final_equity=self.ledger.equity,
            total_return=(self.ledger.equity / self.config.initial_equity) - 1,
            num_fills=...,
            num_intents=...,
            config_path=self._config_persisted_path(),  # Phase 1: config.json, Phase 1.5+: config.yaml
            events_path=self.run_dir / "events.jsonl",
        )

    def _persist_results(self):
        """results/에 캐시 산출물 저장.
        주의: EventLog 1차 원본. 이 파일들은 EventLog에서 재생성 가능."""
        eq = self.ledger.equity_curve()
        eq.write_parquet(self.run_dir / "results" / "equity_curve.parquet")

    def _process_event(self, event):
        ts = event.timestamp

        # 1. 만료 주문 (단순성을 위해 항상 SNAPSHOT, reason="expire")
        expired = self.orderbook.expire_pending(ts)
        self.ledger.on_expired(expired)
        for o in expired:
            self._event_log.append(Event(ts, EventType.ORDER_EXPIRED,
                {"order_id": o.id}))
            self._emit_snapshot(ts, "expire")

        # 2. 시장 스냅샷
        snapshots = self._build_snapshots(ts, event.bar_closes)
        self.current_snapshots = snapshots
        self.ledger.on_market(snapshots)

        # 3. Settlement (Phase 1.5+)
        for symbol, kind in event.settlements:
            cf = self._process_settlement(symbol, kind, ts, snapshots)
            if cf:
                self.ledger.on_settle(cf)
                self._event_log.append(Event(ts, EventType.SETTLE, cf))
                self._emit_snapshot(ts, "settlement")

        # 4. 활성 주문 체결
        for order in self.orderbook.get_active():
            snap = snapshots.get(order.intent.symbol)
            if not snap: continue
            fill = self.execution.try_fill(
                order, snap, self.instruments[order.intent.symbol],
                self.config.bar_path_model)
            if fill:
                self.orderbook.fill(order.id, fill)
                self.ledger.on_fill(fill,
                    self.instruments[order.intent.symbol])
                self._event_log.append(Event(ts, EventType.FILL, fill))
                self._emit_snapshot(ts, "fill")  # implicit, 주기 무관

        # 5. 봉 마감 시 전략
        if event.bar_closes:
            self._bar_count += 1
            if self._bar_count > self.warmup_bars:
                ctx = StrategyContext(self, ts,
                    primary_symbol=self.config.primary_symbol,
                    primary_timeframe=self.config.primary_timeframe)
                intents = self.strategy.on_bar(ctx)

                for i in intents:
                    snap = snapshots[i.symbol]
                    payload = IntentCreatedPayload(
                        intent=i, decision_ts=ts,
                        bar_timestamp=snap.timestamp,
                        bar_close_price=snap.close,
                    )
                    self._event_log.append(Event(ts,
                        EventType.INTENT_CREATED, payload))

                actions = [OrderAction(type="new", intent=i) for i in intents]
                actions += self.strategy.on_pending_orders(
                    ctx, self.orderbook.get_active())

                for a in actions:
                    self._handle_action(a, ts, snapshots)

            # 6. 봉 마감 정기 SNAPSHOT
            if self._bar_count % self.config.snapshot_every_bars == 0:
                self._emit_snapshot(ts, "periodic")
```

**SNAPSHOT 발생 시점·reason 정리**:

| 시점 | snapshot_reason | 주기 무관 |
|------|----------------|-----------|
| ORDER_EXPIRED 직후 | `"expire"` | ✓ |
| SETTLE 직후 | `"settlement"` | ✓ |
| FILL 직후 | `"fill"` | ✓ |
| 봉 마감 + 주기 일치 | `"periodic"` | snapshot_every_bars 따름 |

**같은 ts에 여러 SNAPSHOT 가능**: FILL 직후 `"fill"` + 같은 봉 마감 `"periodic"` 둘 다 찍힘. dedup 안 함. `build_equity_series()`는 각 ts의 마지막 값 또는 reason 필터로 처리.

### 4.3 ClockEvent 처리 단계 순서

`_process_event()`의 단계별 동작·이벤트 발생을 표로 요약. 구현 시 이 순서를 절대 바꾸지 말 것.

| 순서 | 단계 | 발행 이벤트 | 비고 |
|------|------|-------------|------|
| 1 | pending order expire | `ORDER_EXPIRED` + `SNAPSHOT(expire)` | TIF/expires_at 만료 처리 |
| 2 | market snapshot 생성 | (내부 상태) | bar_closes 기반 MarketSnapshot 갱신 |
| 3 | ledger mark-to-market | (내부 상태) | unrealized PnL 갱신, 이벤트 미발행 |
| 4 | settlement (Phase 1.5+) | `SETTLE` + `SNAPSHOT(settlement)` | funding/만기 정산 |
| 5 | active order fill | `FILL` + `SNAPSHOT(fill)` | ExecutionModel.try_fill |
| 6 | warmup 이후 strategy.on_bar | `INTENT_CREATED` (intent당 1) | warmup 전이면 호출 자체 안 됨 |
| 7 | order action 처리 | `ORDER_ADDED` / `ORDER_CANCELLED` / `ORDER_MODIFIED` / `ORDER_REJECTED` | Sizer→Risk→OrderBook |
| 8 | 봉 마감 정기 SNAPSHOT | `SNAPSHOT(periodic)` | `bar_count % snapshot_every_bars == 0`일 때만 |

**원칙**:
- 의사결정(6)은 항상 체결(5) 다음. 같은 봉 안에서 self-fill 금지.
- 모든 SNAPSHOT은 `_emit_snapshot(ts, reason)` 헬퍼 경유.
- 같은 ts에 1단계 expire + 5단계 fill + 8단계 periodic이 동시에 찍힐 수 있음. 정상 동작.

---

## 5. BacktestConfig + 검증

```python
@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    """모든 필드는 키워드 전용. 일부 필드에 기본값이 있어도 non-default 필드와
    공존 가능 (kw_only=True 덕). Python 3.10+ 필요."""
    run_id: str

    # 데이터
    data_source: DataSourceConfig
    instruments: list[str]
    timeframes_per_symbol: dict[str, list[str]]
    primary_symbol: str
    primary_timeframe: str
    start: datetime
    end: datetime
    gap_policy: Literal["notify", "ffill"] = "notify"

    # 실행
    execution_model: Literal["next_bar_open", "slippage_bps", "atr_slippage"]
    bar_path_model: BarPathModel = BarPathModel.PESSIMISTIC
    slippage_bps: float = 0.0
    fee_override: Optional[FeeOverride] = None

    # 포트폴리오
    initial_equity: Decimal
    sizer_default: SizerConfig
    risk_limits: RiskLimits

    # 전략
    strategy_name: str
    strategy_params: dict

    # 워밍업·재현성
    warmup_bars: int = 0
    random_seed: int = 0

    # 출력
    output_dir: Path
    cache_dir: Path
    log_level: str = "INFO"

    # Run 영속화
    persist_run_data: Literal["copy", "symlink", "none"] = "copy"

    # SNAPSHOT 주기
    snapshot_every_bars: int = 1

    # Run 디렉토리 충돌 정책
    on_run_exists: Literal["fail", "overwrite", "auto_suffix", "archive"] = "fail"

    def __post_init__(self):
        """잘못된 값 즉시 검출. ConfigError raise.
        Engine 시작 전 차단되어 디버깅 시간 단축."""
        # snapshot_every_bars
        if self.snapshot_every_bars < 1:
            raise ConfigError(
                f"snapshot_every_bars must be >= 1, got {self.snapshot_every_bars}"
            )
        # warmup_bars
        if self.warmup_bars < 0:
            raise ConfigError(
                f"warmup_bars must be >= 0, got {self.warmup_bars}"
            )
        # initial_equity
        if self.initial_equity <= 0:
            raise ConfigError(
                f"initial_equity must be positive, got {self.initial_equity}"
            )
        # 시간 범위
        if self.start >= self.end:
            raise ConfigError(
                f"start must be before end, got start={self.start}, end={self.end}"
            )
        # primary_symbol이 instruments에 있는지
        primary_in_instruments = any(
            i.endswith(f"/{self.primary_symbol}") or i == self.primary_symbol
            for i in self.instruments
        )
        if not primary_in_instruments:
            raise ConfigError(
                f"primary_symbol '{self.primary_symbol}' not in instruments {self.instruments}"
            )
        # primary_timeframe이 timeframes_per_symbol에 있는지
        tfs = self.timeframes_per_symbol.get(self.primary_symbol, [])
        if self.primary_timeframe not in tfs:
            raise ConfigError(
                f"primary_timeframe '{self.primary_timeframe}' not in "
                f"timeframes_per_symbol[{self.primary_symbol}]={tfs}"
            )
        # slippage_bps
        if self.slippage_bps < 0:
            raise ConfigError(f"slippage_bps must be >= 0, got {self.slippage_bps}")
        # random_seed
        if self.random_seed < 0:
            raise ConfigError(f"random_seed must be >= 0, got {self.random_seed}")
        # on_run_exists (Literal은 dataclass 단계에서 검증되지 않음)
        if self.on_run_exists not in ("fail", "overwrite", "auto_suffix", "archive"):
            raise ConfigError(
                f"on_run_exists must be one of "
                f"{{'fail','overwrite','auto_suffix','archive'}}, "
                f"got {self.on_run_exists!r}"
            )
        # persist_run_data
        if self.persist_run_data not in ("copy", "symlink", "none"):
            raise ConfigError(
                f"persist_run_data must be one of {{'copy','symlink','none'}}, "
                f"got {self.persist_run_data!r}"
            )
        # bar_path_model (BarPathModel enum 멤버 강제)
        if not isinstance(self.bar_path_model, BarPathModel):
            raise ConfigError(
                f"bar_path_model must be a BarPathModel enum member, "
                f"got {type(self.bar_path_model).__name__}"
            )
        # gap_policy
        if self.gap_policy not in ("notify", "ffill"):
            raise ConfigError(
                f"gap_policy must be one of {{'notify','ffill'}}, "
                f"got {self.gap_policy!r}"
            )
        # execution_model
        if self.execution_model not in ("next_bar_open", "slippage_bps", "atr_slippage"):
            raise ConfigError(
                f"execution_model must be one of "
                f"{{'next_bar_open','slippage_bps','atr_slippage'}}, "
                f"got {self.execution_model!r}"
            )

    # Phase 1.5+
    @classmethod
    def from_yaml(cls, path) -> "BacktestConfig": ...
    def to_yaml(self, path) -> None: ...
```

**__post_init__ 검증 효과**:
- 잘못된 값으로 Engine 시작 자체가 안 됨 (Fatal 카테고리 ConfigError)
- 디버깅 시간 대폭 단축
- YAML 로드 시도 동일하게 검증

### 5.1 검증 규칙 표

테스트 케이스 작성 시 이 표를 직접 참조 (`test_config_validation.py`):

| 필드 | 조건 | 실패 시 |
|------|------|---------|
| `snapshot_every_bars` | `>= 1` | `ConfigError` |
| `warmup_bars` | `>= 0` | `ConfigError` |
| `initial_equity` | `> 0` | `ConfigError` |
| `start` / `end` | `start < end` | `ConfigError` |
| `primary_symbol` | `instruments`에 포함 | `ConfigError` |
| `primary_timeframe` | `timeframes_per_symbol[primary_symbol]`에 포함 | `ConfigError` |
| `slippage_bps` | `>= 0` | `ConfigError` |
| `random_seed` | `>= 0` | `ConfigError` |
| `on_run_exists` | `{fail, overwrite, auto_suffix, archive}` 중 하나 | `ConfigError` |
| `persist_run_data` | `{copy, symlink, none}` 중 하나 | `ConfigError` |
| `bar_path_model` | `BarPathModel` enum 멤버 | `ConfigError` |
| `gap_policy` | `{notify, ffill}` 중 하나 | `ConfigError` |
| `execution_model` | `{next_bar_open, slippage_bps, atr_slippage}` 중 하나 | `ConfigError` |

**enum 검증**: `Literal[...]` 필드는 dataclass 단계에서 타입 체크가 안 되므로 `__post_init__`에서 명시 검증.

---

## 6. Run Directory Structure

### 6.1 핵심 원칙
self-contained 패키지. 외부 cache/instruments 정리해도 차트·리포트 재현 가능.

### 6.2 표준 구조

```
runs/{resolved_run_id}/
├── config.json              # Phase 1: 단방향 영속화 (json.dump)
├── config.yaml              # Phase 1.5+: requested + resolved run_id 모두 포함, 양방향 round-trip
├── events.jsonl             # 1차 원본
├── events.parquet           # 분석용 (Phase 1.5)
├── bars/
│   └── {symbol}_{timeframe}.parquet
├── indicators/
│   └── {symbol}_{timeframe}.parquet
├── results/                 # 캐시 산출물
│   ├── equity_curve.parquet
│   ├── trades.parquet       # Phase 2
│   └── metrics.json         # Phase 2
└── charts/
    ├── run_chart.html       # Phase 1.5
    └── report.html          # Phase 2
```

### 6.3 EventLog vs results 관계

```
events.jsonl = 1차 원본
results/*.parquet = 캐시·편의 산출물
불일치 시 EventLog 기준
```

`backtester rebuild-results runs/{run_id}/` (Phase 2)로 results 재생성.

### 6.4 config 영속화 형식

**Phase 1** (`config.json`):
```json
{
  "run_id": "btc_test",
  "resolved_run_id": "btc_test_2",
  "run_dir": "/Users/jinseop/projects/backtester/runs/btc_test_2",
  "primary_symbol": "BTCUSDT",
  "primary_timeframe": "1h",
  "snapshot_every_bars": 1,
  "on_run_exists": "fail",
  "persist_run_data": "copy"
}
```
- `json.dump(config_dict, ..., default=str, indent=2)` — 표준 라이브러리만 사용
- Decimal/datetime/Path/Enum은 `default=str`로 문자열 직렬화
- **읽기는 미지원**. 단방향 영속화. 양방향 round-trip은 Phase 1.5 PR 9에서 YAML로.

**Phase 1.5+** (`config.yaml`):

```yaml
# 사용자가 명시한 원본
run_id: btc_test

# 실제 사용된 (auto_suffix 등에 의해 다를 수 있음)
resolved_run_id: btc_test_2

# 절대 경로 (디버깅 편의)
run_dir: /Users/jinseop/projects/backtester/runs/btc_test_2

# 나머지는 BacktestConfig 그대로
output_dir: ./runs
primary_symbol: BTCUSDT
primary_timeframe: 1h
snapshot_every_bars: 1
on_run_exists: fail
persist_run_data: copy
...
```

원본 + resolved 둘 다 보존: 사용자 의도와 실제 결과 모두 추적 가능.

### 6.5 파일명 규칙
- bars/indicators: `{symbol}_{timeframe}.parquet`
- 특수문자 sanitize: `BTC/USDT` → `BTC_USDT`

### 6.6 영속화 (`persist_run_data`)

| 정책 | 동작 | 용도 |
|------|------|------|
| `copy` (기본) | 복사 | 일반, 결과 공유, 재현 |
| `symlink` | symlink | 디스크 절약 (cache 정리 주의) |
| `none` | 저장 안 함 | 빠른 파라미터 스윕 |

### 6.7 디렉토리 충돌 (`on_run_exists`)

| 정책 | 동작 | 용도 |
|------|------|------|
| `fail` (기본) | RunDirectoryError | 의도하지 않은 덮어쓰기 방지 |
| `overwrite` | 기존 삭제 후 재생성 | 결과 폐기 후 재실행 |
| `auto_suffix` | run_id_2, _3, ... | 빠른 반복 실험·파라미터 스윕 |
| `archive` | {run_id}_archive_{ts}/로 이동 | 결과 보존 + 재실행 |

**resolved_run_id**:
- `fail`/`overwrite`/`archive` → 원본과 같음
- `auto_suffix` → `run_id_N` 형식

CLI는 `verbose=True` 시 명시적 알림 출력. `--quiet`로 끌 수 있음.

### 6.8 사용 흐름

```python
config = BacktestConfig(
    run_id="btc_test",
    on_run_exists="auto_suffix",
    snapshot_every_bars=1,
    ...
)
# config 검증은 __post_init__에서 이미 실행됨

result = BacktestEngine(config, verbose=True).run()
# stdout 예시:
# [INFO] Run directory already existed, applied 'auto_suffix'
# [INFO] Requested run_id: btc_test
# [INFO] Resolved run_id: btc_test_2
# [INFO] Run directory: /path/runs/btc_test_2

print(result.resolved_run_id)  # "btc_test_2"
print(result.run_dir)           # /path/runs/btc_test_2

# 시각화는 result.run_dir 사용
fig = build_run_chart(result.run_dir)
```

---

## 7. 멀티 타임프레임

**Phase 1**: 단일 timeframe만, StrategyContext 인터페이스만 dict.
**Phase 2 PR 13**: ``MultiTimeframeClock`` 활성. (symbol, tf) 별 bar_start 리스트들로부터 ``bar_start + interval`` 합집합을 시간 순으로 emit. 같은 ts 에 여러 TF 가 닫히면 한 ClockEvent 의 ``bar_closes`` dict 에 모두 담긴다. Strategy.on_bar 는 primary TF 가 닫힌 시점에만 호출 (``primary_tf in event.bar_closes.get(primary_symbol, [])``). 보조 TF 단독 마감 시점에서는 mark-to-market 만 수행. ``BarsView[symbol][tf]`` 는 last_closed 시점까지만 노출 (lookahead 차단). ``IndicatorEngine.precompute`` 가 (symbol, tf) 별로 indicators parquet 영속화.
**Lookahead 검출 테스트 필수** — ``test_multitimeframe.test_engine_multitf_h4_view_no_lookahead``: primary 1h + secondary 4h 시나리오에서 ``now=02:00 / 03:00`` 시점에 4h view height = 0, ``now=04:00`` 에서 4h 첫 봉 마감 후 height = 1, 이후 ``05:00 ~ 07:00`` 까지 그대로 1, ``now=08:00`` 에서 4h 두 번째 봉 마감 후 height = 2.

---

## 8. 데이터 표준
OHLCV 스키마 3.1, Parquet Snappy, 검증 자동.

---

## 9. 성능 최적화
- Stateless 지표: Polars vectorized
- 봉 슬라이싱: timestamp → row_index + `df.slice()`
- 시각화 경계만 pandas

---

## 10. 시각화 (Visualization Layer)

### 10.1 분리 원칙
시각화 함수의 입력은 **run_dir 하나**.

### 10.2 3분류

| 분류 | Phase | 라이브러리 |
|------|-------|------------|
| 디버깅 차트 | Phase 1.5 | Plotly |
| 메트릭 리포트 | Phase 2 | quantstats + 자체 |
| 비교·탐색 | Phase 3~4 | Plotly |

### 10.3 viz/equity.py (Phase 1.5)

```python
def build_equity_series(reader: EventLogReader,
                        initial_equity: Decimal) -> pl.DataFrame:
    """SNAPSHOT 이벤트에서 equity 시리즈 추출.
    같은 ts에 여러 SNAPSHOT이 있으면 마지막 것 사용 (group_by + last).

    출력: timestamp, equity, cash, position_size_{symbol},
          realized_pnl, unrealized_pnl, drawdown, drawdown_pct"""
    snapshots = list(reader.by_type(EventType.SNAPSHOT))
    rows = [{
        "timestamp": snap.ts,
        "equity": float(snap.payload["equity"]),
        "cash": float(snap.payload.get("cash", 0)),
        **{f"position_size_{sym}": float(p["size"])
           for sym, p in snap.payload.get("positions", {}).items()},
        "realized_pnl": float(snap.payload.get("realized_pnl", 0)),
        "unrealized_pnl": float(snap.payload.get("unrealized_pnl", 0)),
    } for snap in snapshots]

    df = pl.DataFrame(rows).sort("timestamp")

    # 같은 ts의 중복 제거 (마지막 값 유지)
    df = df.group_by("timestamp").last().sort("timestamp")

    # Drawdown 계산
    df = df.with_columns([
        pl.col("equity").cum_max().alias("running_max"),
    ])
    df = df.with_columns([
        (pl.col("equity") - pl.col("running_max")).alias("drawdown"),
        ((pl.col("equity") - pl.col("running_max")) / pl.col("running_max"))
            .alias("drawdown_pct"),
    ]).drop("running_max")

    return df
```

### 10.4 viz/run_chart.py (Phase 1.5)

```python
def build_run_chart(run_dir: Path) -> go.Figure:
    """run_dir만으로 완전 재현. 외부 cache 의존 없음."""
    reader = EventLogReader(run_dir / "events.jsonl")
    config = load_run_config(run_dir / "config.yaml")
    primary_sym = config["primary_symbol"]
    primary_tf = config["primary_timeframe"]

    bars = pl.read_parquet(run_dir / "bars" / f"{primary_sym}_{primary_tf}.parquet")
    indicators = pl.read_parquet(run_dir / "indicators" / f"{primary_sym}_{primary_tf}.parquet")
    equity_series = build_equity_series(reader, Decimal(str(config["initial_equity"])))

    # 4단 subplot
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
        row_heights=[0.5, 0.15, 0.2, 0.15], vertical_spacing=0.05)
    # 1단: 캔들 + 지표 + intent + fill
    # 2단: 포지션 (계단)
    # 3단: equity
    # 4단: drawdown
    ...
    return fig


def render_run_chart(run_dir: Path) -> Path:
    fig = build_run_chart(run_dir)
    output = run_dir / "charts" / "run_chart.html"
    fig.write_html(output, include_plotlyjs="cdn")
    return output
```

### 10.5 viz/metrics.py (Phase 2)

```python
def compute_core_metrics(equity_series: pl.DataFrame,
                         periods_per_year: int = 365) -> dict:
    """drawdown 컬럼 이미 포함. 통계만 추출."""
    eq = equity_series["equity"]
    returns = eq.pct_change().drop_nulls()
    return {
        "total_return": float((eq[-1] / eq[0]) - 1),
        "sharpe_ratio": sharpe_ratio(returns, periods_per_year),
        "sortino_ratio": sortino_ratio(returns, periods_per_year),
        "max_drawdown": float(equity_series["drawdown_pct"].min()),
        "max_drawdown_duration": max_drawdown_duration(equity_series),
        "calmar_ratio": calmar_ratio(returns, equity_series, periods_per_year),
        "volatility": float(returns.std() * (periods_per_year ** 0.5)),
    }
```

### 10.6 viz/report.py (Phase 2)

UTC 00:00 기준 일별 리샘플링:
```python
daily_eq = eq_pd["equity"].resample("1D", origin="epoch").last().dropna()
```

`periods_per_year`: crypto 365, 주식 252.

### 10.7 viz/compare.py 등 (Phase 3+)

run_dir 리스트 입력.

---

## 11. 구현 편의성 가이드

### 11.1 POLARS_NOTES.md
프로젝트 루트.

### 11.2 Decimal 가드 / 비교
`to_decimal()` / `is_effectively_flat()`.

### 11.3 BaseStrategy
on_bar만 구현.

### 11.4 EventLog 직렬화
`serialize_event_payload` 강제.

### 11.5 SNAPSHOT 정책
- 모든 SNAPSHOT은 `_emit_snapshot(ts, reason)` 헬퍼로 발행
- 직접 Event 생성 시 `snapshot_reason` 누락 위험 → 헬퍼 사용 강제

### 11.6 BacktestConfig 검증
`__post_init__`에서 자동. 잘못된 값 즉시 ConfigError.

### 11.7 Run Directory 컨벤션
- 시각화는 run_dir 하나만
- resolved_run_id 사용 (config의 run_id 아님)
- 색상: long=green, short=red, neutral=gray

### 11.8 EventLog 1차 원본 원칙
results/는 캐시. `rebuild-results`로 재생성.

### 11.9 작업 세션 보호
PR ~300라인. 매 PR 단위 테스트 그린.

---

## 12. 에러 처리 정책

```python
class BacktestError(Exception): pass
class DataError(BacktestError): pass            # Halt
class InstrumentError(BacktestError): pass      # Fatal
class RiskError(BacktestError): pass            # Recoverable
class ExecutionError(BacktestError): pass       # Recoverable
class RunDirectoryError(BacktestError): pass    # Fatal
class ConfigError(BacktestError): pass          # Fatal
```

ConfigError는 BacktestConfig.__post_init__에서 발생. Engine 시작 전 차단.

---

## 13. 테스트 정책

### 13.1 회귀 테스트 게이트 (Phase 1)
BB-KC 새 엔진 포팅. **Phase 1 long-only buy-entry subset regression** — `legacy_fixture ⊆ v8_actual_buys` (timestamp + direction 정확히 일치). v8 가 legacy 보다 더 많은 buy 를 발행하는 것은 RSI 필터 / SHORT / TP-SL / EWM seed 차이 등 의도된 차이로 허용. 자세한 사유 + fixture trim 정책은 §20 PR 8 완료 조건 + `tests/fixtures/README.md` 참조.

### 13.2 Lookahead 검출
전체/절반 데이터 두 번 실행.

### 13.3 Reproducibility

**Phase 1**: 같은 config + random_seed로 두 번 실행 시 발행되는 이벤트 시퀀스의 `(type, ts, payload)` **의미가 동일**해야 한다. dict iteration 순서·order_id 생성 방식 등에서 발생할 수 있는 비결정적 차이는 허용.

**Phase 2**: events.jsonl **바이트 단위 동일** 검증. canonical JSON 도입(`sort_keys=True` + 고정 separator + UTC ISO8601 + Decimal→str + deterministic order_id sequence) 이후 활성화.

### 13.4 시간 모델 단위 테스트
ClockEvent.timestamp, last_closed_time, BarsView.

### 13.5 SNAPSHOT 정책 테스트

**Phase 1 범위**:
- snapshot_every_bars=N 시 정확히 N봉마다 periodic SNAPSHOT
- FILL 직후 reason="fill" SNAPSHOT 발생 (주기 무관)
- 같은 ts에 여러 SNAPSHOT이 events.jsonl에 **기록되는지**까지만 검증 (write 단계).

**Phase 1.5+ 추가**:
- SETTLE 직후 reason="settlement" SNAPSHOT 발생 (주기 무관) — Funding/Settlement 도입 후
- EXPIRE 직후 reason="expire" SNAPSHOT 발생 (주기 무관) — `tif`/`expires_at` 본격 지원 시점에 활성화. Phase 1은 GTC + `expires_at=None`만 지원하므로 만료 케이스 자체가 발생하지 않음.
- `build_equity_series`가 같은 ts 중복을 group_by + last로 정확히 처리 — `EventLogReader` + `viz/equity.py` 도입 후 (Phase 1.5 PR 10).
- Phase 1에서는 위 세 항목 테스트 금지 (해당 코드 경로가 존재하지 않음).

### 13.6 on_run_exists 정책 테스트
- fail: RunDirectoryError
- overwrite: 기존 삭제, 새로 생성
- auto_suffix: run_id_2, _3 자동 부여, resolved_run_id 정확히 반환
- archive: 기존이 archive_{ts}로 이동

### 13.7 BacktestConfig 검증 테스트
- snapshot_every_bars=0 → ConfigError
- warmup_bars=-1 → ConfigError
- initial_equity<=0 → ConfigError
- start >= end → ConfigError
- primary_symbol/timeframe 불일치 → ConfigError

### 13.8 Run Directory self-contained
외부 cache 지운 상태에서 시각화 생성 가능.

### 13.9 EventLog vs results 일치 (Phase 2)
`rebuild-results` 결과와 원본 results 비교.

### 13.10 BacktestResult 정확성
auto_suffix 시 resolved_run_id ≠ requested_run_id 검증.

---

## 14. CLI

### 14.1 명령

```bash
$ backtester run config.yaml [--quiet]
$ backtester report runs/{run_id}/ [--quiet]
$ backtester rebuild-results runs/{run_id}/   # Phase 2
$ backtester compare runs/run1/ runs/run2/    # Phase 3+
$ backtester replay runs/run1/events.jsonl    # Phase 2+
$ backtester runs list
$ backtester runs prune --keep-recent 50      # Phase 4
$ backtester runs compress {run_id}           # Phase 4
```

### 14.2 출력 정책

**기본 (verbose=True)**: auto_suffix/archive/overwrite 발생 시 명시적 stdout 출력.

```
$ backtester run config.yaml
[INFO] Run directory already existed, applied 'auto_suffix'
[INFO] Requested run_id: btc_test
[INFO] Resolved run_id: btc_test_2
[INFO] Run directory: ./runs/btc_test_2
[INFO] Loaded 8760 bars (BTCUSDT 1h)
[INFO] Warmup: 200 bars
[INFO] Backtest complete. Final equity: 12345.67
```

**`--quiet`**: 위 INFO 출력 모두 숨김. 에러는 그대로 stderr.

### 14.3 구현

```python
@app.command()
def run(
    config_path: Path,
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    config = BacktestConfig.from_yaml(config_path)
    # __post_init__이 검증 자동 수행 (ConfigError 발생 가능)
    engine = BacktestEngine(config, verbose=not quiet)
    result = engine.run()
    if not quiet:
        typer.echo(f"[INFO] Final equity: {result.final_equity}")
        typer.echo(f"[INFO] Run directory: {result.run_dir}")
```

---

## 15. 다중 실행 정책

- unique `run_id` (또는 auto_suffix로 자동 부여)
- 출력: `output_dir/{resolved_run_id}/`
- 캐시 공유 (read-only), 쓰기 file lock
- 파라미터 스윕: `concurrent.futures.ProcessPoolExecutor`, `on_run_exists="auto_suffix"` 권장

---

## 16. 디렉토리 구조

```
backtester/
├── core/
│   ├── engine.py
│   ├── config.py
│   ├── context.py
│   ├── orders.py
│   ├── orderbook.py
│   ├── clock.py
│   ├── snapshot.py
│   ├── types.py
│   ├── result.py              # BacktestResult
│   └── errors.py              # ConfigError, RunDirectoryError 포함
├── data/
│   ├── base.py
│   ├── parquet_source.py
│   ├── csv_source.py          # Phase 1.5
│   ├── bybit_source.py        # Phase 2
│   └── cache.py
├── instruments/
│   ├── base.py
│   ├── registry.py
│   └── specs/
├── indicators/
│   ├── engine.py
│   ├── base.py
│   ├── stateless/
│   └── stateful/              # Phase 2+
├── execution/
│   ├── base.py
│   ├── next_bar.py
│   ├── slippage_bps.py        # Phase 2
│   ├── slippage_atr.py        # Phase 2
│   └── funding.py             # Phase 1.5
├── strategies/
│   ├── base.py
│   ├── bbkc_squeeze.py
│   └── frama_channel.py       # Phase 2
├── portfolio/
│   ├── sizer.py
│   ├── risk.py
│   ├── ledger.py
│   └── position.py
├── events/
│   ├── log.py
│   ├── reader.py              # Phase 1.5
│   ├── types.py               # IntentCreatedPayload, SnapshotReason
│   ├── serialize.py
│   └── replay.py              # Phase 2
├── viz/
│   ├── equity.py              # Phase 1.5
│   ├── run_chart.py           # Phase 1.5
│   ├── metrics.py             # Phase 2
│   ├── report.py              # Phase 2
│   ├── compare.py             # Phase 3
│   ├── walkforward_viz.py     # Phase 3
│   └── sweep_heatmap.py       # Phase 4
├── analysis/                  # Phase 2+
│   └── walkforward.py
├── cli/                       # Phase 1.5
│   └── main.py
├── examples/
├── POLARS_NOTES.md
└── tests/
    ├── test_clock.py
    ├── test_lookahead.py
    ├── test_bars_view.py
    ├── test_engine.py
    ├── test_config_validation.py    # __post_init__
    ├── test_run_directory.py        # on_run_exists, resolved_run_id
    ├── test_snapshot_policy.py      # reason, 주기, implicit
    ├── test_orderbook.py
    ├── test_indicators.py
    ├── test_sizer.py
    ├── test_risk.py
    ├── test_ledger.py
    ├── test_position.py
    ├── test_execution.py
    ├── test_event_serialize.py
    ├── test_event_reader.py         # Phase 1.5
    ├── test_equity.py               # Phase 1.5
    ├── test_run_chart.py            # Phase 1.5
    ├── test_rebuild_results.py      # Phase 2
    ├── test_metrics.py              # Phase 2
    └── fixtures/
```

---

## 17. 구현 로드맵

### 17.0 구현 전략

**프로젝트 위치 (확정)**:

별도의 독립 Python 프로젝트로 분리한다.
```
C:\Users\IBKS\Desktop\python\backtester\         # 독립 프로젝트 루트
├── src/backtester/                              # 패키지 본체
│   └── core/                                    # PR 1 산출물
├── tests/
├── docs/backtester_spec_v8.md                   # 본 명세 (기준 문서)
└── pyproject.toml                               # Python 3.10+, hatchling 빌드
```

**패키지명**: `backtester` (단일). 모든 import는 `from backtester.core import ...` 형식.

**기존 코드베이스(JS_Repository) 처리 원칙**:
- `JS_Repository/Crypto/Bybit_Trading/src/backtester/`는 **그대로 동결**. 절대 수정하지 않는다.
- `JS_Repository/Crypto/Bybit_Trading/_legacy/engine/backtest.py`도 동결.
- 신규 v8 구현은 위에 명시한 **독립 프로젝트 안에서만** 한다.
- 기존 전략 코드(BB-KC 등)가 기존 backtester를 import하고 있다면, PR 8 회귀 게이트 시점에 v8로 마이그레이션. 그 전까지 기존 JS_Repository 코드는 손대지 않는다.

**왜 독립 프로젝트인가**:
- 기존 `JS_Repository/Crypto/Bybit_Trading/src/backtester/`는 호출자(전략·optimizer·walk_forward)가 묶여 있어 동일 repo 내 공존이 위험하다.
- v8은 시간 모델(ClockEvent vs OHLCV ts)과 SNAPSHOT 정책이 기존과 호환 불가 — 별도 프로젝트가 가장 안전.
- 독립 프로젝트로 분리하면 의존성·도구체인(pyproject.toml, ruff, mypy, pytest) 모두 깨끗하게 시작 가능.

### 17.1 Phase 1 Scope Guard

문서 후반의 설계는 Phase 1.5/2/3 내용이 섞여 있다. 구현자가 욕심내지 않도록 **Phase 1 범위를 잠근다**.

> **Phase 1의 정의**: 완성형 백테스터가 아니다. **시간 모델 / EventLog / Run Directory / SNAPSHOT 정책이 맞게 작동하는 최소 market-only 엔진**이다. 그 외 영역은 의도적으로 미구현 상태로 남긴다.

**Phase 1 Minimum Support Matrix**:

| 영역 | Phase 1 지원 | 이후 Phase |
|------|--------------|------------|
| Order type | market only | limit / stop / stop_limit: Phase 2 |
| Execution | next_bar_open | slippage / bar path: Phase 2 |
| Position | long / flat | short / leverage: Phase 2 |
| SizeSpec | TargetUnits, TargetNotional, ClosePosition | TargetWeight / FullPosition / ScaleIn: Phase 2 |
| Risk | blacklist_symbols, max_orders_per_symbol | max_total_exposure / max_leverage / max_drawdown_halt: Phase 2 |
| Settlement | 미지원 | Funding / settlement: Phase 1.5 |
| Data source | Parquet only | CSV: Phase 1.5, Bybit: Phase 2 |
| Config load | Python 객체 직접 생성 | YAML load: Phase 1.5 |
| Config save | `config.json` (감사용 단방향) | `config.yaml` round-trip: Phase 1.5 |
| Visualization | 미지원 | run_chart: Phase 1.5, metrics/report: Phase 2 |
| EventLog | JSONL writer만 | Parquet export / Reader: Phase 1.5, replay: Phase 2 |
| Multi-timeframe | 단일 TF | 멀티 TF: Phase 2 |
| Indicators | Stateless (BB, KC, ATR) | Stateful (FRAMA): Phase 2 |
| BarPathModel | enum 정의만 (사용 X) | 4종 동작 + 슬리피지: Phase 2 |

**미래 기능을 위한 "예약 필드" 정책**:

문서에 정의된 타입 중 일부는 미래 Phase에서만 의미를 가진다. **Phase 1 코드에는 이 필드들의 처리 로직을 포함하지 않는다**.

- `Instrument.funding_model`, `Instrument.margin_model`, `Instrument.trading_hours`: Phase 1에서 클래스 자체를 정의하지 않는다. Instrument 정의에서도 해당 필드를 생략하거나 `None` 기본값으로만 둔다 (검증·사용 로직 X).
- `ClockEvent.settlements`: Phase 1에서 항상 빈 리스트(`[]`). `_process_event` 안의 settlement 루프(§4.3 4단계)는 빈 리스트면 즉시 통과. SETTLE 이벤트와 `reason="settlement"` SNAPSHOT은 Phase 1.5 PR 9에서 도입.
- `MarketSnapshot.funding_rate`, `open_interest`, `mark_price`: Phase 1에서 항상 `None`. 사용 로직 X.
- `FeeModel.maker`: Phase 1은 taker만 사용 (`compute_fee`는 `taker` 필드만 참조).
- `OrderIntent.tif`, `expires_at`: Phase 1에서는 GTC + `expires_at=None`만 정상 처리. 그 외 값은 `OrderBook.add` 단계에서 `ConfigError`/`ExecutionError` 또는 단순 무시 (PR 7에서 결정).

**Phase 1에서 구현하는 것** (PR 1~8):
- 단일 timeframe `SimpleClock`
- `ParquetDataSource`만
- `EventLog` JSONL writer (+ `schema_version=1`)
- `BacktestConfig` + `__post_init__` 검증
- **`config.json` 영속화 (단방향, 표준 라이브러리 `json` 사용)** — YAML은 Phase 1.5 PR 9
- Run directory 정책 (fail / overwrite / auto_suffix / archive)
- SNAPSHOT reason 정책 (`_emit_snapshot` 헬퍼 강제)
- `BacktestResult` (requested + resolved + run_dir)
- BB-KC Squeeze 전략 포팅
- Lookahead 방지 + 회귀 + 시간 모델 + Config 검증 단위 테스트

**Phase 1에서 구현하지 않는 것** (절대 손대지 말 것):
- CLI (Phase 1.5)
- YAML config 직렬화 (Phase 1.5)
- CSV / Bybit DataSource (Phase 1.5 / Phase 2)
- FundingModel (Phase 1.5)
- 멀티 timeframe (Phase 2)
- Plotly 시각화 / `viz/run_chart.py` / `viz/equity.py` (Phase 1.5)
- `EventLogReader` + Parquet export (Phase 1.5)
- `rebuild-results` CLI (Phase 2)
- 슬리피지 모델 (Phase 2)
- Stateful 지표 / FRAMA (Phase 2)
- Walk-forward (Phase 2)
- `viz/metrics.py` / `viz/report.py` (Phase 2)
- GlobalClock / 해외주식 / 페어 (Phase 3)

**Phase 1 종료 조건**:
- PR 1~8 모두 머지
- BB-KC Squeeze 회귀 게이트 통과 — **Phase 1 long-only buy-entry subset regression** (`legacy_fixture ⊆ v8_actual_buys`, timestamp + direction 정확히 일치). 의도된 차이(RSI 필터, SHORT, TP-SL, EWM seed, legacy 데몬 폴링 지연)는 §20 PR 8 + `tests/fixtures/README.md` 참조.
- Lookahead 테스트 그린 (전체/절반 데이터 두 번 실행)
- `Crypto/Bybit_Trading/src/backtester/` (구버전) 변경 0건

### Phase 1 — 돌아가는 최소 엔진 (1-2주)

- [ ] `core/types.py`, `core/orders.py`, `core/snapshot.py`, `core/result.py`
- [ ] `core/errors.py` (ConfigError, RunDirectoryError 포함)
- [ ] `core/clock.py` (SimpleClock, ClockHelper)
- [ ] `data/base.py`, `data/parquet_source.py`
- [ ] `instruments/base.py` (FeeModel flat taker), `registry.py`
- [ ] `strategies/base.py`
- [ ] `core/context.py` (BarsView O(1))
- [ ] `core/orderbook.py`
- [ ] `execution/base.py`, `execution/next_bar.py`
- [ ] `portfolio/position.py`, `sizer.py`, `risk.py`, `ledger.py`
- [ ] `indicators/engine.py` (stateless + persist_to)
- [ ] `indicators/stateless/bb.py`, `kc.py`, `atr.py`
- [ ] `events/types.py` (IntentCreatedPayload, SnapshotReason), `serialize.py`, `log.py`
- [ ] `core/config.py` (snapshot_every_bars, on_run_exists, persist_run_data, **__post_init__ 검증**)
- [ ] `core/engine.py`:
  - [ ] `_resolve_run_dir` (4가지 정책, resolved_run_id 반환)
  - [ ] `_notify_resolution` (verbose 알림)
  - [ ] `_emit_snapshot(ts, reason)` 헬퍼
  - [ ] bars/indicators persist
  - [ ] BacktestResult 반환
- [ ] `strategies/bbkc_squeeze.py` 포팅
- [ ] **시간 모델 + Run Directory + SNAPSHOT 정책 + Config 검증 단위 테스트**
- [ ] **회귀 + Lookahead 테스트**

### Phase 1.5 — 부가 인프라 + 디버깅 시각화 (1-2주)

- [ ] `execution/funding.py`
- [ ] `core/config.py` YAML 직렬화 (resolved_run_id 포함 형식)
- [ ] `cli/main.py` (`run`, `report`, `--quiet` 지원)
- [ ] EventLog Parquet export
- [ ] `data/csv_source.py`
- [ ] `events/reader.py` (EventLogReader, by_snapshot_reason)
- [ ] `viz/equity.py` (snapshot 중복 group_by 처리)
- [ ] `viz/run_chart.py`
- [ ] 테스트: cache 지운 상태 차트 생성 (self-contained 검증)

### Phase 2 — 확장 + 메트릭 + rebuild (1-2주)

- [ ] 멀티 timeframe
- [ ] `data/bybit_source.py` + 캐싱
- [ ] `execution/slippage_bps.py`, `slippage_atr.py`
- [ ] FeeModel maker/taker
- [ ] Stateful 지표 (FRAMA), `strategies/frama_channel.py`
- [ ] `analysis/walkforward.py`
- [ ] `events/replay.py`
- [ ] `viz/metrics.py`, `viz/report.py` (UTC 00:00)
- [ ] **CLI `rebuild-results` + EventLog↔results 정합성 회귀**
- [ ] IndicatorEngine disk cache

### Phase 3 — 자산군 확장 + 비교 시각화 (1-2주)

- [ ] GlobalClock (session_based)
- [ ] 해외주식 데이터
- [ ] FRAMA를 SPX/NDX
- [ ] 멀티 자산 (BTC-ETH pairs)
- [ ] Liquidation, margin call
- [ ] FeeModel tiered
- [ ] `viz/compare.py`, `walkforward_viz.py`

### Phase 4 — 도구 + 탐색 시각화 (지속)

- [ ] 파라미터 스윕 (스윕 시 on_run_exists=auto_suffix 권장)
- [ ] `viz/sweep_heatmap.py`
- [ ] CLI `runs prune`, `runs compress`
- [ ] IndicatorEngine LRU eviction
- [ ] 이벤트 로그 resume (선택)

---

## 18. 결정된 사항 정리

| 항목 | 결정 |
|------|------|
| 사용 범위 | A안 (개인용) |
| **프로젝트 위치** | **독립 프로젝트: `C:\Users\IBKS\Desktop\python\backtester\`** |
| **패키지명** | **`backtester` (단일). import 경로 `from backtester.core import ...`** |
| 언어 | Python 100% |
| DataFrame | Polars (시각화 경계만 pandas) |
| 시각화 라이브러리 | Plotly + quantstats |
| Run Directory | self-contained 패키지 |
| persist_run_data 기본값 | copy |
| on_run_exists 기본값 | fail |
| on_run_exists 옵션 | fail / overwrite / auto_suffix / archive |
| **resolved_run_id 추적** | **BacktestResult + 영속화된 config 파일(Phase 1: `config.json`, Phase 1.5+: `config.yaml`)에 모두 기록** |
| **CLI 알림 정책** | **명시적 stdout, --quiet로 끌 수 있음** |
| bars/indicators 파일명 | `{symbol}_{timeframe}.parquet` |
| EventLog vs results | EventLog = 원본, results = 캐시 |
| SNAPSHOT 주기 | snapshot_every_bars=1 + FILL/SETTLE/EXPIRE 직후 implicit |
| **snapshot_reason 필드** | **모든 SNAPSHOT에 fill/settlement/expire/periodic 명시** |
| **같은 ts 중복 SNAPSHOT** | **허용. build_equity_series가 group_by + last로 처리** |
| **BacktestConfig 검증** | **__post_init__에서 자동, ConfigError raise** |
| 데이터 갭 처리 | 알림 + 콜백 |
| 시작 자산군 | Crypto perp |
| 멀티 자산 인터페이스 | dict 기반 |
| 시간 동기화 | SimpleClock → GlobalClock |
| OHLCV timestamp | 봉 시작 시각 |
| ClockEvent.timestamp | 봉 마감 시각 |
| IntentCreatedPayload | decision_ts + bar_timestamp + bar_close_price |
| Phase 1 멀티 TF | 단일 TF |
| 봉 슬라이싱 | timestamp → row_index + df.slice() |
| 내부 timezone | UTC |
| 캐시 포맷 | Parquet (Snappy) |
| 사이즈 단위 | size_unit + size_spec |
| 지표 계산 | IndicatorEngine 사전계산 + 자동 persist |
| 주문 생명주기 | OrderBook |
| Bar Path Model | PESSIMISTIC 기본 |
| Portfolio 분리 | Sizer + RiskManager + Ledger |
| Position | size, avg_price, realized/unrealized PnL |
| Decimal 비교 | abs < tick_size |
| FeeModel Phase 1 | flat taker, ExecutionModel 책임 |
| Funding | Phase 1.5 |
| EventLog Phase 1 | JSONL append + context manager |
| **EventLog `schema_version`** | **모든 라인에 포함, Phase 1 = 1, additive 변경은 동일 버전 유지** |
| EventLogReader | type별 인덱스 (Phase 1.5) |
| Event 직렬화 | serialize_event_payload 강제 |
| 시각화 입력 | run_dir만 |
| viz/equity.py | equity/position/drawdown 단일 DataFrame |
| viz/metrics.py | 통계만 |
| quantstats 리샘플링 | UTC 00:00 (origin="epoch") |
| periods_per_year | crypto 365, 주식 252 |
| 디버깅 차트 | Phase 1.5 |
| 메트릭 리포트 | Phase 2 |
| rebuild-results 명령 | Phase 2 |
| Decimal vs Float | 회계 Decimal, 지표 Float, to_decimal 가드 |
| BaseStrategy | 빈 구현, on_bar만 강제 |
| 설정 통합 | dataclass (Phase 1.5에서 YAML) |
| Random seed | 필수 |
| Warmup | 자동 추정 |

---

## 19. 결정 대기 사항 (Decision Backlog)

미정 사항을 단순 목록이 아닌 **결정 시점 / 현재 기본 방침 / 결정 기준 / 영향 범위**로 관리한다. 각 항목은 결정 시점이 도래하면 PR과 함께 확정하고 본 표에서 제거 → §18 결정 사항 표로 이동.

| 항목 | 결정 시점 | 현재 기본 방침 | 결정 기준 | 영향 범위 |
|------|-----------|----------------|-----------|-----------|
| **로깅 라이브러리** | Phase 1 PR 1 전 | 표준 `logging` 우선 | 테스트 캡처 용이성, 의존성 최소화 | core, cli |
| **자체 메트릭 vs quantstats 비율** | Phase 2 시작 전 | 핵심 메트릭 자체 구현, quantstats 보조 | 재현성, 커스터마이즈 자유도 | viz/metrics, viz/report |
| **Walk-forward 기본 분할 방식** | Phase 2 시작 전 | rolling / expanding 둘 다 후보 유지 | 전략 검증 목적, 데이터 길이 | analysis/walkforward |
| **파라미터 스윕 병렬화** | Phase 4 시작 전 | `concurrent.futures.ProcessPoolExecutor` 우선 | Windows 호환성, 단순성, joblib 의존성 | sweep, cli |
| **이벤트 로그 resume** | Phase 4 전 | 미지원 (fail/overwrite/auto_suffix/archive만) | 복잡도 대비 실 필요성 | events, engine |
| **차트 색상 팔레트** | Phase 1.5 PR 11 전 | long=green / short=red / neutral=gray (§11.7) | 블로그 임베드 가독성, 색맹 접근성 | viz/run_chart, viz/report |
| **Survivorship bias 처리** | Phase 3 (주식 확장 시) | 미지원 | 데이터 소스 가용성, 백테스트 신뢰도 | data, instruments |
| **라이브 어댑터 분리** | Phase 5+ | 본 명세 범위 외 | 페이퍼/실거래 요구사항 명확화 시점 | 별도 프로젝트 |
| **EventLog `schema_version` 마이그레이션 정책** | 첫 스키마 변경 시 | additive는 동일 버전, 의미 변경은 +1 + Reader가 거부/마이그레이션 | 첫 호환성 깨짐 발생 시점 | events/log, events/reader |

**운영 규칙**:
- 결정 시점이 도래했는데 미해결인 항목이 있으면 해당 Phase 시작을 **차단**한다.
- 각 결정은 PR 설명에 "Decision Backlog 항목 X 확정: <결정 내용> + <근거>" 형식으로 기록.
- 새 미정 사항이 발견되면 본 표에 추가 (자유 메모 금지).

---

## 20. Claude Code PR 분할

### Phase 1 (PR 1~8)

각 PR은 산출물(Deliverables) + 완료 조건(Acceptance Criteria) 형식. 완료 조건이 그린이어야 다음 PR 시작.

**PR 분할 옵션 (필요 시 사용)**:

PR 5/6/7이 한 번에 너무 커지면 아래처럼 분할 가능. 구현자(또는 AI)가 첫 시도에서 ~300라인을 넘으면 분할을 우선 시도한다.

| 원본 PR | 분할 옵션 | 비고 |
|---------|-----------|------|
| PR 5 — Portfolio MVP | **PR 5a**: Position + Ledger MVP <br> **PR 5b**: Sizer + Risk MVP | 5a 머지 후 5b 시작 |
| PR 6 — Execution + EventLog | **PR 6a**: Event types + serialize + EventLog (writer 포함) <br> **PR 6b**: Execution next_bar_open | 6a 머지 후 6b 시작 |
| PR 7 — Engine + Config + Run Directory + SNAPSHOT | **PR 7a**: Config + `__post_init__` 검증 + Run Directory 정책 + `config.json` <br> **PR 7b**: Engine 메인 루프 + `_emit_snapshot` + SNAPSHOT 정책 + buy-and-hold 통합 테스트 | 7a 머지 후 7b 시작 |

PR 1/2/3/4/8은 분할하지 않는다 (이미 작거나 회귀 게이트 단위).

**PR 1 — 타입 + 인터페이스**

산출물:
- `core/types.py`, `core/orders.py`, `core/snapshot.py`, `core/result.py`, `core/errors.py`
- ConfigError, RunDirectoryError 포함

완료 조건:
- `from backtester.core import ...` import 그린 (패키지 위치: §17.0)
- `BacktestError` 계층 (DataError / RiskError / ExecutionError / RunDirectoryError / ConfigError) 정의 완료
- `BacktestResult`에 `requested_run_id`, `resolved_run_id`, `run_dir` 필드 존재
- mypy / ruff 에러 0

**PR 2 — 데이터 + Instrument**

산출물:
- `data/base.py`, `data/parquet_source.py`
- `instruments/base.py`, `registry.py`

완료 조건:
- `ParquetDataSource.fetch()`가 `(pl.DataFrame, GapReport)` 반환
- 스키마 검증: timestamp UTC tz-aware, OHLCV 컬럼 존재
- `GapReport.is_significant()` 동작
- BTCUSDT 1h 샘플로 fetch 성공 테스트

**PR 3 — 시간 모델 + IndicatorEngine**

산출물:
- `core/clock.py` (SimpleClock, ClockHelper)
- `indicators/engine.py`, `indicators/stateless/atr.py`, `indicators/stateless/bb.py`

완료 조건:
- ClockEvent.timestamp = 봉 마감 시각 단위 테스트 그린
- `last_closed_time(now)`: now == 마감 시각이면 그 봉이 last_closed
- `IndicatorEngine.precompute(persist_to=...)` 시 `{symbol}_{tf}.parquet` 생성
- `required_warmup_bars()` 정확성 (BB 20 → 19, ATR 14 → 14 등)

**PR 4 — Strategy + Context**

산출물:
- `strategies/base.py`, `core/context.py`, `core/orderbook.py`

Phase 1 OrderBook 범위:
- **필수 구현 + 동작 테스트**: `add`, `cancel`, `fill`, `get_active`
- **최소 동작만 테스트** (실 사용 케이스 X): `modify`, `expire_pending`
  - `modify`: limit/stop 주문이 Phase 2부터 도입되므로 Phase 1에서는 호출 케이스 없음. 본문은 `raise NotImplementedError("Phase 2")`. 테스트는 raise 여부만 검증.
  - `expire_pending`: Phase 1은 GTC + `expires_at=None`만 지원하므로 항상 빈 리스트 반환. 본문은 `return []` 한 줄. 테스트는 빈 리스트 반환 여부만 검증. 만료 케이스(실제 expire) 테스트는 Phase 1.5+.

완료 조건:
- `BarsView[symbol][tf]`이 `last_closed` 이전만 노출 (lookahead 차단 단위 테스트)
- BarsView O(1) 슬라이싱 (timestamp_index 활용, `df.filter` 호출 0)
- OrderBook.add / cancel / fill / get_active 단위 테스트 그린
- `modify` 호출 시 NotImplementedError raise 단위 테스트
- `expire_pending(ts)` 호출 시 빈 리스트 반환 단위 테스트 (Phase 1.5+에서 만료 케이스 추가)

**PR 5 — Portfolio MVP** (Phase 1 한정)

산출물:
- `portfolio/position.py`, `sizer.py`, `risk.py`, `ledger.py`

Phase 1 지원 범위 (Minimum Support Matrix 따름):
- `Position`: long / flat만 (short 미지원, leverage 미지원)
- `Sizer.resolve()`: `TargetUnits`, `TargetNotional`, `ClosePosition`만 처리. 나머지(`TargetWeight`, `FullPosition`, `ScaleIn`)는 `NotImplementedError("Phase 2")`.
- `RiskManager.check()`: `blacklist_symbols`, `max_orders_per_symbol`만 검사.
- `Ledger`: `cash`, `position`, `realized_pnl`, `unrealized_pnl`, `equity` 추적 + `on_fill`, `on_market`, `equity_curve`만.

Phase 1 미지원 (구현하지 말 것):
- short / leverage
- `TargetWeight` / `FullPosition` / `ScaleIn`
- `max_total_exposure` / `max_leverage` / `max_drawdown_halt`
- `Ledger.on_settle` (settlement 경로 자체가 Phase 1.5)
- `Ledger.on_expired`는 noop으로 두되 호출 인터페이스만 정의 (PR 7에서 expire 통합 시 cash 영향 없음 확인)

완료 조건:
- `Position.is_effectively_flat(tick_size)` 동작
- Sizer가 지원하는 3종 SizeSpec 처리. 미지원 SizeSpec 입력 시 `NotImplementedError`.
- RiskManager가 `blacklist_symbols`(거부 → REJECTED) + `max_orders_per_symbol`(초과 → REJECTED)만 차단.
- Ledger.equity / on_fill / on_market / equity_curve 단위 테스트 (on_settle 테스트 X)
- short 진입 시도(현재 flat에서 sell 주문 또는 long 보유분 초과 sell) 시 **Sizer 단계에서** `NotImplementedError("short not supported in Phase 1")` raise 단위 테스트
- Decimal/float 혼용 0 (`to_decimal()` 가드 강제)

**PR 6 — Execution + EventLog (MVP)** (Phase 1 한정)

산출물:
- `execution/base.py`, `execution/next_bar.py`
- `events/types.py` (SnapshotReason, IntentCreatedPayload)
- `events/serialize.py`, `events/log.py` (EVENT_SCHEMA_VERSION = 1)

Phase 1 지원 범위:
- Order type: `market`만 처리. `limit` / `stop` / `stop_limit` 입력 시 `NotImplementedError("Phase 2")`.
- ExecutionModel: `next_bar_open` 단 하나. 다음 봉 open 가격에 즉시 체결.
- `BarPathModel`: enum 정의는 두되 **Phase 1에서는 사용하지 않는다**. 4종 동작 테스트는 Phase 2.
- Slippage: 0 (config의 `slippage_bps`는 검증만 수행, 실제 적용은 Phase 2).

Phase 1 미지원:
- `limit` / `stop` / `stop_limit` 체결 로직
- BarPathModel 4종 분기 테스트
- Slippage 모델 (`slippage_bps.py`, `slippage_atr.py`)

완료 조건:
- market BUY: 다음 봉 open에 정확히 체결 (단위 테스트)
- market SELL on long position: 다음 봉 open에 정확히 청산 (단위 테스트)
- limit/stop/stop_limit 입력 시 `NotImplementedError` raise
- `serialize_event_payload`: Decimal / datetime / Enum / dataclass / nested 모두 round-trip
- EventLog가 모든 라인에 `schema_version` 필드 포함
- EventLog는 context manager 외부 사용 시 RuntimeError

**PR 7 — Engine + Config 검증 + Run Directory + SNAPSHOT 정책**

산출물:
- `core/config.py`: `snapshot_every_bars`, `on_run_exists`, `persist_run_data` 필드 + `__post_init__` 검증
- `core/engine.py`: `_resolve_run_dir`, `_notify_resolution`, `_emit_snapshot(ts, reason)` 헬퍼, bars/indicators persist, BacktestResult 반환
- 더미 매수후홀드 통합 테스트

완료 조건:
- **Config 검증 표 (§5.1) 모든 항목 ConfigError 단위 테스트 그린**
- **on_run_exists 4가지 정책 단위 테스트**:
  - `fail` → RunDirectoryError
  - `overwrite` → 기존 디렉토리 삭제 후 새로 생성
  - `auto_suffix` → `run_id_2` / `_3` 자동 부여, `resolved_run_id`가 정확히 반환됨
  - `archive` → `{run_id}_archive_{ts}/`로 이동, 새로 생성
- **`config.json` 영속화 정책**:
  - **목적: 감사용(audit) 단방향 덤프**. BacktestConfig 복원에 사용하지 않는다.
  - 테스트는 `requested_run_id` / `resolved_run_id` / `run_dir` / 핵심 config 필드(primary_symbol, primary_timeframe, snapshot_every_bars, on_run_exists 등) 존재만 확인. 양방향 round-trip 검증 X.
  - YAML round-trip(`from_yaml` / `to_yaml`)은 Phase 1.5 PR 9에서 구현.
- **resolved_run_id가 BacktestResult + 영속화된 config 파일(Phase 1: `config.json`) + 디렉토리명 모두 일치**
- **모든 SNAPSHOT 이벤트에 `snapshot_reason` 필드 존재** (헬퍼 미사용 시 fail)
- FILL 직후 `reason="fill"` SNAPSHOT 발생 (snapshot_every_bars 무관)
- 같은 ts에 fill + periodic SNAPSHOT 중복 허용 (dedup 안 함)
- ConfigError는 Engine 인스턴스 생성 전에 raise (Engine.__init__ 진입 전)
- 더미 buy-and-hold: 첫 봉 매수, 마지막 봉 close 평가, equity_curve 단조 증가/감소 가능

**PR 8 — BB-KC + 회귀 테스트 (Phase 1 완료)**

산출물:
- `strategies/bbkc_squeeze.py`, `indicators/stateless/kc.py`

BB-KC 포팅 경계 (절대 위반 금지):
- **기존 전략 파일은 수정하지 않는다**. 기존 `Crypto/Bybit_Trading/src/backtester/`는 동결 상태 유지.
- **기존 `JS_Repository/Crypto/Bybit_Trading/src/backtester/`를 import하지 않는다**. 독립 프로젝트의 `backtester` 패키지로만 동작해야 한다.
- 필요한 지표·시그널 계산은 `src/backtester/indicators/stateless/` 안에 **복사 또는 재작성**한다 (재사용 X).
- 기존 모의매매 결과와는 **timestamp + direction**만 회귀 비교한다 (PnL 비교 X — 체결 모델·수수료 가정이 다를 수 있음).

완료 조건:
- **Phase 1 long-only buy-entry subset regression**: 모의매매 결과의 long entry 시그널 중 v8와 매칭되는 fixture 부분집합에 대해 timestamp + direction 정확히 일치 (`legacy_fixture ⊆ v8_actual_buys`). v8 가 legacy 보다 더 많은 buy 를 발행하는 것은 의도된 차이로 허용 — 사유는 다음과 같다:
  - **RSI 필터** (legacy `rsi_filter=70.0` LONG 차단) — Phase 1.5+ 까지 v8 미지원
  - **SHORT 진입** — Phase 1 long-only (Sizer 차단)
  - **TP / SL / be_trail / time_stop** 청산 — Phase 2 (limit/stop)
  - **EWM(Wilder ATR) 시드 처리 차이** + DB 미세 갱신 → squeeze boundary 봉에서 일부 fixture entry 가 v8 와 매칭 안 될 수 있음. 이런 entry 는 회귀 fixture 에서 **사전 trim** 하고 사유를 `tests/fixtures/README.md` 에 기록한다.
  - **Legacy 데몬 폴링 지연** (예: HH:00 release 가 HH+1:15 에 로깅) — fixture 작성자는 squeeze 상태표 기준으로 decision_ts 를 정정해 등록하거나 매칭 실패 시 trim 한다.
- 향후 v8 가 RSI 필터 (Phase 1.5+) / SHORT (Phase 2) / 정밀 EWM seed 일치를 도입하면 fixture trim 을 풀고 entries 를 추가하는 식으로 게이트를 강화한다.
- **Lookahead 테스트 그린**: 전체 데이터 / 절반 데이터 두 번 실행 시 동일 시점까지의 시그널 timestamp/방향 100% 일치 (의미 동일 비교, 바이트 비교 X)
- **Reproducibility (의미 동일 버전)**: 같은 config + random_seed로 두 번 실행 시 발행되는 이벤트 시퀀스의 `(type, ts, payload)` 의미가 동일해야 한다.
  - events.jsonl 바이트 단위 동일은 **Phase 2에서 canonical JSON(sort_keys + 고정 separator + UTC ISO8601 + Decimal→str + deterministic order_id) 도입 후** 검증.
  - Phase 1에서는 dict iteration 순서/order_id 생성 등에서 비결정적 차이가 있을 수 있으므로 의미 비교만 강제.
- **기존 `Crypto/Bybit_Trading/src/backtester/` 변경 0건** (`git diff Crypto/Bybit_Trading/src/backtester/` 빈 출력)
- **독립 프로젝트 어디에서도 `JS_Repository`/`Bybit_Trading` 경로 import 0건** (grep 검증)
- Phase 1 종료 선언 — Phase 1.5 시작 가능

### Phase 1.5 (PR 9~12)

**PR 9 — Funding + YAML + CLI 기본**
- `execution/funding.py` — `FundingModel` (interval_hours, rate_source) + `CashFlow` + `FundingProcessor.process(symbol, ts, instrument, position, market)`. Phase 1.5 PR 9 는 ``rate_source="constant"`` 만 지원, ``"from_data_source"`` 는 후속 PR 에서 wiring. Engine wiring (SETTLE 이벤트 + `Ledger.on_settle` 활성 + `ClockEvent.settlements` 주입) 도 후속 PR.
- `core/config.py` YAML 양방향 round-trip — `BacktestConfig.to_yaml(path)` + `from_yaml(path)`. `strategy_name: str` + `strategy_params: dict` 필드 추가. Engine 영속화 시 `resolved_run_id` / `run_dir` audit 필드를 함께 쓰지만 read 시 무시. ``__post_init__`` 검증이 from_yaml 시점에 자동 실행 → 잘못된 값은 즉시 ConfigError.
- `cli/main.py` — argparse 기반. ``backtester run config.yaml [--quiet]``. `STRATEGY_REGISTRY` (``strategies/registry.py``) 가 `name → BaseStrategy` 매핑 + `build_strategy(name, params)`. ``--quiet`` 는 INFO 알림 (Engine + CLI summary) 모두 차단.
- EventLog Parquet export — `events.jsonl` → `events.parquet` (analytics cache, spec §6.2). 스키마: `schema_version Int64 / ts Datetime UTC / type String / payload String(JSON)`. payload 는 type 별 구조가 달라 평면 컬럼 대신 JSON 문자열로 보존, 분석 시 `pl.col("payload").str.json_decode(...)`.
- `data/csv_source.py` — `CSVDataSource` (`ParquetDataSource` 와 동일 인터페이스). `{base_dir}/{symbol}_{timeframe}.csv`, ISO8601 UTC tz-aware timestamp, OHLCV Float64 캐스트, schema/sort/dup 검증.

**PR 10 — EventLogReader + Equity 시리즈**
- `events/reader.py` — `EventLogReader(events_path, *, max_schema_version)`. JSONL 라인을 순서 보존하며 적재, type 별 인덱스 + `counts_by_type` / `by_type` / `by_snapshot_reason` / `to_dataframe(t)` 메서드. `schema_version > max` 면 `EventLogSchemaError`. malformed JSON 라인은 lineno 와 함께 `ValueError`. 빈 라인 skip.
- `viz/equity.py` — `build_equity_series(reader, initial_equity)` → polars DataFrame. SNAPSHOT 이벤트 → `timestamp / equity / cash / realized_pnl / unrealized_pnl / position_size_{symbol}* / drawdown / drawdown_pct`. 같은 ts 의 중복 SNAPSHOT (FILL 직후 + periodic) 은 `group_by("timestamp", maintain_order=True).last()` 로 dedup. 빈 events → 스키마만 있는 빈 DataFrame.

**PR 11 — Run Chart**
- `viz/run_chart.py` — `build_run_chart(run_dir)` → 4단 plotly Figure (캔들+지표 / 포지션 / equity / drawdown). `render_run_chart(run_dir)` → `run_dir/charts/run_chart.html` (CDN plotly include). `run_dir` 만 입력, 외부 cache 의존 없음 (cache-clean 회귀 테스트 포함).
- Engine 영속화에 `config.yaml` 추가 — `BacktestConfig.to_dict()` + `resolved_run_id` / `run_dir` audit 필드. `config.json` (Phase 1 audit) 도 그대로 유지. `_load_run_config` 헬퍼는 yaml 우선, json fallback.
- CLI `backtester report runs/{run_id}/ [--quiet]` — `render_run_chart` 호출 + HTML 경로 출력. argparse `report` 서브커맨드 + `cmd_report`. 종료 코드 0/2.
- 의존성: `plotly>=5.18` (runtime), mypy override `plotly.*: ignore_missing_imports` (plotly 미배포 stub).

**PR 12 — 회귀 시각 검증 (Phase 1.5 종료 게이트)**
- BB-KC 결과 시각 회귀: ``tests/fixtures/ETHUSDT_1h.parquet`` + ``bbkc_signals.csv`` 로 BacktestEngine 실행 → events.jsonl → ``build_run_chart`` → fixture buy entry timestamp 가 chart intent 마커 (subset gate, PR 8 정책 일관) + EventLogReader.``by_type(INTENT_CREATED)`` 양쪽에 모두 존재하는지 회귀.
- 외부 cache 지운 상태 동작 검증: ``DataSource.base_dir`` 디렉토리 삭제 후에도 ``run_dir`` 만으로 ``build_run_chart`` / ``render_run_chart`` 가 동작 (spec §10.1). bars/indicators parquet 영속물 활용으로 BB/KC indicator scatter trace 가 그대로 그려지는지 검증.

**Phase 1.5 종료 조건** (PR 9~12 머지 완료):
- PR 9~12 모두 머지
- BB-KC 회귀: subset gate (Phase 1) + 시각 회귀 (PR 12) 그린
- ``cache-clean`` 자급: 외부 데이터 cache 삭제 후 chart 재현 가능
- ``BacktestConfig.from_yaml(result.config_path)`` round-trip 통과 (Phase 1.5+ canonical config = config.yaml)
- 기존 ``Crypto/Bybit_Trading/src/backtester/`` 변경 0건
- pytest / ruff / mypy clean
- Phase 2 시작 가능 (멀티 timeframe / BybitDataSource / slippage / FRAMA / walkforward / metrics-report 등)

### Phase 2 (PR 13~)

**PR 13 — 멀티 timeframe**
**PR 14 — BybitDataSource + 캐싱**
- ``data/bybit_source.py`` — ``BybitDataSource(cache_dir, *, category, fetcher)``. 로컬 parquet cache 가 단일 진실 소스 (``{cache_dir}/{symbol}_{timeframe}.parquet``). ``fetch(symbol, tf, start, end)`` 흐름:
    - cache hit (요청 범위 ⊆ cache) → fetcher 미호출, slice 반환
    - 헤드 갭 (``start < cache_min``) → ``fetcher(start, cache_min)``
    - 테일 갭 (``cache_max < end``) → ``fetcher(cache_max, end)``
    - 머지 + dedup + sort + 영속화
- ``KlineFetcher`` Protocol — ``(symbol, interval_code, start, end, category) → list[BybitKlineRow]``. 외부 의존성 없이 stdlib ``urllib`` 으로 ``GET /v5/market/kline`` 단발 호출 (default fetcher). 테스트는 mock fetcher 주입.
- 지원 timeframe: ``1m / 3m / 5m / 15m / 30m / 1h / 2h / 4h / 6h / 12h / 1d``. 그 외 입력 → ``DataError``.
- ``DataSourceConfig.type`` += ``"bybit"`` + ``__post_init__`` 검증. Engine ``_build_data_source`` bybit 분기.
- 회귀 테스트: cache miss / hit / 헤드 갭 / 테일 갭 / 중복 dedup / strictly increasing / Bybit descending → ascending 정렬 / Engine 통합 (pre-populated cache로 네트워크 미사용 smoke).
**PR 15 — 슬리피지 모델**
**PR 16 — FRAMA + stateful**
**PR 17 — Walk-forward**
**PR 18 — viz/metrics.py**
**PR 19 — viz/report.py (UTC 00:00)**
**PR 20 — CLI rebuild-results + EventLog↔results 정합성**

각 PR ~300라인. 매번 회귀 + lookahead 그린.

---

## 21. 주의 사항 (Claude Code에 명시)

### 시간·데이터
- OHLCV timestamp = 봉 시작, ClockEvent.timestamp = 봉 마감. 절대 혼용 금지
- 모든 시간은 UTC, timezone-aware
- 봉 슬라이싱은 timestamp → row_index + `df.slice()`. **`df.filter()` 매 봉 호출 금지**
- POLARS_NOTES.md 항상 참조. pandas 패턴 금지

### Run Directory
- self-contained 패키지. 시각화는 run_dir만 입력
- bars/indicators 파일명: `{symbol}_{timeframe}.parquet`
- persist_run_data 기본값 copy
- **`on_run_exists` 기본값 fail. auto_suffix 시 resolved_run_id가 requested와 다름**
- **사용자에게 노출하는 디렉토리 경로는 항상 resolved_run_id 기반**
- 영속화된 config 파일(Phase 1: `config.json`, Phase 1.5+: `config.yaml`)에 **requested run_id + resolved_run_id + run_dir 모두 저장**

### SNAPSHOT
- **모든 SNAPSHOT은 `_emit_snapshot(ts, reason)` 헬퍼로 발행**. 직접 Event 생성 금지
- **`snapshot_reason`은 fill / settlement / expire / periodic 중 하나로 항상 명시**
- FILL, SETTLE, ORDER_EXPIRED 직후에는 snapshot_every_bars 무관하게 즉시 SNAPSHOT
- **같은 ts에 여러 SNAPSHOT 허용**. dedup은 `build_equity_series`에서 group_by + last로 처리
- snapshot_every_bars 기본값 1, primary_timeframe 기준

### Config
- **BacktestConfig 생성 시 `__post_init__`에서 자동 검증**. 잘못된 값 즉시 ConfigError
- snapshot_every_bars >= 1, warmup_bars >= 0, initial_equity > 0, start < end 등

### CLI
- **`auto_suffix`/`archive`/`overwrite` 발생 시 stdout에 명시적 알림**
- **`--quiet` 옵션으로 알림 끌 수 있음. 기본은 알림 출력**
- 출력 형식:
  ```
  [INFO] Run directory already existed, applied '{policy}'
  [INFO] Requested run_id: {requested}
  [INFO] Resolved run_id: {resolved}
  [INFO] Run directory: {run_dir}
  ```

### EventLog
- 1차 원본. results/는 캐시. 불일치 시 EventLog 기준
- context manager 사용 (직접 open/close 금지)
- payload는 `serialize_event_payload` 강제
- **모든 라인에 `schema_version` 필드 포함**. Phase 1 = `EVENT_SCHEMA_VERSION = 1`
- 필드 추가만 있는 변경은 동일 버전 유지. 의미 변경/제거/타입 변경은 버전 증가 + Reader 마이그레이션

### IntentCreatedPayload
- decision_ts + bar_timestamp + bar_close_price 필수

### 회계
- Decimal과 float 혼용 금지. Ledger 진입은 `to_decimal()`
- Decimal 직접 비교 금지. `is_effectively_flat()` 또는 `abs(a-b) < tick_size`

### 주문·체결
- OrderIntent.size_spec 항상 명시
- PESSIMISTIC bar path 기본값
- SizeSpec 변환은 Sizer만, Fee 계산은 ExecutionModel만
- Clock 우회 금지

### 시각화
- run_dir 하나만 입력
- viz/equity.py가 drawdown 포함, metrics.py는 통계만
- quantstats 리샘플링 `resample("1D", origin="epoch")` (UTC 00:00 고정)
- periods_per_year 명시 (crypto 365, 주식 252)
- 색상: long=green, short=red, neutral=gray

### Phase 범위
- Phase 1에 FundingModel, CLI, YAML, Parquet EventLog, 멀티 TF, 시각화 넣지 말 것
- Phase 1.5에 디버깅 차트(`viz/run_chart.py`) 반드시 포함
- resume 기능은 Phase 4+. 현재는 fail/overwrite/auto_suffix/archive만

### 코드 스타일
- 신규 전략은 BaseStrategy 상속, on_bar만 구현
- PR ~300라인 이내, 단위 테스트 그린 상태
- Warmup 미만에서 strategy.on_bar 미호출
