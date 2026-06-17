@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo Local Booru: установка локального AI runtime для NO_MATCH
python - <<PY
import sys
print('Python:', sys.version)
if sys.version_info >= (3, 14):
    print('ОШИБКА: для torch/transformers лучше Python 3.10/3.11/3.12. На Python 3.14 torch может не установиться.')
    print('Для друга лучше собирать EXE на Python 3.10/3.11 с requirements_visual_ai.txt.')
    raise SystemExit(2)
PY
if errorlevel 1 goto end
python -m pip install --upgrade pip
python -m pip install -r requirements_visual_ai.txt
python - <<PY
import torch, transformers
print('OK: torch', torch.__version__)
print('OK: transformers', transformers.__version__)
PY
:end
pause
