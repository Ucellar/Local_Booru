# Stability Refactor Report

## Что изменено

### Новые файлы

| Файл | Описание |
|------|---------|
| `core/thumb_service.py` | ThumbnailService — асинхронная генерация и кэш превью через QThreadPool |

### Переписанные файлы

| Файл | Что изменилось |
|------|---------------|
| `ui/gallery_page.py` | Полностью переписан: thumbnail в UI thread → async через ThumbnailService; `scan_library()` убран из UI; обогащение тегов только для видимых карточек |
| `app.py` | Добавлен lifecycle ThumbnailService (start/stop) |
| `core/paths.py` | Добавлена `result_output_base()` — разорвана цепочка `library → tagger_engine → tagger` |
| `core/library.py` | Импорт `result_output_base` переключён с `core.tagger_engine` на `core.paths` |
| `core/media_utils.py` | Аналогично — импорт через `core.paths` |
| `core/database/__init__.py` | Добавлен `from . import repository` — исправлен баг при импорте `library_service` |

## Что стало source of truth

**SQLite** — единственный источник правды для:
- списка медиафайлов (таблица `images`)
- тегов и их связей (`tags`, `image_tags`)
- источников (`sources`, `image_sources`)
- статуса обработки (`processed_files`)
- удалённых файлов (`delete_log`)

Sidecar `.txt`/`.json` читаются **только при индексации** (`core/database/indexer.py`) и сразу записываются в SQLite. После индексации sidecar-файлы не нужны.

## Какие старые механизмы отключены

| Механизм | Статус |
|---------|--------|
| `scan_library()` в UI thread галереи | **Удалён** — галерея никогда не вызывает полное сканирование |
| Генерация thumbnail в `ImageCard.__init__` | **Удалена** — заменена на placeholder + async запрос в ThumbnailService |
| Python-фильтрация 100k объектов в памяти | **Не используется** — весь поиск идёт через SQL (`count_search_items` / `search_items`) |
| Eager loading всех тегов на страницу | **Заменён** — `enrich_items()` вызывается только для видимых карточек после рендера |

## Какие операции теперь фоновые

| Операция | Механизм |
|---------|---------|
| Генерация превью | `ThumbnailService` → `QThreadPool` (3 потока) |
| Сканирование/индексация файлов | `TaggerWorker(QThread)` — уже был |
| Поиск дубликатов | `DuplicateScanWorker(QThread)` — уже был |
| Скачивание | `DownloaderWorker(QThread)` — уже был |
| ATF/теггер | `TaggerWorker(QThread)` — уже был |

## Что проверять первым

1. **Запуск** — `python app.py` не должен зависать, окно открывается за < 2 секунды
2. **Галерея** — открывается пустой (без данных), не делает rglob. Превью появляются по мере загрузки
3. **Индексация** — Tagger → Запуск ATF или отдельный Index в Settings. Только после неё в галерее появляются файлы
4. **Поиск** — вводить теги в поисковую строку, результаты приходят из SQLite
5. **Превью** — при скролле не должно быть зависаний UI; карточки показывают placeholder и заполняются асинхронно

## Архитектурные риски которые остались

| Риск | Описание |
|------|---------|
| `tagger_page` делает `rglob` в `TaggerWorker.run()` | Это нормально (QThread), но для 500k файлов rglob медленный — лучше использовать `iter_media_files()` из `media_utils` с ранней остановкой |
| `duplicates_page` делает `item_info()` с `file_md5()` в воркере | MD5 каждого файла — O(n*size). Для 500k файлов это часы. Лучше брать MD5 из SQLite |
| `nomatch_page` читает директорию через `iterdir()` | В QThread, но fallback на файловую систему вместо SQLite |
| Disk thumbnail cache без лимита по размеру | `safe_thumbnail_path()` не чистит старые превью. `cleanup_preview_cache()` вызывается при старте но логика чистки минимальная |
| `enrich_items()` для page tags открывает соединение SQLite каждый раз | Для страницы из 16-64 карточек — OK. Для batch 500+ — стоит добавить connection pool |

## Зависимости

```
pip install PySide6 pillow requests beautifulsoup4 imagehash
```

`cv2` (OpenCV) опционален — нужен для видео-превью. Без него видео показывается как текстовая карточка.
