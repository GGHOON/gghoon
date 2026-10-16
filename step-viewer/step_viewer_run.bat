@echo off
rem STEP 모델링 실행. 이 파일은 step_viewer.py 와 같은 폴더에 두세요.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py) else (set PYCMD=python)

%PYCMD% step_viewer.py
if errorlevel 1 (
  echo.
  echo [오류] 프로그램이 정상 종료되지 않았습니다. 위 메시지를 확인하세요.
  echo  - python 을 찾지 못한다는 메시지: 파이썬이 설치되어 있는지 확인하세요.
  echo  - ModuleNotFoundError 메시지: 아래 명령을 한 번 실행하세요.
  echo      pip install cadquery pyvista
  echo.
  pause
)
