@echo off
rem STEP 뷰어 파일 연결 등록을 해제합니다. (원래 연결된 프로그램으로 되돌림)
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py) else (set PYCMD=python)

%PYCMD% step_viewer.py --unregister
echo.
pause
