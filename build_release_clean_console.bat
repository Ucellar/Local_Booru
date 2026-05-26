@echo off
setlocal
cd /d "%~dp0"
echo Building Local Booru release...

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

pyinstaller --noconfirm --clean --onedir --console ^
  --name Local_Booru ^
  --icon assets\app_icon.ico ^
  --add-data "assets;assets" ^
  app.py

if errorlevel 1 (
  echo BUILD FAILED
  pause
  exit /b 1
)

echo Creating release layout...
set RELEASE=release\Local_Booru
if exist release rmdir /s /q release
mkdir "%RELEASE%"
copy "dist\Local_Booru\Local_Booru.exe" "%RELEASE%\Local_Booru.exe"
xcopy "dist\Local_Booru\_internal" "%RELEASE%\_internal" /E /I /Y >nul
mkdir "%RELEASE%\proj\data"
mkdir "%RELEASE%\proj\assets"
xcopy "assets" "%RELEASE%\proj\assets" /E /I /Y >nul

echo Cleaning temp build folders...
rmdir /s /q build
rmdir /s /q dist

echo DONE: %RELEASE%
echo Source .py files are not copied into release.
pause
