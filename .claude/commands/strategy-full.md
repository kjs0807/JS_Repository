# /strategy-full — 전략 아이디어 → 코드 → 검증 → .exe 풀 파이프라인

## 트리거 조건
이 명령은 사용자가 `/strategy-full` 또는 "전략 처음부터 끝까지", "전략 풀 파이프라인", "strategy full" 등을 입력했을 때 실행된다.

## 사용법
```
/strategy-full
/strategy-full "채권 입찰 이벤트 전략"
/strategy-full "변동성 돌파 전략"
```

## 실행 규칙

**⚠️ 절대 금지: Lead가 직접 설계/구현/질문하지 마라. 반드시 아래 파이프라인을 따라라.**

1. `.claude/skills/strategy-full/SKILL.md`를 읽어라.
2. 3단계 파이프라인을 **반드시** 순차 실행하라:
   - **Stage 1**: `/strategy-builder` — 5축 인터뷰(Risk 12항목 포함) → SSD 생성 → 에이전트 팀(data-architect, literature-scout, risk-analyst, system-designer, implementor, validator, output-verifier) 실행
   - **Stage 2**: `/strategy-developer` — 6개 에이전트(reviewer, tester, researcher, risk-analyst, synthesizer, output-verifier)로 코드 분석 → 공식 기반 점수 판정
   - **Stage 3**: `/build-binary` — .exe 바이너리 빌드
3. Stage 2에서 점수가 기준 미달(< 3.5/5)이면 개선 후 재검증하라 (최대 2회).
4. 각 Stage의 스킬 명세를 참조하라:
   - `.claude/skills/strategy-builder/SKILL.md`
   - `.claude/skills/strategy-developer/SKILL.md`
   - `.claude/skills/build-binary/SKILL.md`
5. **각 Stage의 에이전트는 Agent 도구로 위임하라. Lead가 직접 코드를 작성하거나 자유형 질문을 하면 안 된다.**

## 파이프라인 잠금

SSD 승인 후 PIPELINE_LOCKED 상태에 진입한다:
- 사용자 질문 → 1줄 답변 후 즉시 속행 (자유형 대화 전환 금지)
- "멈춰"/"중단" → 체크포인트 저장 후 중단
- 그 외 → 파이프라인 속행

멈출 수 있는 지점: 인터뷰, SSD 승인, Data Plan RED, FAIL 판정 — 이 4가지만.

## 에러 복구

| 상황 | 대응 |
|------|------|
| Stage 1 에이전트 실패 | 해당 에이전트 1회 재시도. 재실패 시 Lead가 축약 수행 |
| Stage 2 점수 CONDITIONAL | CRITICAL 이슈 수정 후 Stage 2 재실행 (최대 2회) |
| Stage 2 점수 FAIL | 사용자에게 보고, Stage 1 재시작 또는 중단 결정 |
| Stage 3 빌드 실패 | hiddenimports/datas 수정 후 재빌드 (최대 3회) |
| 중단 후 재개 | `full_pipeline_checkpoint.json` + Task 상태로 위치 파악 후 이어서 실행 |

## 인수가 있는 경우

인수로 전략 아이디어 힌트가 주어지면 Stage 1의 인터뷰에서 해당 주제를 중심으로 시작하라.
인수가 없으면 "어떤 전략을 만들고 싶으신가요?"로 시작하라.

ARGUMENTS: $ARGUMENTS
