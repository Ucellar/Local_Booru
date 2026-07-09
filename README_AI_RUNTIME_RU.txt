Local Booru NO_MATCH AI
=======================

NO_MATCH классификация Фото/Рисунок использует локальную CLIP-модель.
Картинки пользователя никуда не отправляются.
Интернет нужен только один раз: скачать веса модели в settings/models/clip.

Есть две разные части:
1) Модель: файлы config/tokenizer/pytorch_model.bin (~605 MB).
2) AI runtime: Python-пакеты torch + transformers + safetensors.

Если запускать исходники через python app.py, установи runtime:
    install_visual_ai.bat

Если отдавать программу обычному человеку, правильный вариант:
    собрать EXE с torch/transformers внутри.
Тогда EXE сам скачает модель при первом запуске, и человеку не надо ничего ставить.

Важно:
- Python 3.14 для torch может быть проблемным.
- Для сборки AI-EXE лучше Python 3.10 или 3.11.
- Если модели нет или runtime не установлен, NO_MATCH оставляет [вид ?], а не врёт real/booru.
