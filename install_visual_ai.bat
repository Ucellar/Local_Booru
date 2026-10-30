@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem Local Booru: onnxruntime (~15 MB) for the Брак check «фото или рисунок», into the Python that run.bat uses.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"
echo Python: %PY%
"%PY%" -m pip install --disable-pip-version-check onnxruntime
if errorlevel 1 goto end
"%PY%" -c "import onnxruntime; print('OK: onnxruntime', onnxruntime.__version__)"
echo.
echo Модель (17 МБ) программа скачает сама. То же самое делает кнопка «Установить onnxruntime»
echo на странице «Настройки» → «Брак: фото или рисунок».
echo Старая проверка CLIP (torch и transformers, около 2,5 ГБ) больше не нужна; если всё же хотите её:
echo   "%PY%" -m pip install -r requirements_visual_ai.txt
:end
pause
