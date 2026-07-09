# Журнал изменений Local Booru

## v1.5 — выбор языка при первом запуске и более безопасная пересборка SQLite

Дата релиза: 2026-07-09

### Добавлено

- Окно выбора языка при первом запуске: **English** или **Russian** до открытия главного окна.
- Сохранение выбранного языка через ключи настроек `language` и `language_selected_once`.
- Публичный английский README для публикации на GitHub.

### Исправлено

- Исправлена миграция `m024_reverse_branch_status`, в которой отсутствовал `NAME`; из-за этого мог падать журнал миграций после чистой пересборки SQLite-базы.
- Runner миграций теперь использует безопасное сгенерированное имя, если в модуле миграции отсутствует `NAME`; это предотвращает вторичный `AttributeError`, который маскировал исходную ошибку.
- Импорт legacy-кэша `NO_MATCH` и реестра удалённых файлов теперь коммитится пачками, чтобы не держать SQLite write-lock слишком долго во время чистой пересборки базы.
- Старт парсера больше не воспринимает заблокированную SQLite-базу как пустую базу.

### Улучшено

- Более безопасные проверки запуска и готовности SQLite после аварийного завершения.
- Low-memory профиль парсера для больших прогонов архива.
- Улучшенная релизная документация для пользователей GitHub.

## CHANGELOG_v413

v413 — выбор языка при первом запуске

- Добавлено стартовое окно первого запуска для выбора языка интерфейса: русский или английский.
- Выбор сохраняется в настройки как `language` и `language_selected_once`, поэтому окно появляется только один раз для workspace/profile.
- Существующий переключатель языка на странице настроек остаётся доступен для последующих изменений.
- Добавлены Qt-free helpers для language bootstrap и тесты.

## CHANGELOG_v412

v412 — исправление миграции SQLite m024 / запуска после удаления БД

- Исправлено отсутствие `NAME` в `m024_reverse_branch_status`, из-за чего падал журнал миграций схемы после пересборки БД.
- Runner миграций теперь использует безопасное fallback-имя, поэтому будущая миграция без `NAME` больше не будет маскировать реальную ошибку миграции.
- Импорт legacy `NO_MATCH` и deleted-registry теперь коммитится пачками, чтобы не держать SQLite writer lock минутами при чистой пересборке БД.
- Исправление специально нацелено на тестовый сценарий с удалённой SQLite-базой, когда медиаархив остаётся на месте, а рабочая БД создаётся заново.

## CHANGELOG_v385

v385 — восстановление паузы парсера / корректировка очистки

- Вернул runtime-кнопку ПАУЗА/ПРОДОЛЖИТЬ в парсер.
- Старт/Стоп остаются одной кнопкой.
- Убраны только настройки пауз/лимитов из пользовательского UI; сама пауза выполнения парсера не убирается.
- Таймаут запроса не стал резать вслепую: он остаётся внутренним предохранителем от зависших сетевых запросов, не пользовательской настройкой.

## CHANGELOG_v388

v388 — предпросмотр парсера открывает оригинал через native Windows path

- Двойной клик по предпросмотру в парсере по-прежнему открывает оригинальный/source-файл, а не управляемую output-копию.
- Убран fallback через `QDesktopServices`/`QUrl(file://...)` для открытия локального предпросмотра парсера на Windows.
- Сначала используется `os.startfile(original_path)`, затем явный `ShellExecuteW` с UTF-16 native path.
- Добавлены более понятные сообщения в лог парсера, когда путь к оригинальному файлу отсутствует или открыть файл не удалось.

## CHANGELOG_v391

v391 — встроенный bootstrap ExifTool

- Добавлены встроенные места поиска ExifTool:
  - `<app>/tools/exiftool/exiftool.exe`
  - `<app>/tools/exiftool/exiftool_files/`
  - `Local_Booru_Archive/settings/tools/exiftool/exiftool.exe`
- Добавлен автоматический Windows bootstrap для ExifTool, если он отсутствует:
  - скачивает официальный 64-bit архив ExifTool в `settings/tools/exiftool`;
  - извлекает `exiftool.exe` и `exiftool_files`;
  - пользовательская настройка не требуется.
- PyInstaller spec теперь включает папку `tools`, поэтому release-сборки могут поставлять ExifTool рядом с приложением.
- Drag export из галереи всё ещё создаёт копию, даже если ExifTool недоступен, но встраивание метаданных ждёт ExifTool.

## CHANGELOG_v394

v394 — исправление category backfill для Gelbooru

- Source-specific просмотр тегов Gelbooru больше не должен полностью оставаться в `general`, когда post DAPI вернул плоский список тегов.
- Ключ фоновой очереди категорий тегов поднят до `flat-sites::tag-groups-v8-gelbooru-rule34-dapi-overlay`, чтобы заново прогнать старые Gelbooru/rule34 category jobs.
- Восстановление категорий Gelbooru теперь пробует tag catalogue для каждого тега, когда multi-name DAPI batch не классифицирует подтверждённые post tags.
- Membership остаётся защищённым: Gelbooru HTML/tag-catalogue может классифицировать только теги, которые уже присутствуют в точном ответе Gelbooru post; он не может добавлять sidebar/recommendation tags.

## CHANGELOG_v395

Local Booru v395 — дренаж категорий текущего прогона

- Фоновая обработка категорий Gelbooru/rule34 теперь отдаёт приоритет задачам, созданным текущим прогоном парсера.
- Live parser больше не загружает весь исторический backlog category-backfill в RAM при старте по умолчанию.
  Старый backlog всё ещё можно явно включить через `tagger_category_startup_seed_backlog=true`.
- Перед `DONE` парсер ждёт завершения category jobs текущего прогона, чтобы свеженайденные Gelbooru/rule34 посты не оставались визуально застрявшими в `general`.
- Добавлен task accounting для остановки category queue.

## CHANGELOG_v396

v396 — обслуживание general-only категорий

- Добавлена кнопка в Настройки → Обслуживание библиотеки:
  «Переразложить general-only теги».
- Кнопка находит уже сохранённые source-наборы Gelbooru/rule34/xbooru/hypnohub,
  где все source-specific теги лежат в `general`.
- Запускается сетевой category recheck по уже подтверждённому source URL.
- Новые теги не добавляются: обновляются только категории уже существующих тегов
  этого конкретного source bundle.
- Перед массовым обновлением создаётся SQLite backup.
- Исправление идёт в фоне через TaskManager, прогресс выводится в статус обслуживания.

## CHANGELOG_v399

v399 — восстановление парсера использует последние завершённые файлы

- Исправлена логика восстановления «перепроверить последние 10 файлов».
- В прошлых сборках файлы отслеживались в момент отправки/старта, поэтому при большом conveyor window recovery-pass мог перепроверять первые/самые старые файлы в очереди.
- Recovery теперь отслеживает `completed_recent`: последние файлы, которые реально завершили обработку парсером.
- После некорректного завершения следующий прогон парсера сначала принудительно перепроверяет именно эти последние завершённые файлы.
- `recent_started` оставлен только как compatibility fallback для старых файлов состояния recovery.

## CHANGELOG_v403

Local Booru v403 — safety/reverse/UI audit patch

- SQLite safety guard: ранее инициализированная БД больше не пересоздаётся молча как пустой файл, если ожидаемый `local_booru_index.sqlite3` отсутствует. Для намеренного полного сброса нужно создать `ALLOW_CREATE_EMPTY_DB.txt` рядом с ожидаемой БД или запустить с `LOCAL_BOORU_ALLOW_NEW_DB=1`.
- Startup log перенесён из папки приложения в `Local_Booru_Archive/settings/output/logs/startup_console.log`.
- GET endpoints Browser Companion теперь требуют такую же авторизацию extension origin/header, как POST; запрос статуса расширения обновлён соответственно.
- Временный network defer reverse-поиска сохраняется в `reverse_retry_queue`, а не остаётся только в RAM.
- e621 IQDB 429/temporary failures ставят branch-local cooldown и retry row; весь парсер не останавливается.
- Reverse branch claim/cancel: если файл уже найден другой веткой, ожидающие sibling branches пропускают его, а summary сообщает `REVERSE_CANCELED`.
- Summary теперь сообщает `REVERSE_RETRY_QUEUED`.
- Source sidebars галереи/граббера: в свёрнутом виде показываются `all` + top 3 источника по количеству; в развёрнутом — все. Настраиваемой сортировки нет.
- r34 theme сделана более угловатой: без скруглённых gallery cards/containers в этой теме.
- Library diagnostics теперь включает сверку source folder vs `site_scan_status`.
- Добавлена безопасная SQLite identifier validation для maintenance/count helper SQL.

## v404_full_audit_refactor

- Первый безопасный разрез `core/tagger/engine.py`: `hashing`/`tag_groups`/`cookies_io`/`atf_html` вынесены в отдельные тестируемые модули, `engine.py` оставлен фасадом совместимости.
- Большой разрез `ui/tagger_page.py`: `TaggerWorker` и `BrowserLoginWorker` вынесены в `ui/tagger/workers.py`, страница стала UI-слоем.
- SQLite schema v23: материализованная таблица `image_effective_tag_category` для ускорения all-source tag facets; storage обновляет кэш при изменении тегов/категорий.
- `DONE`/clean-session стал строже: при `errors`/`deferred`/`reverse_retry` run не помечается clean.
- Release hygiene: `AI_MEMORY` переименован в dev-only, старые CHANGELOG объединены, добавлен `tools/release/make_release_zip.py` и exclude-list.
- `reactor_scraper`/`architecture` перенесены в `tools/deprecated` с compatibility shims.
- Добавлены первые pytest smoke-tests для helpers/release excludes.

## Local Booru v405 — audit hotfixes

- Исправлены тесты, поставленные в v404: ожидание tag-group merge теперь соответствует сохранённому первому написанию.
- Убран side-effect `engine` из импорта пакета `core.tagger`; helper modules можно импортировать без загрузки полного Tagger engine.
- `imagehash` сделан optional для импортов hashing helper; pHash functions возвращают безопасный fallback, если зависимости нет.
- Убран side-effect `save_settings()` из SQLite `connect()`/`ensure_initialized()`; инициализация БД теперь пишет только marker file.
- Добавлена обработка `DatabaseMissingError` при запуске с понятным диалогом и exit code вместо молчаливого продолжения.
- Parser DB error handler теперь считает `DatabaseMissingError` жёстким safe-stop.
- Исправлена resumed reverse clean-state logic: теперь считаются только уже journaled файлы, подходящие под `_should_reverse()`.
- Добавлен effective-category refresh после indexer tag rebuild, e621 metadata repair и invariant repair.
- Category-maintenance scan по URL через `LIKE '%host%'` заменён на индексированный `sources.host` matching.
- Release zip builder теперь запускает pytest по умолчанию и имеет более строгие nested-folder excludes.
- Добавлены `ui/tagger/__init__.py` и PyInstaller hiddenimports для `ui.tagger.workers` и новых tagger helper modules.
- `VACUUM` больше не закрывает принудительно pooled SQLite connections, принадлежащие другим потокам.

## Local Booru v411 — DB lock hard-stop и low-RAM профиль парсера

- Старт парсера больше не воспринимает `database is locked` во время status/site-status checks как пустую базу. Если SQLite всё ещё заблокирована после crash recovery, парсер останавливается до построения очереди.
- Добавлен startup SQLite readiness probe: read test плюс короткий `BEGIN IMMEDIATE`/rollback write-lock test перед scan/status queue build.
- Добавлены runtime performance profiles: `auto`, `low_memory`, `balanced`, `performance`. `auto` определяется по найденной RAM и меняет только worker session copy, не `app_settings.json`.
- Low-memory profile ограничивает reverse admission window/workers, отключает pHash preflight для больших прогонов, использует FILE temp store, меньший SQLite cache и RAM limits на основе общего объёма системной памяти.
- Large-run local preflight пропускается на low-memory системах, вместо прогрева pHash/video-frame cache на десятках тысяч файлов.
- Reverse scheduler перестаёт продвигать уже архивированные `skip_existing` файлы на следующие стадии после того, как ветка вернула `SKIP ARCHIVED`.
