---
name: strategy-developer
description: |
  기존 금융 전략 코드를 자동 분석하여 전략 유형(trading/report)을 식별하고,
  6개 전문 에이전트(reviewer, tester, researcher, risk-analyst, synthesizer, output-verifier)로
  코드 품질, 전략 로직, 통계 검증, 리스크 관리, 출력물 품질, 개선 방안을 종합 리포트로 산출하는 스킬.
  사용자가 "전략 분석해줘", "전략 개선점", "strategy review", "백테스트 검증", "코드 리뷰해줘",
  "이 전략 괜찮아?", "전략 점수" 등 기존 전략의 분석/평가/개선을 요청할 때 반드시 이 스킬을 사용하라.
---

# Strategy Developer Skill — 금융 전략 분석 및 개선점 도출

## 개요

금융 전략 코드를 자동 분석하여 **전략 유형을 식별**하고, 유형에 맞는 전문 에이전트 팀을 구성하여
코드 품질, 전략 로직, 통계 검증, 리스크 관리, 출력물 품질, 개선 방안을 종합 리포트로 산출하는 스킬.

---

## 전략 유형 자동 분류

Lead가 대상 디렉토리를 스캔하여 아래 기준으로 유형을 판별한다.

| 유형 | 판별 기준 | 예시 프로젝트 |
|------|----------|-------------|
| **trading** | backtest/optimizer/trade_manager/order 관련 코드 존재, PnL 계산 로직 | futures_price_mornitor, KTB_Trade, KIS_Trading, Signal_Trading |
| **report** | 분석/리포트/시각화 중심, 매매 실행 없음 | Auction_Strategy, econ_indicators, news_scrap |

유형에 따라 에이전트 프롬프트와 분석 관점이 달라진다.

---

## Agent Teams 구성 (유형별 조건부 라우팅)

### Trading 전략 — 6개 에이전트

| 역할 | Agent 이름 | 모델 | 담당 |
|------|-----------|------|------|
| Lead | (사용자 세션) | Opus | 전략 유형 판별, 전체 조율, 최종 종합 리포트 |
| Agent 1 | strategy-reviewer | Sonnet | 심층 퀀트 코드 리뷰 |
| Agent 2 | strategy-tester | Sonnet | 통계적 검증/과적합 분석 |
| Agent 3 | strategy-researcher | Sonnet | 구현 후 개선 기법 조사 |
| Agent 4 | strategy-risk-analyst | Sonnet | 리스크 관리 감사 |
| Agent 5 | strategy-synthesizer | Sonnet | 개선점 종합 및 구체적 제안 |
| Agent 6 | strategy-output-verifier | Sonnet | 출력물 내용 검증 |

### Report 전략 — 5개 에이전트 (risk-analyst 제외)

| 역할 | Agent 이름 | 모델 | 담당 |
|------|-----------|------|------|
| Lead | (사용자 세션) | Opus | 전략 유형 판별, 전체 조율, 최종 종합 리포트 |
| Agent 1 | strategy-reviewer | Sonnet | 분류 정확성, 데이터 처리, 통계 엄밀성 |
| Agent 2 | strategy-tester | Sonnet | 데이터 신뢰성, 통계 분석 타당성 |
| Agent 3 | strategy-researcher | Sonnet | 분석 프레임워크/시각화 개선 기법 |
| Agent 4 | strategy-synthesizer | Sonnet | 개선점 종합 및 구체적 제안 |
| Agent 5 | strategy-output-verifier | Sonnet | 콘텐츠 관련성, 빈 섹션, 분류 정확도 |

> **라우팅 규칙**: `strategy-risk-analyst`는 **trading 전략에서만** 실행한다.
> Report 전략에서는 리스크 관리 관점이 불필요하므로 스킵하고, 그 가중치를 output-verifier와 tester에 재분배한다.

---

## 실행 흐름

### Step 0: Lead 사전 분석 (전략 유형 판별)

```
1. 대상 프로젝트 경로의 모든 .py 파일 스캔
2. 아래 키워드로 유형 판별:
   - trading 키워드: backtest, optimize, trade, order, pnl, position, stop_loss,
     trailing, entry, exit, signal, indicator, state_machine
   - report 키워드: report, analysis, chart, plot, visualization, lens, synthesis,
     statistics, summary, classifier, scraper, monitor
3. strategy_type = "trading" | "report" 결정
4. 주요 파일 목록, 전략 파라미터, 데이터 흐름 파악
5. 분석 결과를 각 에이전트에 전달할 context 생성
```

### Step 1: 분석 에이전트 일괄 병렬 실행 (4-way / 3-way)

> **핵심 변경**: reviewer, tester, researcher, (trading: risk-analyst)는 서로 독립적이므로
> **하나의 메시지에서 Agent 도구를 동시 호출**하여 최대 병렬화한다.

`.claude/agents/strategy-*.md` 에이전트 프롬프트를 읽은 뒤, 아래와 같이 Agent 도구를 **한 번에** 호출한다:

```
# --- 하나의 응답에서 아래 Agent 호출을 모두 동시에 보낸다 ---

# Agent 1: strategy-reviewer
Agent(name="strategy-reviewer", model="sonnet",
      description="심층 퀀트 코드 리뷰",
      prompt="""
      [.claude/agents/strategy-reviewer.md 프롬프트 전문]

      추가 컨텍스트:
      - 전략 유형: {strategy_type}
      - 대상 경로: {project_path}
      - 주요 파일: {key_files}
      결과를 JSON으로 {project_path}/logs/strategy_review/code_review.json에 저장하라.
      """)

# Agent 2: strategy-tester
Agent(name="strategy-tester", model="sonnet",
      description="통계적 검증/과적합 분석",
      prompt="""
      [.claude/agents/strategy-tester.md 프롬프트 전문]

      전략 유형: {strategy_type}
      대상 경로: {project_path}
      결과를 JSON으로 {project_path}/logs/strategy_review/test_validation.json에 저장하라.
      """)

# Agent 3: strategy-researcher
Agent(name="strategy-researcher", model="sonnet",
      description="개선 기법 조사",
      prompt="""
      [.claude/agents/strategy-researcher.md 프롬프트 전문]

      전략 유형: {strategy_type}
      전략 설명: {strategy_description}
      대상 경로: {project_path}
      결과를 JSON으로 {project_path}/logs/strategy_review/research_findings.json에 저장하라.
      """)

# Agent 4: strategy-risk-analyst (Trading 전략만)
# Report 전략이면 이 Agent는 스킵
Agent(name="strategy-risk-analyst", model="sonnet",
      description="리스크 관리 감사",
      prompt="""
      [.claude/agents/strategy-risk-analyst.md 프롬프트 전문]

      mode: audit
      전략 유형: trading
      대상 경로: {project_path}
      결과를 JSON으로 {project_path}/logs/strategy_review/risk_audit.json에 저장하라.
      """)
```

### Step 2: 분석 결과 수집 → synthesizer + output-verifier 병렬 실행

Step 1의 모든 에이전트가 완료되면, 결과 JSON 파일을 읽어서 synthesizer와 output-verifier를 **동시에** 실행한다.

```
# --- 하나의 응답에서 아래 2개 Agent 동시 호출 ---

# Agent 5: strategy-synthesizer
Agent(name="strategy-synthesizer", model="sonnet",
      description="개선점 종합 제안",
      prompt="""
      [.claude/agents/strategy-synthesizer.md 프롬프트 전문]

      전략 유형: {strategy_type}

      === Reviewer 결과 ===
      {code_review.json 내용}

      === Tester 결과 ===
      {test_validation.json 내용}

      === Researcher 결과 ===
      {research_findings.json 내용}

      === Risk Analyst 결과 ===  (Report 전략이면 이 섹션 생략)
      {risk_audit.json 내용}

      결과를 JSON으로 {project_path}/logs/strategy_review/improvement_proposals.json에 저장하라.
      """)

# Agent 6: strategy-output-verifier
Agent(name="strategy-output-verifier", model="sonnet",
      description="출력물 내용 검증",
      prompt="""
      [.claude/agents/strategy-output-verifier.md 프롬프트 전문]

      strategy_type: {strategy_type}
      대상 경로: {project_path}
      expected_output_description: {strategy_description}
      결과를 JSON으로 {project_path}/logs/strategy_review/output_verification.json에 저장하라.
      """)
```

### Step 3: Lead 종합 리포트 생성

synthesizer + output-verifier 완료 후 최종 리포트를 생성한다.

```
1. 각 에이전트 결과 JSON 파일 6개(trading) 또는 5개(report) 읽기
2. 우선순위 정렬 (Impact × Effort 매트릭스)
3. 종합 점수 산출 (아래 공식 참조 — 유형별 공식이 다름)
4. 최종 리포트를 {project_path}/logs/strategy_review/{YYYYMMDD}_review_report.md에 저장
5. 사용자에게 요약 출력
```

#### 종합 점수 산출 공식

```
Trading 전략:
  overall_score = (
    reviewer.overall_score × 0.25 +
    tester.backtest_integrity × 0.20 +
    risk_analyst.risk_score × 0.20 +
    output_verifier.verification_score × 0.15 +
    researcher.applicability_avg × 0.10 +
    tester.overfitting_penalty × 0.10
  ) / 2  # 10점 → 5점 스케일 변환

Report 전략:
  overall_score = (
    reviewer.overall_score × 0.25 +
    tester.data_reliability × 0.20 +
    output_verifier.verification_score × 0.25 +
    researcher.applicability_avg × 0.15 +
    tester.statistical_validity × 0.15
  ) / 2  # 10점 → 5점 스케일 변환

overfitting_penalty:
  Low = 8, Medium = 5, High = 2
```

---

## 유형별 분석 관점

### Trading 전략 분석 관점

| 카테고리 | 분석 항목 | 담당 에이전트 |
|----------|----------|-------------|
| **로직 정확성** | Lookahead bias, 상태 관리, 수치 안정성, 엣지 소스 검증 | reviewer |
| **백테스트 무결성** | Walk-Forward, 거래 수 충분성, 성과 지표 신뢰성 | tester |
| **과적합 검증** | 파라미터 민감도, 시계열 안정성, 벤치마크 비교 | tester |
| **리스크 관리** | 포지션 사이징, 스톱로스, 비용 민감도, 최악 시나리오 | risk-analyst |
| **출력물 품질** | 거래 로그 정합성, PnL 상식성, 시계열 일관성 | output-verifier |
| **개선 기법** | 지표 대안, 최적화 방법론, 변수 추가/제거 | researcher |
| **종합 제안** | Impact-Effort 매트릭스, 구현 로드맵 | synthesizer |

### Report/분석 전략 분석 관점

| 카테고리 | 분석 항목 | 담당 에이전트 |
|----------|----------|-------------|
| **분류/필터 정확성** | 키워드 매칭 정확도, false positive, 콘텐츠 관련성 | reviewer |
| **통계적 엄밀성** | 검정 타당성, 표본 크기, 다중비교 보정 | tester |
| **데이터 품질** | 결측치, 이상치, 캐시 유효성 | tester |
| **출력물 품질** | 콘텐츠-주제 일치, 빈 섹션, 데이터-분석 일치 | output-verifier |
| **개선 기법** | 분류 개선, 시각화, 자동 인사이트 | researcher |
| **종합 제안** | Impact-Effort 매트릭스, 구현 로드맵 | synthesizer |

---

## 산출물 구조

```
{project_path}/
├── logs/
│   └── strategy_review/
│       ├── {YYYYMMDD}_review_report.md     ← 최종 종합 리포트
│       ├── code_review.json                ← Agent 1 (reviewer) 상세 결과
│       ├── test_validation.json            ← Agent 2 (tester) 상세 결과
│       ├── research_findings.json          ← Agent 3 (researcher) 상세 결과
│       ├── risk_audit.json                 ← (trading만) risk-analyst 상세 결과
│       ├── improvement_proposals.json      ← synthesizer 상세 결과
│       └── output_verification.json        ← output-verifier 상세 결과
```

---

## 최종 리포트 형식

```markdown
# Strategy Review Report: {프로젝트명}
> 분석일: {날짜} | 전략 유형: {trading|report} | 전략: {전략 설명}

## Executive Summary
- 전체 평가: ★★★☆☆ (3.2/5)
- 리스크 등급: B (Good)
- 출력물 신뢰도: PASS
- 핵심 강점 3가지
- 핵심 개선점 3가지

## 1. Code Review 결과 (Reviewer)
### 심각도별 이슈
- [CRITICAL] ...
- [WARNING] ...
- [INFO] ...

## 2. 통계 검증 결과 (Tester)
### Trading 전략인 경우:
- 백테스트 무결성 점수: X/10
- 과적합 위험도: Low/Medium/High
- 기대값: +$X.XX/거래
### Report 전략인 경우:
- 데이터 신뢰성: X/10
- 통계 분석 타당성: X/10

## 3. 리스크 관리 감사 (Risk Analyst) — Trading 전략만
- 리스크 등급: A~F
- 포지션 사이징: 구현됨/미구현
- 거래비용 민감도: 테스트됨/미테스트
- 핵심 갭: ...
(Report 전략에서는 이 섹션 생략)

## 4. 출력물 검증 (Output Verifier)
- 검증 점수: X/10
- 콘텐츠 관련성: PASS/FAIL
- 데이터 정합성: PASS/FAIL
- 발견된 이상: ...

## 5. 관련 연구/기법 조사 (Researcher)
- 적용 가능한 기법 목록
- 추가/제거 권장 변수
- 참고 자료/논문

## 6. 개선 제안 (Synthesizer, 우선순위)
| # | 제안 | Impact | Effort | 우선순위 |
|---|------|--------|--------|---------|
| 1 | ... | High | Low | ★★★ |
| 2 | ... | High | Medium | ★★☆ |
| 3 | ... | Medium | Low | ★★☆ |

## 7. 구현 로드맵
- Phase 1 (즉시 적용): ...
- Phase 2 (단기): ...
- Phase 3 (중기): ...
```

---

## 자동 진행 규칙

> **공통 규칙**: `.claude/references/pipeline-rules.md` 참조 (잠금/복구/비용 절감 규칙)

### 멈출 수 있는 유일한 지점 (strategy-developer 전용)

**Step 4 (최종 리포트 생성) 완료 후에만 멈춰라.** 그 전에는 절대 멈추지 마라.

### 재개 시 추가 확인

- 이전 Agent 결과 파일 존재 확인 (logs/strategy_review/ 내)
- 다음 pending Step부터 이어서 실행

---

## 에이전트 프롬프트 참조

각 에이전트의 상세 프롬프트는 아래 파일에 정의:

- `.claude/agents/strategy-reviewer.md` — 심층 퀀트 코드 리뷰
- `.claude/agents/strategy-tester.md` — 통계적 검증/과적합 분석
- `.claude/agents/strategy-researcher.md` — 구현 후 개선 기법 조사
- `.claude/agents/strategy-risk-analyst.md` — 리스크 관리 감사 (신규)
- `.claude/agents/strategy-synthesizer.md` — 개선점 종합 및 제안 (리네이밍)
- `.claude/agents/strategy-output-verifier.md` — 출력물 내용 검증 (신규)
