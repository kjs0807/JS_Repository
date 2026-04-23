# /code-quality — Python 코드 품질 분석 및 자동 개선

## 트리거 조건
이 명령은 사용자가 `/code-quality` 또는 "코드 품질 분석", "리팩토링 해줘", "코드 정리", "코드 개선", "코드 클린업" 등을 입력했을 때 실행된다.

## 사용법
```
/code-quality [프로젝트경로]
/code-quality KTB_VWAP
/code-quality Signal_Trading
/code-quality .   (현재 디렉토리)
```

## 실행 규칙

1. `.claude/skills/code-quality/SKILL.md`를 읽어라.
2. 대상 프로젝트 경로를 확인하라 (인자 없으면 현재 디렉토리).
3. 4개 분석 에이전트를 병렬 실행하라:
   - `.claude/agents/cq-structure.md` — 코드 구조/아키텍처
   - `.claude/agents/cq-safety.md` — 에러 핸들링/타입 안전성
   - `.claude/agents/cq-performance.md` — 성능 병목
   - `.claude/agents/cq-duplication.md` — 코드 중복/DRY
4. 분석 결과를 수집하고 자동 수정 대상을 선별하라.
5. `.claude/agents/cq-fixer.md`를 읽고 cq-fixer 에이전트로 자동 수정을 실행하라.
6. 최종 리포트를 `{project_path}/logs/code_quality/`에 저장하라.

## 파이프라인 잠금

Step 0 시작 시 PIPELINE_LOCKED 상태:
- 사용자 질문 → 1줄 답변 후 속행
- Step 4 (최종 리포트) 완료 후에만 멈춰라

## 에러 복구

| 상황 | 대응 |
|------|------|
| 에이전트 타임아웃 | 동일 프롬프트로 1회 재시도. 재실패 시 Lead가 해당 분석을 축약 수행 |
| cq-fixer 수정 후 import 에러 | 해당 수정 롤백 후 다음 수정으로 진행 |
| 프로젝트에 .py 파일 없음 | 사용자에게 "분석할 Python 코드가 없습니다" 보고 후 종료 |

ARGUMENTS: $ARGUMENTS
