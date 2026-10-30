@echo off
title Local Booru - build
cd /d "%~dp0"
echo.
echo  Local Booru: building "dist\Local Booru\Local Booru.exe"
echo.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo [1/3] Installing what the build needs...
"%PY%" -m pip install -r requirements.lock.txt pyinstaller --quiet
if errorlevel 1 goto failed
echo [2/3] Removing the previous build...
if exist build rmdir /s /q build
if exist "dist\Local Booru" rmdir /s /q "dist\Local Booru"
echo [3/3] Building...
"%PY%" -m PyInstaller --noconfirm --clean "Local Booru.spec"
if errorlevel 1 goto failed
if not exist "dist\Local Booru\Local Booru.exe" goto failed
echo.
echo  Done: dist\Local Booru\Local Booru.exe
explorer "dist\Local Booru"
pause
exit /b 0
:failed
echo.
echo  The build failed - see the messages above.
pause
exit /b 1
