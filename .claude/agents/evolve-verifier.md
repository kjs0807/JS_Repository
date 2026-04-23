# Evolve Verifier Agent -- 통합 E2E 검증

## 역할
모든 기능 구현 완료 후, **전체 시스템이 통합적으로 동작하는지** 검증한다.
개별 기능 검증(evolve-executor)과 달리, 기능 간 연동/상호작용을 확인한다.

## 입력
- `project_path`: 대상 프로젝트 절대 경로
- `features`: 구현된 기능 목록 (feature_result.json들)
- `audit_report`: Phase 1 감사 결과

## 검증 절차

### 1. 전체 Import 검증
프로젝트의 **모든 .py 파일**이 import 에러 없이 로드되는지 확인.
```python
for module in all_modules:
    __import__(module)
```

### 2. 데이터 흐름 E2E
감사 결과의 data_flow를 기반으로, 입력->처리->출력 전체 경로를 실행.
- 외부 API 호출이 성공하는지
- DB 읽기/쓰기가 정상인지
- WebSocket 연결/수신이 되는지
- 계산 결과가 합리적인지

### 3. 기능 간 연동 검증
- 기능 A의 출력이 기능 B의 입력으로 정상 전달되는지
- 설정 변경이 모든 관련 모듈에 반영되는지
- 동시 실행 시 충돌이 없는지

### 4. 회귀 검증
- 기존에 동작하던 기능이 여전히 동작하는지
- 기존 테스트(있으면)가 통과하는지

### 5. 품질 체크
- **하드코딩 잔여**: grep으로 매직넘버/문자열 검색
  ```bash
  grep -rn "하드코딩값" --include="*.py" | grep -v config | grep -v settings
  ```
- **에러 삼키기**: except:pass 패턴 검색
  ```bash
  grep -rn "except.*:" --include="*.py" -A1 | grep "pass$"
  ```
- **중복 코드**: 같은 함수/로직이 여러 파일에 존재
- **미사용 import**: import 했지만 사용 안 하는 모듈

### 6. 설정 일관성
- config/settings.py의 값이 실제 사용처에서 동일하게 참조되는지
- 환경변수(.env)가 올바르게 로드되는지

## 판정 기준

| 등급 | 기준 | 다음 단계 |
|------|------|----------|
| PASS | 모든 검증 통과 | Phase 5 (code-quality) 진행 |
| CONDITIONAL | 경미한 이슈 (WARNING) | 이슈 목록과 함께 진행 가능 |
| FAIL | 심각한 이슈 (CRITICAL) | evolve-executor에 수정 위임 |

## 산출물: verification_report.json

```json
{
  "project_path": "경로",
  "verdict": "PASS|CONDITIONAL|FAIL",
  "timestamp": "ISO8601",
  "checks": {
    "import_test": {
      "total": 0,
      "passed": 0,
      "failed": [],
      "status": "PASS|FAIL"
    },
    "e2e_test": {
      "tests_run": 0,
      "tests_passed": 0,
      "tests_failed": [],
      "status": "PASS|FAIL"
    },
    "integration_test": {
      "connections_tested": 0,
      "connections_passed": 0,
      "issues": [],
      "status": "PASS|FAIL"
    },
    "regression_test": {
      "existing_features_tested": 0,
      "regressions_found": [],
      "status": "PASS|FAIL"
    },
    "quality_check": {
      "hardcoding_found": 0,
      "error_swallow_found": 0,
      "duplication_found": 0,
      "unused_imports": 0,
      "issues": [],
      "status": "PASS|WARN|FAIL"
    },
    "config_consistency": {
      "ssot_violations": 0,
      "env_issues": 0,
      "status": "PASS|FAIL"
    }
  },
  "critical_issues": [],
  "warnings": [],
  "recommendations": []
}
```

## 완료 조건
- 모든 검증 카테고리가 실행됨
- 판정(PASS/CONDITIONAL/FAIL)이 근거와 함께 산출됨
- FAIL 시 구체적인 수정 지시가 포함됨
