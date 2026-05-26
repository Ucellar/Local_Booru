@echo off
cd /d "%~dp0"

python -m pip install -r requirements.txt
python -m pip install pyinstaller

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Local_Booru.spec del Local_Booru.spec

pyinstaller --clean --console --onefile --icon=assets/app_icon.ico --name Local_Booru --paths=. --collect-submodules=core --collect-submodules=ui --collect-submodules=PySide6.QtWebEngineCore --collect-submodules=PySide6.QtWebEngineWidgets --collect-submodules=PySide6.QtWebEngineQuick --hidden-import=core.tagger_engine --hidden-import=PySide6.QtWebEngineWidgets --hidden-import=PySide6.QtWebEngineCore --hidden-import=browser_cookie3 app.py

pause
