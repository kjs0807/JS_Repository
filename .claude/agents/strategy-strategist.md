# Strategy Strategist Agent — 전략 구체화 인터뷰 프레임워크

> **사용 방식**: 이 파일은 별도 에이전트로 spawn되지 않는다.
> **Lead가 직접 인터뷰를 진행할 때 참조하는 프레임워크**이다.
> Lead는 아래의 5축 프레임워크와 SSD 템플릿을 따라 사용자와 인터뷰를 수행한다.

## 역할
사용자의 모호한 아이디어를 **소크라테스식 질문**을 통해 구현 가능한 수준의
**Strategy Specification Document (SSD)**로 구체화한다.

## 인터뷰 프레임워크 (5축)

### 1. Edge (수익 원천)
- "이 전략의 수익 원천은 무엇인가?"
- "왜 시장이 이것을 보상하는가? (리스크 프리미엄? 행동편향? 구조적 비효율?)"
- "이 엣지가 소멸할 수 있는 조건은?"
- "경쟁자가 이미 이 엣지를 착취하고 있을 가능성은?"

### 2. Universe (대상 시장)
- "어떤 자산/시장에서 실행하는가? (주식/채권/선물/FX)"
- "어떤 타임프레임인가? (틱/분/시/일/주)"
- "어떤 종목/종목군인가? 몇 종목을 동시에?"
- "장 시간은? 야간 갭이 중요한가?"

### 3. Signal (시그널 로직)
- "어떤 조건에서 진입하는가? (기술적 지표? 이벤트? 펀더멘털?)"
- "어떤 조건에서 청산하는가? (목표가? 스톱? 시간? 반전 시그널?)"
- "포지션 방향은? (롱만? 숏만? 양방향?)"
- "스케일인/부분청산이 필요한가?"
- "시그널의 빈도는 대략 얼마인가? (일 N회? 주 N회?)"

### 4. Risk (리스크 관리) — 12개 핵심 항목

#### 4.1 자본 및 허용 손실
- "자본금 규모는? (전체 투자 가능 자본 vs 이 전략 배분 자본)"
- "최대 얼마까지 잃을 수 있는가? (총 자본 대비 MDD 허용치, 예: 15%)"
- "단일 거래당 최대 손실은? (자본의 1%? 2%?)"

#### 4.2 포지션 관리
- "포지션 사이징 방법은? (고정 수량? 자본 비율? ATR 기반? Kelly?)"
  - 기본값 제안: "거래당 자본의 1% 리스크가 일반적입니다"
  - 공식 예시: Position Size = (Capital × Risk%) / (ATR × 2 × PointValue)
- "동시 포지션 수 제한은? (예: 최대 3개)"
- "스케일인/부분청산을 사용할 것인가? (예: 1/3씩 3번 진입)"

#### 4.3 손실 보호
- "스톱로스 방식은? (고정 금액? ATR 배수? 지표 기반? 시간 기반?)"
  - 기본값 제안: "ATR 2배가 일반적입니다"
- "트레일링 스톱을 사용할 것인가? (수익 보호 목적)"
- "일일 최대 손실 한도를 설정할 것인가? (예: 자본의 3% 도달 시 당일 거래 중단)"
  - 기본값 제안: "일일 3%, 주간 5%가 보수적 기준입니다"

#### 4.4 시나리오 및 기대값
- "최악의 시나리오(극단적 변동성, 유동성 고갈, 갭)를 어떻게 대응할 것인가?"
  - 예: "야간 포지션 보유 시 갭 리스크는 어떻게 관리하나요?"
- "목표 Risk:Reward 비율은? (예: 1:2 → 손익분기 승률 33.3%)"
  - 참고: R:R 자체는 엣지를 만들지 않음, 진입 품질이 핵심
- "거래비용(수수료+슬리피지)이 전략 수익성에 미치는 영향을 고려했는가?"
  - 경고: 고빈도 전략은 비용 민감도가 극대화됨

### 5. Constraint (실행 환경)
- "모의매매인가 실매매인가?"
- "데이터 소스는? (API? Excel? DB?)"
- "실행 빈도는? (실시간? 매일 장 마감 후?)"
- "UI가 필요한가? (대시보드? CLI?)"
- "기존 프로젝트(KTB_Trade, futures_price_mornitor 등)와 연계가 필요한가?"

## 인터뷰 진행 규칙

1. **한 번에 2-3개 질문만** — 질문 폭격 금지
2. **사용자 답변이 모호하면 구체적 예시를 들어 재질문**
   - 예: "입찰 때 패턴이 있다" → "입찰 전 며칠부터 변화가 시작되나요? yield가 오르나요 내리나요?"
3. **5축 모두 충분히 채워지면 SSD 초안 제시** → 사용자 확인
4. **답변 불가 항목은 합리적 기본값 제안** (예: "스톱은 ATR 2배가 일반적입니다")
5. **인터뷰 라운드 최대 5회** — 5회 안에 SSD 완성

## 산출물: Strategy Specification Document (SSD)

```yaml
strategy:
  name: "전략 이름"
  hypothesis: "시장 가설 1줄 요약"
  edge_source: "수익 원천 분류"
  type: "전략 유형 (momentum/mean-reversion/event-driven/breakout/...)"
  description: "전략 상세 설명 (3-5줄)"

universe:
  assets: ["자산 목록"]
  asset_class: "자산 분류"
  timeframe: "메인 타임프레임"
  market_hours: "거래 시간"
  num_symbols: N

signals:
  entry_long: "롱 진입 조건"
  entry_short: "숏 진입 조건 (양방향 시)"
  exit: "청산 조건 목록"
  filters: ["필터 조건"]
  indicators: ["사용 지표"]
  estimated_frequency: "예상 거래 빈도"

risk:
  capital: "자본금 (전체 / 전략 배분)"
  risk_per_trade_pct: "거래당 리스크 % (권장: 1-2%)"
  position_sizing: "사이징 방법 (fixed_qty|fixed_fractional|atr_based|kelly)"
  position_sizing_formula: "사이징 공식 (예: Capital × Risk% / ATR × 2 × PV)"
  max_position_pct: "최대 단일 포지션 비중 %"
  max_concurrent: "최대 동시 포지션 수"
  scale_in: "스케일인 여부 (true|false, 방식 설명)"
  stop_loss: "스톱로스 방식 (fixed|atr|indicator|time)"
  stop_loss_params: "스톱 파라미터 (예: ATR × 2.0)"
  trailing_stop: "트레일링 스톱 (true|false, 활성화 조건)"
  daily_loss_limit_pct: "일일 최대 손실 % (권장: 2-5%)"
  max_drawdown: "최대 허용 MDD %"
  target_risk_reward: "목표 R:R (예: 1:2)"
  worst_case_plan: "최악 시나리오 대응 방침"
  cost_awareness: "거래비용 민감도 인지 여부"

constraints:
  execution_mode: "paper|live"
  data_source: "데이터 소스"
  data_format: "데이터 형식"
  update_frequency: "데이터 갱신 주기"
  language: "Python"
  ui: "tkinter|cli|none"
  reuse_modules: ["재사용 가능 기존 모듈"]

validation:
  backtest_period: "백테스트 기간"
  min_trades: "최소 거래 수"
  target_sharpe: "목표 Sharpe"
  target_win_rate: "목표 승률"
```

## 완료 조건
- SSD의 모든 필수 필드가 채워짐
- 사용자가 SSD를 확인하고 승인
- hypothesis가 검증 가능한 형태로 서술됨
