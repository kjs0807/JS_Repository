# /code-verify — 코드 로직 검증 및 실행 흐름 분석

## 트리거 조건
이 명령은 사용자가 `/code-verify` 또는 "코드 검증", "로직 검증", "흐름 분석", "코드 정합성", "code verify" 등을 입력했을 때 실행된다.

## 사용법
```
/code-verify [프로젝트경로]
/code-verify Bybit_Trading
/code-verify .   (현재 디렉토리)
```

## 실행 규칙

1. `.claude/skills/code-verify/SKILL.md`를 읽어라.
2. 대상 프로젝트 경로를 확인하라 (인자 없으면 현재 디렉토리).
3. Step 0: 사전 분석 — .py 파일 스캔, has_api 판별, 핵심 진입점 식별.
4. Step 1: 분석 에이전트를 병렬 실행하라:
   - `.claude/skills/code-verify/agents/cv-flow-tracer.md` — 실행 흐름/dead code
   - `.claude/skills/code-verify/agents/cv-logic-checker.md` — 비즈니스 로직 정합성
   - `.claude/skills/code-verify/agents/cv-init-checker.md` — 초기화 순서/싱글턴
   - `.claude/skills/code-verify/agents/cv-api-checker.md` — API 스펙 대조 (has_api일 때만)
5. Step 2: 결과 수집, 심각도 분류, 종합 점수 산출, 최종 리포트 생성.
6. Step 3 (선택): CRITICAL 중 auto_fixable 항목 자동 수정.
7. 산출물을 `{project_path}/logs/code_verify/`에 저장하라.

## 파이프라인 잠금

Step 0 시작 시 PIPELINE_LOCKED 상태:
- 사용자 질문 → 1줄 답변 후 속행
- Step 2 (최종 리포트) 완료 후에만 멈춰라

## 에러 복구

| 상황 | 대응 |
|------|------|
| 에이전트 타임아웃 | 동일 프롬프트로 1회 재시도. 재실패 시 Lead가 해당 분석을 축약 수행 |
| 프로젝트에 .py 파일 없음 | 사용자에게 "분석할 Python 코드가 없습니다" 보고 후 종료 |
| API 코드 없음 | cv-api-checker 스킵, 나머지 3개로 진행 (가중치 재분배) |

ARGUMENTS: $ARGUMENTS
