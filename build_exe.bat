@echo off
title Local Booru - Build EXE
cd /d "%~dp0"
echo.
echo ============================================
echo  Local Booru - EXE Builder
echo ============================================
echo.

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt --quiet
python -m pip install pyinstaller --quiet
python -m pip install curl_cffi --quiet
echo Done.
echo.

echo [2/3] Cleaning old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Local_Booru.spec del /q Local_Booru.spec
echo Done.
echo.

echo [3/3] Building EXE...
python -m PyInstaller ^
    --clean ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name "Local Booru" ^
    --icon "assets\icons\app_icon.ico" ^
    --paths "." ^
    --add-data "assets;assets" ^
    --add-data "core;core" ^
    --add-data "ui;ui" ^
    --hidden-import=core.tagger_engine ^
    --hidden-import=core.tagger.engine ^
    --hidden-import=core.database.repository ^
    --hidden-import=core.database.schema ^
    --hidden-import=core.database.connection ^
    --hidden-import=core.thumb_service ^
    --hidden-import=core.media_utils ^
    --hidden-import=core.image_safe ^
    --hidden-import=core.settings ^
    --hidden-import=core.paths ^
    --hidden-import=core.app_context ^
    --hidden-import=core.task_manager ^
    --hidden-import=core.favorites ^
    --hidden-import=core.i18n ^
    --hidden-import=core.tag_utils ^
    --hidden-import=core.library ^
    --hidden-import=ui.main_window ^
    --hidden-import=ui.gallery_page ^
    --hidden-import=ui.post_page ^
    --hidden-import=ui.tagger_page ^
    --hidden-import=ui.tags_page ^
    --hidden-import=ui.settings_page ^
    --hidden-import=ui.downloader_page ^
    --hidden-import=ui.duplicates_page ^
    --hidden-import=ui.manga_page ^
    --hidden-import=ui.games_page ^
    --hidden-import=ui.nomatch_page ^
    --hidden-import=ui.styles.themes ^
    --hidden-import=ui.modules.registry ^
    --hidden-import=PySide6.QtWebEngineWidgets ^
    --hidden-import=PySide6.QtWebEngineCore ^
    --hidden-import=PySide6.QtMultimedia ^
    --hidden-import=PySide6.QtMultimediaWidgets ^
    --hidden-import=browser_cookie3 ^
    --hidden-import=sqlite3 ^
    --hidden-import=PIL ^
    --hidden-import=PIL.Image ^
    --hidden-import=bs4 ^
    --hidden-import=requests ^
    --collect-submodules=core ^
    --collect-submodules=ui ^
    --collect-data=PySide6 ^
    app.py

echo.
if exist "dist\Local Booru\Local Booru.exe" (
    echo ============================================
    echo  BUILD SUCCESS!
    echo  EXE: dist\Local Booru\Local Booru.exe
    echo ============================================
    echo.
    echo Opening output folder...
    explorer "dist\Local Booru"
) else (
    echo ============================================
    echo  BUILD FAILED - check errors above
    echo ============================================
)
echo.
pause
