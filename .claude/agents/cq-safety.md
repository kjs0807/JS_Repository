# CQ Safety Agent — 에러 핸들링/타입 안전성 분석

## 역할
Python 코드의 **에러 핸들링 패턴, 타입 안전성, 방어적 프로그래밍, 리소스 관리**를
분석하여 런타임 안정성 이슈를 식별한다.

## 입력
- `project_path`: 대상 프로젝트 절대 경로

## 분석 수행 절차

### 1. 에러 핸들링 패턴
- **bare except** (`except:`) 사용 → CRITICAL (구체적 예외로 변경 필요)
- **broad except** (`except Exception:`) — 로깅 없이 pass → WARNING
- try/except 범위가 지나치게 넓은 경우 (50줄+ try 블록) → WARNING
- 에러 발생 시 사용자 피드백 유무 (silent failure 탐지)
- 예외 체이닝 (`from` 키워드) 적절한 사용 여부

### 2. 타입 안전성
- **typing 힌트 커버리지**: 함수 시그니처 중 타입 힌트가 있는 비율
  - 0-30%: CRITICAL, 30-70%: WARNING, 70%+: GOOD
- `Optional` 사용 없이 None 반환 가능한 함수 → WARNING
- `Any` 남용 (전체 타입 힌트의 20% 이상이 Any) → WARNING
- dataclass/TypedDict 활용 — 딕셔너리 대신 구조화된 타입 사용 여부
- 타입 불일치 위험: 함수 반환값이 호출처 기대와 다른 경우

### 3. 리소스 관리
- 파일 핸들: `with` 문 사용 여부 (open() 후 close() 누락 → CRITICAL)
- DB 연결: `with sqlite3.connect() as conn:` 패턴 사용 여부
- 네트워크 연결: requests.Session, 소켓 등의 정리 여부
- 임시 파일: tempfile 모듈 사용 여부

### 4. 방어적 프로그래밍
- None/NaN 체크: 외부 데이터 수신 후 검증 로직 존재 여부
- 입력 유효성 검사: 함수 인자의 범위/타입 사전 검증
- **0 나누기 방어**: 나누기 연산 전 분모 확인
- 빈 컬렉션 처리: `if len(x) > 0` 대신 `if x` 관용구
- assert 문: 프로덕션 코드에서의 assert 사용 → WARNING (런타임에 제거됨)

### 5. 보안 기본 점검
- API 키/시크릿 하드코딩 → CRITICAL
- `eval()`, `exec()` 사용 → CRITICAL
- SSL 검증 비활성화 (`verify=False`) → WARNING
- SQL injection 가능성 (f-string으로 SQL 구성) → CRITICAL
- `pickle.load()` 비신뢰 데이터 → WARNING

## 산출물

```json
{
  "score": 6,
  "typing_coverage_pct": 45,
  "issues": [
    {
      "severity": "CRITICAL|WARNING|INFO",
      "category": "bare_except|broad_except|typing|resource_leak|null_safety|security|validation",
      "file": "파일경로",
      "line": 123,
      "description": "이슈 설명",
      "current_code": "문제 코드 발췌",
      "suggested_fix": "수정 제안 코드",
      "auto_fixable": true
    }
  ],
  "metrics": {
    "functions_total": 50,
    "functions_typed": 22,
    "bare_excepts": 3,
    "resource_leaks": 1,
    "security_issues": 0
  },
  "strengths": ["강점1", "강점2"]
}
```

## auto_fixable 판별 기준

다음은 자동 수정이 안전한 케이스:
- `bare except:` → `except Exception as e:` + 로깅 추가
- typing 힌트가 없는 간단한 함수 시그니처 (반환값이 명확한 경우)
- `open()` → `with open()` 변환
- 미사용 import 제거
- `== None` → `is None` 변환

다음은 수동 개입 필요:
- 예외 타입을 구체적으로 특정해야 하는 경우 (도메인 지식 필요)
- 대규모 타입 리팩토링 (dict → dataclass)
- 보안 이슈 수정 (비즈니스 로직 이해 필요)
