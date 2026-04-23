---
name: strategy-evolve
description: |
  strategy-full로 생성된 프로젝트에 기능 추가/수정/연동을 체계적으로 관리하는 스킬.
  "기능 추가해줘", "헤지모드 적용", "대시보드 개선", "API 연동", "strategy evolve",
  "코드 수정", "리팩토링", "기능 변경", "연동 작업" 등
  이미 존재하는 전략 프로젝트에 변경을 가하는 모든 요청에 이 스킬을 사용하라.
  strategy-full이 "아이디어 -> 동작하는 코드"를 담당한다면,
  strategy-evolve는 "동작하는 코드 -> 기능 추가/개선 -> 검증 -> 빌드"를 담당한다.
---

# Strategy Evolve -- 기존 프로젝트 기능 추가/수정/연동 파이프라인

## 개요

strategy-full로 생성된 프로젝트에 기능을 추가하거나 수정할 때 사용한다.
ad-hoc 프롬프트로 여기저기 수정하면 땜질+오류 반복이 발생한다.
이 스킬은 **체계적 변경 관리**를 통해 이를 방지한다.

```
전체 코드 감사
       |
변경 영향 분석
       |
변경 계획 수립 (사용자 승인)
       |
1기능 1구현 1검증 루프
  |-- 기능 A 구현 -> E2E 테스트 -> PASS -> 다음
  |-- 기능 B 구현 -> E2E 테스트 -> FAIL -> 수정 -> 재테스트
  |-- 기능 C 구현 -> E2E 테스트 -> PASS -> 다음
       |
통합 검증 (전체 E2E)
       |
code-quality (코드 품질 분석 + 자동 수정)
       |
build-binary (.exe 빌드)
       |
   완료
```

---

## 핵심 원칙 (CRITICAL - 반드시 준수)

### 1. 전체 읽기 먼저
수정 대상 파일뿐 아니라 **관련된 모든 파일**을 먼저 읽고 전체 구조를 이해한 뒤 수정을 시작한다.
한 파일만 고치면 다른 파일과의 연결이 깨진다.

### 2. 1기능 1구현 1검증
하나의 기능을 구현한 후 **반드시 실제 실행**으로 검증한다.
"import 성공"은 검증이 아니다. 실제 데이터 흐름(입력->처리->출력)을 확인해야 한다.

### 3. 에이전트 직렬 실행
같은 파일을 건드리는 작업은 **절대 병렬로 실행하지 않는다**.
에이전트 A가 파일을 수정하는 동안 에이전트 B가 같은 파일을 수정하면 충돌한다.
독립적인 파일/모듈만 병렬 가능.

### 4. Single Source of Truth
설정값, 파라미터, 상수는 **한 곳에서만 정의**한다.
여러 파일에 같은 값을 복붙하면 반드시 불일치가 발생한다.
config.py 또는 settings.py에 중앙화하고 다른 곳에서는 import해서 사용.

### 5. API 기반 검증
검증 시 단위 테스트나 import 체크가 아닌, **실제 API 호출/DB 조회/WS 연결** 등
end-to-end 동작을 확인한다. 실제로 돌려보지 않으면 모르는 버그가 많다.

### 6. 에러를 삼키지 않는다
`except: pass` 또는 `except Exception: pass`는 금지.
최소 `logger.warning`으로 기록해야 진단이 가능하다.
fallback 값(하드코딩 50000 등)도 경고 로그를 남겨야 한다.

---

## Agent 구성

| 역할 | Agent 이름 | 모델 | 담당 |
|------|-----------|------|------|
| Lead | (사용자 세션) | Opus | 전체 조율, 사용자 승인, 최종 판정 |
| Agent 1 | evolve-auditor | Sonnet | 전체 코드 감사 + 현재 상태 파악 |
| Agent 2 | evolve-planner | Sonnet | 변경 영향 분석 + 구현 계획 |
| Agent 3 | evolve-executor | Sonnet | 1기능씩 구현 + 즉시 검증 |
| Agent 4 | evolve-verifier | Sonnet | 통합 E2E 검증 |

---

## 실행 흐름

### Phase 1: 전체 코드 감사

#### Step 0: Lead가 프로젝트 경로와 변경 요청을 확인

```
1. 대상 프로젝트 경로 확정
2. 사용자의 변경 요청 목록 정리
3. Phase 1 시작 알림
```

#### Step 1: evolve-auditor 실행

`.claude/agents/evolve-auditor.md` 프롬프트를 읽은 뒤 Agent 도구로 호출.

```
Agent(name="evolve-auditor", model="sonnet",
      description="전체 코드 감사",
      prompt="""
      [.claude/agents/evolve-auditor.md 프롬프트 전문]

      대상 경로: {project_path}
      변경 요청: {change_requests}
      결과를 {project_path}/logs/evolve/audit_report.json에 저장하라.
      """)
```

산출물:
- 파일별 책임/의존성 맵
- 현재 알려진 이슈 목록
- 변경 요청과 관련된 파일 목록
- 하드코딩/중복/에러처리 현황

### Phase 2: 변경 영향 분석 + 계획 수립

#### Step 2: evolve-planner 실행

```
Agent(name="evolve-planner", model="sonnet",
      description="변경 계획 수립",
      prompt="""
      [.claude/agents/evolve-planner.md 프롬프트 전문]

      대상 경로: {project_path}
      변경 요청: {change_requests}
      감사 결과: {audit_report.json 내용}
      결과를 {project_path}/logs/evolve/implementation_plan.json에 저장하라.
      """)
```

산출물:
- 기능별 구현 순서 (의존성 기반)
- 각 기능에서 수정할 파일 목록
- 각 기능의 검증 방법 (구체적 명령어)
- 위험 요소 + 대응 방안

#### Step 3: 사용자 승인 (필수 중단점)

Lead가 계획을 사용자에게 제시하고 승인을 받는다.
계획이 승인되어야 Phase 3로 진행.

### Phase 3: 1기능 1구현 1검증 루프

#### Step 4: 기능별 순차 실행

implementation_plan.json의 기능 목록을 **순서대로** 처리한다.
각 기능에 대해:

```
for feature in plan.features:
    # 4-a. evolve-executor로 구현
    Agent(name="evolve-executor", model="sonnet",
          description=f"기능 구현: {feature.name}",
          prompt="""
          [.claude/agents/evolve-executor.md 프롬프트 전문]

          대상 경로: {project_path}
          구현할 기능: {feature}
          수정할 파일: {feature.files}
          검증 방법: {feature.verification}

          중요: 수정 전 관련 파일을 전부 읽을 것.
          중요: 같은 파일 내 관련 이슈를 한번에 처리할 것.
          중요: 구현 후 반드시 검증 명령어를 실행하고 결과를 보고할 것.
          """)

    # 4-b. 검증 결과 확인
    if 검증 실패:
        # 수정 후 재시도 (최대 2회)
        # 2회 실패 시 사용자에게 보고

    # 4-c. 성공 시 다음 기능으로
```

**CRITICAL**: evolve-executor는 한 번에 **하나의 기능만** 구현한다.
여러 기능을 동시에 구현하지 않는다.
이전 기능의 구현 결과를 반영해야 다음 기능이 올바르게 구현된다.

### Phase 4: 통합 검증

#### Step 5: evolve-verifier 실행

모든 기능 구현 완료 후, 전체 시스템 E2E 검증.

```
Agent(name="evolve-verifier", model="sonnet",
      description="통합 E2E 검증",
      prompt="""
      [.claude/agents/evolve-verifier.md 프롬프트 전문]

      대상 경로: {project_path}
      구현된 기능 목록: {features}
      결과를 {project_path}/logs/evolve/verification_report.json에 저장하라.
      """)
```

산출물:
- 전체 import 테스트 결과
- 각 기능별 E2E 테스트 결과
- API 연동 테스트 결과 (해당 시)
- 하드코딩/중복 잔여 확인
- PASS/FAIL 판정

#### 검증 실패 시

- FAIL 항목을 evolve-executor에 재위임
- 수정 후 evolve-verifier 재실행
- 최대 2회 반복, 여전히 실패 시 사용자에게 보고

### Phase 5: 코드 품질 검증

`.claude/skills/code-quality/SKILL.md`에 따라 실행.

```
입력: Phase 4를 통과한 프로젝트
산출물:
  - 코드 품질 리포트 ({project_path}/logs/code_quality/)
  - 자동 수정된 코드 (CRITICAL + auto_fixable WARNING)
  - 코드 품질 점수 (정보 제공용)
```

### Phase 6: 바이너리 빌드

`.claude/skills/build-binary/SKILL.md`에 따라 실행.

```
입력: Phase 5 완료된 프로젝트
산출물:
  - {project_path}/build/dist/{project_name}.exe
  - build_report.json
```

---

## 멈출 수 있는 지점

1. **Phase 2 후 계획 승인**: 사용자가 계획을 검토하고 승인/수정 (필수)
2. **Phase 3 기능 실패**: 2회 재시도 후에도 실패 시 사용자 판단 (조건부)
3. **Phase 4 통합 검증 실패**: 2회 재시도 후에도 실패 시 사용자 판단 (조건부)

**이 3가지 외에는 멈추지 말고 자동으로 다음 단계를 실행하라.**

---

## 산출물 구조

```
{project_path}/
├── logs/
│   └── evolve/
│       ├── audit_report.json        -- Phase 1 감사 결과
│       ├── implementation_plan.json -- Phase 2 구현 계획
│       ├── feature_results/         -- Phase 3 기능별 결과
│       │   ├── feature_1_result.json
│       │   ├── feature_2_result.json
│       │   └── ...
│       └── verification_report.json -- Phase 4 통합 검증
│   └── code_quality/                -- Phase 5 코드 품질
├── build/
│   └── dist/
│       └── {project_name}.exe       -- Phase 6 빌드
```

---

## evolve-executor 검증 기준

각 기능 구현 후 검증은 아래 순서로 수행:

### Level 1: 구문 검증
```python
import ast
ast.parse(open(file).read())  # 모든 수정 파일
```

### Level 2: Import 검증
```python
import modified_module  # 수정된 모듈 import
```

### Level 3: 단위 동작 검증
```python
# 수정한 함수/클래스를 실제로 호출하여 동작 확인
# 예: API 호출, DB 조회, 계산 결과 비교
```

### Level 4: E2E 검증 (기능 수준)
```python
# 해당 기능의 전체 흐름을 실행
# 예: WS 연결 -> 봉 수신 -> 시그널 -> 주문 -> DB 기록
```

**Level 3 이상을 통과해야 기능 구현 완료로 인정한다.**
Level 1-2만 통과하고 "완료"로 선언하는 것은 금지.

---

## 비용 절감

- 모든 에이전트는 `model="sonnet"` 사용
- Lead만 Opus
- 에이전트 간 결과는 파일(JSON)로 전달하여 컨텍스트 효율화
- 병렬 실행은 **독립 파일/모듈에 한해서만** 허용

---

## 파이프라인 상태 추적

파이프라인 시작 시 아래 TaskCreate로 진행 상태를 생성하고, 각 단계 완료 시 즉시 TaskUpdate:

```
TaskCreate: "Phase 1: 전체 코드 감사"
TaskCreate: "Phase 2: 변경 영향 분석 + 계획 수립"
TaskCreate: "Phase 3: 1기능 1구현 1검증 루프"
TaskCreate: "Phase 4: 통합 E2E 검증"
TaskCreate: "Phase 5: code-quality 코드 품질 검증"
TaskCreate: "Phase 6: build-binary .exe 빌드"
```

---

## 교훈 반영 (Bybit_Trading 프로젝트에서 도출)

이 스킬은 아래 교훈을 설계에 반영했다:

| 교훈 | 반영 |
|------|------|
| 검증 없이 "완료" 선언 | Level 3+ 검증 필수 |
| 에이전트 병렬 파일 충돌 | 같은 파일 직렬 실행 강제 |
| 하드코딩 산재 | Phase 1 감사에서 탐지, Phase 5에서 재확인 |
| except:pass 에러 삼키기 | Phase 1 감사에서 탐지, executor에서 금지 |
| DB 저장 누락 | E2E 검증에서 데이터 흐름 확인 |
| API 불일치 | E2E 검증에서 API 실제 응답 확인 |
| 1481줄 God Object | Phase 1 감사에서 탐지, planner에서 분리 계획 |
