@echo off
rem STEPViewer 파일 연결 해제 (원래 연결된 프로그램으로 되돌림)
cd /d "%~dp0"
start "" /wait STEPViewer.exe --unregister
