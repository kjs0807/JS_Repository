# /strategy-developer — 금융 전략 분석 및 개선점 도출

## 트리거 조건
이 명령은 사용자가 `/strategy-developer` 또는 "전략 분석해줘", "전략 개선점", "strategy review", "백테스트 검증" 등을 입력했을 때 실행된다.

## 사용법
```
/strategy-developer [프로젝트경로]
/strategy-developer futures_price_mornitor
/strategy-developer KTB_Trade
/strategy-developer Auction_Strategy
/strategy-developer news_scrap
/strategy-developer .   (현재 디렉토리)
```

## 실행 규칙

1. 먼저 대상 경로의 코드를 스캔하여 전략 유형(trading / report)을 자동 판별하라.
2. `.claude/skills/strategy-developer/SKILL.md`를 읽고 해당 유형에 맞는 분석을 수행하라.
3. 6개 에이전트를 순서대로 실행하라:
   - Agent 1: strategy-reviewer (심층 퀀트 코드 리뷰)
   - Agent 2~4: strategy-tester + strategy-researcher + strategy-risk-analyst (3-way 병렬)
   - Agent 5: strategy-synthesizer (개선점 종합)
   - Agent 6: strategy-output-verifier (출력물 내용 검증)
4. 에이전트 프롬프트는 `.claude/agents/strategy-*.md` 파일을 참조하라.
5. 종합 점수를 공식 기반으로 산출하라 (SKILL.md의 점수 산출 공식 참조).
6. 최종 리포트를 `{project_path}/logs/strategy_review/` 에 저장하라.

## 실행 절차

1. `.claude/skills/strategy-developer/SKILL.md`를 읽어라.
2. 대상 프로젝트 경로를 확인하라 (인자 없으면 현재 디렉토리).
3. 전략 유형 판별 (Step 0).
4. `.claude/agents/strategy-reviewer.md` 를 읽고 Agent 1 실행.
5. `.claude/agents/strategy-tester.md`, `.claude/agents/strategy-researcher.md`, `.claude/agents/strategy-risk-analyst.md` 를 읽고 Agent 2, 3, 4 병렬 실행.
6. Agent 1~4 결과를 `.claude/agents/strategy-synthesizer.md` 에 따라 Agent 5에 전달.
7. `.claude/agents/strategy-output-verifier.md` 를 읽고 Agent 6 실행.
8. 최종 종합 리포트 생성 (공식 기반 점수) 및 사용자에게 요약 출력.

## 파이프라인 잠금

Step 0 시작 시 PIPELINE_LOCKED 상태:
- 사용자 질문 → 1줄 답변 후 속행
- Step 3 (최종 리포트) 완료 후에만 멈춰라

## 에러 복구

| 상황 | 대응 |
|------|------|
| 에이전트 타임아웃 | 동일 프롬프트로 1회 재시도. 재실패 시 Lead가 해당 분석을 축약 수행 |
| 프로젝트에 .py 파일 없음 | 사용자에게 "분석할 Python 코드가 없습니다" 보고 후 종료 |
| 전략 유형 판별 불명확 | 양쪽 키워드가 혼재 시 trading으로 기본 판별 (더 엄격한 분석 적용) |

ARGUMENTS: $ARGUMENTS
