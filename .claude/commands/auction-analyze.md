# /auction-analyze — 국고채 입찰 전 수급/대차 분석 및 유사 시점 금융시장 분석

## 트리거 조건
사용자가 `/auction-analyze` 또는 "입찰 분석", "auction analyze", "입찰 전 분석" 등을 입력했을 때 실행.

## 사용법
```
/auction-analyze --date 2026-04-13 --tenor 10 --bond "국고03250-3512(25-11)"
/auction-analyze --date 2026-03-31 --tenor 30 --bond "국고03500-5603(26-2)"
/auction-analyze --date 2026-04-06 --tenor 3 --bond "국고02750-2812(25-10)"
/auction-analyze   (인자 없으면 사용자에게 입찰일/테너/종목명 질문)
```

## 실행 규칙

1. `.claude/skills/auction-analyze/SKILL.md`를 읽고 지시에 따라라.
2. 2단계 파이프라인: Stage 1(데이터 분석) → 사용자 대기 → Stage 2(금융시장 분석).
3. Stage 1 결과를 사용자에게 보여준 뒤 **반드시 멈추고** 사용자의 데이터 제공을 기다려라.
4. 사용자가 인포맥스 데이터를 넣었다고 하면 Stage 2를 진행하라.
5. 분석 코드는 `KTB/Auction/Documents/` 에 있는 `.py` 파일을 실행하라. 직접 분석 로직을 구현하지 마라.

## 필수 파라미터

| 파라미터 | 설명 | 예시 |
|----------|------|------|
| `--date` | 입찰일 (YYYY-MM-DD) | 2026-03-31 |
| `--tenor` | 테너 (2, 3, 5, 10, 20, 30) | 30 |
| `--bond` | 입찰 종목 전체명 | "국고03500-5603(26-2)" |

인자가 없으면 사용자에게 물어볼 것.

ARGUMENTS: $ARGUMENTS
