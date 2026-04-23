@echo off
echo ============================================
echo   Economic News Monitor - Build Script
echo ============================================
echo.

echo [1/3] Installing dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] pip install failed. Check Python and pip installation.
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

echo [2/3] Building exe... (takes 1~3 min)
pyinstaller --onefile --windowed --name "NewsMonitor" --hidden-import=pystray._win32 --hidden-import=feedparser --hidden-import=requests --hidden-import=PIL --hidden-import=PIL.Image --hidden-import=PIL.ImageDraw --noconfirm news_monitor.py

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Build failed!
    pause
    exit /b 1
)
echo [OK] Build complete
echo.

echo [3/3] Done!
echo ============================================
echo   Output: dist\NewsMonitor.exe
echo   Share this file with your team.
echo ============================================
echo.
pause
