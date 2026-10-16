@echo off
rem STEP 파일(.stp/.step) 더블클릭 시 STEPViewer.exe 로 열리도록 등록 (관리자 권한 불필요)
rem 이 파일은 STEPViewer.exe 와 같은 폴더에 두세요. 폴더를 옮기면 다시 실행해야 합니다.
cd /d "%~dp0"
start "" /wait STEPViewer.exe --register
