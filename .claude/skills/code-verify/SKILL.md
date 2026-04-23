---
name: code-verify
description: |
  코드 로직 검증 및 실행 흐름 분석. 실행 흐름 추적, 계산 정합성, 초기화 순서, API 스펙 대조.
  code-quality가 "코드가 잘 짜여졌나"를 본다면, code-verify는 "코드가 맞게 동작하나"를 본다.
  사용자가 "코드 검증", "로직 검증", "흐름 분석", "코드 정합성", "code verify" 등을 입력했을 때 사용.
---

# code-verify — 코드 로직 검증 및 실행 흐름 분석

## 개요

코드가 **의도대로 동작하는지** 검증하는 스킬.
code-quality가 "코드가 잘 짜여졌나" (구조/스타일)를 본다면,
code-verify는 "코드가 맞게 동작하나" (로직/흐름/계산)를 본다.

### code-quality와의 차이

| 관점 | code-quality | code-verify |
|------|-------------|-------------|
| 질문 | 코드가 깨끗한가? | 코드가 정확한가? |
| 분석 | 구조, 타입, 성능, 중복 | 실행 흐름, 계산 정합성, 초기화 순서 |
| 잡는 버그 | 미사용 변수, bare except, God Object | 레버리지 이중 적용, dead code 뒤 로직, 싱글턴 타이밍 |
| 시점 | 코드 완성 후 정리 | 코드 수정할 때마다 |

---

## 에이전트 구성

| 에이전트 | 모델 | 역할 | 실행 조건 |
|----------|------|------|-----------|
| **cv-flow-tracer** | sonnet | 실행 흐름 추적, dead code, 변수 생명주기 | 항상 |
| **cv-logic-checker** | sonnet | 비즈니스 로직 정합성, 계산 검증 | 항상 |
| **cv-init-checker** | sonnet | 초기화 순서, 모듈 의존성, 싱글턴 타이밍 | 항상 |
| **cv-api-checker** | sonnet | API 스펙 대조, 요청/응답 검증 | API 코드 존재 시만 |

---

## 실행 흐름

### Step 0: Lead 사전 분석

```
1. 대상 프로젝트의 모든 .py 파일 스캔
2. 프로젝트 유형 판별:
   - has_api: requests/httpx/aiohttp import 또는 REST/WebSocket 클라이언트 존재
   - has_state: 상태 저장/복원 로직 존재 (JSON/pickle/DB)
   - has_init_chain: 복잡한 초기화 체인 (싱글턴, 팩토리, DI)
3. 핵심 진입점 파일 식별 (main.py, app.py, __main__.py 등)
4. logs/code_verify/ 디렉토리 생성
5. cv-api-checker 실행 여부 결정 (has_api == true일 때만)
```

### Step 1: 3-way (또는 4-way) 병렬 분석

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ cv-flow-tracer  │  │ cv-logic-checker │  │ cv-init-checker  │  │ cv-api-checker  │
│                 │  │                  │  │                  │  │ (조건부 실행)    │
│ - dead code     │  │ - 계산 정합성     │  │ - 초기화 순서     │  │ - 파라미터 검증  │
│ - 미정의 변수   │  │ - 값 전파 추적    │  │ - 싱글턴 타이밍   │  │ - tick/lot size │
│ - 도달불가 경로 │  │ - 분기 누락       │  │ - import 순환     │  │ - 에러코드 처리  │
└────────┬────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬────────┘
         │                    │                     │                     │
         └────────────────────┴─────────────────────┴─────────────────────┘
                                      │
                               Step 2: Lead 종합
```

### Step 2: Lead 종합 판정

```
1. 4개 에이전트 결과 JSON 수집
2. 이슈를 심각도별 분류:
   - CRITICAL: 실행 시 크래시 또는 잘못된 결과 (즉시 수정 필요)
   - HIGH: 특정 조건에서 오동작 (수정 권장)
   - MEDIUM: 잠재적 문제 (검토 권장)
   - LOW: 개선 제안
3. 종합 점수 산출 (5점 만점)
4. 최종 리포트 생성
```

### Step 3 (선택): 자동 수정

```
CRITICAL 이슈 중 auto_fixable == true인 항목만 자동 수정.
수정 후 import 검증 (py_compile).
수정 실패 시 롤백.
```

---

## 에이전트별 상세

### cv-flow-tracer — 실행 흐름 추적기

프롬프트: `.claude/skills/code-verify/agents/cv-flow-tracer.md`

분석 항목:
1. **Dead code 탐지**: return/raise/break 뒤 도달 불가 코드
2. **변수 생명주기**: 정의 전 사용 (NameError), 정의 후 미사용
3. **함수 호출 체인**: A() → B() → C() 경로에서 예외 전파
4. **분기 완전성**: if/elif에서 else 누락, 빈 except
5. **루프 불변식**: 무한 루프 가능성, break 조건 누락

출력: `flow_trace_report.json`
```json
{
  "issues": [
    {
      "id": "FLOW-001",
      "severity": "CRITICAL",
      "category": "dead_code",
      "file": "engine.py",
      "line": 318,
      "description": "return 문 이후 3줄 도달 불가",
      "code_snippet": "return os.path.dirname(...)\n\nself._update_pairs()  # dead",
      "fix_suggestion": "__init__의 _prefill_buffers() 뒤로 이동",
      "auto_fixable": false
    }
  ],
  "summary": { "critical": 1, "high": 0, "medium": 2, "low": 3 },
  "score": 7
}
```

### cv-logic-checker — 비즈니스 로직 검증기

프롬프트: `.claude/skills/code-verify/agents/cv-logic-checker.md`

분석 항목:
1. **계산 정합성**: 같은 값이 여러 곳에서 계산될 때 일관성 (fee, PnL, qty)
2. **단위 추적**: leverage가 qty에 이미 포함되었는데 또 곱하는 등
3. **상태 머신 전이**: 유효하지 않은 상태 전이, 누락된 전이
4. **경계값 처리**: 0으로 나누기, 음수 수량, 빈 리스트 인덱싱
5. **비즈니스 규칙 일관성**: TP/SL 방향이 포지션 방향과 맞는지 등

출력: `logic_check_report.json`

### cv-init-checker — 초기화 순서 검증기

프롬프트: `.claude/skills/code-verify/agents/cv-init-checker.md`

분석 항목:
1. **모듈 레벨 실행**: import 시점에 실행되는 코드 (싱글턴, 전역 변수)
2. **초기화 순서 의존성**: A가 B에 의존하는데 B가 먼저 초기화 안 됨
3. **import 바인딩 추적**: `from X import Y`는 스냅샷 — 런타임 싱글턴 교체 시 소비자가 stale 객체 참조
4. **순환 import**: A → B → A 순환 참조
5. **상태 복원 정합성**: save/load 사이 스키마 불일치
6. **리소스 생명주기**: 파일/DB/소켓 열고 닫기 짝 맞음

출력: `init_check_report.json`

### cv-api-checker — API 스펙 대조기 (조건부)

프롬프트: `.claude/skills/code-verify/agents/cv-api-checker.md`

**실행 조건**: Step 0에서 `has_api == true`일 때만 실행

분석 항목:
1. **요청 파라미터 검증**: 필수 파라미터 누락, 타입 불일치
2. **가격/수량 정밀도**: tick_size, qty_step, min_notional 준수
3. **에러 코드 처리**: API 에러별 분기 처리 존재 여부
4. **인증 처리**: API 키 하드코딩, 토큰 갱신 누락
5. **Rate limit**: 요청 빈도 제한 준수
6. **모드 호환성**: 헤지모드/원웨이 모드별 파라미터 차이

출력: `api_check_report.json`

---

## 점수 산출

```
verify_score = (
  flow_tracer.score × 0.30 +
  logic_checker.score × 0.35 +
  init_checker.score × 0.20 +
  api_checker.score × 0.15    # has_api==false이면 나머지 가중치 재분배
) / 2   # 10점→5점 스케일

가중치 재분배 (API 없을 때):
  flow: 0.35, logic: 0.40, init: 0.25
```

| 점수 | 판정 | 의미 |
|------|------|------|
| 4.0+ | VERIFIED | 주요 로직 검증 통과 |
| 3.0-3.9 | CONDITIONAL | CRITICAL 없으나 HIGH 이슈 존재 |
| 2.0-2.9 | NEEDS_FIX | CRITICAL 이슈 존재, 수정 필요 |
| <2.0 | FAIL | 다수 CRITICAL, 실행 불가 수준 |

---

## 산출물 구조

```
{project_path}/logs/code_verify/
├── {YYYYMMDD}_verify_report.md     ← 최종 종합 리포트
├── flow_trace_report.json          ← cv-flow-tracer 결과
├── logic_check_report.json         ← cv-logic-checker 결과
├── init_check_report.json          ← cv-init-checker 결과
├── api_check_report.json           ← cv-api-checker 결과 (조건부)
└── fix_report.json                 ← 자동 수정 결과 (Step 3 실행 시)
```

---

## strategy-full 연동 (선택)

strategy-full 파이프라인에서 **Stage 2.5**로 조건부 실행 가능:

```
Stage 2 (strategy-developer) → overall_score >= 3.5
  ↓
[선택] Stage 2.5 (code-verify) → verify_score >= 3.0이면 통과
  ↓
Stage 3 (code-quality) → 코드 정리
```

**기본값은 비활성화.** strategy-full에서 `--verify` 플래그로 활성화:
```
/strategy-full --verify [아이디어]
```

---

## 멈출 수 있는 지점

1. FAIL 판정 시 (verify_score < 2.0) — 사용자에게 보고
2. 그 외: 자동 진행 (pipeline-rules.md 준수)
