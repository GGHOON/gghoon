@echo off
rem ===== STEP 뷰어 배포용 묶음(exe) 만들기 (경량판) =====
rem 파이썬과 cadquery-ocp/pyvista 가 설치된 이 PC에서 한 번만 실행하세요.
rem 결과: dist\STEPViewer 폴더  →  이 폴더를 통째로(또는 압축해서) 다른 PC에 복사하면 파이썬 없이 실행됩니다.
rem 이 폴더에 step_viewer.py, prune_vtk.py, exe_파일연결_등록.bat, exe_파일연결_해제.bat 이 함께 있어야 합니다.
cd /d "%~dp0"
set APP=dist\STEPViewer

rem --- 1) CAD 엔진(OCP)과 pyvista 가 '실제로' 설치된 파이썬을 고른다 (py 런처가 다른 버전을 고르는 경우 방지)
set PYCMD=
for %%P in (python py) do (
  if not defined PYCMD (
    %%P -c "import OCP.STEPControl, pyvista, pyvistaqt" >nul 2>nul && set PYCMD=%%P
  )
)
if not defined PYCMD (
  echo [오류] OCP^(cadquery-ocp^)·pyvista·pyvistaqt 가 함께 설치된 파이썬을 찾지 못했습니다.
  echo   설치된 파이썬 목록:
  py -0p 2>nul
  echo   해결: 프로그램을 실행하던 파이썬에서  python -m pip install cadquery-ocp pyvista pyside6-essentials pyvistaqt qtpy
  pause
  exit /b 1
)
echo 사용할 파이썬:
%PYCMD% -c "import sys, OCP, pyvista; print('  ', sys.executable); print('   OCP:', OCP.__file__); print('   pyvista', pyvista.__version__)"

echo [1/5] PyInstaller 설치/업데이트 중...
%PYCMD% -m pip install -U pyinstaller
if errorlevel 1 goto :fail

echo [2/5] 묶는 중... (5~15분 걸릴 수 있습니다)
%PYCMD% -m PyInstaller --noconfirm --clean --onedir --windowed --name STEPViewer ^
  --collect-all OCP ^
  --hidden-import vtkmodules.vtkRenderingOpenGL2 --hidden-import vtkmodules.vtkInteractionStyle ^
  --hidden-import vtkmodules.vtkRenderingFreeType --hidden-import vtkmodules.vtkRenderingAnnotation ^
  --hidden-import vtkmodules.vtkIOXML --hidden-import vtkmodules.vtkIOGeometry ^
  --hidden-import vtkmodules.vtkFiltersCore --hidden-import vtkmodules.vtkFiltersGeneral ^
  --hidden-import vtkmodules.vtkFiltersSources --hidden-import vtkmodules.vtkRenderingUI ^
  --hidden-import vtkmodules.qt.QVTKRenderWindowInteractor --hidden-import pyvistaqt --hidden-import qtpy ^
  --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtNetwork ^
  --exclude-module PySide6.QtPdf --exclude-module PySide6.QtSvg ^
  --exclude-module cadquery --exclude-module casadi --exclude-module nlopt --exclude-module ezdxf ^
  --exclude-module IPython --exclude-module trame --exclude-module PyQt5 --exclude-module PySide6 ^
  --exclude-module numba --exclude-module llvmlite --exclude-module cv2 --exclude-module imageio_ffmpeg ^
  --exclude-module imageio --exclude-module pandas --exclude-module scipy --exclude-module sklearn ^
  step_viewer.py
if errorlevel 1 goto :fail
if not exist "%APP%\_internal\OCP" (
  echo [오류] 묶음에 CAD 엔진^(OCP^)이 들어가지 않았습니다.
  goto :fail
)

echo [3/5] 3D 엔진에서 쓰지 않는 파일 정리 중...
%PYCMD% prune_vtk.py %APP%

echo [4/5] 자체 점검 중... (계산 프로세스 시험 + 작은 3D 창이 잠깐 떴다 사라집니다)
call :selftest
if errorlevel 1 (
  echo   점검 실패: 정리한 파일을 되돌리고 다시 점검합니다.
  %PYCMD% prune_vtk.py %APP% --restore
  call :selftest
  if errorlevel 1 goto :selftestfail
) else (
  if exist "%APP%\_vtk_removed" rmdir /s /q "%APP%\_vtk_removed"
)
echo   점검 통과

echo [5/5] 파일 연결 배치파일 복사 중...
copy /y "exe_파일연결_등록.bat" "%APP%\파일연결_등록.bat" >nul
copy /y "exe_파일연결_해제.bat" "%APP%\파일연결_해제.bat" >nul
del /q "%APP%\step_selftest.log" 2>nul

echo.
echo 완료: %~dp0%APP%
echo  - STEPViewer.exe 를 더블클릭하면 메인 창이 뜹니다.
echo  - 다른 PC에는 %APP% 폴더를 통째로 복사하세요.
echo  - 복사한 PC에서 "파일연결_등록.bat" 을 한 번 실행하면 STEP 더블클릭으로 열립니다.
explorer "%~dp0%APP%"
pause
exit /b 0

:selftest
rem 창 모드 exe 는 종료 코드가 배치로 잘 전달되지 않을 수 있어, 점검 결과 파일 내용으로 판정한다
del /q "%APP%\step_selftest.log" 2>nul
start "" /wait "%APP%\STEPViewer.exe" --selftest
findstr /b /c:"OK" "%APP%\step_selftest.log" >nul 2>nul
exit /b %errorlevel%

:selftestfail
echo.
echo [오류] 자체 점검에 실패했습니다. 아래 내용을 복사해서 알려 주세요.
type "%APP%\step_selftest.log"
pause
exit /b 1

:fail
echo.
echo [오류] 묶는 중 문제가 생겼습니다. 위 메시지를 복사해서 알려 주세요.
pause
exit /b 1
