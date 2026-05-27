# Что изменено для модульности

## Добавлено

### `core/app_context.py`
Общий контекст приложения. Сейчас хранит настройки и простой реестр сервисов.
Старые страницы всё ещё получают `MainWindow` как parent, поэтому совместимость сохранена.

### `core/tasks.py`
Минимальная заготовка для безопасного запуска тяжёлых задач в `QThread`.
Старые worker-классы пока не тронуты.

### `ui/modules/registry.py`
Реестр страниц. Теперь новая страница добавляется через `PageSpec`, а не через ручное редактирование всей навигации.

### `ui/styles/themes.py`
Темы вынесены из `MainWindow`, чтобы окно не было одновременно и навигатором, и хранилищем CSS.

## Изменено

### `ui/main_window.py`
`MainWindow` стал shell-слоем:
- создаёт верхнюю панель;
- создаёт страницы из реестра;
- переключает страницы;
- применяет тему;
- хранит `AppContext`.

## Что специально не трогалось
- `core/tagger_engine.py` — большой и рискованный файл, лучше дробить отдельно.
- `ui/downloader_page.py` — внутри много backend-логики, но перенос нужно делать отдельным шагом.
- `ui/manga_page.py` — сканирование и UI пока смешаны, но рабочий сценарий не ломался.

## Как добавлять новый модуль
1. Создать страницу в `ui/<name>_page.py`.
2. Добавить импорт и `PageSpec` в `ui/modules/registry.py`.
3. Указать workspace: `tagger`, `manga`, `games`, `downloader` или `system`.
4. Если странице нужен refresh при открытии — поставить `refresh_on_open=True`.


## Deep modular attempt

Раздроблены крупные точки:

- `ui/downloader_page.py` теперь совместимая обёртка; реализация в `ui/downloader/`:
  - `helpers.py` — URL/API/cookies/hash/tag helpers;
  - `worker.py` — `DownloaderWorker`;
  - `page.py` — `DownloaderPage`.
- `ui/manga_page.py` теперь совместимая обёртка; реализация в `ui/manga/`:
  - `helpers.py` — сканирование, архивы, метаданные, pixmap;
  - `widgets.py` — `MangaCard`, `ChapterSelection`;
  - `reader.py` — `MangaReader`;
  - `page.py` — `MangaPage`.
- `core/tagger_engine.py` теперь совместимая обёртка; основной код перенесён в `core/tagger/engine.py`; добавлены фасады `settings.py`, `media.py`, `results.py`, `cookies.py`.

Это намеренно переходная архитектура: старые импорты не должны ломаться, но новые изменения уже можно делать внутри пакетов.
