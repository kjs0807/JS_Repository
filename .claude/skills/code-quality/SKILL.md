---
name: code-quality
description: |
  Python 코드의 소프트웨어 엔지니어링 품질을 분석하고 자동 개선하는 멀티 에이전트 스킬.
  strategy-developer가 전략 로직/통계/리스크를 분석한다면, 이 스킬은 순수 코드 품질에 집중한다:
  리팩토링, 중복 제거, 성능 최적화, 에러 핸들링, 타입 안전성, 의존성 관리, 테스트 가능성.
  사용자가 "코드 품질 분석", "리팩토링 해줘", "코드 정리", "code quality", "코드 개선",
  "중복 코드 제거", "타입 힌트 추가", "에러 핸들링 개선", "성능 최적화", "코드 클린업",
  "코드 리뷰해줘" (전략이 아닌 일반 코드 관점) 등을 요청할 때 이 스킬을 사용하라.
  strategy-full 파이프라인에서는 strategy-developer 이후, build-binary 이전에 실행된다.
---

# Code Quality Skill — Python 코드 품질 분석 및 자동 개선

## 개요

Python 프로젝트의 **소프트웨어 엔지니어링 품질**을 5개 전문 에이전트로 분석하고,
발견된 이슈를 자동으로 수정하는 스킬.

strategy-developer와의 역할 분담:
- **strategy-developer**: 전략 로직 정확성, 통계적 유효성, 리스크 관리 → "전략이 올바른가?"
- **code-quality** (이 스킬): 코드 구조, 성능, 안전성, 유지보수성 → "코드가 깔끔한가?"

---

## Agent 구성

| 역할 | Agent 이름 | 모델 | 담당 |
|------|-----------|------|------|
| Lead | (사용자 세션) | Opus | 전체 조율, 이슈 수집, 자동 수정, 최종 리포트 |
| Agent 1 | cq-structure | Sonnet | 코드 구조/아키텍처 분석 |
| Agent 2 | cq-safety | Sonnet | 에러 핸들링/타입 안전성/방어적 프로그래밍 |
| Agent 3 | cq-performance | Sonnet | 성능 병목/메모리/I/O 최적화 |
| Agent 4 | cq-duplication | Sonnet | 코드 중복/DRY 원칙/추상화 기회 |
| Agent 5 | cq-fixer | Sonnet | 수집된 이슈를 실제 코드에 자동 적용 |

---

## 실행 흐름

### Step 0: Lead 사전 분석

```
1. 대상 프로젝트 경로의 모든 .py 파일 스캔
2. 파일별 라인 수, import 구조, 클래스/함수 목록 파악
3. 프로젝트 규모 판단 (small: <500줄, medium: 500-2000줄, large: 2000줄+)
4. logs/code_quality/ 디렉토리 생성
5. 분석 컨텍스트를 각 에이전트에 전달할 형태로 정리
```

### Step 1: 분석 에이전트 4-way 병렬 실행

4개 분석 에이전트는 서로 독립적이므로 **하나의 응답에서 Agent 도구를 동시 호출**한다.
각 에이전트 프롬프트는 `.claude/agents/cq-*.md` 파일을 읽어서 전문을 포함한다.

```
# --- 하나의 응답에서 아래 4개 Agent를 동시에 호출 ---

# Agent 1: cq-structure
Agent(name="cq-structure", model="sonnet",
      description="코드 구조/아키텍처 분석",
      prompt="""
      [.claude/agents/cq-structure.md 프롬프트 전문]

      대상 경로: {project_path}
      결과를 JSON으로 {project_path}/logs/code_quality/structure_report.json에 저장하라.
      """)

# Agent 2: cq-safety
Agent(name="cq-safety", model="sonnet",
      description="에러 핸들링/타입 안전성 분석",
      prompt="""
      [.claude/agents/cq-safety.md 프롬프트 전문]

      대상 경로: {project_path}
      결과를 JSON으로 {project_path}/logs/code_quality/safety_report.json에 저장하라.
      """)

# Agent 3: cq-performance
Agent(name="cq-performance", model="sonnet",
      description="성능 병목 분석",
      prompt="""
      [.claude/agents/cq-performance.md 프롬프트 전문]

      대상 경로: {project_path}
      결과를 JSON으로 {project_path}/logs/code_quality/performance_report.json에 저장하라.
      """)

# Agent 4: cq-duplication
Agent(name="cq-duplication", model="sonnet",
      description="코드 중복/DRY 분석",
      prompt="""
      [.claude/agents/cq-duplication.md 프롬프트 전문]

      대상 경로: {project_path}
      결과를 JSON으로 {project_path}/logs/code_quality/duplication_report.json에 저장하라.
      """)
```

### Step 2: Lead 이슈 수집 및 우선순위 결정

4개 에이전트의 결과 JSON을 읽어서:
1. 전체 이슈를 심각도(CRITICAL/WARNING/INFO)별로 분류
2. 자동 수정 가능 여부 판별 (auto_fixable: true/false)
3. 영향도 × 난이도 기반 우선순위 정렬
4. 수정 계획(fix_plan)을 작성 — cq-fixer에 전달할 지시 목록

```
수정 대상 선별 기준:
- CRITICAL 이슈: 모두 수정 (bare except, 리소스 누수, 타입 오류 등)
- WARNING 중 auto_fixable: 수정 (typing 추가, import 정리, docstring 보강 등)
- WARNING 중 수동 필요: 리포트에만 기록 (아키텍처 변경, 대규모 리팩토링 등)
- INFO: 리포트에만 기록
```

### Step 3: cq-fixer 에이전트 실행

수집된 이슈 중 자동 수정 가능한 항목을 실제 코드에 적용한다.

```
Agent(name="cq-fixer", model="sonnet",
      description="코드 품질 이슈 자동 수정",
      prompt="""
      [.claude/agents/cq-fixer.md 프롬프트 전문]

      대상 경로: {project_path}

      === 수정 계획 ===
      {fix_plan JSON}

      수정 완료 후 {project_path}/logs/code_quality/fix_report.json에 결과를 저장하라.
      """)
```

### Step 4: Lead 검증 및 최종 리포트

```
1. cq-fixer가 수정한 파일에서 import 에러가 없는지 확인 (python -c "import ...")
2. 기존 테스트가 있으면 실행하여 regression 없음 확인
3. 종합 점수 산출 (아래 공식)
4. 최종 리포트를 {project_path}/logs/code_quality/{YYYYMMDD}_quality_report.md에 저장
5. 사용자에게 요약 출력
```

#### 종합 점수 산출 공식

```
code_quality_score = (
  structure.score × 0.25 +
  safety.score × 0.30 +
  performance.score × 0.20 +
  duplication.score × 0.25
) / 2  # 10점 → 5점 스케일 변환

점수 해석:
- ≥ 4.0/5: EXCELLENT — 코드 품질 우수
- 3.0~3.9/5: GOOD — 사소한 개선점만 존재
- 2.0~2.9/5: FAIR — 주요 개선 필요
- < 2.0/5: POOR — 대규모 리팩토링 권장
```

---

## 산출물 구조

```
{project_path}/
├── logs/
│   └── code_quality/
│       ├── {YYYYMMDD}_quality_report.md  ← 최종 종합 리포트
│       ├── structure_report.json          ← Agent 1 결과
│       ├── safety_report.json             ← Agent 2 결과
│       ├── performance_report.json        ← Agent 3 결과
│       ├── duplication_report.json        ← Agent 4 결과
│       └── fix_report.json                ← Agent 5 수정 결과
```

---

## 최종 리포트 형식

```markdown
# Code Quality Report: {프로젝트명}
> 분석일: {날짜} | 파일 수: N | 총 라인: N

## Executive Summary
- 코드 품질: ★★★★☆ (3.8/5)
- 자동 수정: N건 적용 / M건 보류
- 핵심 강점 3가지
- 핵심 개선점 3가지

## 1. 코드 구조 (Structure)
- 점수: X/10
- 모듈 간 결합도: 높음/보통/낮음
- God Object 여부: 있음/없음
- 순환 의존성: 있음/없음

## 2. 안전성 (Safety)
- 점수: X/10
- bare except 사용: N건
- 타입 힌트 커버리지: XX%
- 리소스 누수 위험: N건

## 3. 성능 (Performance)
- 점수: X/10
- I/O 병목: N건
- 불필요한 루프/복사: N건
- 메모리 이슈: N건

## 4. 중복 (Duplication)
- 점수: X/10
- 중복 코드 블록: N건
- 추상화 기회: N건
- DRY 위반 심각도: 높음/보통/낮음

## 5. 자동 수정 결과 (Fixer)
| # | 파일 | 수정 내용 | 카테고리 |
|---|------|----------|---------|
| 1 | config.py | bare except → specific exception | safety |
| 2 | data_loader.py | typing 힌트 추가 | safety |

## 6. 수동 개선 권장 사항 (우선순위)
| # | 제안 | Impact | Effort | 카테고리 |
|---|------|--------|--------|---------|
| 1 | ... | High | Low | structure |
| 2 | ... | Medium | Medium | performance |
```

---

## 자동 진행 규칙

> **공통 규칙**: `.claude/references/pipeline-rules.md` 참조 (잠금/복구/비용 절감 규칙)

### 멈출 수 있는 유일한 지점 (code-quality 전용)

**Step 4 (최종 리포트) 완료 후에만 멈춰라.** 그 전에는 절대 멈추지 마라.

### strategy-full 파이프라인에서의 위치

```
strategy-builder → strategy-developer → **code-quality** → build-binary
```

strategy-full에서 호출 시:
- strategy-developer의 점수가 PASS (≥ 3.5/5)인 프로젝트에 대해 실행
- code-quality 점수는 파이프라인 통과/차단에 영향을 주지 않음 (정보 제공용)
- CRITICAL 이슈가 있으면 cq-fixer가 자동 수정한 후 build-binary로 진행
- 수정 후 기존 기능이 깨지지 않았는지 검증 (import 체크 + 실행 테스트)

---

## 에이전트 프롬프트 참조

- `.claude/agents/cq-structure.md` — 코드 구조/아키텍처 분석
- `.claude/agents/cq-safety.md` — 에러 핸들링/타입 안전성
- `.claude/agents/cq-performance.md` — 성능 병목 분석
- `.claude/agents/cq-duplication.md` — 코드 중복/DRY 분석
- `.claude/agents/cq-fixer.md` — 이슈 자동 수정
