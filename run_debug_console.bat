@echo off
cd /d "%~dp0"
set LOCAL_BOORU_STARTUP_CONSOLE=1
echo Local Booru 2 with a visible console...
call run.bat
echo.
echo Closed. Press any key.
pause >nul
