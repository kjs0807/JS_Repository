"""OverseasFutures_WS PyInstaller 빌드 자동화 스크립트."""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
BUILD_DIR = PROJECT_ROOT / 'build'
DIST_DIR = BUILD_DIR / 'dist'
WORK_DIR = BUILD_DIR / 'work'
SPEC_PATH = BUILD_DIR / 'OverseasFutures_WS.spec'
EXE_PATH = DIST_DIR / 'OverseasFutures_WS.exe'
REQUIREMENTS = PROJECT_ROOT / 'requirements.txt'
REPORT_PATH = BUILD_DIR / 'build_report.json'


def clean_build() -> None:
    """build/dist 및 build/work 디렉토리 삭제."""
    for d in (DIST_DIR, WORK_DIR):
        if d.exists():
            shutil.rmtree(d)
            print(f'[clean] 삭제: {d}')
        else:
            print(f'[clean] 없음 (건너뜀): {d}')


def install_dependencies() -> None:
    """requirements.txt 의존성 설치 (subprocess 방식)."""
    if not REQUIREMENTS.exists():
        print(f'[deps] requirements.txt 없음: {REQUIREMENTS}')
        return
    print(f'[deps] 의존성 확인 중...')
    import subprocess
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-r', str(REQUIREMENTS), '--quiet'],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print(f'[deps] 완료')
        else:
            print(f'[deps] 경고: {result.stderr[:200]}')
    except subprocess.TimeoutExpired:
        print(f'[deps] 경고: pip 타임아웃 (120초), 계속 진행')
    except Exception as exc:
        print(f'[deps] 경고: pip 설치 실패 ({exc}), 계속 진행')


def run_build(spec_path: Path) -> None:
    """PyInstaller로 spec 파일 빌드 실행 (직접 import 방식)."""
    print(f'[build] PyInstaller 실행: {spec_path}')
    import PyInstaller.__main__
    old_argv = sys.argv[:]
    old_cwd = os.getcwd()
    try:
        os.chdir(str(PROJECT_ROOT))
        PyInstaller.__main__.run([
            str(spec_path),
            '--distpath', str(DIST_DIR),
            '--workpath', str(WORK_DIR),
            '--noconfirm',
        ])
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
    print(f'[build] 빌드 완료')


def verify_build(exe_path: Path) -> bool:
    """exe 파일 존재 여부 및 크기 확인."""
    if not exe_path.exists():
        print(f'[verify] FAIL: exe 없음: {exe_path}')
        return False
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f'[verify] OK: {exe_path}  ({size_mb:.1f} MB)')
    return True


def generate_report(success: bool, exe_path: Path) -> None:
    """build/build_report.json 생성."""
    report = {
        'project': 'OverseasFutures_WS',
        'build_time': datetime.now().isoformat(),
        'success': success,
        'exe_path': str(exe_path) if exe_path.exists() else None,
        'exe_size_bytes': exe_path.stat().st_size if exe_path.exists() else None,
        'spec': str(SPEC_PATH),
        'python': sys.version,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'[report] 저장: {REPORT_PATH}')


if __name__ == '__main__':
    print('=== OverseasFutures_WS 빌드 시작 ===')
    print(f'project_root: {PROJECT_ROOT}')

    clean_build()
    install_dependencies()
    run_build(SPEC_PATH)
    ok = verify_build(EXE_PATH)
    generate_report(ok, EXE_PATH)

    if ok:
        print('\n=== 빌드 성공 ===')
        sys.exit(0)
    else:
        print('\n=== 빌드 실패 ===')
        sys.exit(1)
