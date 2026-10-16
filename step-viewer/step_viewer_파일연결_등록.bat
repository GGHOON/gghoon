@echo off
rem STEP 파일(.stp/.step) 더블클릭 시 STEP 뷰어로 열리도록 등록합니다. (관리자 권한 필요 없음)
rem 이 파일은 step_viewer.py 와 같은 폴더에 두세요. 폴더를 옮기면 다시 실행해야 합니다.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py) else (set PYCMD=python)

%PYCMD% step_viewer.py --register
echo.
pause
