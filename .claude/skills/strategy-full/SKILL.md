---
name: strategy-full
description: |
  전략의 전체 생명주기를 하나의 파이프라인으로 실행하는 스킬: strategy-builder(구현) -> strategy-developer(검증) -> code-quality(코드 품질 개선).
  사용자가 "전략 처음부터 끝까지", "전략 풀 파이프라인", "strategy full", "전략 만들어줘",
  "새 전략 자동화" 등 새로운 전략의 기획-구현-검증 전 과정을 한번에 요청할 때 이 스킬을 사용하라.
  build-binary(.exe 빌드)는 strategy-evolve 스킬로 이전됨.
  기능 추가/수정/연동은 strategy-evolve를 사용하라.
---

# Strategy Full Pipeline -- 아이디어 -> 코드 -> 검증

## 개요

전략의 초기 생명주기(기획-구현-검증)를 하나의 파이프라인으로 실행한다.
build-binary(.exe 빌드)와 기능 추가/수정은 strategy-evolve 스킬에서 담당한다.

```
strategy-builder (구체화 + 구현 + 리스크 설계)
       |
strategy-developer (전략 분석 + 점수 + 출력물 검증)
       |
   점수 >= 3.5/5? --No--> 개선 후 재검증 (최대 2회)
       |Yes                    |
       v                       v (여전히 실패)
  code-quality             사용자에게 보고
  (코드 품질 분석 + 자동 수정)
       |
   완료 (기능 추가/빌드는 strategy-evolve로)
```

---

## 파이프라인 단계

### Stage 1: Strategy Builder

`.claude/skills/strategy-builder/SKILL.md`에 따라 실행.

```
입력: 사용자 아이디어 (또는 인수)
산출물:
  - 동작하는 전략 코드 (project_path)
  - SSD, Data Plan, Research Brief, (trading: Risk Plan), Blueprint
  - Validation Report + Verification Report
  - Validator 점수 + Output Verifier 점수 (builder 내부 검증)
```

**Stage 1 → 2 진행 기준** (10점 스케일):
- Validator ≥ 8.0 AND Output Verifier ≥ 8.0 → Stage 2 진행
- 하나라도 6.0~7.9 → CONDITIONAL: must_fix 수정 후 재검증
- 하나라도 < 6.0 → FAIL: 사용자에게 보고 후 중단 여부 확인

### Stage 2: Strategy Developer

`.claude/skills/strategy-developer/SKILL.md`에 따라 실행.

```
입력: Stage 1에서 생성된 project_path
산출물:
  - 종합 리포트 ({project_path}/logs/strategy_review/)
  - 전체 평가 점수 (1~5, 공식 기반 산출)
  - 리스크 등급 (A~F)
  - 출력물 검증 결과
  - 개선 제안 목록 (우선순위별)
```

#### 점수 판정 기준 (통일)

> **스케일 안내**: Stage 1(builder)의 validator/verifier는 **10점 스케일**, Stage 2(developer)의 overall_score는 **5점 스케일**이다.
> developer의 점수는 각 에이전트의 10점 점수에 가중치를 곱한 뒤 `/2`로 5점 스케일로 변환한 것이다.

| Stage 2 점수 (5점) | 판정 | 다음 단계 |
|---------------------|------|----------|
| ≥ 3.5/5 | PASS | Stage 3 (code-quality) 진행 |
| 2.5~3.4/5 | CONDITIONAL | CRITICAL 이슈 수정 후 Stage 2 재실행 |
| < 2.5/5 | FAIL | 사용자에게 보고, Stage 1부터 재시작 또는 중단 |

> **주의**: 이 테이블이 유일한 점수 기준이다. 다른 곳에 다른 임계값이 있으면 이 테이블을 따른다.

#### 재검증 루프

```
max_iterations = 2

for i in range(max_iterations):
    1. strategy-developer 실행
    2. 점수 확인
    3. PASS (≥ 3.5) → Stage 3 진행, break
    4. CONDITIONAL (2.5~3.4) →
       a. CRITICAL 이슈 목록 추출
       b. implementor 에이전트에 수정 위임 (Lead 직접 수정 금지)
       c. output-verifier 재실행 (출력물 품질 재확인)
       d. 다음 iteration으로
    5. FAIL (< 2.5) → 사용자에게 보고, break

if 2회 반복 후에도 CONDITIONAL:
    사용자에게 현재 상태 보고 + Stage 3 강제 진행 여부 확인
```

### Stage 3: Code Quality

`.claude/skills/code-quality/SKILL.md`에 따라 실행.

```
입력: Stage 2를 통과한 project_path
산출물:
  - 코드 품질 리포트 ({project_path}/logs/code_quality/)
  - 자동 수정된 코드 (CRITICAL 이슈 + auto_fixable WARNING)
  - 코드 품질 점수 (1~5, 정보 제공용 — 파이프라인 통과/차단에 미영향)
```

> **파이프라인 규칙**: code-quality 점수가 낮아도 완료로 진행한다. 빌드는 strategy-evolve에서.
> 이 단계의 목적은 빌드 전에 코드를 정리하는 것이지 차단이 아니다.
> 단, CRITICAL 이슈(bare except, 리소스 누수 등)는 cq-fixer가 자동 수정한다.

### (Stage 4: Build Binary - strategy-evolve로 이전됨)

빌드가 필요하면 strategy-evolve 파이프라인 또는 /build-binary를 별도 실행하라.

---

## 실행 흐름

### Step 0: Lead 초기화

```
1. 사용자 인수에서 아이디어 힌트 추출 (있으면)
2. 파이프라인 시작 알림
3. PIPELINE_LOCKED = true
4. Stage 1 진입
```

### Step 1: Strategy Builder 실행

```
strategy-builder 스킬의 전체 흐름 실행:
  - 인터뷰 (Phase 1) — 사용자 상호작용 필요
  - 조사 & 리스크 설계 (Phase 2) — 에이전트 자동 (3-way 병렬)
  - 설계 (Phase 3) — 에이전트 자동
  - 구현 (Phase 4) — 에이전트 자동
  - 검증 (Phase 5) — validator + output-verifier 자동

결과:
  - project_path 확정
  - validator_score + verifier_score 확인
  - PASS → Step 2
  - FAIL → 사용자에게 보고
```

### Step 2: Strategy Developer 실행

```
strategy-developer 스킬의 전체 흐름 실행:
  - 전략 유형 판별 (자동)
  - 6개 에이전트 순차/병렬 실행
    (reviewer, tester+researcher+risk-analyst 병렬, synthesizer, output-verifier)
  - 종합 리포트 생성 (공식 기반 점수 산출)

결과:
  - overall_score 확인 (공식 기반, 1~5 스케일)
  - PASS (≥ 3.5) → Step 3
  - CONDITIONAL (2.5~3.4) → 수정 후 재실행
  - FAIL (< 2.5) → 사용자에게 보고
```

### Step 3: Code Quality 실행

```
code-quality 스킬의 전체 흐름 실행:
  - 4개 분석 에이전트 병렬 (structure, safety, performance, duplication)
  - 자동 수정 대상 선별 → cq-fixer 에이전트 실행
  - 수정 후 import/실행 검증

결과:
  - 코드 품질 점수 (X.X/5) — 정보 제공용, 통과/차단 무관
  - 자동 수정 N건 적용
  - 수동 개선 권장 사항 목록
```

### Step 4: Build Binary 실행

```
build-binary 스킬의 전체 흐름 실행:
  - 코드 분석 → GUI 생성 → PyInstaller 빌드

결과:
  - .exe 파일 경로
  - 빌드 성공/실패
```

### Step 5: 최종 리포트

```
파이프라인 완료 요약:
  - 전략명: {name}
  - 프로젝트 경로: {project_path}
  - Builder Validator 점수: X.X/10
  - Builder Output Verifier 점수: X.X/10
  - Developer Review 점수: X.X/5 (공식 기반)
  - Developer 리스크 등급: A~F (trading만, report는 N/A)
  - Developer 출력물 검증: PASS/CONDITIONAL/FAIL
  - Code Quality 점수: X.X/5
  - Code Quality 자동 수정: N건 적용
  - .exe 경로: {exe_path}
  - .exe 크기: XX MB
  - 총 소요 단계: N
  - 주요 특징 요약
  - 거래비용 민감도 요약 (Trading 전략)
```

---

## 산출물 구조

```
{workspace_root}/{strategy_name}/
├── (전략 코드 파일들)
├── risk_manager.py                    ← Trading만 필수, Report에서는 생략
├── build/
│   ├── analysis.json
│   ├── gui_launcher.py
│   ├── {strategy_name}.spec
│   ├── build.py
│   ├── build_report.json
│   └── dist/
│       └── {strategy_name}.exe      ← 최종 바이너리
├── logs/
│   ├── strategy_build/              ← Stage 1 산출물
│   │   ├── ssd.yaml
│   │   ├── data_plan.yaml
│   │   ├── research_brief.yaml
│   │   ├── risk_plan.yaml           ← (trading만) 신규
│   │   ├── blueprint.yaml
│   │   ├── validation_report.yaml
│   │   ├── verification_report.json ← 신규
│   │   └── checkpoint.json          ← 신규
│   └── strategy_review/             ← Stage 2 산출물
│       ├── {YYYYMMDD}_review_report.md
│       ├── code_review.json
│       ├── test_validation.json
│       ├── research_findings.json
│       ├── risk_audit.json          ← (trading만) 신규
│       ├── improvement_proposals.json
│       └── output_verification.json ← 신규
```

---

## 자동 진행 규칙

> **공통 규칙**: `.claude/references/pipeline-rules.md` 참조 (잠금/복구/비용 절감 규칙)

### 멈출 수 있는 유일한 지점 (strategy-full 전용)

1. **Stage 1 인터뷰**: 전략 아이디어 구체화 (필수)
2. **SSD 승인**: 인터뷰 결과 확인 (필수)
3. **Data Plan RED**: 데이터 수집 불가 시 사용자 판단 (조건부)
4. **FAIL 시 판단**: 재시작/중단/강제 진행 (조건부)

**이 4가지 외에는 절대 멈추지 말고 자동으로 다음 단계를 실행하라.**

### 파이프라인 상태 추적 (TaskCreate 필수)

파이프라인 시작 시 아래 TaskCreate로 진행 상태를 생성하고, 각 단계 완료 시 즉시 TaskUpdate하라:

```
TaskCreate: "Stage 1-Phase 1: 인터뷰 + SSD 생성"
TaskCreate: "Stage 1-Phase 2: data-architect + literature-scout (+ trading: risk-analyst) 병렬"
TaskCreate: "Stage 1-Phase 3: system-designer 설계"
TaskCreate: "Stage 1-Phase 4: implementor 구현"
TaskCreate: "Stage 1-Phase 5: validator + output-verifier 검증"
TaskCreate: "Stage 2: strategy-developer 분석 + 점수 판정"
TaskCreate: "Stage 3: code-quality 코드 품질 분석 + 자동 수정"
TaskCreate: "(Stage 4 제거됨 - build-binary는 strategy-evolve에서)"
```

**멈췄다가 재개할 때**: Task 상태와 checkpoint를 확인하여 현재 위치를 파악하고, 다음 pending 항목부터 이어서 실행하라.

### 풀 파이프라인 체크포인트

각 Stage 완료 시 `{project_path}/logs/full_pipeline_checkpoint.json`에 상태를 저장한다:

```json
{
  "pipeline": "strategy-full",
  "current_stage": 2,
  "strategy_type": "trading|report",
  "project_path": "/path/to/project",
  "stages": {
    "stage_1_builder": {
      "status": "completed",
      "validator_score": 8.5,
      "verifier_score": 8.0,
      "timestamp": "ISO8601"
    },
    "stage_2_developer": {
      "status": "in_progress",
      "iteration": 1,
      "overall_score": null,
      "timestamp": "ISO8601"
    },
    "stage_3_code_quality": {
      "status": "pending",
      "quality_score": null,
      "fixes_applied": 0
    },
    "stage_4_build": {
      "status": "pending"
    }
  }
}
```

재개 시: 이 파일 + builder의 `checkpoint.json` + Task 상태를 종합하여 위치를 파악한다.

### 팀 관리

각 Stage에서 별도 팀을 생성/삭제한다:
- Stage 1: `strategy-builder` 팀
- Stage 2: `strategy-dev` 팀
- Stage 3: `code-quality` 팀
- Stage 4: `build-binary` 팀

동시에 2개 이상의 팀이 존재하지 않도록 한다.

### 비용 절감

- 모든 에이전트는 `model="sonnet"` 사용
- Lead만 Opus
- Stage 간 결과를 파일로 전달하여 컨텍스트 효율화
