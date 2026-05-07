@echo off
chcp 65001 >nul
title Bond_Auto Daily Refresh

echo ================================================================
echo  Bond_Auto.xlsx Daily Refresh
echo ================================================================
echo.
echo Bond_Auto.xlsx 가 닫혀있어야 합니다.
echo (열려있으면 자동으로 닫힙니다)
echo.
echo Press any key to start...
pause >nul
echo.

cd /d "C:\Python\Summary_Daily\py_files"
python refresh_summary.py

echo.
echo ================================================================
echo  Done. Press any key to close.
echo ================================================================
pause >nul
