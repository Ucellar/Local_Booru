"""The page «Настройки»: what the program does by itself and where its data lives — each section a form
saved on its own (``webui/settings_form``), changed only through ``settings_store.save`` (the keys of
the sections, nothing else).  Actions next to the settings they belong to: a light copy now, the
thumbnail cache, the browser extension's pairing code, the settings profile.

(v5.44.161; the owner: «настройки полный пиздец блять»; the mockup «Настройки» approved 11.11.2026) The sections are
on the left (:data:`PANES`) with a search of every setting, a setting is a row — its name, one line of explanation,
the control on the right; a long explanation is behind «Подробнее».  «Сайты и ключи» is a section of this page
(«Парсер и сайты»: ``webui/pages/sites.py`` gives its part, the address /sites opens the page at it); «Диагностика»
— its own pages inside the same frame (:func:`frame`: «Проверка и починка»).  The source folder is chosen on
«Парсер».
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path

from webui.html import esc, human_size, page_frame
from webui.server import Request, Response
from webui.settings_form import Field, FieldError, Section, clean

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
    router.add("GET", r"/api/settings/comic/state", comic_state)          # (v5.44.113) «Проверить сейчас»


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
          hint="что лежит в корзине дольше, удаляется навсегда",
          more="Проверяется при запуске программы и раз в 5 минут, пока она открыта."),
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

# (v5.44.161) A setting's ``hint`` is the one line of its row; what is longer is its ``more`` («Подробнее»).
GALLERY = Section("gallery", "Галерея", fields=(
    Field("webui_posts_per_page", "choice", "Картинок на странице", default=0,
          choices=((0, "сколько помещается в окно"), (20, "20"), (42, "42"), (60, "60"), (100, "100"), (200, "200")),
          hint="«сколько помещается в окно» — ряды превью до низа окна, без прокрутки",
          more="Размер превью («мелкие / средние / крупные» над плитками) меняет и число картинок."),
    Field("card_sets_tiles", "bool", "Сборка в галерее — одной плиткой",
          "все её карточки листаются на странице карточки; в корзине и Браке карточки всегда по одной", default=True),
    Field("post_sets_enabled", "bool", "Сборка над картинкой карточки",
          "зелёной плашкой, как на Danbooru: превью всей сборки по порядку; там же «изменить»", default=True),
    Field("card_sets_auto", "bool", "Собирать сборки самой",
          "из скачанного: посты с родителем, страницы одной работы, серии одного автора, комиксы", default=True,
          more="Родитель и дочерние посты, страницы одной работы (pixiv, твит, Patreon…), серии одного загрузившего "
               "и комиксы. Сборки, которые вы меняли или собрали сами, программа не трогает."),
    Field("card_sets_ask_sites", "bool", "Узнать у сайтов про уже скачанное",
          "кто залил пост и кто его родитель — один раз, в фоне", default=True,
          more="Для карточек, скачанных до версии 5.21: без этого их не собрать в сборки. Сайты спрашиваются пачками, "
               "ответ запоминается навсегда."),
    Field("card_sets_remote", "bool", "Недостающие части сборки — с сайта",
          "части, которых нет в библиотеке, — превью с сайта с пометкой «api»", default=True,
          more="У сборок «родитель и дочерние», «одна работа» и «серия»: серию программа ищет среди постов того же "
               "загрузившего, работу — по ссылке на источник; на странице такой части — «Скачать». У карточки, "
               "которая в библиотеке одна, — родитель и дочерние её поста на сайте (страница карточки спрашивает "
               "сайт)."),
))

VIDEOS = Section("videos", "Видео",
                 lead="Длинные видео библиотеки показываются на вкладке «Видео», а не в галерее (в корзине и Браке "
                      "видны все). Сериалы на ней — из папки видео: одна папка — один сериал, файлы в ней — серии.",
                 fields=(
    Field("video_tab_seconds", "choice", "Видео длиннее", default=60,
          choices=((0, "не переносить — все видео в галерее"), (30, "30 секунд"), (60, "1 минуты"),
                   (120, "2 минут"), (300, "5 минут"), (600, "10 минут")),
          hint="такие видео — на вкладке «Видео»; длину программа узнаёт сама, в фоне"),
    Field("video_source_dubs", "bool", "Все озвучки тайтлов с YummyAnime",
          "«Скачать с сайта»: озвучки и субтитры из плееров Kodik (до 720p) и CVH (до 1080p)", default=True,
          more="Каждая озвучка — своей вкладкой сериала; ссылки на видео программа берёт перед скачиванием каждой "
               "серии. Выключено — серии берутся с AniLiberty (строка ниже)."),
    Field("video_dub_merge", "bool", "Собирать озвучки серии в один файл",
          "картинка одна, озвучки — дорожками звука одного MKV: переключаются в плеере", default=True,
          more="После «Скачать» озвучки каждой серии становятся дорожками одного файла; сдвиги и вставки студий "
               "программа находит по кадрам. Отдельные файлы озвучек удаляются, когда общий файл проверен. Галочка "
               "на странице «Скачать с сайта» — для одного скачивания."),
    Field("yummy_api_url", "text", "Адрес API YummyAnime", default="https://api.yani.tv",
          placeholder="https://api.yani.tv", hint="если сайт переедет, впишите новый адрес его API"),
    Field("video_source_aniliberty", "bool", "Серии тайтлов с YummyAnime — с AniLiberty",
          "когда озвучки выше выключены или YummyAnime их не отдал", default=True,
          more="«Скачать с сайта»: тот же тайтл программа находит на AniLiberty по номерам Shikimori и MyAnimeList "
               "(озвучка одна — AniLiberty), а описание и постер берёт с YummyAnime."),
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
          "подписки, «Галерея (booru)», расширение; выключено — только то, что уже есть", default=True),
    Field("session_media_downloads_prompt", "bool", "Спрашивать об этом при каждом запуске",
          "вопрос «Разрешить скачивание новых медиа в этой сессии?»", default=False),
    Field("large_download_warning_count", "int", "Спрашивать, если за раз больше", default=1000, lo=1, hi=1_000_000,
          suffix="файлов", hint="подписки и «Галерея (booru)»"),
    Field("disk_free_reserve_gb", "float", "Оставлять свободными на диске", default=2.0, lo=0, hi=1000,
          suffix="ГБ", hint="загрузка и копии не займут последнее место; 0 — без запаса"),
    Field("grabber_cache_limit_mb", "int", "Кэш картинок «Галереи (booru)»", default=200, lo=0, hi=10_000, suffix="МБ",
          hint="0 — по умолчанию, 200 МБ"),
    Field("automatic_quality_upgrade_enabled", "bool", "Ставить оригинал вместо уменьшенной копии",
          "уменьшенную копию с сайта (sample_…, thumbnail_…) программа сама заменяет оригиналом", default=True,
          more="В фоне: скачивает оригинал поста карточки, сверяет, что это та же картинка и она больше, и ставит его "
               "на место копии — теги, источники и сборка карточки остаются, файл получает имя оригинала. Так же — "
               "лучшая копия, которую приносит расширение."),
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
                   lead="Как программа обращается со своей базой SQLite. Копии, проверка и обслуживание базы — в "
                        "разделе «Проверка и починка».",
                   fields=(
    Field("sqlite_cache_mb", "int", "Кэш SQLite", default=40, lo=8, hi=512, suffix="МБ",
          hint="на каждое соединение: больше — быстрее большие запросы, но больше памяти; после перезапуска"),
    Field("sqlite_checkpoint_on_exit", "bool", "При закрытии переносить журнал WAL в базу",
          "файл журнала рядом с базой не растёт от запуска к запуску", default=True),
    Field("sqlite_idle_checkpoint_enabled", "bool", "Переносить журнал WAL и в простое",
          "когда журнал больше порога ниже и программа ничего не делает; никого не ждёт и не держит", default=True),
    Field("sqlite_idle_checkpoint_threshold_mb", "int", "Порог журнала WAL", default=64, lo=16, hi=4096, suffix="МБ"),
    Field("sqlite_idle_checkpoint_interval_seconds", "choice", "Проверять журнал не чаще", default=300,
          choices=((60, "раза в минуту"), (120, "раза в 2 минуты"), (300, "раза в 5 минут"), (600, "раза в 10 минут"),
                   (900, "раза в 15 минут"), (1800, "раза в 30 минут"), (3600, "раза в час"))),
    Field("derived_cache_external_enabled", "bool", "Дерево похожих и категории тегов — в отдельном файле",
          "рядом с базой, а не в ней; после смены пересоберите оба в «Проверка и починка → Обслуживание»", default=False),
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
          hint="«Медленные операции» в разделе «Проверка и починка»"),
))

# (v5.44.116) What the program does with manga and manhwa by itself — normal fields of «Сайты манги» (the owner's
# review): the nightly round of new chapters, and «Из архива»'s switches (they were on its page alone)
MANGA_AUTO = Section("manga_auto", "Что программа делает сама", fields=(
    Field("manhwa_watch_auto", "bool", "Новые главы — скачивать самой",
          "раз в сутки, ночью: новые бесплатные главы тайтлов, которые скачала программа", default=True,
          more="Тайтлы манги и манхвы; у каждого — ещё и своя галочка на его странице. Компьютер был выключен ночью — "
               "после запуска. Проверка идёт, только пока за компьютером никого нет (она заняла бы процессор); "
               "«Продолжить» в «Загрузках» пускает её сразу. «Остановить» там же — до следующей ночи."),
    Field("manga_archive_auto", "bool", "Узнавать тайтлы «Из архива» самой, в фоне",
          "по файлам и названию: обложка, описание и главы, которых в папке нет", default=True,
          more="Тайтлы папок манги и манхвы программы в папке парсера и копии манги из папки парсера — по ссылке на "
               "сайт в файлах папки, по названию из файлов, по имени папки."),
    Field("manga_archive_pictures", "bool", "…и по картинкам (SauceNAO)",
          "когда не помогли файлы и названия; не больше 40 поисков в день", default=True,
          more="Эти 40 поисков — вместе с проверкой манги из папки парсера."),
    # (v5.44.161) here, not among the gallery's (the mockup): it is about what gets into «Манга»
    Field("comic_manga_auto", "bool", "Комиксы и манга — в папку манги",
          "только то, что подтвердил сайт манги; остальное остаётся в галерее", default=True,
          more="Подтверждение — тайтл сайта манги с теми же страницами, ComicInfo.xml или ссылка. Комикс галереи от 15 "
               "страниц (пул e621 или сборка комикса) — копией в папку манги, его карточки — в корзину («перенесено "
               "в мангу», оттуда их можно вернуть). Манга из папки парсера — копией, её файлы и карточки остаются на "
               "месте (вкладка «Манга из папки» страницы «Парсер»). Что не подтвердилось — остаётся сборкой галереи; "
               "пул e621 сам по себе мангу не подтверждает."),
))

SECTIONS = (WINDOW, GALLERY, VIDEOS, LIBRARY, TAGS, BACKUP, THUMBS, DOWNLOAD, COMPANION, MANGA_AUTO, VISUAL, DATABASE,
            DEVELOPER)
BY_ID = {s.id: s for s in SECTIONS}


SIDES = (("", "Как у оформления"), ("light", "Светлая"), ("dark", "Тёмная"))


def _looks_section() -> Section:
    """The look of each page of the menu — kept with the page's own choices (``webui/prefs.py``), not in the
    settings; the menu «Оформление» of a page changes the same choice.  (v5.44.161) With them: the side of every page
    at once (``side_all``: light, dark, or each look's own) and «Плавные переходы» (``motion``)."""
    from webui import prefs
    from webui.html import PAGE_NAMES
    from webui.skins import SKINS
    looks = tuple((s.id, s.name) for s in SKINS)
    fields = []
    for page, (key, default) in prefs.PAGE_LOOKS.items():
        gallery = key == "skin"
        fields.append(Field(key, "choice", "Оформление" if gallery else PAGE_NAMES[page],
                            hint="" if gallery else "своё оформление страницы",
                            choices=looks if gallery else ((prefs.FOLLOW, "как у галереи"), *looks),
                            default=default if default is not None else prefs.FOLLOW))
    fields.append(Field("side_all", "choice", "Светлая или тёмная сторона", default="", choices=SIDES,
                        hint="у всех страниц сразу; оформлениям r34 не задаётся — у них сторона своя"))
    fields.append(Field("motion", "choice", "Плавные переходы", default="vivid",
                        choices=tuple((level, prefs.MOTION_WORDS[level]) for level in ("none", "calm", "vivid")),
                        hint="появление страниц, меню, плитки под курсором, ход загрузок"))
    return Section("looks", "Оформление", fields=tuple(fields))


LOOKS = _looks_section()


def _side_all(stored: dict) -> str:
    """The side every page has now: «light» or «dark» when all of them were given the same one, else "" (each look's
    own, or the pages differ)."""
    from webui import prefs, skins
    sides = {str(stored.get(vkey) or "") for vkey in prefs.VARIANT_KEYS}
    return next(iter(sides)) if len(sides) == 1 and next(iter(sides)) in skins.VARIANTS else ""


def looks_now() -> dict:
    """What the section «Оформление» shows: each page's look, the side of all, the motion."""
    from webui import prefs
    stored = prefs.load()
    return {**prefs.choices(stored), "side_all": _side_all(stored), "motion": prefs.motion(stored)}


def _looks_block() -> str:
    """«Оформление» — one row for every page (the gallery's look: the pages «как у галереи» take it), the pages that
    have a look of their own named under it, the lists of the pages behind «изменить по страницам»."""
    from webui import prefs
    from webui.html import PAGE_NAMES
    from webui.settings_form import _control_html, row_html
    from webui.skins import BY_ID
    now = looks_now()
    by_key = {f.key: f for f in LOOKS.fields}
    own, same, pages = [], 0, []
    for page, (key, _default) in prefs.PAGE_LOOKS.items():
        if key == "skin":
            continue
        choice = now[key]
        if choice == prefs.FOLLOW:
            same += 1
        else:
            own.append(f'<span><b>{esc(PAGE_NAMES[page])}</b>{esc(BY_ID[choice].name if choice in BY_ID else choice)}</span>')
        item = by_key[key]
        pages.append(row_html(item.label, "", _control_html(item, choice, f"looks-{key}"), fid=f"looks-{key}", key=key))
    chips = "".join(own)
    if same:
        from webui.pages.post_sets import plural
        chips += ('<span class="same">' + ("остальные " if own else "все ")
                  + plural(same, ("страница", "страницы", "страниц")) + " — как у галереи</span>")
    skin = by_key["skin"]
    first = row_html(skin.label, "", _control_html(skin, now["skin"], "looks-skin"), fid="looks-skin", key="skin",
                     hint_html='одно на все страницы — или своё у каждой: <button type="button" class="link" '
                               'data-action="looks-pages" aria-expanded="false" aria-controls="looks-pages">изменить '
                               'по страницам</button>')
    rest = "".join(_row(by_key[key], now) for key in ("side_all", "motion"))
    save = ('<button type="submit" class="form-submit" hidden tabindex="-1">Сохранить</button>'
            '<p class="form-error" role="alert" hidden></p>')
    return (f'<section class="settings-block sx-block" id="set-looks" data-title="{esc(LOOKS.title)}">'
            f'<form class="settings-form" data-form="settings" data-section="looks"><div class="sx-group">{first}'
            f'<div class="sx-pages" data-looks-chips>{chips}</div>'
            f'<div class="sx-pages-edit" id="looks-pages" hidden>{"".join(pages)}</div>{rest}</div>{save}</form></section>')


def _row(item: Field, values: dict) -> str:
    from webui.settings_form import field_row
    return field_row(item, values, LOOKS.id)


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


def _comic_extra(settings: dict) -> str:
    """(v5.44.113) «Комиксы и манга — в папку манги»: what was moved, what waits, «Проверить сейчас» and its journal —
    the owner: «и как мне перепроверить архив?»."""
    from core import comic_manga
    try:
        st = comic_manga.status(settings)
    except Exception as exc:  # noqa: BLE001 - a broken file of the settings folder: the button still works
        _LOG.debug("settings page: comics not counted: %s", exc)
        st = {"comics": 0, "copies": 0, "waiting": 0, "looked_at": 0, "due": False, "no_room": ""}
    parts = [f"в папке манги комиксов из галереи — {st['comics']}, манги из папки парсера — {st['copies']}"]
    if st["waiting"]:
        parts.append(f"в папке парсера ждут проверки: {st['waiting']}")       # (v5.44.125) or the sites of manga
    if st["due"]:
        parts.append("папку парсера программа пересмотрит в ближайшие минуты")
    elif st["looked_at"]:
        parts.append(f"папка парсера просмотрена {_day(st['looked_at'])}")
    room = f'<p class="hint warn">{esc(st["no_room"])}</p>' if st["no_room"] else ""
    return (f'<p class="hint">Комиксы и манга: {esc("; ".join(parts))}.</p>'
            '<details class="sx-more"><summary>Что делает «Проверить сейчас»</summary><p>Комиксы галереи программа '
            'переносит сама, раз в несколько минут, — те, что подтвердил сайт манги (тайтл с теми же страницами). '
            '«Проверить сейчас» заново смотрит папку парсера и копирует в папку манги подтверждённую там сайтом манги '
            'мангу — ComicInfo.xml, ссылка или тайтл с теми же страницами (каждая — строкой в журнале; что не '
            f'подтвердилось — парсер берёт как обычно), потом переносит комиксы.</p></details>{room}'
            '<div class="tool-actions"><button type="button" data-action="settings-act" data-act="comic_manga_check">'
            'Проверить сейчас</button><span class="action-status" role="status"></span></div>'
            '<div class="job" data-job data-comic-job hidden><div class="job-head"><b class="job-title"></b> '
            '<span class="job-status"></span></div><pre class="job-log" tabindex="0" aria-label="Журнал"></pre></div>')


def comic_state(req: Request) -> Response:
    """(v5.44.113) The task «Проверить сейчас» for the page (its journal)."""
    from core import comic_manga, page_jobs
    task = page_jobs.current(comic_manga.TASK)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    return Response.json({"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))})


def _download_extra(settings: dict) -> str:
    """(v5.44.13) What became of the reduced copies (``core/reduced_copies``): how many got their original."""
    from core import reduced_copies
    st = reduced_copies.status(settings)
    if not any(st.values()):
        return ""
    parts = [f"заменены оригиналами — {st['done']}"]
    if st["kept"]:
        parts.append(f"оставлены как есть (у поста нет большего оригинала той же картинки, он уже есть или это "
                     f"видео) — {st['kept']}")
    if st["later"]:
        parts.append(f"ждут сайта или сети — {st['later']}")
    return f'<p class="hint">Уменьшенные копии: {esc("; ".join(parts))}.</p>'


def _manga_auto_extra(settings: dict) -> str:
    """(v5.44.116) What SauceNAO needs for the pictures (as «Из архива» said it beside its switches), and the list of
    the parser's folder's titles — «Скачать → Из архива»."""
    from core import manga_pictures as P
    if not P.key_of(settings):
        note = "Ключа SauceNAO нет (Настройки → Парсер и сайты) — по картинкам программа не ищет."
    elif not settings.get("enable_saucenao", True):
        note = "SauceNAO выключен в настройках парсера — по картинкам программа не ищет."
    else:
        note = (f"По картинкам — не больше {P.PER_TITLE} поисков SauceNAO на тайтл и {P.per_day(settings)} в день, "
                "в фоне — только когда не работает парсер.")
    return (f'<p class="hint">{esc(note)}</p>'
            '<p class="hint"><a href="/get/archive">Тайтлы в папке парсера — что программа считает мангой</a></p>'
            + _comic_extra(settings))


EXTRAS = {"backup": _backup_extra, "thumbs": _thumbs_extra, "companion": _companion_extra, "visual": _visual_extra,
          "gallery": _gallery_extra, "download": _download_extra, "manga_auto": _manga_auto_extra}


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
    from core.manhwa_library import manhwa_root
    from core.video_library import video_root
    video = video_root(settings)
    manhwa = manhwa_root(settings)
    manhwa_note = "" if str(settings.get("manhwa_root") or "").strip() else ' <small class="muted">по умолчанию — в архиве</small>'
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
    <span class="tool-label">Папка манхвы</span><span class="tool-value"><span class="tool-path">{esc(manhwa)}</span>{manhwa_note}
      <span class="tool-actions"><button type="button" data-action="settings-choose" data-key="manhwa_root">Выбрать…</button>
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


# (v5.44.161) The sections of the page, on its left: (id, name, icon, the blocks of its pane, the address of a section
# that is a page of its own — «Проверка и починка» is «Диагностика» inside the same frame).  None — a line between the
# sections of what the program does and of where its data is.  A block's anchor (#set-…) opens its section; the old
# tabs' addresses (#tab-…) open the sections their blocks went to (:data:`OLD_TABS`).
PANES = (
    ("look", "Внешний вид", "palette", ("looks", "window", "tags"), ""),
    ("gallery", "Галерея", "image", ("gallery", "library", "thumbs", "visual"), ""),
    ("videos", "Видео", "video", ("videos",), ""),
    ("manga", "Манга и манхва", "book", ("manga_auto", "manga_sites"), ""),
    ("download", "Скачивание", "download", ("download", "companion"), ""),
    ("sites", "Парсер и сайты", "sites", ("sites",), ""),
    ("phone", "Телефон", "phone", ("phone",), ""),
    None,
    ("storage", "Хранение и копии", "disk", ("folders", "backup"), ""),
    ("diag", "Проверка и починка", "wrench", (), "/diag"),
    ("advanced", "Для опытных", "sliders", ("db", "logs", "dev"), ""),
    ("profile", "Профиль", "user", ("profile",), ""),
)
OLD_TABS = {"main": "look", "library": "storage", "download": "download", "manga": "manga", "phone": "phone",
            "visual": "gallery", "db": "advanced", "profile": "profile"}
# what the search of the settings finds on the other pages: (name, a line about it, address, the page of the menu it
# is of — "" for any mode; a page the mode «SFW» hides is not pointed at)
POINTERS = (
    ("Проверка библиотеки", "что не так с файлами и базой — найти и починить", "/diag", ""),
    ("Медленные страницы", "какие страницы программы открываются долго", "/diag", ""),
    ("Обслуживание", "сжать базу, пересчитать счётчики, почистить кэш и превью", "/diag/maintenance", ""),
    ("Удаление и сброс", "удалить часть библиотеки или начать её заново", "/diag/reset", ""),
    ("Дубли", "одинаковые и похожие картинки галереи", "/dupes", "dupes"),
    ("Вход на сайты манги и их адреса", "cookies, токен, новый адрес сайта, который переехал; язык глав MangaDex",
     "/settings#set-manga-sites", ""),
    ("Папка с картинками для парсера", "выбирается на странице «Парсер»", "/parser", "parser"),
)

_STROKE = 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'
ICONS = {
    "search": '<circle cx="8.5" cy="8.5" r="5"/><path d="m12.5 12.5 4 4"/>',
    "palette": '<path d="M10 3a7 7 0 1 0 0 14c1 0 1.5-.7 1.5-1.5 0-1.5 1-2 2.3-2H15a2 2 0 0 0 2-2A7 7 0 0 0 10 3z"/>'
               '<circle cx="6.8" cy="9" r=".6"/><circle cx="9.2" cy="6.4" r=".6"/><circle cx="12.6" cy="7.2" r=".6"/>',
    "image": '<rect x="3" y="4" width="14" height="12" rx="1"/><path d="m3.5 14 3.5-3.5 3 3 2.5-2.5 4 4"/>'
             '<circle cx="12.5" cy="7.5" r="1"/>',
    "video": '<rect x="3" y="4.5" width="14" height="11" rx="1"/><path d="m8.5 7.8 4 2.2-4 2.2z"/>',
    "book": '<path d="M10 5.5C8.5 4.4 6.5 4 3.5 4v11c3 0 5 .4 6.5 1.5 1.5-1.1 3.5-1.5 6.5-1.5V4c-3 0-5 .4-6.5 1.5zM10 5.5v11"/>',
    "download": '<path d="M10 3.5v8.5M6.5 9l3.5 3.5L13.5 9M4 15.5h12"/>',
    "sites": '<path d="M3 5.5h14M3 10h7M3 14.5h5"/><circle cx="14" cy="13" r="2.6"/><path d="m16 15 1.8 1.8"/>',
    "phone": '<rect x="6" y="2.5" width="8" height="15" rx="1.5"/><path d="M9 14.5h2"/>',
    "disk": '<ellipse cx="10" cy="5.5" rx="6" ry="2.3"/><path d="M4 5.5v9c0 1.3 2.7 2.3 6 2.3s6-1 6-2.3v-9M4 10c0 1.3 2.7 2.3 6 2.3s6-1 6-2.3"/>',
    "wrench": '<path d="M12.8 3.2a4 4 0 0 0-4.3 5.5L3.3 14a1.7 1.7 0 0 0 2.4 2.4l5.2-5.2a4 4 0 0 0 5.6-4.3l-2.4 2.4-2.1-.5-.5-2.1z"/>',
    "sliders": '<path d="M3 6h7M14 6h3M3 10h3M10 10h7M3 14h9M16 14h1"/><circle cx="12" cy="6" r="1.8"/><circle cx="8" cy="10" r="1.8"/><circle cx="14" cy="14" r="1.8"/>',
    "user": '<circle cx="10" cy="7" r="3"/><path d="M4 16.5c.8-3 3-4.5 6-4.5s5.2 1.5 6 4.5"/>',
}


def icon(name: str) -> str:
    return f'<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false" {_STROKE}>{ICONS[name]}</svg>'


def _shown_panes() -> list:
    """The sections this mode shows: in «SFW» the parser's sites are not («Сайты» is a page of the gallery's world)."""
    from webui import content_mode
    mode = content_mode.mode()
    return [pane for pane in PANES
            if pane is None or pane[0] != "sites" or content_mode.page_shown("sites", mode)]


def frame(active: str, content: str, *, here: bool = False, find: str = "") -> str:
    """The frame of «Настройки» around ``content``: the heading, on the left the search of a setting and the
    sections (``active`` — the one shown), on the right the content.  ``here`` — the page of the sections themselves
    (they are switched without a reload: the links are #…); else a page of its own (the links lead to «Настройки»,
    the search is asked of it)."""
    links = []
    for pane in _shown_panes():
        if pane is None:
            links.append("<hr>")
            continue
        pane_id, name, picture, _blocks, href = pane
        address = href or (f"#{pane_id}" if here else f"/settings#{pane_id}")
        on = pane_id == active
        attrs = ' class="on" aria-current="page"' if on else ""
        data = "" if href else f' data-pane="{pane_id}"'
        links.append(f'<a href="{address}"{attrs}{data}>{icon(picture)}<span>{esc(name)}</span></a>')
    search = (f'<form class="sx-search" role="search" action="/settings" method="get">{icon("search")}'
              f'<input type="search" name="q" value="{esc(find)}" placeholder="Найти настройку" autocomplete="off" '
              'spellcheck="false" aria-label="Найти настройку"></form>')
    return (f'<div class="sx-body"><nav class="sx-nav" aria-label="Разделы настроек">{search}{"".join(links)}</nav>'
            f'<div class="sx-main">{content}</div></div>')


def in_frame(active: str, body: str, tabs: tuple = ()) -> str:
    """A page of its own as a section of «Настройки» («Диагностика»: ``active`` — "diag"): the same frame around it;
    its heading «Настройки» leads to the page of the sections.  ``tabs`` — ((label, href, current), …): the pages of
    the section, above its content."""
    bar = ""
    if tabs:
        links = "".join(f'<a href="{esc(href)}" class="active" aria-current="page">{esc(label)}</a>' if current
                        else f'<a href="{esc(href)}">{esc(label)}</a>' for label, href, current in tabs)
        bar = f'<nav class="sx-tabs" aria-label="Страницы раздела">{links}</nav>'
    return ('<div class="sx sx-doc"><div class="sx-head"><a class="sx-up" href="/settings">Настройки</a></div>'
            f'{frame(active, bar + body)}</div>')


_BLOCK_HEAD = re.compile(r'^\s*<section class="([^"]*)" id="([^"]*)"([^>]*)>\s*<h2>(.*?)</h2>', re.S)


def as_group(block: str, untitled: frozenset = frozenset()) -> str:
    """A block of the page that is not rows of settings (the folders, the phone, the profile…) inside a section: its
    heading as a group's title (none when it is one of ``untitled`` — a group named as its section), its content — a
    panel."""
    found = _BLOCK_HEAD.match(block)
    if not found:
        return block
    classes, block_id, attrs, title = found.groups()
    body = block[found.end():]
    cut = body.rfind("</section>")
    name = _plain(title).strip()
    head = "" if name in untitled else f'<h3 class="sx-title">{title}</h3>'
    return (f'<section class="{classes} sx-block sx-custom" id="{block_id}"{attrs} data-title="{esc(name)}">'
            f'{head}<div class="sx-group sx-box">{body[:cut]}</div></section>')


def _plain(markup: str) -> str:
    return re.sub(r"<[^>]+>", "", markup)


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


def settings_page(req: Request, open_pane: str = "") -> Response:
    """The page: every section a pane, one shown (``open_pane`` — by the address /sites; else by the address's #…,
    else the one seen last).  ``?q=…`` — the search of a setting asked from another page of the frame."""
    from webui.settings_form import render_rows, row_html
    s = req.settings
    blocks = {}
    pane_names = frozenset(pane[1] for pane in PANES if pane is not None)   # a group named as its section: one title
    for section in SECTIONS:
        extra = EXTRAS.get(section.id)
        fields = section_fields(section)
        if extra is not None or fields != section.fields:
            section = Section(section.id, section.title, section.lead, fields, extra(s) if extra else section.extra,
                              section.note, section.danger)
        blocks[section.id] = render_rows(section, s, title="" if section.title in pane_names else None)
    from webui.pages import manga_sites, phone, sites
    blocks.update({"folders": as_group(_folders_html(s)), "logs": as_group(_logs_html()),
                   "profile": as_group(_profile_html()), "looks": _looks_block(),
                   "phone": as_group(phone.settings_block(s), pane_names),
                   "manga_sites": manga_sites.settings_block(s)})
    shown = [pane for pane in _shown_panes() if pane is not None]
    if any(pane[0] == "sites" for pane in shown):
        blocks["sites"] = sites.settings_part(req)
    ids = [pane[0] for pane in shown if not pane[4]]
    first = open_pane if open_pane in ids else ids[0]
    panes = []
    for pane_id, name, _picture, parts, href in shown:
        if href:
            continue
        inner = "".join(blocks.pop(part) for part in parts if part in blocks)
        panes.append(f'<section class="sx-pane" id="pane-{pane_id}" data-pane="{pane_id}" data-title="{esc(name)}" '
                     f'aria-labelledby="pane-{pane_id}-h"{"" if pane_id == first else " hidden"}>'
                     f'<h2 id="pane-{pane_id}-h">{esc(name)}</h2>{inner}</section>')
    blocks.pop("sites", None)
    if blocks:                                       # a block no section names: shown, never lost
        panes.append('<section class="sx-pane" data-pane="other" data-title="Остальное"><h2>Остальное</h2>'
                     + "".join(blocks.values()) + "</section>")
    from webui import content_mode
    mode = content_mode.mode()
    pointers = "".join(row_html(name, about, f'<a class="button" href="{esc(href)}">Открыть</a>', cls="sx-pointer")
                       for name, about, href, of_page in POINTERS
                       if not of_page or content_mode.page_shown(of_page, mode))
    find = req.arg("q")[:80]
    content = f"""<section class="sx-pane sx-found" data-found hidden aria-live="polite">
    <h2 data-found-title>Найдено</h2>
    <p class="sx-none" data-found-none hidden>Такой настройки нет. Попробуйте другое слово — или посмотрите разделы слева.</p>
  </section>
  {"".join(panes)}
  <section class="sx-pane sx-also" data-also hidden>
    <div class="sx-group sx-pointers" data-title="Смотрите также">{pointers}</div>
  </section>
  <div class="settings-savebar sx-save" role="region" aria-label="Несохранённые изменения" hidden>
    <p class="savebar-what"><span data-bar-text>Изменено</span><b data-dirty-list></b></p>
    <div class="tool-actions">
      <span class="action-status" role="status"></span>
      <button type="button" data-action="settings-revert">Отменить</button>
      <button type="button" class="primary" data-action="settings-save-all">Сохранить</button>
    </div>
  </div>"""
    body = f"""
<section class="tool settings settings-page sx" id="settings" data-open="{esc(first)}" data-old-tabs="{esc(json.dumps(OLD_TABS))}">
  <header class="sx-head"><h1>Настройки</h1></header>
  {frame(first, content, here=True, find=find)}
</section>"""
    return Response.html(page_frame("Настройки", body, active="sites" if open_pane == "sites" else "settings", subnav=False,
                                    kind="tool", body_data={"tool": "settings"}, assets=("settings",), **_prefs()))


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
    if section.id == MANGA_AUTO.id and changes.get("manga_archive_auto") is False:
        from webui.pages import manga_archive           # (v5.44.116) switched off: a round of the background stops
        manga_archive.stop_background()
    return Response.json({"ok": True, "message": "Сохранено"})


def _save_looks(body: dict) -> Response:
    """«Оформление страниц»: the choices go where the menu «Оформление» keeps them; this page's own look changed —
    the page is shown again in it."""
    from webui import prefs
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    was = prefs.look("settings")
    now = looks_now()
    try:
        changes = clean(LOOKS, values, now)
    except FieldError as exc:
        return Response.json({"ok": False, "error": str(exc), "field": exc.field.key}, 400)
    side = changes.pop("side_all", "")
    if side != now["side_all"]:                 # (v5.44.161) every page's side at once: given, or each look's own again
        changes.update({vkey: side for vkey in prefs.VARIANT_KEYS})
    prefs.save(changes)
    new = prefs.look("settings")
    reload = (new["skin"], new["variant"]) != (was["skin"], was["variant"]) or changes.get("motion") != now["motion"]
    return Response.json({"ok": True, "message": "Сохранено", "reload": reload})


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
    if act == "comic_manga_check":                 # (v5.44.113) comics and manga to the manga folder — now
        from core import comic_manga, page_jobs
        try:
            started = comic_manga.check_now(s)
        except comic_manga.ComicError as exc:
            return Response.json({"ok": False, "error": str(exc)}, 409)
        except page_jobs.TaskBusy:
            return Response.json({"ok": False, "error": "Проверка уже идёт — её журнал ниже"}, 409)
        return Response.json({"ok": True, "id": started.id, "message": "Проверка идёт — журнал ниже"})
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
