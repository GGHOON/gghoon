@echo off
rem ===== STEP 뷰어 설치 (인터넷에서 파이썬과 라이브러리를 받아 설치) =====
rem 이 파일을 step_viewer.py 와 같은 폴더에 두고 더블클릭하세요. 관리자 권한은 필요 없습니다.
rem 회사 정책상 이 PC에 프로그램 설치가 허용되는지 먼저 확인하세요.
cd /d "%~dp0"

echo [1/3] 파이썬 확인 중...
set PYCMD=
where py >nul 2>nul && set PYCMD=py
if not defined PYCMD (where python >nul 2>nul && python -c "import sys" >nul 2>nul && set PYCMD=python)
if defined PYCMD goto :havepy

echo   파이썬이 없어 설치합니다 (winget 사용, 현재 사용자 전용)...
where winget >nul 2>nul
if errorlevel 1 goto :nowinget
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :nowinget
rem 방금 설치한 파이썬 경로를 이 창에서 바로 쓰도록 추가
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
where py >nul 2>nul && set PYCMD=py
if not defined PYCMD set PYCMD=python

:havepy
echo   사용할 파이썬: %PYCMD%
%PYCMD% --version

echo [2/3] 라이브러리 설치 중... (처음 한 번, 수 분 걸릴 수 있습니다)
%PYCMD% -m pip install --upgrade pip
%PYCMD% -m pip install --user "cadquery-ocp==7.9.3.1.1" "pyvista==0.49.1" "vtk==9.6.2" "pyside6-essentials==6.12.0" "pyvistaqt==0.13.1" "qtpy==2.4.3"
if errorlevel 1 goto :pipfail

echo [3/3] 설치 확인 중...
%PYCMD% -c "import OCP.STEPControl, pyvista; print('  OK')"
if errorlevel 1 goto :pipfail

echo.
echo 설치 완료!
echo  - step_viewer_run.bat 을 더블클릭하면 메인 창이 뜹니다.
echo  - STEP 더블클릭으로 열려면 step_viewer_파일연결_등록.bat 을 한 번 실행하세요.
pause
exit /b 0

:nowinget
echo.
echo [안내] 자동 설치를 할 수 없습니다. 아래처럼 직접 설치한 뒤 이 파일을 다시 실행하세요.
echo   1) https://www.python.org/downloads/windows/ 에서 Python 3.12 (64-bit) 설치 파일 받기
echo   2) 설치 첫 화면에서 "Add python.exe to PATH" 체크 후 Install Now
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:pipfail
echo.
echo [오류] 라이브러리 설치에 실패했습니다.
echo  - 인터넷 연결 또는 회사 방화벽(프록시) 때문일 수 있습니다. 위 메시지를 복사해서 알려 주세요.
pause
exit /b 1
