---
name: strategy-builder
description: |
  전략 아이디어를 딥 인터뷰로 구체화한 뒤 7단계 에이전트 파이프라인으로 백테스트 실행 가능한 전략 코드를 자동 생성하는 스킬.
  사용자가 "전략 만들어줘", "새 전략", "전략 구현해줘", "strategy build", "볼린저 밴드 전략 만들어줘" 등
  새로운 금융 전략의 구현을 요청할 때 반드시 이 스킬을 사용하라.
  전략 아이디어가 모호하거나 구체적이든 상관없이, 새 전략 코드를 생성하는 모든 요청에 트리거된다.
---

# Strategy Builder Skill — 전략 아이디어 → 동작하는 코드 자동 생성

## 개요

사용자의 모호한 전략 아이디어를 **딥 인터뷰**로 구체화한 뒤,
7단계 에이전트 파이프라인을 통해 **백테스트 실행 가능한 전략 코드**를 자동 생성하는 스킬.

---

## Agent Teams 구성

| 역할 | Agent 이름 | 모델 | 담당 |
|------|-----------|------|------|
| Lead | (사용자 세션) | Opus | 전체 조율, 인터뷰 진행, 최종 검증 |
| Agent 1 | strategist | Sonnet | 딥 인터뷰 → SSD (Strategy Specification Document) |
| Agent 2 | data-architect | Sonnet | 데이터 요구사항 분석 → Data Plan |
| Agent 3 | literature-scout | Sonnet | 선행 연구 조사 → Research Brief |
| Agent 4 | risk-analyst | Sonnet | 리스크 관리 설계 → Risk Plan |
| Agent 5 | system-designer | Sonnet | 코드 구조 설계 → Technical Blueprint |
| Agent 6 | implementor | Sonnet | 전략 코드 구현 |
| Agent 7 | validator | Sonnet | 코드 검증 → Validation Report (점수 산출) |
| Agent 8 | output-verifier | Sonnet | 출력물 내용 검증 → Verification Report |

---

## 실행 흐름

### Phase 1: 전략 구체화 (인터뷰)

#### Step 0: Lead가 사용자와 인터뷰

Lead가 직접 `.claude/agents/strategy-strategist.md`의 5축 프레임워크를 사용하여
사용자와 소크라테스식 인터뷰를 진행한다.

```
인터뷰 규칙:
- 한 번에 2-3개 질문만 (질문 폭격 금지)
- 모호한 답변은 예시를 들어 재질문
- 최대 5라운드
- 답변 불가 항목은 합리적 기본값 제안
- 5축 (Edge, Universe, Signal, Risk, Constraint) 모두 충분히 채워지면 SSD 초안 제시
- Risk 축은 12개 항목으로 확장됨 — 반드시 포지션 사이징, 손실 한도, 기대값까지 다룰 것

산출물: SSD (YAML) — 사용자 승인 필요
```

#### 인터뷰 상태 저장
인터뷰 각 라운드 완료 시 진행 상태를 자동 저장:
```
{project_path}/logs/strategy_build/ssd_draft.yaml
```
중단 후 재개 시 이 파일에서 진행 상태를 복원한다.

### Phase 2: 조사 & 리스크 설계 (유형별 조건부 병렬)

#### Step 1: 에이전트 병렬 실행

SSD 확정 후, 전략 유형에 따라 병렬 에이전트를 구성한다.
`.claude/agents/strategy-*.md` 에이전트 프롬프트를 읽은 뒤, **하나의 응답에서 Agent 도구를 동시 호출**한다.

**Trading: 3-way 병렬** (data-architect + literature-scout + risk-analyst)
**Report: 2-way 병렬** (data-architect + literature-scout) — risk-analyst 스킵

```
# --- 하나의 응답에서 아래 Agent 호출을 모두 동시에 보낸다 ---

# Agent: data-architect (항상 실행)
Agent(name="data-architect", model="sonnet",
      description="데이터 요구사항 분석",
      prompt="""
      [.claude/agents/strategy-data-architect.md 프롬프트 전문]

      === SSD ===
      {ssd_yaml}

      프로젝트 경로: {workspace_root}
      결과를 {project_path}/logs/strategy_build/data_plan.yaml에 저장하라.
      """)

# Agent: literature-scout (항상 실행)
Agent(name="literature-scout", model="sonnet",
      description="선행 연구 조사",
      prompt="""
      [.claude/agents/strategy-literature-scout.md 프롬프트 전문]

      === SSD ===
      {ssd_yaml}
      결과를 {project_path}/logs/strategy_build/research_brief.yaml에 저장하라.
      """)

# Agent: risk-analyst (Trading 전략만)
# Report 전략이면 이 Agent는 스킵
Agent(name="risk-analyst", model="sonnet",
      description="리스크 관리 설계",
      prompt="""
      [.claude/agents/strategy-risk-analyst.md 프롬프트 전문]

      mode: design
      === SSD ===
      {ssd_yaml}
      결과를 {project_path}/logs/strategy_build/risk_plan.yaml에 저장하라.
      """)
```

#### Step 2: 에이전트 완료 → SSD 보완 (Lead)

- Data Architect 결과에서 unavailable 데이터가 있으면 SSD 수정
- Literature Scout 권장사항 중 중요 항목을 SSD에 반영
- **(Trading만)** Risk Analyst의 Risk Plan을 SSD risk 섹션에 반영
- **Data Plan이 RED verdict이면**: 사용자에게 보고하고 진행 여부 확인 (필수 중단점)
- 보완된 SSD를 사용자에게 간략히 보고 (1-2줄)

#### 체크포인트 저장
```
Phase 2 완료 시 저장:
{project_path}/logs/strategy_build/checkpoint.json
{
  "phase": 2,
  "status": "completed",
  "strategy_type": "trading|report",
  "artifacts": ["ssd.yaml", "data_plan.yaml", "research_brief.yaml"],
  "artifacts_trading_only": ["risk_plan.yaml"],
  "data_plan_verdict": "GREEN|YELLOW|RED",
  "timestamp": "ISO8601"
}
```

### Phase 3: 설계

#### Step 3: Agent: system-designer 실행

Data Plan, Research Brief, (Trading이면 Risk Plan), (보완된) SSD를 종합하여 코드 구조를 설계한다.

```
Agent(name="system-designer", model="sonnet",
      description="코드 구조 설계",
      prompt="""
      [.claude/agents/strategy-system-designer.md 프롬프트 전문]

      전략 유형: {strategy_type}
      # Report 전략이면 "Risk Plan 없음 — risk_manager.py 불필요" 명시

      === SSD ===
      {ssd_yaml}

      === Data Plan ===
      {data_plan.yaml 내용}

      === Research Brief ===
      {research_brief.yaml 내용}

      === Risk Plan ===  (Trading 전략만, Report이면 이 섹션 생략)
      {risk_plan.yaml 내용}

      프로젝트 경로: {project_path}
      결과를 {project_path}/logs/strategy_build/blueprint.yaml에 저장하라.
      """)
```

### Phase 4: 구현

#### Step 4: Agent: implementor 실행

Technical Blueprint (+ Trading이면 Risk Plan)에 따라 전략 코드를 작성한다.

```
Agent(name="implementor", model="sonnet",
      description="전략 코드 구현",
      prompt="""
      [.claude/agents/strategy-implementor.md 프롬프트 전문]

      전략 유형: {strategy_type}
      # Trading이면: "risk_manager.py 필수 생성"
      # Report이면: "risk_manager.py 불필요, 대신 출력물 검증 함수 포함"

      === SSD ===
      {ssd_yaml}

      === Technical Blueprint ===
      {blueprint.yaml 내용}

      === Data Plan ===
      {data_plan.yaml 내용}

      === Risk Plan ===  (Trading 전략만, Report이면 이 섹션 생략)
      {risk_plan.yaml 내용}

      프로젝트 경로: {project_path}
      """)
```

### Phase 5: 검증 (2단계)

#### Step 5-a: Agent: validator 실행

구현된 코드를 실행하고 기본 검증하여 점수를 산출한다.

```
Agent(name="validator", model="sonnet",
      description="코드 실행 검증",
      prompt="""
      [.claude/agents/strategy-validator.md 프롬프트 전문]

      === SSD ===
      {ssd_yaml}

      === Technical Blueprint ===
      {blueprint.yaml 내용}

      프로젝트 경로: {project_path}
      결과를 {project_path}/logs/strategy_build/validation_report.yaml에 저장하라.
      """)
```

#### Step 5-b: Agent: output-verifier 실행

validator PASS 후, 출력물의 **내용적 정합성**을 검증한다.

```
Agent(name="output-verifier", model="sonnet",
      description="출력물 내용 검증",
      prompt="""
      [.claude/agents/strategy-output-verifier.md 프롬프트 전문]

      strategy_type: {strategy_type}
      === SSD ===
      {ssd_yaml}

      프로젝트 경로: {project_path}
      expected_output_description: {ssd.strategy.hypothesis + ssd.universe 요약}
      결과를 {project_path}/logs/strategy_build/verification_report.json에 저장하라.
      """)
```

### Phase 6: 결과 처리

#### Step 6: Lead 최종 판정

```
종합 판정 (validator + output-verifier):
- 둘 다 PASS (validator ≥ 8.0 AND verifier ≥ 8.0): 성공 → 완료 리포트 출력
- 하나라도 CONDITIONAL: must_fix 항목을 Lead가 직접 수정 후 해당 검증만 재실행 (최대 2회)
- 하나라도 FAIL: 사용자에게 주요 실패 원인 보고 → SSD 수정 또는 중단 결정
```

---

## 프로젝트 생성 규칙

### 디렉토리 구조

새 전략 프로젝트는 워크스페이스 루트에 생성:

```
{workspace_root}/{strategy_name}/
├── __init__.py
├── config.py           ← 종목/파라미터/경로/리스크 설정
├── data_loader.py      ← 데이터 수집/로딩/전처리
├── risk_manager.py     ← 리스크 관리 (Trading만 필수, Report에서는 생략)
├── strategy.py         ← 전략 로직 (FSM/Signal/Event)
├── backtest.py         ← 백테스트 엔진
├── optimizer.py        ← 파라미터 최적화 (선택)
├── app.py              ← 메인 진입점
├── dashboard.py        ← tkinter UI (선택)
├── DB/                 ← 데이터 저장소
└── logs/               ← 결과물
```

### 프로젝트 명명

- SSD의 strategy.name에서 snake_case로 변환
- 예: "KTB Auction Momentum" → `ktb_auction_momentum`

### 필수 구현 사항 (교훈 반영)

1. **거래비용**: 백테스트에 `slippage_bp` 파라미터 필수
2. **Lookahead bias 방지**: 시그널 봉의 close가 아닌 다음 봉 open으로 체결
3. **MIN_TRADES**: 최소 10건 이상 (통계적 유의성 위해 30건 권장)
4. **Sharpe 계산**: `initial_capital > 0` 설정 또는 절대 PnL 기반
5. **SQLite**: `with sqlite3.connect() as conn:` 패턴
6. **NaN 방어**: 지표 계산 결과에 대한 NaN/None 체크
7. **risk_manager.py**: **Trading 전략에서만 필수** (포지션 사이징, 일일 손실 한도, 기대값, 비용 민감도). Report 전략에서는 생략.

---

## 산출물 구조

```
{project_path}/
├── (전략 코드 파일들)
├── logs/
│   └── strategy_build/
│       ├── ssd.yaml                    ← 확정된 SSD
│       ├── ssd_draft.yaml             ← 인터뷰 중단 시 임시 저장
│       ├── data_plan.yaml              ← Data Architect 산출
│       ├── research_brief.yaml         ← Literature Scout 산출
│       ├── risk_plan.yaml              ← (trading만) Risk Analyst 산출
│       ├── blueprint.yaml              ← System Designer 산출
│       ├── validation_report.yaml      ← Validator 산출
│       ├── verification_report.json    ← Output Verifier 산출
│       └── checkpoint.json             ← Phase별 진행 상태
```

---

## 에이전트 프롬프트 참조

각 에이전트의 상세 프롬프트:

- `.claude/agents/strategy-strategist.md` — 딥 인터뷰, SSD 생성
- `.claude/agents/strategy-data-architect.md` — 데이터 요구사항
- `.claude/agents/strategy-literature-scout.md` — 선행 연구
- `.claude/agents/strategy-risk-analyst.md` — 리스크 관리 설계 (신규)
- `.claude/agents/strategy-system-designer.md` — 코드 구조 설계
- `.claude/agents/strategy-implementor.md` — 코드 구현
- `.claude/agents/strategy-validator.md` — 실행 검증 및 점수 산출
- `.claude/agents/strategy-output-verifier.md` — 출력물 내용 검증 (신규)

---

## 자동 진행 규칙

> **공통 규칙**: `.claude/references/pipeline-rules.md` 참조 (잠금/복구/비용 절감 규칙)

### 멈출 수 있는 유일한 지점 (strategy-builder 전용)

1. **Phase 1 인터뷰 중**: 사용자 답변 대기 (필수)
2. **SSD 승인**: 사용자 확인 대기 (필수)
3. **Data Plan RED**: 사용자 판단 대기 (조건부)
4. **Phase 6 FAIL 판정 시**: 재시작/중단 결정 (조건부)

**이 4가지 외에는 절대 멈추지 말고 다음 Phase를 즉시 실행하라.**

### 재개 시 추가 확인

- `checkpoint.json` → 현재 완료된 Phase 파악
- `ssd_draft.yaml` → 인터뷰 중단이면 이어서 진행
- 이전 Phase의 산출물 파일 존재 확인 (logs/strategy_build/ 내)

---

## 특수 케이스

### 기존 프로젝트 기반 전략

SSD의 `constraints.reuse_modules`에 기존 모듈이 지정된 경우:
- System Designer가 해당 모듈을 분석하여 재사용 방법 결정
- `import` / `adapt` / `pattern` / `new` 분류

### 데이터 수집 불가 (Data Plan RED)

Data Architect가 `RED` 판정한 경우:
- **파이프라인 중단** → 사용자에게 상황 보고
- 대안 제시 (대체 데이터, 범위 축소 등)
- 사용자 결정: 진행(YELLOW로 변경) / SSD 수정 / 중단

### 인터뷰 중단

사용자가 인터뷰 중 "일단 이 정도로 해줘" 등을 말하면:
- 미완성 항목을 합리적 기본값으로 채움
- `ssd_draft.yaml`에 현재 상태 저장
- SSD 초안 제시 후 확인 요청
