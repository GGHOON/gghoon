@echo off
rem ===== STEP 뷰어 배포용 묶음(exe) 만들기 (경량판) =====
rem 파이썬과 cadquery-ocp/pyvista 가 설치된 이 PC에서 한 번만 실행하세요.
rem 결과: dist\STEPViewer 폴더  →  이 폴더를 통째로(또는 압축해서) 다른 PC에 복사하면 파이썬 없이 실행됩니다.
rem 이 폴더에 step_viewer.py, prune_vtk.py, exe_파일연결_등록.bat, exe_파일연결_해제.bat 이 함께 있어야 합니다.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py) else (set PYCMD=python)
set APP=dist\STEPViewer

echo [1/5] PyInstaller 설치/업데이트 중...
%PYCMD% -m pip install -U pyinstaller
if errorlevel 1 goto :fail

echo [2/5] 묶는 중... (5~15분 걸릴 수 있습니다)
%PYCMD% -m PyInstaller --noconfirm --clean --onedir --windowed --name STEPViewer ^
  --hidden-import vtkmodules.vtkRenderingOpenGL2 --hidden-import vtkmodules.vtkInteractionStyle ^
  --hidden-import vtkmodules.vtkRenderingFreeType --hidden-import vtkmodules.vtkRenderingAnnotation ^
  --hidden-import vtkmodules.vtkIOXML --hidden-import vtkmodules.vtkIOGeometry ^
  --hidden-import vtkmodules.vtkFiltersCore --hidden-import vtkmodules.vtkFiltersGeneral ^
  --hidden-import vtkmodules.vtkFiltersSources --hidden-import vtkmodules.vtkRenderingUI ^
  --exclude-module cadquery --exclude-module casadi --exclude-module nlopt --exclude-module ezdxf ^
  --exclude-module IPython --exclude-module trame --exclude-module PyQt5 --exclude-module PySide6 ^
  --exclude-module numba --exclude-module llvmlite --exclude-module cv2 --exclude-module imageio_ffmpeg ^
  --exclude-module imageio --exclude-module pandas --exclude-module scipy --exclude-module sklearn ^
  step_viewer.py
if errorlevel 1 goto :fail

echo [3/5] 3D 엔진에서 쓰지 않는 파일 정리 중...
%PYCMD% prune_vtk.py %APP%

echo [4/5] 자체 점검 중... (작은 3D 창이 잠깐 떴다 사라집니다)
start "" /wait "%APP%\STEPViewer.exe" --selftest
if errorlevel 1 (
  echo   점검 실패: 정리한 파일을 되돌리고 다시 점검합니다.
  %PYCMD% prune_vtk.py %APP% --restore
  start "" /wait "%APP%\STEPViewer.exe" --selftest
  if errorlevel 1 goto :selftestfail
) else (
  rmdir /s /q "%APP%\_vtk_removed"
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
