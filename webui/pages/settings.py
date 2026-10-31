"""The page «Настройки»: what the program does by itself and where its data lives — each section a form
saved on its own (``webui/settings_form``), changed only through ``settings_store.save`` (the keys of
the sections, nothing else).  Actions next to the settings they belong to: a light copy now, the
thumbnail cache, the browser extension's pairing code, the settings profile.

Checks and repairs of the library are on «Диагностика»; the source folder is chosen on «Парсер»,
sites and keys on «Сайты».
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from webui.html import esc, human_size, page_frame
from webui.server import Request, Response
from webui.settings_form import Field, FieldError, Section, clean, render

_LOG = logging.getLogger("local_booru.webui")

KIND = "settings"
IMPORT_WAIT = 30.0             # seconds the page waits for the program window to load a profile


def register(router) -> None:
    router.add("GET", r"/settings", settings_page)
    router.add("POST", r"/api/settings/save", settings_save)
    router.add("POST", r"/api/settings/act", settings_act)
    router.add("GET", r"/api/settings/profile\.zip", profile_export)
    router.add("GET", r"/api/settings/diagnostics\.zip", diagnostics_export)
    router.add("POST", r"/api/settings/profile", profile_import)
    router.add("GET", r"/api/settings/move/state", move_state)


def _prefs() -> dict:
    from webui import prefs
    stored = prefs.load()
    return {"thumbs": stored.get("thumbs", "m")}         # the look is the page's own (page_frame)


# ── the sections ───────────────────────────────────────────────────────────
# Bounds and choices came from the old settings page (ui/settings_page.py, gone in v5.40).
WINDOW = Section("window", "Окно программы",
                 lead="Страницы программы показывает её окно. Если на этом компьютере окно их не показывает (белое "
                      "или закрывается само), страницы можно открывать в обычном браузере: тогда окно программы "
                      "маленькое — оно держит программу запущенной.",
                 fields=(
    Field("ui_shell", "choice", "Открывать", default="web",
          choices=(("web", "в окне программы"), ("browser", "в браузере")), hint="со следующего запуска программы"),
    Field("webview_gpu", "choice", "Рисовать страницы", default="auto",
          choices=(("auto", "видеокартой — быстрее"), ("off", "без видеокарты")),
          hint="«без видеокарты» — если картинки показываются полосами или кусками старой; со следующего запуска"),
))

LIBRARY = Section("library", "Новые файлы и корзина", fields=(
    Field("imports_to_inbox", "bool", "Новые файлы сначала показывать в разделе «Новые»",
          "скачанное и найденное парсером; потом они уходят в общий список сами", default=True),
    Field("inbox_auto_archive_hours", "int", "Сколько держать в «Новых»", default=24, lo=1, hi=24 * 365, suffix="ч"),
    Field("deleted_reimport_policy", "choice", "Если снова попался удалённый файл", default="skip",
          choices=(("skip", "не брать — корзина его держит"), ("return_inbox", "вернуть в «Новые»"))),
    Field("trash_auto_purge_days", "choice", "Очищать корзину", default=0,
          choices=((0, "только вручную"), (7, "через 7 дней"), (30, "через 30 дней")),
          hint="что лежит в корзине дольше, удаляется навсегда — при запуске программы и раз в 5 минут, пока она открыта"),
))

TAGS = Section("tags", "Теги в списках",
               lead="Скрытые теги остаются в базе и находятся поиском — их просто нет в списках тегов галереи.",
               fields=(
    Field("hide_single_char_tags", "bool", "Не показывать теги из одного знака", default=True),
    Field("hide_technical_tags", "bool", "Не показывать служебные теги", "tagme, md5:…, source:…, uploaded_by_…",
          default=True),
    Field("hide_meta_tags", "bool", "Не показывать мета-теги", default=False),
    Field("hide_rating_tags", "bool", "Не показывать теги рейтинга", default=False),
    Field("ignore_numeric_tags", "bool", "Не записывать теги из одних цифр",
          "для новых тегов; уже записанные остаются", default=False),
))

BACKUP = Section("backup", "Резервные копии",
                 lead="Лёгкая копия — база, настройки и ручные пометки в одном ZIP; картинки и кэш не копируются. "
                      "Надёжнее всего держать копии на другом диске.",
                 fields=(
    Field("light_backup_enabled", "bool", "Делать копии при закрытии программы", default=False),
    Field("light_backup_include_cookies", "bool", "Вместе с cookies сайтов", "иначе после восстановления войти на сайты заново",
          default=False),
    Field("light_backup_interval_hours", "int", "Не чаще, чем раз в", default=24, lo=1, hi=24 * 7, suffix="ч"),
    Field("light_backup_keep_last", "int", "Хранить последних", default=10, lo=1, hi=100, suffix="копий"),
    Field("light_backup_dir", "path", "Папка для копий", path_kind="folder",
          placeholder="пусто — папка резервных копий архива"),
))

GALLERY = Section("gallery", "Галерея", fields=(
    Field("webui_posts_per_page", "choice", "Картинок на странице", default=0,
          choices=((0, "сколько помещается в окно"), (20, "20"), (42, "42"), (60, "60"), (100, "100"), (200, "200")),
          hint="«сколько помещается в окно» — ряды превью до низа окна без прокрутки; «мелкие / средние / крупные» "
               "меняют и число"),
    Field("card_sets_tiles", "bool", "Сборка в галерее — одной плиткой",
          "все её карточки листаются на странице карточки; в корзине и Браке карточки всегда по одной", default=True),
    Field("post_sets_enabled", "bool", "Сборка над картинкой карточки",
          "зелёной плашкой, как на Danbooru: превью всей сборки по порядку; там же «изменить»", default=True),
    Field("card_sets_auto", "bool", "Собирать сборки самой",
          "из скачанного: родитель и дочерние посты, страницы одной работы (pixiv, твит, Patreon…), серии одного "
          "загрузившего и комиксы; сборки, которые вы меняли или собрали сами, она не трогает",
          default=True),
    Field("card_sets_ask_sites", "bool", "Узнать у сайтов про уже скачанное",
          "один раз, в фоне, пачками: кто залил пост и его родитель — для карточек, скачанных до версии 5.21; "
          "ответ запоминается навсегда", default=True),
    Field("card_sets_remote", "bool", "Недостающие части сборки — с сайта",
          "у сборок «родитель и дочерние», «одна работа» и «серия»: части, которых нет в библиотеке, — превью с "
          "сайта с пометкой «api» (серию программа ищет среди постов того же загрузившего, работу — по ссылке на "
          "источник); на странице такой части — «Скачать». У карточки, которая в библиотеке одна, — родитель и "
          "дочерние её поста на сайте (страница карточки спрашивает сайт)", default=True),
))

VIDEOS = Section("videos", "Вкладка «Видео»",
                 lead="Длинные видео библиотеки показываются на вкладке «Видео», а не в галерее (в корзине и Браке "
                      "видны все). Сериалы на ней — из папки видео: одна папка — один сериал, файлы в ней — серии.",
                 fields=(
    Field("video_tab_seconds", "choice", "Видео длиннее", default=60,
          choices=((0, "не переносить — все видео в галерее"), (30, "30 секунд"), (60, "1 минуты"),
                   (120, "2 минут"), (300, "5 минут"), (600, "10 минут")),
          hint="такие видео — на вкладке «Видео»; длину видео из библиотеки программа узнаёт сама, в фоне"),
    Field("video_source_dubs", "bool", "Все озвучки тайтлов с YummyAnime",
          "«Скачать с сайта»: озвучки и субтитры тайтла из плееров Kodik (до 720p) и CVH (до 1080p) — каждая своей "
          "вкладкой сериала; ссылки на видео программа берёт перед скачиванием каждой серии. Выключено — серии с "
          "AniLiberty (строка ниже)",
          default=True),
    Field("video_dub_merge", "bool", "Собирать озвучки серии в один файл",
          "после «Скачать» озвучки каждой серии становятся дорожками звука одного MKV: картинка одна, озвучки "
          "переключаются в плеере; сдвиги и вставки студий программа находит по кадрам. Отдельные файлы озвучек "
          "удаляются, когда общий файл проверен. Галочка на странице «Скачать с сайта» — для одного скачивания",
          default=True),
    Field("yummy_api_url", "text", "Адрес API YummyAnime", default="https://api.yani.tv",
          placeholder="https://api.yani.tv", hint="если сайт переедет, впишите новый адрес его API"),
    Field("video_source_aniliberty", "bool", "Серии тайтлов с YummyAnime — с AniLiberty",
          "«Скачать с сайта»: тот же тайтл программа найдёт на AniLiberty по номерам Shikimori и MyAnimeList (одна "
          "озвучка — AniLiberty), а описание и постер возьмёт с YummyAnime. Работает, когда озвучки выше выключены "
          "или YummyAnime их не отдал",
          default=True),
    Field("aniliberty_url", "text", "Адрес AniLiberty", default="https://aniliberty.top",
          placeholder="https://aniliberty.top",
          hint="если сайт переедет, впишите новый адрес; anilibria.top — его зеркало"),
))

THUMBS = Section("thumbs", "Превью", fields=(
    Field("thumb_cleanup_on_exit", "bool", "Подчищать кэш превью при закрытии",
          "старые превью построятся снова, когда понадобятся", default=True),
    Field("thumb_keep_recent", "int", "Оставлять последних", default=500, lo=50, hi=10_000, suffix="превью"),
))

DOWNLOAD = Section("download", "Скачивание", fields=(
    Field("session_media_downloads_default", "bool", "Скачивать новые файлы",
          "подписки, граббер, расширение; выключено — программа работает только с тем, что уже есть", default=True),
    Field("session_media_downloads_prompt", "bool", "Спрашивать об этом при каждом запуске",
          "вопрос «Разрешить скачивание новых медиа в этой сессии?»", default=False),
    Field("large_download_warning_count", "int", "Спрашивать, если за раз больше", default=1000, lo=1, hi=1_000_000,
          suffix="файлов", hint="подписки и граббер"),
    Field("disk_free_reserve_gb", "float", "Оставлять свободными на диске", default=2.0, lo=0, hi=1000,
          suffix="ГБ", hint="загрузка и копии не займут последнее место; 0 — без запаса"),
    Field("grabber_cache_limit_mb", "int", "Кэш картинок граббера", default=200, lo=0, hi=10_000, suffix="МБ",
          hint="0 — по умолчанию, 200 МБ"),
))

COMPANION = Section("companion", "Браузер и расширение",
                    lead="Ссылки на сайты из окна программы открываются в браузере. Расширение Local Booru Companion "
                         "в этом браузере передаёт программе то, что вы смотрите на сайтах, — его нужно один раз подключить кодом.",
                    fields=(
    Field("external_browser_mode", "choice", "Ссылки открывать в", default="default",
          choices=(("default", "браузере Windows по умолчанию"), ("custom", "браузере, выбранном ниже"))),
    Field("external_browser_executable", "path", "Файл браузера", path_kind="file", placeholder="…\\chrome.exe",
          hint="только для «выбранного ниже»"),
))

DATABASE = Section("db", "База",
                   lead="Как программа обращается со своей базой SQLite. Копии, проверка и обслуживание базы — на "
                        "«Диагностике».",
                   fields=(
    Field("sqlite_cache_mb", "int", "Кэш SQLite", default=40, lo=8, hi=512, suffix="МБ",
          hint="на каждое соединение с базой: больше — быстрее большие запросы, но больше памяти; после перезапуска"),
    Field("sqlite_checkpoint_on_exit", "bool", "При закрытии переносить журнал WAL в базу",
          "файл журнала рядом с базой не растёт от запуска к запуску", default=True),
    Field("sqlite_idle_checkpoint_enabled", "bool", "Переносить журнал WAL и в простое",
          "когда журнал больше порога ниже и программа ничего не делает; никого не ждёт и не держит", default=True),
    Field("sqlite_idle_checkpoint_threshold_mb", "int", "Порог журнала WAL", default=64, lo=16, hi=4096, suffix="МБ"),
    Field("sqlite_idle_checkpoint_interval_seconds", "choice", "Проверять журнал не чаще", default=300,
          choices=((60, "раза в минуту"), (120, "раза в 2 минуты"), (300, "раза в 5 минут"), (600, "раза в 10 минут"),
                   (900, "раза в 15 минут"), (1800, "раза в 30 минут"), (3600, "раза в час"))),
    Field("derived_cache_external_enabled", "bool", "Дерево похожих и категории тегов — в отдельном файле",
          "рядом с базой, а не в ней; после смены пересоберите оба на «Диагностика → Обслуживание»", default=False),
))

def _visual_section() -> Section:
    from webui.pages import visual
    return Section("visual", "Брак: фото или рисунок",
                   lead="Модель смотрит на файлы Брака и отмечает, настоящее это фото или рисунок (3D и картинки "
                        "нейросетей — тоже рисунки), — для фильтра «фото / рисунок» в разделе «Брак». Работает на "
                        "этом компьютере, картинки никуда не отправляются. Свою отметку можно поставить на "
                        "странице карточки Брака — она важнее модели.",
                   fields=visual.fields())


VISUAL = _visual_section()

DEVELOPER = Section("dev", "Для разработчика", fields=(
    Field("performance_slow_ms", "int", "Записывать операции дольше", default=100, lo=25, hi=10_000, suffix="мс",
          hint="«Медленные операции» на странице «Диагностика»"),
))

SECTIONS = (WINDOW, GALLERY, VIDEOS, LIBRARY, TAGS, BACKUP, THUMBS, DOWNLOAD, COMPANION, VISUAL, DATABASE, DEVELOPER)
BY_ID = {s.id: s for s in SECTIONS}


def _looks_section() -> Section:
    """The look of each page of the menu — kept with the page's own choices (``webui/prefs.py``), not in the
    settings; the menu «Оформление» of a page changes the same choice."""
    from webui import prefs
    from webui.html import PAGE_NAMES
    from webui.skins import SKINS
    looks = tuple((s.id, s.name) for s in SKINS)
    fields = []
    for page, (key, default) in prefs.PAGE_LOOKS.items():
        gallery = key == "skin"
        fields.append(Field(key, "choice", PAGE_NAMES[page], hint="его берут страницы «как у галереи»" if gallery else "",
                            choices=looks if gallery else ((prefs.FOLLOW, "как у галереи"), *looks),
                            default=default if default is not None else prefs.FOLLOW))
    return Section("looks", "Оформление страниц",
                   lead="У каждой страницы меню — своё оформление. Меню «Оформление» на самой странице меняет "
                        "только её; там же — «Это оформление — на всех страницах».", fields=tuple(fields))


LOOKS = _looks_section()


def _check(section: Section, changes: dict, settings: dict) -> None:
    """Checks a value cannot make by itself: the copies never go into the source archive (it only
    reads there — ``light_backup.backup_folder_problem``, the same rule as the window's dialog and
    the copy itself), and a folder is a full path."""
    folder = str(changes.get("light_backup_dir") or "")
    if section.id == "backup" and folder:
        from core.light_backup import backup_folder_problem
        item = next(f for f in section.fields if f.key == "light_backup_dir")
        if not Path(folder).is_absolute():
            raise FieldError(item, "нужен полный путь к папке")
        problem = backup_folder_problem(settings, folder)
        if problem:
            raise FieldError(item, problem)


def _after_save(section: Section, changes: dict) -> dict:
    """Settings that follow from others."""
    if section.id == "backup" and "light_backup_enabled" in changes:
        changes["light_backup_on_exit"] = changes["light_backup_enabled"]   # the closing is the only moment it is made
    return changes


# ── extras of the sections (actions) ───────────────────────────────────────
def _backup_extra(settings: dict) -> str:
    from core.light_backup import _backup_root
    last = int(settings.get("light_backup_last_at") or 0)
    when = time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "ещё не было"
    return (f'<p class="hint">Последняя копия: {esc(when)}. Куда: <span class="tool-path">{esc(_backup_root(settings))}</span></p>'
            '<div class="tool-actions"><button type="button" data-action="settings-act" data-act="light_backup">'
            'Сделать копию сейчас</button><span class="action-status" role="status"></span></div>')


def _thumbs_extra(_settings: dict) -> str:
    from core.library_lifecycle import folder_size
    from core.paths import CACHE_DIR
    size = folder_size(Path(CACHE_DIR) / "thumbs")
    return (f'<p class="hint">Кэш превью сейчас: {esc(human_size(size))}.</p>'
            '<div class="tool-actions"><button type="button" data-action="settings-act" data-act="thumbs_clear" '
            'data-ask="Превью построятся заново — нажмите ещё раз">Очистить кэш превью</button>'
            '<span class="action-status" role="status"></span></div>')


def _companion_extra(settings: dict) -> str:
    try:
        from core.browser_pairing import list_extensions
        paired = list_extensions(settings)
    except Exception as exc:                       # an old base without the table: shown, not fatal
        _LOG.warning("settings page: paired extensions not read: %s: %s", type(exc).__name__, exc)
        paired = []
        note = f'<p class="tool-warn">Список подключённых расширений не прочитан: {esc(type(exc).__name__)}</p>'
    else:
        note = ""
    paired = [p for p in paired if isinstance(p, dict) and not int(p.get("revoked_at") or 0)]
    rows = "".join(f'<li>{esc(p.get("display_name") or "Companion")} <small>подключено {esc(_day(p.get("created_at")))}, '
                   f'последний раз {esc(_day(p.get("last_seen_at")) or "—")}</small></li>' for p in paired)
    listed = (f'<p class="hint">Подключено расширений: {len(paired)}</p><ul class="diag-files">{rows}</ul>'
              if paired else '<p class="hint">Расширение ещё не подключено.</p>')
    return (f'{note}{listed}<div class="tool-actions">'
            '<button type="button" data-action="settings-act" data-act="pair_code" data-out="#pair-code">'
            'Код для подключения расширения</button>'
            '<button type="button" class="danger" data-action="settings-act" data-act="pair_revoke" '
            'data-ask="Отключить все расширения? Нажмите ещё раз">Отключить все</button>'
            '<span class="action-status" role="status"></span></div>'
            '<p class="setting-out" id="pair-code" hidden></p>'
            '<p class="hint">Код действует 10 минут: введите его в окне расширения.</p>')


def _day(ts) -> str:
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts > 0 else ""


def _visual_extra(settings: dict) -> str:
    from webui.pages import visual
    return visual.extra(settings)


def _gallery_extra(settings: dict) -> str:
    """How the сборки stand: how many, what the sites told, what is left to ask; «Собрать сборки заново»."""
    from core import site_posts
    from core.database.connection import db
    try:
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT COUNT(*), COALESCE(SUM(locked),0) FROM card_sets").fetchone()
            sets, mine = int(row[0] or 0), int(row[1] or 0)
    except Exception as exc:                       # an old base: said, not fatal
        _LOG.debug("settings page: сборки not counted: %s", exc)
        sets, mine = 0, 0
    st = site_posts.status(settings)
    asked = f"сайты рассказали о {st['known']} постах" + (f" (нет на сайте: {st['gone']})" if st["gone"] else "")
    if st["waiting"]:
        busy = st.get("busy") or ""
        asked += f"; ещё спросить: {st['waiting']}" + (
            "" if not busy else f" (ждёт: {busy})" if busy == site_posts.MAINTENANCE else f" (медленнее, пока {busy})")
    elif not st["scanned"] and site_posts.enabled(settings):
        asked += "; что ещё спросить — программа посмотрит в фоне"
    notes = "".join(f"<li>{esc(host)}: {esc(note)}</li>" for host, note in sorted(st["notes"].items()))
    notes = f'<ul class="diag-files">{notes}</ul>' if notes else ""
    return (f'<p class="hint">Сборок: {sets}' + (f", из них ваших (собраны или изменены вами): {mine}" if mine else "")
            + f'. Про скачанное {esc(asked)}.</p>{notes}'
            '<div class="tool-actions"><button type="button" data-action="settings-act" data-act="card_sets_rebuild">'
            'Собрать сборки заново</button><span class="action-status" role="status"></span></div>')


EXTRAS = {"backup": _backup_extra, "thumbs": _thumbs_extra, "companion": _companion_extra, "visual": _visual_extra,
          "gallery": _gallery_extra}


def _move_html() -> str:
    """(v5.36) «Перенести архив на другой диск»: the button; a move under way (the copy, its state, «Продолжить»,
    «Отменить», «Перезапустить и закончить перенос»); after a move — «Удалить старую копию»; the task's journal."""
    from core import archive_move, page_jobs
    order = archive_move.pending()
    task = page_jobs.current(archive_move.KIND)
    busy = task is not None and task.running
    stop = ('<button type="button" class="danger" data-action="settings-act" data-act="move_stop"'
            f'{"" if busy else " hidden"}>Остановить</button>')
    if order is None:
        what = ('<button type="button" data-action="settings-move">Перенести на другой диск…</button>'
                '<span class="action-status" role="status"></span>')
        state = ""
    else:
        target = esc(order.get("target") or "")
        cancel = ('<button type="button" data-action="settings-act" data-act="move_cancel" data-ask="Перенос '
                  'отменится, сделанная копия удалится — нажмите ещё раз">Отменить перенос</button>')
        if order.get("phase") == archive_move.COPIED:
            state = (f'Копия готова: <span class="tool-path">{target}</span><br>Перезапустите программу: при запуске '
                     'она докопирует изменённое, перенесёт базу и откроет архив на новом месте.')
            what = ('<button type="button" class="primary" data-action="settings-restart" data-ask="Программа '
                    'закроется и откроется снова — нажмите ещё раз">Перезапустить и закончить перенос</button>'
                    f'{cancel}')
        else:
            copied = (f'<br>Скопировано {esc(archive_move.human(order.get("bytes") or 0))} из '
                      f'{esc(archive_move.human(order.get("total_bytes") or 0))}' if order.get("total_bytes") else "")
            state = f'Копия делается в <span class="tool-path">{target}</span>{copied}'
            what = ('<button type="button" data-action="settings-act" data-act="move_continue"'
                    f'{" hidden" if busy else ""}>Продолжить копирование</button>{cancel}')
        what += f'{stop}<span class="action-status" role="status"></span>'
        if order.get("error"):
            state += f'<br><span class="bad">Не получилось: {esc(order["error"])}</span>'
        state = f'<span class="move-state">{state}</span>'
    last = archive_move.last_move()
    old = ""
    if last and last.get("old_exists"):
        when = time.strftime("%d.%m.%Y", time.localtime(float(last.get("at") or 0)))
        old = (f'<span class="tool-label">Старая копия</span><span class="tool-value"><span class="tool-path">'
               f'{esc(last["from"])}</span> <small class="muted">архив перенесён отсюда {esc(when)}; программа её '
               'больше не открывает</small> <span class="tool-actions"><button type="button" class="danger" '
               'data-action="settings-act" data-act="move_delete_old" data-ask="Все файлы старой копии удалятся '
               'насовсем, не в корзину — нажмите ещё раз">Удалить старую копию</button>'
               f'{stop if order is None else ""}<span class="action-status" role="status"></span></span></span>')
    job = ('<div class="job move-job" data-job data-move-job hidden><div class="job-head"><b class="job-title"></b> '
           '<span class="job-status"></span></div><pre class="job-log" tabindex="0" aria-label="Журнал переноса">'
           '</pre></div>')
    return (f'<span class="tool-label">Перенос архива</span><span class="tool-value">{state}'
            f'<span class="tool-actions">{what}</span></span>{old}'
            f'<span class="tool-label"></span><span class="tool-value">{job}</span>')


def move_state(req: Request) -> Response:
    """The page task of the archive's move (the copy, its cancel, the old copy's removal) for the page."""
    from core import archive_move, page_jobs
    task = page_jobs.current(archive_move.KIND)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    return Response.json({"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))})


def _archive_html(settings: dict) -> tuple[str, str]:
    """(the rows of the archive open now and of the one the next start opens — when another was chosen or one is to be
    made from «Документов» —, how to move it or make it)."""
    from core import archive_create, archive_switch
    from core.paths import DATA_DIR
    nxt = archive_switch.pending()
    move = later = create = ""
    if archive_switch.portable():
        where = (f'<span class="tool-path">{esc(archive_switch.open_archive())}</span> '
                 f'<small class="muted">база и настройки — в {esc(DATA_DIR.name)}, файлы библиотеки — в output</small>')
        label = "Подключить другой архив…"
        move = _move_html()
        how = ('<p class="hint">«Перенести на другой диск…» — программа скопирует весь архив (файлы библиотеки, базу, '
               'настройки, кэш, журналы, копии базы), пока вы работаете, а при следующем запуске перенесёт базу и '
               'откроет архив на новом месте; старый архив остаётся как был, пока вы его не удалите. «Подключить '
               'другой архив…» — для архива, который уже лежит в другом месте.</p>')
    else:
        where = (f'<span class="tool-path">{esc(DATA_DIR)}</span> <small class="muted">в «Документах»: архива ещё '
                 'нет</small>')
        label = "Подключить существующий архив…"
        archive = archive_create.target(settings).parent
        order = archive_create.pending()
        if order is None:
            create = ('<button type="button" data-action="settings-act" data-act="archive_create" data-ask="Архив '
                      'создастся при следующем запуске — нажмите ещё раз">Создать архив</button>')
        else:
            failed = (f'<br><span class="bad">В прошлый раз не получилось: {esc(order["error"])}</span>'
                      if order.get("error") else "")
            later = (f'<span class="tool-label">При следующем запуске</span><span class="tool-value">создать архив '
                     f'<span class="tool-path">{esc(order.get("archive") or archive)}</span> <small class="muted">'
                     f'перезапустите программу: при запуске она скопирует в него базу, настройки и кэш</small>{failed}'
                     ' <span class="tool-actions"><button type="button" class="primary" data-action="settings-restart" '
                     'data-ask="Программа закроется и откроется снова — нажмите ещё раз">Перезапустить и создать '
                     'архив</button><button type="button" data-action="settings-act" data-act="archive_create_cancel">'
                     'Не создавать</button><span class="action-status" role="status"></span></span></span>')
        how = ('<p class="hint">Архив — одна папка Local_Booru_Archive с базой, настройками и файлами библиотеки: его '
               'можно держать на любом диске, перенести целиком на другой и подключить на другом компьютере. «Создать '
               'архив» — при следующем запуске программа скопирует базу, настройки, кэш и журналы из «Документов» в '
               f'<span class="tool-path">{esc(archive)}</span>, рядом с файлами библиотеки, и откроется с ним; папка '
               'в «Документах» останется как была. «Подключить существующий архив…» — для архива, который уже '
               'есть.</p>')
    if nxt is not None:
        later = (f'<span class="tool-label">При следующем запуске</span><span class="tool-value">'
                 f'<span class="tool-path">{esc(nxt.parent)}</span> <small class="muted">перезапустите программу; '
                 'до этого всё сохраняется в открытый архив</small> <span class="tool-actions">'
                 '<button type="button" data-action="settings-act" data-act="archive_stay">Остаться на открытом</button>'
                 '<span class="action-status" role="status"></span></span></span>')
    return (f'<span class="tool-label">Архив</span><span class="tool-value">{where} <span class="tool-actions">{create}'
            f'<button type="button" data-action="settings-archive">{esc(label)}</button>'
            f'<span class="action-status" role="status"></span></span></span>{later}{move}', how)


def _folders_html(settings: dict) -> str:
    from core.storage_roots import configured_output_root       # shows the folder, does not create it
    root = str(settings.get("root") or "")
    manga = str(settings.get("manga_root") or "")
    from core.video_library import video_root
    video = video_root(settings)
    video_note = "" if str(settings.get("video_root") or "").strip() else ' <small class="muted">по умолчанию — в архиве</small>'
    archive, how = _archive_html(settings)
    return f"""<section class="settings-block" id="set-folders"><h2>Папки</h2>
  <div class="tool-grid">
    {archive}
    <span class="tool-label">Рабочая библиотека</span><span class="tool-value"><span class="tool-path">{esc(configured_output_root(settings))}</span></span>
    <span class="tool-label">Исходная папка парсера</span><span class="tool-value"><span class="tool-path">{esc(root or "не выбрана")}</span>
      <a href="/parser">изменить на странице «Парсер»</a></span>
    <span class="tool-label">Папка манги</span><span class="tool-value"><span class="tool-path">{esc(manga or "не выбрана")}</span>
      <span class="tool-actions"><button type="button" data-action="settings-choose" data-key="manga_root">Выбрать…</button>
      <span class="action-status" role="status"></span></span></span>
    <span class="tool-label">Папка видео</span><span class="tool-value"><span class="tool-path">{esc(video)}</span>{video_note}
      <span class="tool-actions"><button type="button" data-action="settings-choose" data-key="video_root">Выбрать…</button>
      <span class="action-status" role="status"></span></span></span>
  </div>
  {how}
  <p class="hint">Исходная папка только читается: программа копирует из неё, но ничего в ней не меняет.</p>
</section>"""


def _logs_html() -> str:
    from core.paths import LOGS_DIR
    return f"""<section class="settings-block" id="set-logs"><h2>Журналы и отчёт об ошибке</h2>
  <p class="hint">Журналы программы: <span class="tool-path">{esc(LOGS_DIR)}</span></p>
  <div class="tool-actions">
    <button type="button" data-action="settings-act" data-act="open_logs">Открыть папку журналов</button>
    <a class="button" href="/api/settings/diagnostics.zip" download>Собрать отчёт об ошибке</a>
    <span class="action-status" role="status"></span>
  </div>
  <p class="hint">Отчёт — ZIP с журналами, настройками и сведениями о библиотеке, чтобы показать, что пошло не так.
  Ключи, логины, cookies и адреса с ключами из него убраны; картинок и базы в нём нет.</p>
</section>"""


def _profile_html() -> str:
    return """<section class="settings-block" id="set-profile"><h2>Профиль настроек</h2>
  <p class="hint">Все настройки одним файлом — чтобы перенести их на другой компьютер. База и картинки в него не входят.</p>
  <div class="tool-actions">
    <a class="button" href="/api/settings/profile.zip" download>Скачать профиль</a>
    <a class="button" href="/api/settings/profile.zip?secrets=1" download>Скачать с ключами и логинами</a>
  </div>
  <form class="settings-form" data-form="profile-import">
    <div class="field-row"><label for="profile-file">Загрузить профиль</label>
      <input type="file" id="profile-file" name="profile" accept=".zip,.json,application/zip,application/json"></div>
    <div class="tool-actions"><button type="submit" data-ask="Настройки заменятся настройками из файла — нажмите ещё раз">Загрузить</button>
      <span class="action-status" role="status"></span></div>
    <p class="hint">Профиль — ZIP отсюда или из прежних версий программы (подойдёт и app_settings.json). Перед загрузкой
    текущие настройки сохраняются копией; ключи, которых нет в файле, и папки этого компьютера остаются прежними.</p>
  </form>
</section>"""


# The tabs of the page (like the old settings page's tab strip): one part shown at a time.  A section's anchor
# (#set-…) opens its tab; links from other pages keep working.
TABS = (
    ("main", "Основное", "Как выглядит программа: какое окно открывается, оформление страниц, что видно в списках "
     "тегов, превью.", ("window", "looks", "gallery", "videos", "tags", "thumbs")),
    ("library", "Библиотека", "Где лежат данные, что делается с новыми и удалёнными файлами, резервные копии.",
     ("folders", "library", "backup")),
    ("download", "Скачивание и браузер", "Граббер, подписки и расширение браузера.", ("download", "companion")),
    ("visual", "Брак: фото или рисунок", "Модель, которая отличает фото от рисунка в Браке.", ("visual",)),
    ("db", "База и журналы", "Как программа обращается со своей базой, журналы и отчёт об ошибке.",
     ("db", "logs", "dev")),
    ("profile", "Профиль", "Все настройки одним файлом — перенести на другой компьютер.", ("profile",)),
)


DUBS_FIELDS = ("video_source_dubs", "video_dub_merge", "yummy_api_url")


def section_fields(section: Section) -> tuple:
    """The fields the page shows.  The public copy of the program has no voice-overs from the sites' players (its
    ``core/video_dubs`` is a stand-in with ``AVAILABLE = False``, ``publish/core/video_dubs.py``): their fields go."""
    if section.id != "videos":
        return section.fields
    from core import video_dubs
    if getattr(video_dubs, "AVAILABLE", True):
        return section.fields
    return tuple(f for f in section.fields if f.key not in DUBS_FIELDS)


def settings_page(req: Request) -> Response:
    s = req.settings
    blocks = {}
    for section in SECTIONS:
        extra = EXTRAS.get(section.id)
        fields = section_fields(section)
        if extra is not None or fields != section.fields:
            section = Section(section.id, section.title, section.lead, fields, extra(s) if extra else section.extra,
                              section.note, section.danger)
        blocks[section.id] = render(section, s)
    from webui import prefs
    blocks.update({"folders": _folders_html(s), "logs": _logs_html(), "profile": _profile_html(),
                   "looks": render(LOOKS, prefs.choices())})
    tabs, panes = [], []
    for tab_id, label, lead, parts in TABS:
        tabs.append(f'<button type="button" role="tab" id="tab-{tab_id}" aria-controls="pane-{tab_id}" '
                    f'aria-selected="false" tabindex="-1" data-tab="{tab_id}">{esc(label)}</button>')
        panes.append(f'<div class="settings-pane" id="pane-{tab_id}" role="tabpanel" aria-labelledby="tab-{tab_id}" '
                     f'data-tab="{tab_id}"><p class="pane-lead">{esc(lead)}</p>'
                     + "".join(blocks.pop(part) for part in parts) + "</div>")
    if blocks:                                       # a section no tab names: shown, never lost
        panes.append('<div class="settings-pane" data-tab="other">' + "".join(blocks.values()) + "</div>")
    body = f"""
<section class="tool settings settings-page" id="settings">
  <header class="tool-head">
    <h1>Настройки</h1>
    <p class="tool-lead">Что программа делает сама и где лежат её данные. Сайты и ключи — на странице «Сайты»,
    проверка и починка библиотеки — на «Диагностике».</p>
  </header>
  <nav class="settings-tabs" role="tablist" aria-label="Разделы настроек" hidden>{"".join(tabs)}</nav>
  {"".join(panes)}
  <div class="settings-savebar" role="region" aria-label="Несохранённые изменения" hidden>
    <p class="savebar-what"><span data-bar-text>Есть несохранённые изменения</span><span data-dirty-list></span></p>
    <div class="tool-actions">
      <button type="button" class="primary" data-action="settings-save-all">Сохранить</button>
      <button type="button" data-action="settings-revert">Отменить изменения</button>
      <span class="action-status" role="status"></span>
    </div>
  </div>
</section>"""
    return Response.html(page_frame("Настройки", body, active="settings", subnav=False, kind="tool",
                                    body_data={"tool": "settings"}, **_prefs()))


# ── saving ─────────────────────────────────────────────────────────────────
def settings_save(req: Request) -> Response:
    from webui import settings_store
    body = req.json()
    if str(body.get("section") or "") == LOOKS.id:
        return _save_looks(body)
    section = BY_ID.get(str(body.get("section") or ""))
    if section is None or not section.fields:
        return Response.json({"ok": False, "error": "Нет такого раздела"}, 400)
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    try:
        changes = clean(section, values, req.settings)
        _check(section, changes, req.settings)
        changes = _after_save(section, changes)
    except FieldError as exc:
        return Response.json({"ok": False, "error": str(exc), "field": exc.field.key}, 400)
    if not settings_store.save(changes):
        return Response.json({"ok": False, "error": "Окно программы не ответило — не сохранено"}, 503)
    return Response.json({"ok": True, "message": "Сохранено"})


def _save_looks(body: dict) -> Response:
    """«Оформление страниц»: the choices go where the menu «Оформление» keeps them; this page's own look changed —
    the page is shown again in it."""
    from webui import prefs
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    before = prefs.look("settings")["skin"]
    try:
        changes = clean(LOOKS, values, prefs.choices())
    except FieldError as exc:
        return Response.json({"ok": False, "error": str(exc), "field": exc.field.key}, 400)
    prefs.save(changes)
    return Response.json({"ok": True, "message": "Сохранено", "reload": prefs.look("settings")["skin"] != before})


# ── actions ────────────────────────────────────────────────────────────────
def settings_act(req: Request) -> Response:
    act = str(req.json().get("act") or "")
    s = req.settings
    if act == "light_backup":
        from core.light_backup import create_light_backup
        result = dict(create_light_backup(s, reason="manual", force=True) or {})
        if not result.get("created"):
            from core.redaction import sanitize_text
            return Response.json({"ok": False, "error": "Копия не сделана: "
                                  + sanitize_text(str(result.get("error") or "неизвестно почему"))}, 409)
        from webui import settings_store
        settings_store.save({"light_backup_last_at": int(time.time())})
        return Response.json({"ok": True, "message": f"Копия: {result.get('path') or result.get('zip') or ''}", "reload": True})
    if act == "thumbs_clear":
        from core.library_lifecycle import clear_thumbnail_cache
        result = clear_thumbnail_cache(s)
        busy = f"; занято сейчас и оставлено: {result['busy']}" if result.get("busy") else ""
        return Response.json({"ok": True, "message": f"Освобождено {human_size(int(result.get('bytes') or 0))}{busy}",
                              "reload": True})
    if act == "pair_code":
        from core.browser_pairing import create_pairing_code
        code = create_pairing_code(s, display_name="Local Booru Companion")
        return Response.json({"ok": True, "message": "Код готов", "show": code})
    if act == "pair_revoke":
        from core.browser_pairing import revoke_all
        count = revoke_all(s)
        return Response.json({"ok": True, "message": f"Отключено: {int(count or 0)}", "reload": True})
    if act == "card_sets_rebuild":
        from core import card_sets
        from core.nomatch_manual import library_busy
        busy = library_busy()
        if busy:
            return Response.json({"ok": False, "error": busy}, 409)
        if not card_sets.auto_enabled(s):
            return Response.json({"ok": False, "error": "«Собирать сборки самой» выключено"}, 409)
        result = card_sets.rebuild(s, force=True)
        if result.get("skipped"):
            return Response.json({"ok": False, "error": "Сборки сейчас уже собираются — попробуйте через минуту"}, 409)
        return Response.json({"ok": True, "reload": True,
                              "message": f"Готово: сборок {result['sets']}, новых {result['created']}, "
                                         f"ушло {result['removed']}"})
    if act in ("move_continue", "move_stop", "move_cancel", "move_delete_old"):
        return _move_act(act, s)
    if act == "archive_stay":
        from core import archive_switch
        archive_switch.stay()
        return Response.json({"ok": True, "message": "Следующий запуск откроет этот архив", "reload": True})
    if act == "archive_create":
        from core import archive_create
        result = archive_create.request(s)
        if not result.get("ok"):
            return Response.json({"ok": False, "error": result.get("error") or "Не получилось"}, 409)
        return Response.json({"ok": True, "reload": True,
                              "message": "Перезапустите программу: при запуске она создаст архив"})
    if act == "archive_create_cancel":
        from core import archive_create
        archive_create.cancel()
        return Response.json({"ok": True, "message": "Архив не создаётся", "reload": True})
    if act == "open_logs":
        from core.paths import LOGS_DIR
        from webui import desktop
        try:
            Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
            desktop.open_file(Path(LOGS_DIR))
        except OSError as exc:                     # no file manager on this computer, no access
            return Response.json({"ok": False, "error": f"Папка не открылась: {exc}"}, 409)
        return Response.json({"ok": True, "message": "Открыто"})
    return Response.json({"ok": False, "error": "Неизвестное действие"}, 400)


def _move_act(act: str, settings: dict) -> Response:
    """The buttons of «Перенос архива»: the copy goes on, stops, the move is taken back, the old copy is removed."""
    from core import archive_move, page_jobs
    task = page_jobs.current(archive_move.KIND)
    if act == "move_stop":
        if task is None or not task.running:
            return Response.json({"ok": False, "error": "Сейчас ничего не идёт"}, 409)
        task.stop()
        return Response.json({"ok": True, "message": "Останавливаю"})
    if task is not None and task.running:
        return Response.json({"ok": False, "error": f"Сейчас идёт: {task.label} — дождитесь или остановите"}, 409)
    if act == "move_continue":
        if archive_move.pending() is None:
            return Response.json({"ok": False, "error": "Перенос не заказан"}, 409)
        work, label = archive_move.copy_job, "Перенос архива"
    elif act == "move_cancel":
        if archive_move.pending() is None:
            return Response.json({"ok": False, "error": "Перенос не заказан"}, 409)
        work, label = archive_move.cancel_job, "Отмена переноса архива"
    else:
        record = archive_move.last_move()
        if not record or not record.get("old_exists"):
            return Response.json({"ok": False, "error": "Старой копии нет"}, 409)
        work, label = archive_move.delete_old_job(dict(settings)), "Удаление старой копии архива"
    try:
        started = page_jobs.start(archive_move.KIND, label, work)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "id": started.id, "message": "Запущено"})


# ── the settings profile ───────────────────────────────────────────────────
def _drop_temp(folder: Path) -> None:
    """A temporary folder of a profile, with whatever is in it."""
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except OSError as exc:                         # a file still held by something: the temp folder is the system's
        _LOG.warning("temporary profile folder %s not removed: %s", folder, exc)


def profile_export(req: Request) -> Response:
    import tempfile
    from core.settings_bundle import export_profile
    secrets = req.arg("secrets") == "1"
    folder = Path(tempfile.mkdtemp(prefix="lb_profile_"))
    try:
        data = Path(export_profile(req.settings, folder / "profile.zip", include_secrets=secrets)).read_bytes()
    finally:
        _drop_temp(folder)
    stamp = time.strftime("%Y%m%d_%H%M")
    name = f"Local_Booru_settings_{stamp}{'_with_keys' if secrets else ''}.zip"
    return Response(200, data, "application/zip", {"Content-Disposition": f'attachment; filename="{name}"'})


def diagnostics_export(req: Request) -> Response:
    """«Собрать отчёт об ошибке»: the ZIP of ``core.diagnostics`` (journals and settings without keys, logins,
    cookies and keyed addresses) as a download."""
    import tempfile
    from core.diagnostics import create_diagnostic_zip
    folder = Path(tempfile.mkdtemp(prefix="lb_diag_"))
    try:
        data = Path(create_diagnostic_zip(req.settings, folder / "diagnostics.zip")).read_bytes()
    finally:
        _drop_temp(folder)
    name = f"Local_Booru_diagnostics_{time.strftime('%Y%m%d_%H%M')}.zip"
    return Response(200, data, "application/zip", {"Content-Disposition": f'attachment; filename="{name}"'})


def _import_without_window(path: Path) -> dict:
    """``python -m webui`` alone: the profile over the settings file (under the page saving's lock)."""
    from core.redaction import sanitize_text
    from core.settings import load_settings
    from core.settings_bundle import import_profile
    from webui import settings_store
    try:
        with settings_store._FILE_LOCK:
            loaded = import_profile(path, load_settings(), apply=True)
    except Exception as exc:                       # nothing applied; the page hears why
        _LOG.warning("settings profile import failed: %s", sanitize_text(f"{type(exc).__name__}: {exc}"))
        return {"ok": False, "error": sanitize_text(str(exc)) or type(exc).__name__}
    return {"ok": True, "backup": loaded.get("backup") or "", "skipped": list(loaded.get("skipped") or [])}


def profile_import(req: Request) -> Response:
    """A profile file sent by the page (the bytes of the .zip): its settings over the current ones, a copy
    of the current settings first; the folders of this computer stay (``settings_bundle.LOCAL_KEYS``),
    values of another kind than the setting's are left out.  In the program window the window applies
    it (its settings are the ones in memory)."""
    import codecs
    import tempfile
    from core.redaction import sanitize_text
    from core.settings_bundle import ProfileError, read_profile
    data = req.body or b""
    if not data:
        return Response.json({"ok": False, "error": "Файл не выбран"}, 400)
    folder = Path(tempfile.mkdtemp(prefix="lb_profile_in_"))
    try:
        text_start = data[len(codecs.BOM_UTF8):] if data.startswith(codecs.BOM_UTF8) else data
        path = folder / ("profile.json" if text_start.lstrip()[:1] == b"{" else "profile.zip")
        path.write_bytes(data)
        try:
            read_profile(path)                     # a file that is not a profile: refused before anything changes
        except ProfileError as exc:
            return Response.json({"ok": False, "error": f"Это не профиль настроек Local Booru: {sanitize_text(str(exc))}"}, 400)
        from webui import service
        answer: dict = {}
        outcome = service.call_window("import_profile", {"path": str(path), "answer": answer},
                                      wait=IMPORT_WAIT, finish_wait=IMPORT_WAIT * 4)
        if outcome == "no_window":
            result = _import_without_window(path)
        elif outcome == "done":
            result = answer
        elif outcome == "late":                    # taken by the window, not finished yet
            return Response.json({"ok": False, "error": "Окно программы загружает профиль дольше обычного — "
                                  "проверьте настройки, когда оно освободится"}, 503)
        else:                                      # not taken in time: the window will not load it later
            return Response.json({"ok": False, "error": "Окно программы не ответило — профиль не загружен"}, 503)
    finally:
        _drop_temp(folder)
    if not result.get("ok"):
        return Response.json({"ok": False, "error": "Профиль не загружен: " + str(result.get("error") or "неизвестно почему")}, 409)
    skipped = [str(k)[:60] for k in (result.get("skipped") or [])]
    note = (f"; не взято {len(skipped)} (неизвестны этой версии или не того вида): {', '.join(skipped[:8])}"
            f"{'…' if len(skipped) > 8 else ''}" if skipped else "")
    return Response.json({"ok": True, "message": f"Загружено; прежние настройки — {result.get('backup') or 'без копии'}{note}",
                          "reload": True, "skipped": skipped})
