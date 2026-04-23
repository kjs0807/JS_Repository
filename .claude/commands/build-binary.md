# /build-binary — Python 프로젝트를 GUI .exe로 빌드

## 트리거 조건
이 명령은 사용자가 `/build-binary` 또는 "바이너리로 만들어줘", "exe로 만들어줘", ".exe 빌드" 등을 입력했을 때 실행된다.

## 사용법
```
/build-binary [프로젝트경로]
/build-binary futures_price_mornitor
/build-binary .   (현재 디렉토리)
```

## 실행 규칙

1. 사용자에게 질문하지 마라. 모든 판단은 이 명세 + SKILL.md 기준으로 자율 결정하라.
2. 에러 발생 시 자체 디버깅 3회 시도 → 실패 시 우회 방법 적용.
3. 중간에 승인/확인을 요청하지 마라.

## 실행 절차

1. `.claude/skills/build-binary/SKILL.md`를 읽어라.
2. 대상 프로젝트 경로를 확인하라 (인자 없으면 현재 디렉토리).
3. 가상환경(venv, .venv, conda) 존재 시 해당 환경의 패키지 목록을 참조하라.
4. SKILL.md의 Agent 구성에 따라 Analyzer → Designer → Compiler 순차 실행하라.
5. 최종 .exe 파일 경로와 빌드 리포트를 출력하라.

## 에러 복구

| 상황 | 대응 |
|------|------|
| 빌드 실패 (ModuleNotFoundError) | .spec의 hiddenimports에 누락 모듈 추가 후 재빌드 (최대 3회) |
| 빌드 실패 (FileNotFoundError) | .spec의 datas에 누락 파일 추가 후 재빌드 |
| GUI 프리징 | threading.Thread로 기능 실행 확인, gui_launcher.py 수정 |
| __main__ 블록 없는 프로젝트 | 진입점을 분석하여 app.py 자동 생성 |
| PyInstaller 미설치 | `pip install pyinstaller` 자동 실행 |
