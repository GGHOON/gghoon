@echo off
rem 검은 창 없이 실행하는 버전. 오류 메시지가 보이지 않으므로 먼저 step_viewer_run.bat 로 정상 동작을 확인하세요.
cd /d "%~dp0"

where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw step_viewer.py
) else (
  start "" pythonw step_viewer.py
)
