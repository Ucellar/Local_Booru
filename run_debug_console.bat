@echo off
cd /d "%~dp0"
set LOCAL_BOORU_STARTUP_CONSOLE=1
echo Starting Local Booru with visible console...
python app.py
echo.
echo Local Booru closed. Press any key to close this console.
pause >nul
