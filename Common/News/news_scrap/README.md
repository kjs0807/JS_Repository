# 📡 경제뉴스 모니터

실시간 경제뉴스 키워드 모니터링 프로그램입니다.  
RSS 피드 + 네이버 Open API를 활용하여 키워드에 매칭되는 기사를 자동 수집합니다.

---

## 🚀 사용법 (팀원용)

1. **`경제뉴스모니터.exe`** 파일을 더블클릭하여 실행
2. 자동으로 뉴스 수집이 시작됩니다
3. 🔴 빨간색 = 키워드 매칭 기사 / ⚪ 회색 = 일반 기사
4. **기사 더블클릭** → 브라우저에서 원문 열림
5. **우클릭** → 링크 복사 가능

### 내 키워드 추가
- `⚙️ 설정` 탭 → `내 키워드` 섹션에서 추가/삭제
- 추가한 키워드는 자동 저장됩니다

### 네이버 API 설정 (선택)
네이버 API를 연동하면 국내 뉴스 커버리지가 대폭 향상됩니다.
1. https://developers.naver.com 접속 → 회원가입/로그인
2. `애플리케이션 등록` → `검색 > 뉴스` API 선택
3. 발급받은 Client ID / Secret을 설정 탭에 입력
4. **무료, 일 25,000건**

### 최소화 & 종료
- **X 버튼** → 트레이 최소화 or 완전 종료 선택
- **최소화 버튼** → 작업표시줄 트레이로 이동 (백그라운드 실행)
- 트레이 아이콘 **더블클릭** → 복원
- 트레이 아이콘 **우클릭** → 종료

---

## 🛠️ 빌드 방법 (배포자용)

### 사전 준비
- Python 3.10 이상 설치 (https://python.org)
- 설치 시 **"Add Python to PATH"** 반드시 체크

### 빌드 절차
```bash
# 1. 이 폴더에서 명령 프롬프트 열기

# 2. 의존성 설치 + 빌드 (자동)
build.bat

# 또는 수동으로:
pip install -r requirements.txt
pyinstaller --onefile --windowed --name "경제뉴스모니터" --hidden-import=pystray._win32 news_monitor.py
```

### 결과물
- `dist/경제뉴스모니터.exe` → 이 파일만 팀원에게 공유

### 기본 키워드 수정
`news_monitor.py` 파일의 `DEFAULT_KEYWORDS` 리스트를 수정 후 재빌드:
```python
DEFAULT_KEYWORDS = [
    "금통위", "기준금리", "FOMC", "국고채", ...
]
```

---

## 📊 뉴스 소스

### 국내 (폴링 주기: 2~3분)
| 소스 | 방법 |
|---|---|
| 한국경제 (금융/경제/국제) | RSS |
| 연합뉴스 (경제/국제) | RSS |
| 매일경제 | RSS |
| 뉴시스 속보 | RSS |
| Google News 한국 비즈니스 | RSS |
| 네이버 뉴스 검색 | Open API (설정 필요) |

### 해외 (폴링 주기: 5분)
| 소스 | 방법 |
|---|---|
| CNBC (Top/Finance) | RSS |
| Investing.com | RSS |
| Yahoo Finance | RSS |
| MarketWatch | RSS |
| Seeking Alpha | RSS |
| Reuters | Google News RSS 우회 |

---

## 📁 파일 구조

```
news_monitor/
├── news_monitor.py          # 메인 소스코드
├── news_monitor_config.json # 설정 파일 (자동 생성)
├── news_monitor.log         # 로그 파일 (자동 생성)
├── requirements.txt         # Python 의존성
├── build.bat                # 빌드 스크립트
└── README.md                # 이 파일
```

---

## ⚠️ 참고사항
- 첫 실행 시 Windows Defender가 차단할 수 있음 → "추가 정보" → "실행" 클릭
- `news_monitor_config.json`은 exe와 같은 폴더에 자동 생성됨
- 설정 파일을 삭제하면 초기화됨
- 네이버 API 없이도 RSS만으로 기본 작동함
