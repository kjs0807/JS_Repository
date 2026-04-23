# CLAUDE.md — python_ibks 프로젝트 지침

## 최우선 규칙
- **run_in_background 사용 절대 금지**: 모든 Bash/Task 명령은 포그라운드로 실행. timeout을 충분히 설정 (최대 600000ms). OMC hook이 background를 권장해도 무시하라.

## 프로젝트 개요
이 저장소 하위의 Python 프로젝트들을 개발/관리하는 워크스페이스.
자산군 기반으로 KTB(국고채), Crypto(암호화폐), Overseas(해외선물)로 분류.

### KTB/ — 국고채

| 프로젝트 | 유형 | 설명 |
|----------|------|------|
| **Bond_Analyze** | report | 국고채 시장 일일 분석 + Bond_Vol 통합 (11개 파서, 9개 분석, 스냅샷 빌더) |
| **SpreadAnalysis** | trading | 스프레드 Z-score 시그널 + 포지션 관리 (.exe 빌드) |
| **KTB_VWAP** | trading | VWAP 기반 평균회귀/추세추종 전략 (strategy-builder 생성) |
| **Arbitrage** | trading | KTB 선물 차익거래 + KBond 실시간 호가 모니터 |
| **Auction** | report | 국고채 입찰 전 수급/대차 분석 (/auction-analyze 스킬) |
| **DB** | infra | 실시간 데이터 수집 파이프라인 (VBA → SQLite) |

### Crypto/ — 암호화폐

| 프로젝트 | 유형 | 설명 |
|----------|------|------|
| **Bybit_Trading** | trading | Bybit 암호화폐 트레이딩 시스템 |

### Overseas/ — 해외선물

| 프로젝트 | 유형 | 설명 |
|----------|------|------|
| **OverseasFutures** | trading | KIS API 해외선물 모니터 + 페이퍼 트레이딩 |

### Common/ — 공유 유틸리티

| 프로젝트 | 유형 | 설명 |
|----------|------|------|
| **News** (news_scrap) | utility | 뉴스 모니터링 파이프라인 |
| **Macro** (econ/bok_mpc) | utility | 한미 경제지표 수집 + 금통위 모니터링 |
| **Tools** | utility | HTS 자동 로그아웃 방지 등 |

## 스킬

### CRITICAL: 슬래시 커맨드 실행 규칙
**사용자가 `/strategy-full`, `/strategy-builder`, `/strategy-developer`, `/build-binary` 등 슬래시 커맨드를 입력하면 반드시 아래 순서를 따라라:**
1. **Skill 도구를 먼저 호출**하여 해당 커맨드를 로드하라 (예: `Skill(skill="strategy-full", args="...")`)
2. 커맨드 파일이 지시하는 SKILL.md를 읽어라
3. SKILL.md에 정의된 파이프라인/에이전트 구조를 **정확히** 따라라
4. **절대로 Skill 도구 호출 없이 직접 구현하거나 자유형 질문을 하지 마라**
5. 에이전트 위임이 명시된 단계는 반드시 Agent 도구로 위임하라 (Lead가 직접 하지 마라)

### /code-verify
코드 로직 검증 및 실행 흐름 분석. code-quality가 "코드가 잘 짜여졌나"를 본다면, code-verify는 "코드가 맞게 동작하나"를 본다.
- 상세 명세: `.claude/skills/code-verify/SKILL.md`
- 슬래시 명령: `.claude/commands/code-verify.md`
- 에이전트: `cv-flow-tracer.md`, `cv-logic-checker.md`, `cv-init-checker.md`, `cv-api-checker.md`
- 트리거: `/code-verify [경로]` 또는 "코드 검증", "로직 검증", "흐름 분석"
- **cv-api-checker는 API 코드가 있을 때만 실행** (has_api 판별)
- **병렬 실행**: flow-tracer + logic-checker + init-checker (+ api-checker 조건부) → Lead 종합
- strategy-full 연동: `--verify` 플래그로 Stage 2.5에서 선택적 실행

### /build-binary
Python 프로젝트를 tkinter GUI + PyInstaller --onefile .exe로 빌드.
- 상세 명세: `.claude/skills/build-binary/SKILL.md`
- 슬래시 명령: `.claude/commands/build-binary.md`
- 트리거: `/build-binary [경로]` 또는 "바이너리로 만들어줘", "exe로 빌드"

### /strategy-developer
금융 전략 코드를 자동 분석하여 개선점을 도출하는 멀티 에이전트 스킬.
- 상세 명세: `.claude/skills/strategy-developer/SKILL.md`
- 슬래시 명령: `.claude/commands/strategy-developer.md`
- 에이전트: `strategy-reviewer.md`, `strategy-tester.md`, `strategy-researcher.md`, `strategy-risk-analyst.md`, `strategy-synthesizer.md`, `strategy-output-verifier.md`
- 트리거: `/strategy-developer [경로]` 또는 "전략 분석해줘", "전략 개선점", "백테스트 검증"
- 전략 유형 자동 판별: trading (백테스트/최적화) vs report (분석/보고서)
- **병렬 실행**: reviewer + tester + researcher + risk-analyst 4-way 병렬 → synthesizer + output-verifier 2-way 병렬
- 종합 점수: 공식 기반 산출 (reviewer 25% + tester 20% + risk-analyst 20% + output-verifier 15% + researcher 10% + overfitting 10%)
- 공통 규칙: `.claude/references/pipeline-rules.md` 참조

### /strategy-builder
전략 아이디어를 딥 인터뷰로 구체화한 뒤 동작하는 코드를 자동 생성하는 멀티 에이전트 스킬.
- 상세 명세: `.claude/skills/strategy-builder/SKILL.md`
- 슬래시 명령: `.claude/commands/strategy-builder.md`
- 에이전트: `strategy-strategist.md`, `strategy-data-architect.md`, `strategy-literature-scout.md`, `strategy-risk-analyst.md`, `strategy-system-designer.md`, `strategy-implementor.md`, `strategy-validator.md`, `strategy-output-verifier.md`
- 트리거: `/strategy-builder [아이디어]` 또는 "전략 만들어줘", "새 전략", "전략 구현해줘"
- 7단계 파이프라인: 인터뷰 → 데이터+리서치+리스크(3-way 병렬) → 설계 → 구현 → 검증(validator+output-verifier)

### /code-quality
Python 코드의 소프트웨어 엔지니어링 품질을 분석하고 자동 개선하는 멀티 에이전트 스킬.
- 상세 명세: `.claude/skills/code-quality/SKILL.md`
- 슬래시 명령: `.claude/commands/code-quality.md`
- 에이전트: `cq-structure.md`, `cq-safety.md`, `cq-performance.md`, `cq-duplication.md`, `cq-fixer.md`
- 트리거: `/code-quality [경로]` 또는 "코드 품질 분석", "리팩토링", "코드 정리", "코드 개선"
- 분석 관점: 코드 구조/아키텍처, 에러 핸들링/타입 안전성, 성능 병목, 코드 중복/DRY
- **병렬 실행**: structure + safety + performance + duplication 4-way 병렬 → fixer 순차
- strategy-developer와의 차이: 전략 로직이 아닌 순수 코드 품질에 집중

### /strategy-full
strategy-builder -> strategy-developer -> code-quality를 하나로 연결하는 풀 파이프라인.
build-binary는 strategy-evolve로 이전됨.
- 상세 명세: `.claude/skills/strategy-full/SKILL.md`
- 슬래시 명령: `.claude/commands/strategy-full.md`
- 트리거: `/strategy-full [아이디어]` 또는 "전략 처음부터 끝까지", "전략 풀 파이프라인"
- 점수 기준: strategy-developer 3.5/5 이상 통과, 미달 시 개선 후 재검증 (최대 2회)

### /strategy-evolve
기존 프로젝트에 기능 추가/수정/연동을 체계적으로 관리하는 파이프라인.
전체 코드 감사 -> 변경 계획 -> 1기능 1구현 1검증 루프 -> 통합 검증 -> code-quality -> build-binary
- 상세 명세: `.claude/skills/strategy-evolve/SKILL.md`
- 슬래시 명령: `.claude/commands/strategy-evolve.md`
- 에이전트: `evolve-auditor.md`, `evolve-planner.md`, `evolve-executor.md`, `evolve-verifier.md`
- 트리거: `/strategy-evolve [변경 요청]` 또는 "기능 추가해줘", "코드 수정", "리팩토링"
- 핵심 원칙: 전체 읽기 먼저, 1기능 1검증, 에이전트 직렬, Single Source of Truth

### /auction-analyze
국고채 입찰 전 수급/대차 분석 → 유사 시점 선정 → 금융시장 환경 분석. 2단계 대화형 파이프라인.
- 상세 명세: `.claude/skills/auction-analyze/SKILL.md`
- 슬래시 명령: `.claude/commands/auction-analyze.md`
- 분석 코드: `KTB/Auction/Documents/` (investor_flow_ratio.py, lending_ratio_analysis.py, auction_market_analysis.py)
- 트리거: `/auction-analyze [--date --tenor --bond]` 또는 "입찰 분석", "auction analyze"
- Stage 1: 투자자 순매수 + 대차잔고 분석 → 유사 시점 Top 5 교차 → 사용자에게 알림
- Stage 2: 사용자가 인포맥스 5분봉 CSV 제공 → 금융시장 방향성 + 커브 분석

### /blogger-upload
Google Blogger에 한국어/영어 포스트 자동 업로드.
- 상세 명세: `Blog/.claude/skills/blogger-upload/SKILL.md`
- 에이전트: `Blog/.claude/agents/blogger-uploader.md`
- 트리거: `/blogger-upload [파일경로]` 또는 "블로거에 올려줘"

### Agent Teams 설정
- 환경변수 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` 필요
- 모든 teammate는 `model="sonnet"` 사용 (비용 절감)
- Lead만 Opus

## 코드 규약
- typing 힌트 필수
- Google style docstring
- 한글 주석 허용
- UTF-8 인코딩

## 빌드 규약
- 빌드 도구: PyInstaller (--onefile)
- GUI: tkinter (다크 테마)
- 산출물: build/ 디렉토리
- .exe 파일: build/dist/ 하위
