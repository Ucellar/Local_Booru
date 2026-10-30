@echo off
rem Local Booru: the .venv beside it, or the system python.
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo Python: %PY%
"%PY%" app.py
if errorlevel 1 pause
