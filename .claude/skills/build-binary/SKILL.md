---
name: build-binary
description: |
  Python 프로젝트를 분석하여 tkinter GUI 메뉴를 자동 생성하고 PyInstaller --onefile로 단일 .exe 바이너리를 빌드하는 스킬.
  사용자가 "exe로 빌드", "바이너리로 만들어줘", ".exe 만들어줘", "빌드해줘", "실행파일로",
  "PyInstaller", "배포용으로 만들어줘" 등 Python 프로젝트의 실행파일 빌드를 요청할 때 반드시 이 스킬을 사용하라.
---

# Build Binary Skill — Python → GUI .exe 자동 빌드

## 개요

Python 프로젝트를 분석하여 tkinter GUI 메뉴를 자동 생성하고,
PyInstaller --onefile로 단일 .exe 바이너리를 빌드하는 스킬.

Agent Teams를 활용하여 Analyzer → Designer → Compiler 3단계로 병렬/순차 실행.

---

## Agent Teams 구성

| 역할 | Agent 이름 | 모델 | 담당 |
|------|-----------|------|------|
| Lead | (사용자 세션) | Opus | 전체 조율, 최종 검증, 빌드 리포트 |
| Teammate 1 | analyzer | Sonnet | 코드 분석 → 기능 목록, 진입점, 의존성 추출 |
| Teammate 2 | designer | Sonnet | tkinter GUI 메인 런처 작성 |
| Teammate 3 | compiler | Sonnet | PyInstaller 빌드 + 테스트 |

---

## 실행 흐름

### Step 0: Lead 사전 준비

```
- 대상 프로젝트 경로 확정
- build/ 디렉토리 생성 (빌드 산출물 저장용)
- 기존 main.py 또는 진입점 파일 확인
```

### Step 1: Analyzer 실행

```
Agent(name="analyzer", model="sonnet",
      description="프로젝트 코드 분석",
      prompt="""
     당신은 Python 프로젝트 분석 전문 에이전트입니다.

     작업 디렉토리: {project_path}

     다음을 수행하세요:

     1. 프로젝트의 모든 .py 파일을 읽고 분석하라:
        - 각 파일의 주요 함수/클래스 목록
        - if __name__ == '__main__' 블록이 있는 파일 식별
        - argparse 또는 CLI 인자를 받는 부분 식별

     2. 기능 단위 분류:
        - 독립적으로 실행 가능한 기능을 식별하라
        - 각 기능의 이름, 설명, 진입 함수, 필요 인자를 정리하라
        - 예시:
          * 기능1: "Expert 블로그 글 작성" → blog_writer.py::write_expert()
          * 기능2: "General 블로그 글 작성" → blog_writer.py::write_general()
          * 기능3: "전체 실행" → blog_writer.py::write_all()

     3. 의존성 분석:
        - requirements.txt 또는 import 문에서 외부 패키지 목록 추출
        - 데이터 파일, 설정 파일 등 런타임 필요 리소스 식별
        - .env 파일 필요 여부 확인

     4. 빌드 고려사항:
        - PyInstaller에서 문제될 수 있는 패키지 식별
          (예: pandas, numpy, matplotlib 등 hidden imports 필요)
        - 런타임에 필요한 데이터 파일 (--add-data 대상)

     분석 결과를 build/analysis.json으로 저장하라:
     {
       "project_name": "프로젝트명",
       "project_path": "절대경로",
       "features": [
         {
           "id": "feature_1",
           "name": "표시명 (한글)",
           "description": "기능 설명",
           "module": "모듈경로",
           "entry_function": "함수명",
           "args": [{"name": "arg1", "type": "str", "required": true, "description": "설명"}],
           "has_user_input": true
         }
       ],
       "dependencies": {
         "packages": ["requests", "pandas", ...],
         "hidden_imports": ["pandas._libs", ...],
         "data_files": [{"src": "config/products.json", "dest": "config"}],
         "env_required": true,
         "env_keys": ["API_KEY", ...]
       },
       "entry_points": ["main.py"],
       "python_version": "3.x"
     }

     """)
```

### Step 2: Analyzer 완료 → Designer 실행

Analyzer 완료 후, build/analysis.json을 기반으로 Designer를 실행한다.

```
Agent(name="designer", model="sonnet",
      description="tkinter GUI 런처 작성",
      prompt="""
     당신은 Python GUI 디자이너 에이전트입니다.

     작업 디렉토리: {project_path}

     먼저 build/analysis.json을 읽어서 기능 목록을 확인하세요.

     ## 담당: tkinter GUI 런처 작성

     build/gui_launcher.py를 생성하세요:

     ### GUI 설계 원칙

     1. 메인 윈도우:
        - 제목: analysis.json의 project_name
        - 크기: 500x400 (조정 가능)
        - 배경색: 다크 테마 (#1a1a2e 또는 유사)
        - 아이콘: 없어도 무방

     2. 기능 선택 영역:
        - analysis.json의 features 배열을 읽어서 동적으로 버튼 생성
        - 각 버튼: 기능 이름 + 간단한 설명 표시
        - 버튼 스타일: 둥근 모서리 느낌, 호버 효과
        - "전체 실행" 버튼이 있으면 상단에 강조 배치

     3. 사용자 입력 처리:
        - feature.has_user_input == true인 기능은 클릭 시
          입력 다이얼로그(simpledialog) 또는 서브 윈도우로 인자 수집
        - feature.args의 각 항목에 대해:
          * required == true: 빈 값 불허 + 경고
          * type에 따라 Entry(str), Spinbox(int), Checkbutton(bool) 사용

     4. 실행 영역:
        - 기능 실행 시 별도 스레드에서 실행 (GUI 프리징 방지)
        - 진행 상태: 하단 상태바 또는 프로그레스 바
        - 실행 완료/에러 시 messagebox로 알림

     5. 출력 영역:
        - 하단에 scrolled text 위젯으로 로그/결과 출력
        - stdout/stderr를 이 위젯으로 리다이렉트

     6. .env 처리:
        - dependencies.env_required == true이면
          시작 시 .env 파일 존재 여부 확인
          없으면 경고 + .env 경로 입력 다이얼로그

     ### 코드 구조

     ```python
     import tkinter as tk
     from tkinter import ttk, messagebox, simpledialog, scrolledtext
     import threading
     import json
     import sys
     import os
     import importlib

     class AppLauncher:
         def __init__(self, root):
             self.root = root
             self.analysis = self._load_analysis()
             self._setup_ui()

         def _load_analysis(self) -> dict:
             '''build/analysis.json 로드. 번들된 exe에서도 동작하도록
             sys._MEIPASS 경로 처리 포함.'''

         def _setup_ui(self):
             '''메인 UI 구성'''

         def _create_feature_button(self, parent, feature: dict):
             '''기능 버튼 생성'''

         def _run_feature(self, feature: dict):
             '''기능 실행 (별도 스레드)'''

         def _collect_args(self, feature: dict) -> dict:
             '''사용자 입력 수집 다이얼로그'''

         def _redirect_output(self):
             '''stdout/stderr를 텍스트 위젯으로 리다이렉트'''

     if __name__ == '__main__':
         root = tk.Tk()
         app = AppLauncher(root)
         root.mainloop()
     ```

     ### 중요: PyInstaller 호환성

     - 리소스 경로: sys._MEIPASS 또는 os.path.dirname(os.path.abspath(__file__))
       ```python
       def resource_path(relative_path):
           if hasattr(sys, '_MEIPASS'):
               return os.path.join(sys._MEIPASS, relative_path)
           return os.path.join(os.path.abspath('.'), relative_path)
       ```
     - analysis.json을 resource_path()로 로드
     - 모듈 import는 importlib.import_module() 사용 (동적 로딩)

     ### 테스트

     build/gui_launcher.py를 직접 실행하여 GUI가 정상 표시되는지 확인:
     ```
     python build/gui_launcher.py
     ```

     """)
```

### Step 3: Designer 완료 → Compiler 실행

Designer 완료 후, gui_launcher.py를 포함하여 .spec 파일과 빌드 스크립트를 생성한다.

> **순차 실행 이유**: Compiler가 gui_launcher.py의 import 경로를 .spec에 반영해야 하므로 Designer 이후에 실행한다.

```
Agent(name="compiler", model="sonnet",
      description="PyInstaller 빌드 준비",
      prompt="""
     당신은 PyInstaller 빌드 전문 에이전트입니다.

     작업 디렉토리: {project_path}

     먼저 build/analysis.json을 읽어서 의존성 정보를 확인하세요.

     ## 담당: PyInstaller .spec 파일 생성 + 빌드 준비

     ### 1. PyInstaller 설치 확인

     ```
     pip install pyinstaller --break-system-packages  (필요시)
     pyinstaller --version  # 버전 확인
     ```

     ### 2. build/{project_name}.spec 파일 생성

     analysis.json을 기반으로 .spec 파일을 생성하세요:

     ```python
     # -*- mode: python ; coding: utf-8 -*-
     import sys
     import os

     block_cipher = None

     # analysis.json에서 추출한 정보
     project_name = '{project_name}'
     entry_script = 'build/gui_launcher.py'

     # Hidden imports (analysis.json의 dependencies.hidden_imports)
     hidden_imports = [
         # analysis.json에서 동적으로 채우기
     ]

     # 데이터 파일 (analysis.json의 dependencies.data_files)
     datas = [
         ('build/analysis.json', '.'),
         # analysis.json의 data_files에서 동적으로 채우기
         # 예: ('config/products.json', 'config'),
     ]

     # .env 파일 포함 (존재하면)
     if os.path.exists('.env'):
         datas.append(('.env', '.'))

     a = Analysis(
         [entry_script],
         pathex=['{project_path}'],
         binaries=[],
         datas=datas,
         hiddenimports=hidden_imports,
         hookspath=[],
         hooksconfig={},
         runtime_hooks=[],
         excludes=['pytest', 'test', 'tests'],
         win_no_prefer_redirects=False,
         win_private_assemblies=False,
         cipher=block_cipher,
         noarchive=False,
     )

     pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

     exe = EXE(
         pyz,
         a.scripts,
         a.binaries,
         a.zipfiles,
         a.datas,
         [],
         name=project_name,
         debug=False,
         bootloader_ignore_signals=False,
         strip=False,
         upx=True,
         upx_exclude=[],
         runtime_tmpdir=None,
         console=False,          # GUI 앱이므로 콘솔 숨김
         disable_windowed_traceback=False,
         argv_emulation=False,
         target_arch=None,
         codesign_identity=None,
         entitlements_file=None,
     )
     ```

     ### 3. 빌드 스크립트 생성

     build/build.py를 생성하세요:

     ```python
     '''PyInstaller 빌드 자동화 스크립트'''
     import subprocess
     import sys
     import os
     import json
     import shutil

     def clean_build():
         '''이전 빌드 산출물 정리'''
         for d in ['dist', '__pycache__']:
             if os.path.exists(d):
                 shutil.rmtree(d)

     def install_dependencies():
         '''requirements.txt 패키지 설치'''
         if os.path.exists('requirements.txt'):
             subprocess.run([sys.executable, '-m', 'pip', 'install',
                           '-r', 'requirements.txt'], check=True)

     def run_build(spec_path):
         '''PyInstaller 빌드 실행'''
         cmd = [
             sys.executable, '-m', 'PyInstaller',
             spec_path,
             '--clean',
             '--noconfirm',
             '--distpath', 'build/dist',
             '--workpath', 'build/work',
             '--specpath', 'build',
         ]
         result = subprocess.run(cmd, capture_output=True, text=True)
         return result

     def verify_build(exe_path):
         '''빌드된 .exe 존재 및 크기 확인'''
         if os.path.exists(exe_path):
             size_mb = os.path.getsize(exe_path) / (1024 * 1024)
             return True, size_mb
         return False, 0

     def generate_report(success, exe_path, size_mb, build_output):
         '''빌드 리포트 생성'''
         report = {
             'success': success,
             'exe_path': exe_path,
             'size_mb': round(size_mb, 1),
             'build_log': build_output[-2000:] if build_output else ''
         }
         with open('build/build_report.json', 'w') as f:
             json.dump(report, f, indent=2, ensure_ascii=False)
         return report

     if __name__ == '__main__':
         clean_build()
         install_dependencies()

         spec = 'build/{project_name}.spec'
         result = run_build(spec)

         exe = f'build/dist/{project_name}.exe'
         success, size = verify_build(exe)
         report = generate_report(success, exe, size,
                                  result.stdout + result.stderr)

         if success:
             print(f'빌드 성공: {exe} ({size:.1f} MB)')
         else:
             print(f'빌드 실패. build/build_report.json 참조')
             print(result.stderr[-1000:])
     ```

     ### 4. 일반적인 hidden imports 처리

     analysis.json의 packages를 확인하여 알려진 hidden imports를 추가:

     | 패키지 | 필요한 hidden imports |
     |--------|---------------------|
     | pandas | pandas._libs, pandas._libs.tslibs |
     | numpy | numpy.core._methods, numpy.lib.format |
     | matplotlib | matplotlib.backends.backend_tkagg |
     | requests | urllib3, certifi, charset_normalizer |
     | dotenv | dotenv |
     | schedule | schedule |
     | websockets | websockets.legacy |

     ### 5. 빌드 실행하지 마라

     .spec 파일과 build.py만 생성하세요.
     실제 빌드 실행은 Lead가 Designer 완료 후 수행합니다.


     추가 지시: build/gui_launcher.py를 읽어서 해당 파일이 import하는 모듈도
     .spec의 hiddenimports에 자동으로 포함시켜라.
     """)
```

### Step 4: Compiler 완료 → Lead가 빌드 실행

```
1. build/gui_launcher.py가 정상 실행되는지 확인:
   python build/gui_launcher.py
2. 에러 있으면 Lead가 직접 수정

3. 빌드 실행:
   python build/build.py

4. 빌드 실패 시:
   - build_report.json의 에러 분석
   - hidden imports 누락 → .spec에 추가 후 재빌드
   - 데이터 파일 누락 → datas에 추가 후 재빌드
   - 최대 3회 재시도

5. 빌드 성공 시:
   - build/dist/{project_name}.exe 실행하여 GUI 표시 확인
   - 기능 버튼 클릭 테스트 (가능하면)
```

### Step 5: 최종 리포트

```
최종 출력:
- .exe 파일 경로: build/dist/{project_name}.exe
- 파일 크기: XX MB
- 포함된 기능 수: N개
- 빌드 시간: XX초
- 주의사항 (있으면)
```

---

## 산출물 구조

```
{project_path}/
├── build/
│   ├── analysis.json         ← Analyzer 산출
│   ├── gui_launcher.py       ← Designer 산출
│   ├── {project_name}.spec   ← Compiler 산출
│   ├── build.py              ← Compiler 산출
│   ├── build_report.json     ← 빌드 결과
│   ├── dist/
│   │   └── {project_name}.exe  ← 최종 바이너리
│   └── work/                 ← PyInstaller 임시 (삭제 가능)
└── (기존 프로젝트 파일들)
```

---

## GUI 디자인 가이드라인

### 다크 테마 색상

```python
COLORS = {
    'bg_primary': '#1a1a2e',      # 메인 배경
    'bg_secondary': '#16213e',    # 카드 배경
    'bg_hover': '#0f3460',        # 호버
    'accent': '#e94560',          # 강조 (버튼)
    'accent_hover': '#ff6b6b',    # 강조 호버
    'text_primary': '#eaeaea',    # 주 텍스트
    'text_secondary': '#a0a0a0',  # 보조 텍스트
    'success': '#2ecc71',         # 성공
    'error': '#e74c3c',           # 에러
    'border': '#2a2a4a',          # 테두리
}
```

### GUI 레이아웃

```
┌─────────────────────────────────────┐
│  📦 프로젝트명              [─][□][×] │
├─────────────────────────────────────┤
│                                     │
│  ┌─────────────────────────────┐    │
│  │  🚀 전체 실행               │    │  ← 강조 버튼 (있으면)
│  └─────────────────────────────┘    │
│                                     │
│  ┌──────────┐  ┌──────────────┐     │
│  │ 기능 1   │  │ 기능 2       │     │  ← 기능 버튼 그리드
│  │ 설명...  │  │ 설명...      │     │
│  └──────────┘  └──────────────┘     │
│                                     │
│  ┌──────────┐  ┌──────────────┐     │
│  │ 기능 3   │  │ 기능 4       │     │
│  │ 설명...  │  │ 설명...      │     │
│  └──────────┘  └──────────────┘     │
│                                     │
├─────────────────────────────────────┤
│  📋 실행 로그                        │
│  ┌─────────────────────────────┐    │
│  │ > 초기화 완료...            │    │  ← ScrolledText
│  │ > 기능 1 실행 중...         │    │
│  │ > 완료!                     │    │
│  └─────────────────────────────┘    │
├─────────────────────────────────────┤
│  ⏳ 대기 중                    v1.0  │  ← 상태바
└─────────────────────────────────────┘
```

### 사용자 입력 다이얼로그 (feature.has_user_input == true)

```
┌─────────────────────────────────┐
│  ⚙️ Expert 블로그 글 작성 설정   │
├─────────────────────────────────┤
│                                 │
│  주제: [________________________] │
│                                 │
│  키워드: [______________________] │
│                                 │
│  분량:  ○ A4 1장  ● A4 3장     │
│                                 │
│  ☑ SEO 태그 자동 생성           │
│                                 │
│     [취소]           [실행]     │
└─────────────────────────────────┘
```

---

## 특수 케이스 처리

### 기능이 1개뿐인 프로젝트
- 메뉴 선택 UI 없이 바로 실행 (또는 최소 확인 버튼만)
- GUI는 로그 출력 + 상태바만 표시

### .env 필요 프로젝트
- 시작 시 .env 존재 확인
- 없으면: 파일 선택 다이얼로그 또는 키 입력 폼 표시
- .env를 exe와 같은 디렉토리에서 찾도록 resource_path() 적용

### 대용량 패키지 (pandas, numpy, matplotlib)
- --onefile 빌드 시 exe 크기 200MB+ 가능
- UPX 압축 적용 (upx=True)
- 불필요한 패키지 excludes에 추가 (pytest, test 등)

---

## 에러 복구 전략

| 에러 | 대응 |
|------|------|
| ModuleNotFoundError (빌드 후 실행 시) | .spec의 hiddenimports에 추가 → 재빌드 |
| FileNotFoundError (데이터 파일) | .spec의 datas에 추가 → 재빌드 |
| GUI 프리징 | threading.Thread로 기능 실행 확인 |
| .exe 실행 시 콘솔 창 | .spec의 console=False 확인 |
| 한글 깨짐 | encoding='utf-8' 확인, spec에 utf-8 명시 |
| tkinter import 실패 | Python 설치 시 tcl/tk 포함 여부 확인 |
