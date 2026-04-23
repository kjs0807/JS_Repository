# /strategy-builder — 전략 아이디어를 동작하는 코드로 구현

## 트리거 조건
이 명령은 사용자가 `/strategy-builder` 또는 "전략 만들어줘", "새 전략", "strategy build", "전략 구현해줘" 등을 입력했을 때 실행된다.

## 사용법
```
/strategy-builder
/strategy-builder "국채 입찰 모멘텀 전략"
/strategy-builder "볼린저 밴드 평균회귀"
```

## 실행 규칙

**⚠️ 절대 금지: Lead가 직접 설계/구현하지 마라. 반드시 아래 파이프라인의 에이전트에게 위임하라.**

1. `.claude/skills/strategy-builder/SKILL.md`를 읽어라.
2. `.claude/agents/strategy-strategist.md`의 **5축 프레임워크(Edge, Universe, Signal, Risk, Constraint)**로 사용자와 인터뷰를 시작하라. 자유형 질문 금지.
   - **Risk 축은 12개 항목으로 확장됨**: 포지션 사이징 공식, 일일 손실 한도, 기대값, 거래비용 민감도까지 반드시 다룰 것.
3. 인터뷰 완료 후 **SSD(Strategy Specification Document)를 YAML로 작성**하고 사용자 승인을 받아라. SSD 없이 다음 단계로 진행 금지.
4. SSD 확정 후 7단계 에이전트 파이프라인을 **Agent 도구로** 실행하라:
   - Phase 2: data-architect + literature-scout + risk-analyst (병렬, Agent 도구 3개 동시 호출)
   - Phase 3: system-designer (Agent 도구)
   - Phase 4: implementor (Agent 도구)
   - Phase 5: validator + output-verifier (Agent 도구, validator 후 verifier 순차)
5. 에이전트 프롬프트는 `.claude/agents/strategy-*.md` 파일을 참조하라.
6. 최종 산출물을 `{project_path}/logs/strategy_build/`에 저장하라.
7. **Lead의 역할은 조율/전달/판정뿐이다. 코드 작성, 아키텍처 설계, 데이터 조사, 리스크 설계는 해당 에이전트에게 위임하라.**

## 파이프라인 잠금

SSD 승인 후 PIPELINE_LOCKED 상태에 진입한다:
- 사용자 질문 → 1줄 답변 후 즉시 속행
- "멈춰"/"중단" → 체크포인트 저장 후 중단 (ssd_draft.yaml 또는 checkpoint.json)
- Data Plan RED → 사용자 판단 대기

## 에러 복구

| 상황 | 대응 |
|------|------|
| 에이전트 타임아웃 | 동일 프롬프트로 1회 재시도. 재실패 시 Lead가 핵심 작업만 직접 수행 |
| Data Plan RED | 사용자에게 보고 후 대안 제시 (대체 데이터, 범위 축소) |
| Implementor 코드 에러 | validator 결과의 must_fix 항목 기반으로 수정 후 재검증 (최대 2회) |
| 인터뷰 5라운드 초과 | 미완성 항목은 합리적 기본값으로 채우고 SSD 초안 제시 |

## 인수가 있는 경우

인수로 전략 아이디어 힌트가 주어지면 해당 주제를 중심으로 인터뷰를 시작하라.
인수가 없으면 "어떤 전략을 만들고 싶으신가요?"로 시작하라.

ARGUMENTS: $ARGUMENTS
