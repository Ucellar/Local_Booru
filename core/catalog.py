"""(v5.44.90) «Лента» of the tabs «Манга», «Манхва» and «Видео» — the titles of the sites in one list, with filters.

The owner (6.11.2026): «а вопрос где нахуй ленты манги и видео?»; the look — the mockups he approved on the design
canvas («Манга → Онлайн — каталог с фильтрами», «Видео → Онлайн — каталог сериалов»: «вот теперь отлично делай»).

What the sites' catalogs take — asked from the owner's computer on 6.11.2026 (``_to_delete/cl96_research*.txt``):

* Remanga ``/api/v2/search/catalog/?ordering=&count=30&page=`` — ``types``, ``genres`` and ``categories`` (each one
  more: all of them), ``exclude_genres``, ``exclude_categories``, ``status``, ``count_chapters_gte|lte``,
  ``issue_year_gte|lte``, ``rating_gte|lte`` (``avg_rating_gte`` and ``age_limit`` it does not know: the answer is the
  same); orders ``-views``, ``-chapter_date``, ``-rating``, ``-count_chapters``, ``-id``; ``next`` — one page more;
  count 30 at most (60 — 400); a name it does not take (``query`` — the same answer): by a name —
  ``/api/v2/search/?query=&count=30&page=`` (no filters; no ``next``: a full page — maybe more).  Without a sign-in it
  gives no erotica (its catalog has no ``is_erotic``): «NSFW» does not ask it.
* MangaLib ``api.cdnlibs.org/api/manga?site_id[]=1&sort_by=&sort_type=desc&page=`` (60 a page) — ``types[]``,
  ``genres[]`` and ``tags[]`` (all of them), ``genres_exclude[]``, ``tags_exclude[]``, ``status[]``,
  ``chap_count_min|max``, ``year_min|max``, ``rating_min|max``, ``q``; orders ``views``, ``last_chapter_at``,
  ``rate_avg``, ``chap_count``, ``created_at``; ``meta.has_next_page``.  Its erotica — the genre «Эротика».
* YummyAnime ``api.yani.tv/anime?limit=&offset=`` — ``genres``/``exclude_genres`` (numbers, a comma: all of them),
  ``types`` (tv, movie, ova, ona, special), ``status`` (ongoing, released, announcement — a comma: any of them),
  ``from_year``/``to_year``, ``min_rating``/``max_rating``, ``q``; ``sort`` top (its own — the rating by its votes),
  views, rating, year, title, id, and ``sort_forward=0`` — the other way (biggest first) for views/rating/year/id; the
  number of episodes it does not filter (a parameter it does not know is said in ``warnings``); its list has no
  episodes.

A genre or tag one site does not have: asked as «all of these» — that site has no such title (it is not asked, and
the page says why); as «not these» — nothing to leave out there.  Two sites' titles that are one (a name, the kind and
the year: as ``manhwa_search.merge``) — one tile.  A page of the list — every site's page of that number, their titles
taken in turns by their place there, without those the list showed on its pages before (:data:`_SHOWN`).  The sites
are asked in threads, one request of a page however many ask it; a page of the program waits :data:`WAIT` and then
says «отвечает» (as ``manhwa_feed`` since 5.44.89).  Covers — only the addresses the sites gave (:func:`cover`).
The program's rule about minors: the tag of that kind is never offered and always left out (:data:`NEVER_TAGS`).

(v5.44.93) The owner: «а где хентай? … в видео нет хентая», «в всё все а в sfw только sfw тобиш без хентая с nsfw только
хентай», «оно не должно показывать то что уже скачано».  The sites of hentai alone (:data:`ADULT_SITES`) are in the
lists: Хентай-тян in «Манга» (its lists ``/manga/newest``, ``/mostviews`` — 20 a page, its search; it filters
nothing: with a filter, or an order it has not, it is not asked — said why), Hentasis in «Видео» (``hentasis1.top``,
DataLife Engine: ``/page/N/`` — 10 a page, newest first; asked 6.11.2026 — no other order: its «dlenewssortby» does
nothing, no genres as links, no search a program gets).  The mode (:func:`sites_for`): «SFW» — not them; «Всё» —
every site; «NSFW» — they alone and MangaLib's «Эротика» (MangaLib gives none without a sign-in: 0 of the genre
asked 6.11.2026 — then it is not asked, said why).  Titles in the program's folders (:func:`downloaded`: ``.lb/
source.json`` of both folders and their other sites, the names of the manga, the manhwa and the series; a series'
page — ``source_url``) are not shown.

(v5.44.97) The owner: «дак а блять с nhentai показывать мангу?» — nhentai in «Манга» (``core/nhentai.py``: its API v2,
asked 6.11.2026): «Новые главы» and «Новые тайтлы» — its newest (``/api/v2/galleries``), «Популярное» and a name — its
search (ten a minute), 25 a page; no filters, no other order (not asked, said why); a gallery the program's rule
leaves out (its tags by their numbers and names, its titles) — not a tile; the tile — «Хентай · N стр.», its
thumbnail, the title without its brackets; downloaded (``nhentai-<n>`` in the manga folder) — not shown.

(v5.44.99) The owner: «https://20.allhen.online/ добавить» — AllHentai in «Манга» and «Манхва» (``manhwa_sources.
AllHentai``, asked 6.11.2026): its catalog API ``/api/catalog/search?offset=&sortType=`` (50 a page: «Популярное» —
POPULARITY, «Новые главы» — DATE_UPDATE, «Новые тайтлы» — DATE_CREATE, «По оценке» — RATING; no filters — not asked
with them, said why), a name — its search ``/search/suggestion`` (one page); its kinds — ``category`` (the tab's:
«Манга», «Комикс», «3D», «Без текста» / «Манхва», «Маньхуа»); a title the program's rule leaves out (its names, its
``elements`` — genres and tags) — not a tile."""
from __future__ import annotations

import hashlib
import html as _html
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse

from core import filter as F
from core import manhwa_sources as S
from core import nhentai as N
# (v5.44.101) the filters no switch turns off — in ``core/filter.py``; their names here for the code that uses them
from core.filter import HAVE_TTL, Have, downloaded, forget_have  # noqa: F401

_LOG = logging.getLogger("local_booru.catalog")

KINDS = ("manga", "manhwa", "video")
TTL = 10 * 60                     # a site's page kept (seconds)
FAILED_TTL = 2 * 60               # …one that did not come
WAIT = 2.5                        # a page of the program waits this long, then «отвечает»
PART_WAIT = 8.0                   # …and its «?part=1» this long
# (v5.44.98) a site that has not answered this long after its page was first asked is not waited for: the others'
# tiles are shown, its own come after them (Хентай-тян: 12–15 s a page at best, 6.11.2026 — a hang of 45 s)
LATE = 6.0
PAGES_ASKED_KEPT = 200
PAGES_KEPT = 120
COVERS_KEPT = 4000
QUERIES_KEPT = 60                 # queries whose pages shown are remembered (not to show a title twice)
MAX_PAGE = 100
VIDEO_PAGE = 40                   # titles of YummyAnime asked a page
REMANGA_PAGE = 30
MANGALIB_PAGE = 60
MAX_CHOSEN = 12                   # genres or tags chosen at most (each way)
HENTAICHAN_PAGE = 20              # (v5.44.93) titles of a page of Хентай-тян's lists
HENTASIS_PAGE = 10                # …of Hentasis
HENTASIS_SITE = "https://hentasis1.top"
COVER_WIDTH = 480                 # px: a cover wider is given smaller (a tile is ~175 px; YummyAnime's — 1448 px)

# ── genres and tags ───────────────────────────────────────────────────────────────────────────────────────────────
# Remanga's genres (``/api/forms/titles/?get=genres``, 6.11.2026)
REMANGA_GENRES = {
    "Боевые искусства": 3, "Гарем": 5, "Гендерная интрига": 6, "Героическое фэнтези": 7, "Детектив": 8, "Дзёсэй": 9,
    "Додзинси": 10, "Драма": 11, "История": 13, "Киберпанк": 14, "Кодомо": 15, "Комедия": 50, "Махо-сёдзё": 17,
    "Меха": 18, "Мистика": 19, "Мурим": 51, "Научная фантастика": 20, "Повседневность": 21, "Постапокалиптика": 22,
    "Приключения": 23, "Психология": 24, "Романтика": 25, "Сверхъестественное": 27, "Сёдзё": 28, "Сёдзё-ай": 29,
    "Сёнэн": 30, "Сёнэн-ай": 31, "Спорт": 32, "Студенты": 242, "Сэйнэн": 33, "Трагедия": 34, "Триллер": 35,
    "Ужасы": 36, "Фантастика": 37, "Фэнтези": 38, "Школьники": 39, "Экшен": 2, "Элементы юмора": 16, "Этти": 40,
    "Юри": 41, "Яой": 43}
# MangaLib's genres of its site 1 (``/api/constants?fields[]=genres``, ``site_ids`` with 1)
MANGALIB_GENRES = {
    "Арт": 32, "Безумие": 91, "Боевик": 34, "Вампиры": 36, "Военное": 89, "Гарем": 37, "Гендерная интрига": 38,
    "Героическое фэнтези": 39, "Демоны": 81, "Детектив": 40, "Дзёсэй": 41, "Драма": 43, "Игра": 44, "Исекай": 79,
    "История": 45, "Киберпанк": 46, "Кодомо": 76, "Комедия": 47, "Космос": 83, "Магия": 85, "Махо-сёдзё": 48,
    "Машины": 90, "Меха": 49, "Мистика": 50, "Музыка": 80, "Научная фантастика": 51, "Омегаверс": 77, "Пародия": 86,
    "Повседневность": 52, "Полиция": 82, "Постапокалиптика": 53, "Приключения": 54, "Психология": 55, "Романтика": 56,
    "Самурайский боевик": 57, "Сверхъестественное": 58, "Сёдзё": 59, "Сёнэн": 61, "Спорт": 63, "Супер сила": 87,
    "Сэйнэн": 64, "Трагедия": 65, "Триллер": 66, "Ужасы": 67, "Фантастика": 68, "Фэнтези": 69, "Школа": 70,
    "Эротика": 71, "Этти": 72}
# (v5.44.96) HentaiLib's genres (site 4 of the same API; asked 6.11.2026): MangaLib's numbers, without «Эротика» — and
# without «Школа» (70), «Кодомо» (76): the program's rule about minors on a site of hentai (:data:`NEVER_GENRE_IDS`)
HENTAILIB_GENRES = {
    "Арт": 32, "Безумие": 91, "Боевик": 34, "Боевые искусства": 35, "Вампиры": 36, "Военное": 89, "Гарем": 37,
    "Гендерная интрига": 38, "Героическое фэнтези": 39, "Демоны": 81, "Детектив": 40, "Дзёсэй": 41, "Драма": 43,
    "Игра": 44, "Исекай": 79, "История": 45, "Киберпанк": 46, "Комедия": 47, "Космос": 83, "Магия": 85,
    "Махо-сёдзё": 48, "Машины": 90, "Меха": 49, "Мистика": 50, "Музыка": 80, "Научная фантастика": 51,
    "Омегаверс": 77, "Пародия": 86, "Повседневность": 52, "Полиция": 82, "Постапокалиптика": 53, "Приключения": 54,
    "Психология": 55, "Романтика": 56, "Самурайский боевик": 57, "Сверхъестественное": 58, "Сёдзё": 59,
    "Сёдзё-ай": 60, "Сёнэн": 61, "Сёнэн-ай": 62, "Спорт": 63, "Супер сила": 87, "Сэйнэн": 64, "Трагедия": 65,
    "Триллер": 66, "Ужасы": 67, "Фантастика": 68, "Фэнтези": 69, "Этти": 72, "Юри": 73, "Яой": 74}
# …its tags of site 4 (217 at the site; here without «Лоликон» 372, «Сетакон» 373, «Юные» 145, «Девочки-волшебницы» 50
# — :data:`NEVER_IDS`; its titles carry MangaLib's tags too — its «Лоли» 187 left out as well)
HENTAILIB_TAGS = {
    "3D": 1, "DILF": 363, "Footfuck": 5, "Footjob": 6, "FPP (Вид от первого лица)": 289, "Handjob": 7,
    "Koikatsu": 1825, "Scat": 13, "Selfcest": 286, "Shemale": 220, "Tomboy": 14, "Unbirth": 283, "Vore": 367,
    "X-Ray": 15, "Алкоголь": 16, "Ампутация": 408, "Анал": 17, "Андроид": 18, "Анилингус": 19, "Анимация (GIF)": 350,
    "Антигерой": 175, "Аристократия": 4837, "Арт": 20, "Артбук": 364, "Ахэгао": 2, "Бакуню": 21, "Бара": 293,
    "БДСМ": 22, "Без проникновения": 336, "Без текста": 23, "Без трусиков": 24, "Без цензуры": 25,
    "Безответная любовь": 671, "Беременность": 26, "Бикини": 27, "Близнецы": 28, "Больница": 30, "Большая грудь": 31,
    "Большая попка": 32, "Борьба": 33, "Буккакэ": 34, "В бассейне": 35, "В ванной": 36,
    "В государственном учреждении": 37, "В общественном месте": 38, "В очках": 8, "В первый раз": 39,
    "В транспорте": 40, "Вампиры": 386, "Вибратор": 42, "Викторианская эпоха": 4809, "Военные": 198, "Втроём": 43,
    "Газлайтинг": 4834, "ГГ - Мэри Сью": 4779, "Гиперсексуальность": 547, "Гипноз": 44, "Глубокий минет": 45,
    "Горничные": 171, "Горячий источник": 46, "Градостроение": 4813, "Групповой секс": 47, "Гуро": 307,
    "Гяру и Гангуро": 48, "Девушка-туалет": 51, "Демоны": 157, "Дилдо": 53,
    "Домохозяйка": 54, "Друзья детства": 4828, "Дыра в стене": 55, "Жестокость": 56, "Живая одежда": 290,
    "Запугивание": 625, "Зверолюди": 162, "Золотой дождь": 57, "Зомби": 155, "Зоофилия": 351, "Зрелые женщины": 59,
    "Игра на выживание": 681, "Избиение": 223, "Измена": 713, "Изнасилование": 61, "Инопланетяне": 62, "Инсеки": 366,
    "Инцест": 63, "Иррумация": 544, "Исполнение желаний": 64, "Исторические": 380, "Камера": 66, "Кляп": 288,
    "Колготки": 67, "Косплей": 68, "Кримпай": 3, "Куннилингус": 69, "Купальники": 70, "Лактация": 284,
    "Латекс и кожа": 71, "Магия": 170, "Маленькая грудь": 73, "Манипуляция памятью": 478, "Мастурбация": 74,
    "Матриархат": 705, "Медсестра": 221, "Мерзкий дядька": 76, "Месть": 259, "Милф": 77, "Много девушек": 78,
    "Много парней": 370, "Много спермы": 79, "Молоко": 80, "Монашка": 353, "Монстродевушки": 81, "Монстры": 82,
    "Мочеиспускание": 83, "Мужская беременность": 795, "Мурим": 632, "На природе": 84, "Наблюдение": 85,
    "Насекомые": 285, "Не хентай": 1827, "Небритая киска": 86, "Небритые подмышки": 87, "Недопонимание": 4823,
    "Нетораре": 88, "Ножницы": 481, "Нэтори": 11, "Обмен телами": 346, "Обратный исекай": 4841, "Обычный секс": 90,
    "Огромная грудь": 91, "Огромный член": 92, "Одержимость": 4826, "Омегавсерс": 507, "Омораси": 93,
    "Оральный секс": 94, "Орки": 95, "Остановка времени": 296, "Офисные Работники": 200, "Оякодон": 359,
    "Пайзури": 96, "Парень пассив": 97, "Переодевание": 98, "Пирсинг": 308, "Пляж": 99, "Подвязки": 282,
    "Подглядывание": 101, "Подчинение": 102, "Пожилой протагонист": 674, "Полиамория": 692, "Полностью CGI": 560,
    "Похищение": 103, "Превозмогание": 104, "Принуждение": 105, "Прозрачная одежда": 106,
    "Проникновение в соски": 371, "Проституция": 107, "Психические отклонения": 108, "Психический слом": 9,
    "Публичный секс": 109, "Пытки": 224, "Пьяные": 110, "Рабы": 356, "Разница в возрасте": 398, "Регрессия": 637,
    "С Сюжетом": 337, "Сuminside": 4, "Сгенерировано ИИ": 633, "Секс-игрушки": 112, "Сексуально возбуждённая": 113,
    "Сибари": 114, "Современность": 4808, "Спортивная форма": 117, "Спортивное тело": 334, "Спящие": 118,
    "Средневековье": 644, "Стимуляция пальцами": 1863, "Страпон": 119, "Стримы": 4821, "Суккуб": 120,
    "Татуировки": 29, "Темнокожие": 121, "Тентакли": 122, "Толстушки": 123, "Трап": 125, "Трисомия": 438,
    "Увеличение груди": 4825, "Ужасы": 126, "Униформа": 127, "Учитель": 352, "Ушастые": 128, "Фантазии": 129,
    "Фемдом": 130, "Фестиваль": 131, "Фетиш": 132, "Фиктивные отношения": 741, "Фистинг": 133, "Фурри": 134,
    "Футанари": 136, "Футанари имеет парня": 137, "Фэнтези мир": 4810, "Цельный купальник": 138, "Цундэрэ": 139,
    "Частично сгенерировано ИИ": 4839, "Чикан": 140, "Чулки": 141, "Шимайдон": 365, "Шлюха": 142, "Шоу-бизнес": 4812,
    "Эксгибиционизм": 143, "Эксперименты над людьми": 1839, "Эльфы": 217, "Эмоциональное насилие": 4835,
    "Яндере": 368}
EROTICA = "Эротика"               # MangaLib's own word for what is explicit (``content_marks``: hentai)
# one genre under two names: shown by the first
_SAME = {"Боевик": "Экшен", "Школьники": "Школа"}
MANGA_TOP = ("Романтика", "Фэнтези", "Экшен", "Комедия", "Драма", "Приключения", "Повседневность", "Сёнэн", "Сэйнэн",
             "Школа", "Сверхъестественное", "Боевые искусства", "Психология", "Трагедия", "Ужасы", "Гарем")
# Remanga's categories — its tags (``/api/forms/titles/?get=categories``, 6.11.2026)
REMANGA_TAGS = {
    "Алхимия": 47, "Амнезия": 121, "Ангелы": 48, "Аниме": 127, "Антигерой": 26, "Антиутопия": 49, "Апокалипсис": 50,
    "Аристократия": 117, "Армия": 51, "Артефакты": 52, "Боги": 45, "Бои на мечах": 122, "Борьба за власть": 54,
    "Будущее": 55, "В цвете": 6, "Веб": 5, "Вестерн": 56, "Видеоигры": 35, "Владыка демонов": 57,
    "Волшебные существа": 59, "Воспоминания из другого мира": 60, "Выживание": 41, "ГГ женщина": 63, "ГГ имба": 110,
    "ГГ мужчина": 64, "ГГ не человек": 123, "Геймеры": 61, "Гильдии": 62, "Горничные": 23, "Грузовик-сан": 125,
    "Гяру": 28, "Демоны": 15, "Дружба": 67, "Ёнкома": 8, "Жестокий мир": 69, "Животные компаньоны": 70,
    "Зверолюди": 19, "Зомби": 14, "Игровые элементы": 73, "Исекай": 115, "Космос": 76, "Криминал": 36,
    "Кулинария": 16, "Культивация": 18, "Лоли": 108, "Магическая академия": 78, "Магия": 22, "Медицина": 17,
    "Месть": 79, "Монстры": 38, "Музыка": 39, "Навыки": 80, "Наёмники": 81, "Насилие / жестокость": 82,
    "Научпоп": 824, "Нежить": 83, "Ниндзя": 30, "Обратный Гарем": 40, "Офисные работники": 31, "Пародия": 85,
    "Подземелья": 86, "Политика": 87, "Полиция": 32, "Путешествия во времени": 43, "Разумные расы": 88,
    "Ранги силы": 68, "Реинкарнация": 13, "Роботы": 89, "Рыцари": 90, "Самураи": 33, "Сборник": 10, "Сингл": 11,
    "Система": 91, "Скрытие личности": 93, "Спасение мира": 94, "Средневековье": 25, "Стимпанк": 92,
    "Супер герои": 95, "Традиционные игры": 34, "Тупой ГГ": 109, "Умный ГГ": 111, "Упоротость": 124,
    "Управление территорией": 114, "Учебное заведение": 126, "Учитель / ученик": 96, "Хикикомори": 21, "Шантаж": 99}
# MangaLib's tags of its site 1 (``/api/constants?fields[]=tags``, ``site_ids`` with 1)
MANGALIB_TAGS = {
    "Body Horror": 641, "Азартные игры": 304, "Алхимия": 225, "Амнезия / Потеря памяти": 347, "Ангелы": 226,
    "Антигерой": 175, "Антиутопия": 227, "Апокалипсис": 228, "Аристократия": 4837, "Армия": 229, "Артефакты": 230,
    "Безответная любовь": 671, "Боги": 218, "Боевые искусства": 526, "Бои на мечах": 231, "Борьба за власть": 232,
    "Брат и сестра": 233, "Будущее": 234, "Ведьма": 339, "Вестерн": 235, "Видеоигры": 204,
    "Викторианская эпоха": 4809, "Виртуальная реальность": 214, "Владыка демонов": 236, "Военные": 198, "Война": 237,
    "Волшебники / маги": 281, "Волшебные существа": 239, "Воспоминания из другого мира": 240, "Выживание": 212,
    "Газлайтинг": 4834, "Гайдверс": 1828, "ГГ - Мэри Сью": 4779, "ГГ женщина": 294, "ГГ имба": 292,
    "ГГ мужчина": 295, "ГГ не ояш": 325, "ГГ не человек": 331, "Геймеры": 241, "Гильдии": 242, "Глупый ГГ": 297,
    "Гоблины": 245, "Горничные": 171, "Градостроение": 4813, "Гуро": 307, "Гяру": 197, "Демоны": 157,
    "Драконы": 313, "Древний Китай": 679, "Дружба": 247, "Друзья детства": 4828, "Жестокий мир": 249,
    "Животные компаньоны": 250, "Завоевание мира": 251, "Запугивание": 625, "Зверолюди": 162,
    "Здоровые отношения": 4831, "Злые духи": 252, "Зомби": 155, "Игра на выживание": 681, "Игровые элементы": 253,
    "Изгнание": 4820, "Измена": 713, "Империи": 254, "Исторические": 380, "Камера": 66, "Квесты": 255,
    "Кемпинг": 789, "Космос": 256, "Кулинария": 152, "Культивирование": 369, "ЛГБТ": 343, "Легендарное оружие": 257,
    "Лоли": 187, "Магическая академия": 258, "Магия": 170, "Матриархат": 705, "Мафия": 172, "Медицина": 159,
    "Месть": 259, "Монстродевушки": 81, "Монстры": 82, "Мурим": 632, "Навыки / способности": 260, "Наёмники": 261,
    "Насилие / жестокость": 262, "Недопонимание": 4823, "Нежить": 263, "Нездоровые отношения": 4832, "Ниндзя": 199,
    "Обмен телами": 346, "Обратный Гарем": 210, "Обратный исекай": 4841, "Обретённая семья": 643,
    "Огнестрельное оружие": 264, "Одержимость": 4826, "От ненависти до любви": 4827, "Офисные Работники": 200,
    "Пародия": 265, "Пираты": 341, "Подземелья": 314, "Пожилой протагонист": 674, "Политика": 311, "Полиция": 201,
    "Полностью CGI": 560, "Преступники / Криминал": 205, "Призраки / Духи": 196, "Приручение существ": 691,
    "Прыжки между мирами": 321, "Путешествие": 646, "Путешествие во времени": 213, "Рабы": 356,
    "Разница в возрасте": 398, "Разумные расы": 268, "Ранги силы": 248, "Регрессия": 637, "Реинкарнация": 154,
    "Роботы": 269, "Родительство": 678, "Рыцари": 270, "Самураи": 202, "Сгенерировано ИИ": 633, "Сирота": 506,
    "Система": 271, "Скрытие личности": 273, "Современность": 4808, "Спасение мира": 274, "Спортивное тело": 334,
    "Средневековье": 644, "Стимпанк": 272, "Стримы": 4821, "Супергерои": 275, "Традиционные игры": 184,
    "Умный ГГ": 302, "Учитель": 352, "Фиктивные отношения": 741, "Философия": 277, "Фэнтези мир": 4810,
    "Хикикомори": 166, "Холодное оружие": 278, "Частично сгенерировано ИИ": 4839, "Шантаж": 279, "Шоу-бизнес": 4812,
    "Эксперименты над людьми": 1839, "Эльфы": 217, "Якудза": 165, "Яндере": 368, "Япония": 280}
# one tag under two names (letters alike — «Супер герои» and «Супергерои» — are one by themselves): shown by the first
_SAME_TAGS = {"Культивирование": "Культивация", "Навыки / способности": "Навыки",
              "Путешествие во времени": "Путешествия во времени", "Преступники / Криминал": "Криминал",
              "Амнезия / Потеря памяти": "Амнезия", "Учитель": "Учитель / ученик", "Глупый ГГ": "Тупой ГГ"}
NEVER_TAGS = F.CATALOG_NEVER_TAGS  # the program's rule about minors: never offered, always left out (core/filter.py)

# YummyAnime's genres (``/anime/genres``: value, title, group — -1 «Японские», -2 «Классические», -3 «Другие», or the
# genre it is a part of)
YUMMY_GENRES = (
    (2, "Бисёнэн", -1), (4, "Дзёсэй", -1), (5, "Махо-сёдзё", -1), (6, "Сёдзё", -1), (7, "Сёдзё-ай", -1),
    (8, "Сёнэн", -1), (9, "Сёнэн-ай", -1), (10, "Сэйнэн", -1), (11, "Этти", -1), (25, "Меха", -1),
    (102, "Гендерная интрига", -1), (106, "Исэкай", -1),
    (13, "Вестерн", -2), (14, "Детектив", -2), (15, "Драма", -2), (16, "Комедия", -2), (18, "Преступный мир", -2),
    (31, "Мистика", -2), (32, "Приключения", -2), (33, "Романтика", -2), (35, "Триллер", -2), (36, "Ужасы", -2),
    (37, "Фантастика", -2), (42, "Фэнтези", -2), (63, "Экшен", -2), (91, "Спорт", -2), (98, "Психология", -2),
    (100, "Эротика", -2),
    (17, "Пародия", 16), (19, "Воры", 18), (20, "Мафия/Якудза", 18), (21, "Охотники за головами", 18),
    (22, "Пираты", -3), (23, "Террористы", 18), (24, "Убийцы", 18), (26, "Андроиды", 37),
    (27, "Пилотируемые роботы", 25), (28, "Силовые костюмы", 37), (29, "Искусственный интеллект", 37),
    (30, "Трансформеры", 25), (34, "Любовный треугольник", 33), (38, "Инопланетные расы", 37), (39, "Киборги", 37),
    (40, "Космос", 37), (41, "Путешествия во времени", 37), (43, "Альтернативная реальность", 37), (44, "Ангелы", 42),
    (45, "Божества", 42), (46, "Вампиры", 42), (47, "Ведьмы", 42), (48, "Демоны", 42), (49, "Драконы", 42),
    (50, "Зомби", 103), (51, "Магия", 42), (52, "Призраки", 103), (53, "Русалки", 42),
    (54, "Современное фэнтези", 42), (55, "Суккубы", 42), (56, "Темное фэнтези", 42), (57, "Темные эльфы", 42),
    (58, "Феи", 42), (59, "Целый фэнтези мир", -3), (60, "Эльфы", 42), (61, "Виртуальная реальность", 104),
    (62, "Параллельный мир", 42), (64, "Боевые искусства", 63), (65, "Ниндзя", 63), (66, "Перестрелки", 63),
    (67, "Прокси бои", -3), (68, "Самураи", 63), (69, "Сражения на мечах", 63), (70, "Суперспособности", 103),
    (72, "Альтернативная история", -3), (73, "Антивойна", -3), (74, "Антиутопия", -3), (75, "Война", -3),
    (76, "Военная тематика", -3), (77, "Гарем", -3), (78, "Искусство", -3), (79, "Музыка", 78),
    (80, "Исторический", -3), (81, "Киберпанк", -3), (82, "Кулинария", -3), (85, "Нелинейный сюжет", -3),
    (86, "Повседневность", -3), (87, "Политика", -3), (88, "Полицейские", -3), (89, "Постапокалиптика", -3),
    (90, "Русские в аниме", -3), (92, "Баскетбол", 91), (93, "Стимпанк", -3), (94, "Тайный заговор", -3),
    (95, "Школьная жизнь", -3), (96, "Гарем (для девочек)", -3), (97, "Полулюди", -3), (99, "Манга", 78),
    (101, "Не японское", -3), (103, "Сверхъестественное", -3), (104, "Игры", -3), (107, "Китайское 3D", 101),
    (108, "Мотоциклы", -3), (109, "Хулиганы", -3))
VIDEO_TOP = ("Сёнэн", "Сэйнэн", "Сёдзё", "Комедия", "Драма", "Романтика", "Приключения", "Фэнтези", "Экшен",
             "Детектив", "Мистика", "Ужасы", "Фантастика", "Повседневность", "Спорт", "Исэкай")


def _sorted(table: dict) -> "OrderedDict[str, dict]":
    return OrderedDict(sorted(table.items(), key=lambda x: x[0].casefold().replace("ё", "е")))


def _merged(*tables, same: dict) -> "OrderedDict[str, dict]":
    """{shown name: {site: its number}} of the sites' tables ((site, table) each); one name of them (``same``, or the
    letters alike — ``name_key``) — one entry, shown by the first site's name."""
    out: dict = {}
    keys: dict = {}
    for site, table in tables:
        for name, number in table.items():
            shown = same.get(name, name)
            shown = keys.setdefault(S.name_key(shown), shown)
            out.setdefault(shown, {})[site] = number
    return _sorted(out)


MANGA_GENRES = _merged(("remanga", REMANGA_GENRES), ("mangalib", MANGALIB_GENRES), ("hentailib", HENTAILIB_GENRES),
                      same=_SAME)
_NEVER = {S.name_key(n) for n in NEVER_TAGS}
_ALL_TAGS = _merged(("remanga", REMANGA_TAGS), ("mangalib", MANGALIB_TAGS), ("hentailib", HENTAILIB_TAGS),
                    same=_SAME_TAGS)
MANGA_TAGS = OrderedDict((k, v) for k, v in _ALL_TAGS.items() if S.name_key(k) not in _NEVER)
NEVER_IDS = {site: tuple(v[site] for k, v in _ALL_TAGS.items() if S.name_key(k) in _NEVER and site in v)
             for site in ("remanga", "mangalib")}
# (v5.44.96) «Лоликон», «Сетакон», «Юные», «Девочки-волшебницы» — and MangaLib's «Лоли» (187): HentaiLib's titles carry
# MangaLib's numbers of tags too (6.11.2026: 3 of its first 60 opened had «Лоли» 187 alone)
NEVER_IDS["hentailib"] = F.HENTAILIB_NEVER_TAGS
NEVER_GENRE_IDS = F.NEVER_GENRE_IDS                # …and «Школа», «Кодомо» (core/filter.py)
never_genres = F.never_genres
VIDEO_GENRES = _sorted({title: {"yummy": value} for value, title, _g in YUMMY_GENRES})


def genres(kind: str) -> "OrderedDict[str, dict]":
    return VIDEO_GENRES if kind == "video" else MANGA_GENRES


def tags(kind: str) -> "OrderedDict[str, dict]":
    return OrderedDict() if kind == "video" else MANGA_TAGS


def offered(table: "OrderedDict[str, dict]", kind: str, mode: str, *, of_genres: bool = False) -> list:
    """(v5.44.96) The names of a table of genres (``of_genres``) or tags the page offers in this mode: those of a site
    the list can ask (``sites_shown``) — not a tag of HentaiLib alone in «SFW», not one of Remanga alone in «NSFW»; a
    genre a site has as a tag (:data:`GENRE_AS_TAG`) — its; a genre the site leaves out in the mode
    (:func:`never_genres`) — not its."""
    shown = sites_shown(kind, mode)

    def takes(name: str, ids: dict, site: str) -> bool:
        if not of_genres:
            return site in ids
        if site in ids:
            return ids[site] not in never_genres(site, mode)
        return kind != "video" and name in GENRE_AS_TAG.get(site, {})
    return [name for name, ids in table.items() if any(takes(name, ids, site) for site in shown)]


def top_genres(kind: str) -> tuple:
    return VIDEO_TOP if kind == "video" else MANGA_TOP


# ── types, statuses, orders ───────────────────────────────────────────────────────────────────────────────────────
TYPES = {
    "manga": (("manga", "Манга", {"remanga": (1,), "mangalib": (1, 4), "hentailib": (1, 4)}),
              ("comics", "Комиксы", {"remanga": (4,), "mangalib": (9,), "hentailib": (9,)})),
    "manhwa": (("manhwa", "Манхва", {"remanga": (2,), "mangalib": (5,), "hentailib": (5,)}),
               ("manhua", "Маньхуа", {"remanga": (3,), "mangalib": (6,), "hentailib": (6,)}),
               ("russian", "Русские", {"remanga": (5,), "mangalib": (8,), "hentailib": (8,)}),
               ("other", "Другое", {"remanga": (6, 7, 22)})),
    "video": (("tv", "ТВ-сериал", {"yummy": ("tv",)}), ("movie", "Фильм", {"yummy": ("movie",)}),
              ("ova", "OVA и ONA", {"yummy": ("ova", "ona")}), ("special", "Спешл", {"yummy": ("special",)})),
}
STATUS = {
    "manga": (("ongoing", "Выходит", {"remanga": (2,), "mangalib": (1,), "hentailib": (1,)}),
              ("done", "Завершён", {"remanga": (1,), "mangalib": (2,), "hentailib": (2,)}),
              ("announce", "Анонс", {"remanga": (5,), "mangalib": (3,), "hentailib": (3,)}),
              ("frozen", "Заморожен", {"remanga": (3,), "mangalib": (4, 5), "hentailib": (4, 5)})),
    "video": (("ongoing", "Выходит", {"yummy": ("ongoing",)}), ("done", "Вышел", {"yummy": ("released",)}),
              ("announce", "Анонс", {"yummy": ("announcement",)})),
}
ORDERS = {
    "manga": (("popular", "Популярное", {"remanga": "-views", "mangalib": "views", "hentailib": "views"}),
              ("updates", "Новые главы", {"remanga": "-chapter_date", "mangalib": "last_chapter_at",
                                          "hentailib": "last_chapter_at"}),
              ("rating", "По оценке", {"remanga": "-rating", "mangalib": "rate_avg", "hentailib": "rate_avg"}),
              ("chapters", "Больше глав", {"remanga": "-count_chapters", "mangalib": "chap_count",
                                           "hentailib": "chap_count"}),
              ("new", "Новые тайтлы", {"remanga": "-id", "mangalib": "created_at", "hentailib": "created_at"})),
    # «Новые» — by the year, newest first; without a status chosen — out already or coming out (the announcements of
    # 2028 would be the whole first page)
    "video": (("popular", "Популярное", {"yummy": ("views", "0")}), ("top", "По оценке", {"yummy": ("top", "")}),
              ("new", "Новые", {"yummy": ("year", "0")}), ("title", "По алфавиту", {"yummy": ("title", "")})),
}
SITES = {"manga": ("remanga", "mangalib", "hentailib", "hentaichan", "allhentai", "nhentai"),
         "manhwa": ("remanga", "mangalib", "hentailib", "allhentai"), "video": ("yummy", "hentasis")}
LABELS = {"remanga": "Remanga", "mangalib": "MangaLib", "yummy": "YummyAnime", "hentaichan": "Хентай-тян",
          "hentasis": "Hentasis", "hentailib": "HentaiLib", "nhentai": "nhentai", "allhentai": "AllHentai"}
# (v5.44.93) sites of hentai alone; (v5.44.96) HentaiLib; (v5.44.97) nhentai; (v5.44.99) AllHentai
ADULT_SITES = ("hentaichan", "hentasis", "hentailib", "nhentai", "allhentai")
FILTERING = ("remanga", "mangalib", "yummy", "hentailib")  # the sites that take the filters
# (v5.44.93) the orders of the lists of a site of hentai: its own list for each (none — it is not asked)
# — Хентай-тян's «Популярное» is empty now (19 bytes, 6.11.2026: ``Hentaichan.popular_empty``): «Обновления» for it too
HENTAICHAN_ORDER = {"popular": "new", "updates": "new", "new": "new"}
HENTASIS_ORDERS = ("popular", "new")         # its one list — the newest first: as «Популярное» too (said on the page)
# (v5.44.97) nhentai: «Популярное» — its search by the favourites of all time, the new — its list of the newest
NHENTAI_ORDER = {"popular": "popular", "updates": "date", "new": "date"}
# (v5.44.99) AllHentai: its catalog's orders (an order it does not know is its default — RATING: not asked by that);
# its kinds (``category``) of each tab, and of each type the tab offers
ALLHENTAI_ORDER = {"popular": "POPULARITY", "updates": "DATE_UPDATE", "new": "DATE_CREATE", "rating": "RATING"}
ALLHENTAI_PAGE = 50
ALLHENTAI_KINDS = {"manga": {"manga": ("Манга", "3D", "Без текста"), "comics": ("Комикс",)},
                   "manhwa": {"manhwa": ("Манхва",), "manhua": ("Маньхуа",)}}
_ALLHENTAI_KNOWN = frozenset(k for tab in ALLHENTAI_KINDS.values() for names in tab.values() for k in names)
_ALLHENTAI_STATUS = {"ONGOING": "ongoing", "FINISHED": "done", "ANNOUNCE": "announce", "POSTPONED": "frozen",
                     "FROZEN": "frozen", "PAUSED": "frozen"}
# a tile's words of the sites' types and statuses
_REMANGA_TYPE = {1: "manga", 4: "comics", 2: "manhwa", 3: "manhua", 5: "russian", 6: "other", 7: "other", 22: "other"}
_MANGALIB_TYPE = {1: "manga", 4: "manga", 9: "comics", 5: "manhwa", 6: "manhua", 8: "russian"}
_TYPE_WORD = {"manga": "Манга", "comics": "Комикс", "manhwa": "Манхва", "manhua": "Маньхуа", "russian": "Русский",
              "other": "Другое", "tv": "ТВ", "movie": "Фильм", "ova": "OVA", "ona": "ONA", "special": "Спешл"}
_REMANGA_STATUS = {2: "ongoing", 1: "done", 5: "announce", 3: "frozen", 6: "licensed"}
_MANGALIB_STATUS = {1: "ongoing", 2: "done", 3: "announce", 4: "frozen", 5: "frozen"}
_YUMMY_STATUS = {"ongoing": "ongoing", "released": "done", "announcement": "announce"}
_STATUS_WORD = {"ongoing": "Выходит", "done": "Завершён", "announce": "Анонс", "frozen": "Заморожен",
                "licensed": "Лицензия"}


def _players_here() -> bool:
    """(v5.44.114) The owner's copy reads the series of its video site (``core/video_dubs``); the public copy has a
    stand-in (``AVAILABLE = False``) — that site is not in its «Лента»."""
    from core import video_dubs
    return bool(getattr(video_dubs, "AVAILABLE", True))


def sites_for(kind: str, mode: str) -> tuple:
    """(v5.44.93) The kind's sites the mode of the program shows: «SFW» — not the sites of hentai; «NSFW» — they and
    MangaLib (its «Эротика»); «Всё» — every one."""
    mine = SITES["video" if kind == "video" else kind]
    if kind == "video" and not _players_here():
        mine = tuple(s for s in mine if s != "yummy")  # (v5.44.114) the public copy: its titles open nowhere there
    if mode == "sfw":
        return tuple(s for s in mine if s not in ADULT_SITES)
    if mode == "nsfw":
        return tuple(s for s in mine if s in ADULT_SITES or s == "mangalib")
    return mine


def sites_shown(kind: str, mode: str) -> tuple:
    """(v5.44.93) The sites whose titles the list of the mode can have: :func:`sites_for` without MangaLib in «NSFW»
    when the program has no sign-in there (it gives no hentai then — it is not asked, the page says why)."""
    return tuple(s for s in sites_for(kind, mode) if not (s == "mangalib" and mode == "nsfw" and not _signed(s)))


def filters_taken(kind: str, mode: str) -> bool:
    """(v5.44.93) Whether a site of the list takes the filters (NSFW of the videos: Hentasis alone — it takes none; of
    the manga without MangaLib's sign-in — Хентай-тян alone)."""
    return any(s in FILTERING for s in sites_shown(kind, mode))


def _signed(site: str) -> bool:
    return bool(S.token_of(site))


def types(kind: str) -> tuple:
    return TYPES["video" if kind == "video" else kind]


def statuses(kind: str) -> tuple:
    return STATUS["video" if kind == "video" else "manga"]


def orders(kind: str) -> tuple:
    return ORDERS["video" if kind == "video" else "manga"]


# ── a query ───────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Query:
    kind: str
    order: str = ""
    text: str = ""
    genres: tuple = ()               # shown names: all of them
    without: tuple = ()              # shown names: none of them
    tags: tuple = ()                 # (manga) tags: all of them
    untags: tuple = ()               # …none of them
    types: tuple = ()                # keys of :data:`TYPES`
    status: tuple = ()               # keys of :data:`STATUS`
    chapters: tuple = (0, 0)         # (from, to); 0 — no bound (not for the video: YummyAnime does not filter them)
    rating: tuple = (0.0, 0.0)
    years: tuple = (0, 0)
    sites: tuple = ()                # the sites asked (() — all of the kind's)
    mode: str = "all"                # «SFW / Всё / NSFW» of the program

    def __post_init__(self):
        kind = self.kind if self.kind in KINDS else "manga"
        object.__setattr__(self, "kind", kind)
        keys = [k for k, _l, _s in orders(kind)]
        object.__setattr__(self, "order", self.order if self.order in keys else keys[0])
        known = genres(kind)
        chosen = tuple(dict.fromkeys(g for g in self.genres if g in known))[:MAX_CHOSEN]
        object.__setattr__(self, "genres", chosen)
        object.__setattr__(self, "without", tuple(dict.fromkeys(
            g for g in self.without if g in known and g not in chosen))[:MAX_CHOSEN])
        known = tags(kind)
        chosen = tuple(dict.fromkeys(g for g in self.tags if g in known))[:MAX_CHOSEN]
        object.__setattr__(self, "tags", chosen)
        object.__setattr__(self, "untags", tuple(dict.fromkeys(
            g for g in self.untags if g in known and g not in chosen))[:MAX_CHOSEN])
        object.__setattr__(self, "types", tuple(k for k, _l, _s in types(kind) if k in self.types))
        object.__setattr__(self, "status", tuple(k for k, _l, _s in statuses(kind) if k in self.status))
        object.__setattr__(self, "text", " ".join(str(self.text or "").split())[:120])
        object.__setattr__(self, "mode", self.mode if self.mode in ("sfw", "all", "nsfw") else "all")
        object.__setattr__(self, "chapters", (0, 0) if kind == "video" else _pair(self.chapters, int, 100000))
        object.__setattr__(self, "rating", _pair(self.rating, float, 10.0))
        object.__setattr__(self, "years", _pair(self.years, int, 2100))
        mine = sites_for(kind, self.mode)
        picked = tuple(s for s in mine if s in self.sites)
        object.__setattr__(self, "sites", picked if picked and len(picked) < len(mine) else ())

    def sig(self) -> str:
        return hashlib.sha1(repr(self).encode("utf-8")).hexdigest()[:16]

    def asked(self) -> tuple:
        """The sites of the query (of those the mode shows — :func:`sites_for`)."""
        return self.sites or sites_for(self.kind, self.mode)

    def plain(self) -> bool:
        """Nothing chosen but the order (and the mode)."""
        return not (self.text or self.genres or self.without or self.tags or self.untags or self.types or self.status
                    or any(self.chapters) or any(self.rating) or any(self.years) or self.sites)


def _pair(value, cast, top) -> tuple:
    try:
        low, high = (cast(max(0, min(top, float(x or 0)))) for x in (tuple(value) + (0, 0))[:2])
    except (TypeError, ValueError):
        return (cast(0), cast(0))
    if low and high and high < low:
        low, high = high, low
    return (low, high)


def as_number(text, cast=int):
    """«20», «8,5», «» → a number (0 — none)."""
    raw = str(text or "").strip().replace(",", ".")
    try:
        return cast(float(raw)) if raw else cast(0)
    except ValueError:
        return cast(0)


# ── the sites' addresses ─────────────────────────────────────────────────────────────────────────────────────────
def _ids(table: tuple, keys: tuple, site: str) -> list:
    return [x for key, _label, sites in table if key in keys for x in sites.get(site, ())]


def _all_ids(table: tuple, site: str) -> list:
    return [x for _k, _l, sites in table for x in sites.get(site, ())]


def _bounds(pairs: "list[tuple[str, object]]") -> str:
    return "".join(f"&{name}={value:g}" if isinstance(value, float) else f"&{name}={value}"
                   for name, value in pairs if value)


def _genre_as_tag() -> dict:
    """{site: {a genre it has not: its tag of that name}} — MangaLib has «Боевые искусства» and «Мурим» as tags,
    Remanga «Исекай», «Магия», «Демоны» as categories."""
    out = {}
    for site in ("remanga", "mangalib", "hentailib"):
        by_key = {S.name_key(name): ids[site] for name, ids in MANGA_TAGS.items() if site in ids}
        out[site] = {name: by_key[S.name_key(name)] for name, ids in MANGA_GENRES.items()
                     if site not in ids and S.name_key(name) in by_key}
    return out


GENRE_AS_TAG = _genre_as_tag()


def _genre_of(q: Query, name: str, site: str) -> "tuple[str, int] | None":
    """("g", its number) of a genre at ``site``, ("t", the number) of its tag of that name — None: neither."""
    number = genres(q.kind)[name].get(site)
    if number is not None:
        return "g", number
    tag = GENRE_AS_TAG.get(site, {}).get(name) if q.kind != "video" else None
    return ("t", tag) if tag is not None else None


def _wanted(q: Query, site: str) -> "tuple[list, list, str]":
    """([("g" or "t", the number)] of the genres and tags all of which a title of ``site`` has, […] of those it has
    none of, why it has no such title: "" — it may)."""
    label = LABELS[site]
    out, left = [], []
    never = never_genres(site, q.mode)
    for name in q.genres:
        got = _genre_of(q, name, site)
        if got is None:
            return [], [], f"у {label} нет жанра «{name}»"
        if got[0] == "g" and got[1] in never:       # (v5.44.96) the program's rule about minors
            return [], [], f"хентай с жанром «{name}» программа не показывает"
        out.append(got)
    for name in q.tags:
        number = tags(q.kind)[name].get(site)
        if number is None:
            return [], [], f"у {label} нет тега «{name}»"
        out.append(("t", number))
    for name in q.without:
        got = _genre_of(q, name, site)
        if got is not None:
            left.append(got)
    left += [("t", tags(q.kind)[name][site]) for name in q.untags if site in tags(q.kind)[name]]
    left += [("t", n) for n in NEVER_IDS.get(site, ())]
    left += [("g", n) for n in never]
    return out, list(dict.fromkeys(left)), ""


def remanga_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(Remanga's address for page ``page`` of the query, why it is not asked)."""
    source = S.by_name("remanga")
    if source is None:
        return None, ""
    if q.mode == "nsfw":
        return None, "у Remanga без входа нет хентая"
    if q.text:                                           # its search: by the name, its own order, no filters
        if q.genres or q.without or q.tags or q.untags or q.status or any(q.chapters) or any(q.rating) \
                or any(q.years):
            return None, "Remanga ищет по названию без фильтров"
        return f"{source.api()}/api/v2/search/?query={quote(q.text)}&count={REMANGA_PAGE}&page={page}", ""
    chosen = _ids(types(q.kind), q.types, "remanga") if q.types else _all_ids(types(q.kind), "remanga")
    if not chosen:
        return None, "у Remanga нет таких типов"
    wanted, left, why = _wanted(q, "remanga")
    if why:
        return None, why
    order = dict((k, s) for k, _l, s in orders(q.kind))[q.order]["remanga"]
    parts = [f"ordering={order}", f"count={REMANGA_PAGE}", f"page={page}"] + [f"types={t}" for t in chosen]
    parts += [f"genres={n}" if what == "g" else f"categories={n}" for what, n in wanted]
    parts += [f"exclude_genres={n}" if what == "g" else f"exclude_categories={n}" for what, n in left]
    parts += [f"status={s}" for s in _ids(statuses(q.kind), q.status, "remanga")]
    query = "&".join(parts) + _bounds([("count_chapters_gte", q.chapters[0]), ("count_chapters_lte", q.chapters[1]),
                                       ("rating_gte", float(q.rating[0])), ("rating_lte", float(q.rating[1])),
                                       ("issue_year_gte", q.years[0]), ("issue_year_lte", q.years[1])])
    return f"{source.api()}/api/v2/search/catalog/?{query}", ""


def mangalib_url(q: Query, page: int) -> "tuple[str | None, str]":
    if q.mode == "nsfw" and not _signed("mangalib"):     # (v5.44.93) its «Эротика» without a sign-in: 0 titles
        return None, "MangaLib показывает хентай только со входом («Настройки → Сайты манги»)"
    return _lib_url(q, page, "mangalib")


def hentailib_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.96) HentaiLib: the API and the filters of MangaLib, its site 4 — every title of it is hentai; what the
    program's rule leaves out — always (``_wanted``: :data:`NEVER_IDS`, :data:`NEVER_GENRE_IDS`)."""
    return _lib_url(q, page, "hentailib")


def _lib_url(q: Query, page: int, site: str) -> "tuple[str | None, str]":
    source = S.by_name(site)
    if source is None:
        return None, ""
    label = LABELS[site]
    chosen = _ids(types(q.kind), q.types, site) if q.types else _all_ids(types(q.kind), site)
    if not chosen:
        return None, f"у {label} нет таких типов"
    wanted, left, why = _wanted(q, site)
    if why:
        return None, why
    order = dict((k, s) for k, _l, s in orders(q.kind))[q.order][site]
    parts = [f"site_id[]={source.SITE_ID}", f"page={page}", f"sort_by={order}", "sort_type=desc"] + \
        [f"types[]={t}" for t in chosen]
    erotica = MANGA_GENRES[EROTICA]["mangalib"] if site == "mangalib" else None
    genre_ids = [n for what, n in wanted if what == "g"] + ([erotica] if erotica and q.mode == "nsfw" else [])
    parts += [f"genres[]={n}" for n in dict.fromkeys(genre_ids)]
    parts += [f"tags[]={n}" for what, n in wanted if what == "t"]
    unwanted = [n for what, n in left if what == "g"] + ([erotica] if erotica and q.mode == "sfw" else [])
    parts += [f"genres_exclude[]={n}" for n in dict.fromkeys(unwanted)]
    parts += [f"tags_exclude[]={n}" for what, n in left if what == "t"]
    parts += [f"status[]={s}" for s in _ids(statuses(q.kind), q.status, site)]
    if q.text:
        parts.append(f"q={quote(q.text)}")
    query = "&".join(parts) + _bounds([("chap_count_min", q.chapters[0]), ("chap_count_max", q.chapters[1]),
                                       ("rating_min", float(q.rating[0])), ("rating_max", float(q.rating[1])),
                                       ("year_min", q.years[0]), ("year_max", q.years[1])])
    return f"{source.api()}/api/manga?{query}", ""


def yummy_url(q: Query, page: int, api: str) -> "tuple[str | None, str]":
    sort, forward = dict((k, s) for k, _l, s in orders("video"))[q.order]["yummy"]
    parts = [f"limit={VIDEO_PAGE}", f"offset={VIDEO_PAGE * (page - 1)}", f"sort={sort}"]
    if forward:
        parts.append(f"sort_forward={forward}")
    if q.genres:
        parts.append("genres=" + ",".join(str(VIDEO_GENRES[g]["yummy"]) for g in q.genres))
    if q.without:
        parts.append("exclude_genres=" + ",".join(str(VIDEO_GENRES[g]["yummy"]) for g in q.without))
    chosen = _ids(types("video"), q.types, "yummy")
    if chosen:
        parts.append("types=" + ",".join(chosen))
    said = _ids(statuses("video"), q.status, "yummy")
    if not said and q.order == "new":
        said = list(F.VIDEO_NEW_STATUSES)          # (core/filter.py) no announcements without a status chosen
    if said:
        parts.append("status=" + ",".join(said))
    if q.text:
        parts.append(f"q={quote(q.text)}")
    query = "&".join(parts) + _bounds([("from_year", q.years[0]), ("to_year", q.years[1]),
                                       ("min_rating", float(q.rating[0])), ("max_rating", float(q.rating[1]))])
    return f"{api.rstrip('/')}/anime?{query}", ""


def _filtered(q: Query, *, types_too: bool = True) -> bool:
    return bool(q.genres or q.without or q.tags or q.untags or q.status or any(q.chapters) or any(q.rating)
                or any(q.years) or types_too and q.types)


def hentaichan_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.93) Хентай-тян: its lists as they are (it filters nothing), its search — one page."""
    source = S.by_name("hentaichan")
    if source is None:
        return None, ""
    if _filtered(q, types_too=False):
        return None, "у Хентай-тян нет фильтров — его тайтлы видны без них"
    if q.types and "manga" not in q.types:
        return None, "у Хентай-тян только манга"
    if q.text:
        return (source.search_url(q.text) if page == 1 else None), ""
    how = HENTAICHAN_ORDER.get(q.order)
    if how is None:
        return None, "Хентай-тян так не сортирует"
    return source.feed_url(how, page), ""


def nhentai_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.97) nhentai: its newest or its search (by a name, «Популярное») — no filters, no other order."""
    if _filtered(q, types_too=False):
        return None, "у nhentai нет фильтров — его галереи видны без них"
    if q.types and "manga" not in q.types:
        return None, "у nhentai только манга"
    how = NHENTAI_ORDER.get(q.order)
    if how is None:
        return None, "nhentai так не сортирует"
    return N.list_url(how, page, q.text), ""


def allhentai_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.99) AllHentai: its catalog (no filters), its search by a name — one page."""
    source = S.by_name("allhentai")
    if source is None:
        return None, ""
    if _filtered(q, types_too=False):
        return None, "у AllHentai нет фильтров — его тайтлы видны без них"
    kinds = ALLHENTAI_KINDS.get(q.kind, {})
    if q.types and not any(t in kinds for t in q.types):
        return None, "у AllHentai таких нет"
    if q.text:
        return (f"{source.site()}/search/suggestion?query={quote(q.text)}" if page == 1 else None), ""
    how = ALLHENTAI_ORDER.get(q.order)
    if how is None:
        return None, "AllHentai так не сортирует"
    return f"{source.site()}/api/catalog/search?offset={ALLHENTAI_PAGE * (page - 1)}&sortType={how}", ""


def hentasis_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.93) Hentasis: its list, the newest first — no filters, no search, no other order."""
    if _filtered(q):
        return None, "у Hentasis нет фильтров — его тайтлы видны без них"
    if q.text:
        return None, "Hentasis не ищет по названию"
    if q.order not in HENTASIS_ORDERS:
        return None, "Hentasis так не сортирует"
    return HENTASIS_SITE + ("/" if page <= 1 else f"/page/{page}/"), ""


def addresses(q: Query, n: int, settings: "dict | None" = None) -> "dict[str, tuple]":
    """{site: (its address for page ``n`` of the query or None, why not)} — the query's sites."""
    out = {}
    for site in q.asked():
        if site == "remanga":
            out[site] = remanga_url(q, n)
        elif site == "mangalib":
            out[site] = mangalib_url(q, n)
        elif site == "hentailib":
            out[site] = hentailib_url(q, n)
        elif site == "hentaichan":
            out[site] = hentaichan_url(q, n)
        elif site == "hentasis":
            out[site] = hentasis_url(q, n)
        elif site == "nhentai":
            out[site] = nhentai_url(q, n)
        elif site == "allhentai":
            out[site] = allhentai_url(q, n)
        else:
            from core import video_dubs
            out[site] = yummy_url(q, n, video_dubs.api_of(settings or {}))
    return out


# ── a site's page of the list ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Item:
    """A title of a site's catalog (enough for a tile and for :func:`_merge`)."""
    site: str
    key: str
    names: list
    url: str
    type: str = ""                   # the site's word (:func:`manhwa_sources._fits` compares them)
    shown_type: str = ""             # the tile's word
    year: int = 0
    cover: str = ""
    rating: float = 0.0
    votes: int = 0
    adult: bool = False
    status: str = ""                 # the tile's word


@dataclass
class Got:
    items: list = field(default_factory=list)
    more: bool = False
    error: str = ""
    at: float = field(default_factory=time.time)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _remanga_items(data, q: Query) -> tuple:
    source = S.by_name("remanga")
    items = data.get("results") if isinstance(data, dict) else None
    if items is None and isinstance(data, dict):
        items = data.get("content")
    wanted = set(_ids(types(q.kind), q.types, "remanga") if q.types else _all_ids(types(q.kind), "remanga"))
    out = []
    for x in items or []:
        if not isinstance(x, dict) or not x.get("dir"):
            continue
        kind = _dict(x.get("type"))
        if q.text and S._int(kind.get("id")) not in wanted:
            continue                                  # its search has no filters: the kinds of this tab at least
        names = [x.get("main_name"), x.get("secondary_name"), *str(x.get("another_name") or "").split(" / ")]
        cover = _dict(x.get("cover"))
        path = str(cover.get("mid") or cover.get("low") or cover.get("high") or "")
        dir_ = str(x["dir"])
        state = _dict(x.get("status"))
        out.append(Item(site="remanga", key=dir_, names=[str(n).strip() for n in names if n and str(n).strip()],
                        url=f"{source.site()}/manga/{quote(dir_, safe='')}", type=str(kind.get("name") or ""),
                        shown_type=_TYPE_WORD.get(_REMANGA_TYPE.get(S._int(kind.get("id")), ""),
                                                  str(kind.get("name") or "")),
                        year=S._int(x.get("issue_year")), cover=urljoin(source.site() + "/", path) if path else "",
                        rating=round(S._float(x.get("avg_rating")), 1), votes=S._int(x.get("total_votes")),
                        adult=bool(x.get("is_erotic")),
                        status=_STATUS_WORD.get(_REMANGA_STATUS.get(S._int(state.get("id")), ""),
                                                str(state.get("name") or ""))))
    if isinstance(data, dict) and "next" in data and not q.text:
        return out, bool(data.get("next"))
    return out, len(items or []) >= REMANGA_PAGE


def _mangalib_items(data, q: Query, site: str = "mangalib") -> tuple:
    source = S.by_name(site)
    items = (data.get("data") if isinstance(data, dict) else None) or []
    out = []
    for x in items:
        if not isinstance(x, dict) or not x.get("slug_url"):
            continue
        names = [str(n).strip() for n in (x.get("rus_name"), x.get("eng_name"), x.get("name")) if n and str(n).strip()]
        age = str(_dict(x.get("ageRestriction")).get("label") or "")
        adult = source.adult_only or q.mode == "nsfw" or "RX" in age.upper()
        if adult and S.depicts_minors(names):         # (v5.44.96) the program's rule about minors — by the name too
            continue                                  # (the site's tags leave out what it tags so, not the rest)
        cover = _dict(x.get("cover"))
        rating = _dict(x.get("rating"))
        kind = _dict(x.get("type"))
        state = _dict(x.get("status"))
        out.append(Item(site=site, key=str(x["slug_url"]), names=names, adult=adult,
                        url=f"{source.site()}/ru/manga/{x['slug_url']}", type=str(kind.get("label") or ""),
                        shown_type=_TYPE_WORD.get(_MANGALIB_TYPE.get(S._int(kind.get("id")), ""),
                                                  str(kind.get("label") or "")),
                        year=S._year(x.get("releaseDate") or x.get("releaseDateString")),
                        cover=str(cover.get("default") or cover.get("md") or cover.get("thumbnail") or ""),
                        rating=round(S._float(rating.get("average")), 1), votes=S._int(rating.get("votes")),
                        status=_STATUS_WORD.get(_MANGALIB_STATUS.get(S._int(state.get("id")), ""),
                                                str(state.get("label") or ""))))
    meta = _dict(data.get("meta")) if isinstance(data, dict) else {}
    return out, bool(meta.get("has_next_page")) if "has_next_page" in meta else len(items) >= MANGALIB_PAGE


def _yummy_items(data, q: Query) -> tuple:
    from core import video_dubs
    items = data.get("response") if isinstance(data, dict) else None
    if isinstance(items, dict):
        items = items.get("data")
    out = []
    for x in items or []:
        if not isinstance(x, dict) or not x.get("anime_url"):
            continue
        poster = _dict(x.get("poster"))
        cover = str(poster.get("medium") or poster.get("big") or poster.get("small") or "")
        if cover.startswith("//"):
            cover = "https:" + cover
        kind = _dict(x.get("type"))
        alias = str(kind.get("alias") or "")
        sorts = [str(g.get("title")) for g in x.get("genres") or [] if isinstance(g, dict) and g.get("title")]
        age = str(_dict(x.get("min_age")).get("title") or "").strip()
        names = [str(x.get("title") or "").strip()] + [str(n).strip() for n in x.get("other_titles") or []
                                                      if isinstance(n, str)][:2]
        state = _YUMMY_STATUS.get(str(_dict(x.get("anime_status")).get("alias") or ""), "")
        out.append(Item(site="yummy", key=str(x["anime_url"]), names=[n for n in names if n],
                        url=f"{video_dubs.SITE}/catalog/item/{quote(str(x['anime_url']), safe='')}",
                        type=alias, shown_type=_TYPE_WORD.get(alias, str(kind.get("shortname") or "")),
                        year=S._int(x.get("year")), cover=cover,
                        rating=round(S._float(_dict(x.get("rating")).get("average")), 1),
                        votes=S._int(_dict(x.get("rating")).get("counters")),
                        adult=age.casefold() == "rx" or "Хентай" in sorts,
                        status="Вышел" if state == "done" else _STATUS_WORD.get(state, "")))
    return out, len(items or []) >= VIDEO_PAGE


_HENTASIS_ITEM = re.compile(r'<div\b[^>]*\bclass=["\'][^"\']*\bshort-item\b[^"\']*["\'][^>]*>')


def _plain(fragment: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", str(fragment or ""))).split())


def hentasis_items(text: str, page: int = 1, base: str = "") -> tuple:
    """(v5.44.93) ([:class:`Item`], whether there is a page more) of a page of Hentasis's list: ``div.short-item`` —
    the link ``a.short-link`` «Русское / Romaji (2026г.)», the picture of ``.short-img``, the rating (the width of
    ``.current-rating``, %) and the votes, the lines of ``.mov-lines`` («Год выпуска», «Жанр», «Эпизоды»…).  A post
    of the site's own (one name, no year: «Есть свет в конце тонеля…» — the first of its first page) and a title that
    depicts minors (its genres: ``manhwa_sources.depicts_minors``) — not.  ``base`` — the page's address (its
    addresses are read against it)."""
    base = base or HENTASIS_SITE + "/"
    starts = list(_HENTASIS_ITEM.finditer(text))
    out, seen = [], set()
    for n, m in enumerate(starts):
        block = text[m.end():starts[n + 1].start() if n + 1 < len(starts) else m.end() + 30000]
        link = next((a for a in re.finditer(r"<a\b([^>]*)>(.*?)</a>", block, re.S)
                     if re.search(r'class=["\'][^"\']*\bshort-link\b', a.group(1))), None)
        href = re.search(r'href=["\']([^"\']+)["\']', link.group(1)) if link else None
        if not href:
            continue
        url = urljoin(base, _html.unescape(href.group(1)))
        number = re.search(r"/(\d+)-[^/]*\.html?$", urlparse(url).path)
        if not number or number.group(1) in seen:
            continue
        title = re.sub(r"\s*\(\s*\d{4}(?:\s*[-–]\s*\d{4})?\s*г?\.?\s*\)\s*$", "", _plain(link.group(2)))
        names = [x.strip() for x in title.split(" / ") if x.strip()]
        if len(names) < 2:
            continue                                   # the site's own post, not a title
        if not S._cyrillic(names[0]) and any(S._cyrillic(x) for x in names[1:]):
            names.sort(key=lambda x: not S._cyrillic(x))      # «Sister Breeder / Заводчик сестёр»: the Russian first
        lines = {}
        for label, value in re.findall(r'<li>\s*<div\s+class=["\']mov-label["\']>(.*?)</div>(.*?)</li>', block, re.S):
            lines[_plain(label).strip(" :")] = _plain(value)
        genres = [g.strip() for g in lines.get("Жанр", "").split(",") if g.strip()]
        if S.depicts_minors(genres):
            continue                                   # the program's rule
        year = re.search(r"\b(?:19|20)\d{2}\b", lines.get("Год выпуска", ""))
        picture = re.search(r'class=["\'][^"\']*\bshort-img\b[^"\']*["\'][^>]*>\s*<img\b([^>]*)>', block)
        src = (re.search(r'\bdata-src=["\']([^"\']+)["\']', picture.group(1))
               or re.search(r'\bsrc=["\']([^"\']+)["\']', picture.group(1))) if picture else None
        cover = urljoin(base, _html.unescape(src.group(1).strip())) if src else ""
        rate = re.search(r'current-rating["\'][^>]*width:\s*(\d+(?:\.\d+)?)%', block)
        votes = re.search(r'data-vote-num-id=["\']\d+["\'][^>]*>\s*([\d\s]+)<', block)
        episodes = lines.get("Эпизоды", "").replace("-", "–")
        seen.add(number.group(1))
        out.append(Item(site="hentasis", key=number.group(1), names=names, url=url, shown_type="Хентай",
                        year=int(year.group(0)) if year else 0, cover=cover,
                        rating=round(float(rate.group(1)) / 10, 1) if rate else 0.0,
                        votes=int(re.sub(r"\D", "", votes.group(1)) or 0) if votes else 0, adult=True,
                        status=(f"серии {episodes}" if re.search(r"\D", episodes) else
                                f"{episodes} серия" if episodes == "1" else f"{episodes} серий" if episodes else "")))
    pages = [int(x) for x in re.findall(r'href=["\'][^"\']*/page/(\d+)/?["\']', text)]
    return out, any(x > page for x in pages)


def _hentasis_page(net, address: str, q: Query) -> tuple:
    body, ctype = net.fetch(address, what=LABELS["hentasis"],
                            headers={"Referer": HENTASIS_SITE + "/", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    number = re.search(r"/page/(\d+)/?$", address)
    return hentasis_items(S._decoded(body, ctype), int(number.group(1)) if number else 1, address)


def _hentaichan_page(net, address: str, q: Query) -> tuple:
    """(v5.44.93) A page of Хентай-тян's lists or its search (``manhwa_sources.Hentaichan.listing``: a title depicting
    minors is not in it); (v5.44.95) the parts of one work — one tile, named as the work («Укрощение строптивого - часть
    22» six times on its «Обновления» of 6.11.2026; the card of any part is the whole work)."""
    source = S.by_name("hentaichan")
    hits, more = source.listing(net, address)
    return [Item(site="hentaichan", key=h.key, names=names, url=h.url, type="Манга", shown_type="Хентай", cover=h.cover,
                 adult=True) for h, names in works_of(hits)], more


def _allhentai_items(data, q: Query) -> tuple:
    """(v5.44.99) ([:class:`Item`], whether there is a page more) of AllHentai's catalog or search, every kind (the
    page is kept once for both tabs — :func:`_tab_fits` takes the tab's); a title the program's rule leaves out (its
    names, its genres and tags) — not."""
    source = S.by_name("allhentai")
    out = []
    if isinstance(data, dict) and "suggestions" in data:          # its search: names, a link, a kind
        for x in data.get("suggestions") or []:
            element = x.get("elementId") if isinstance(x, dict) and isinstance(x.get("elementId"), dict) else {}
            link = str(x.get("link") or "") if isinstance(x, dict) else ""
            key = source.key_of(source.site() + link) if link.startswith("/") else ""
            if not key or str(element.get("type") or "MANGA") != "MANGA":
                continue
            names = list(dict.fromkeys(str(n).strip() for n in [x.get("value"), *(x.get("names") or [])]
                                       if n and str(n).strip()))
            kind = next((str(k) for k in (element.get("typeName"), x.get("additional")) if k in _ALLHENTAI_KNOWN), "")
            if S.depicts_minors(names):
                continue                                  # the program's rule
            out.append(Item(site="allhentai", key=key, names=names, url=f"{source.site()}/{key}", type=kind,
                            shown_type=kind or "Хентай", cover=str(x.get("thumbnail") or ""), adult=True))
        return out, False
    rows = (data.get("list") if isinstance(data, dict) else None) or []
    for x in rows:
        element = x.get("elementId") if isinstance(x, dict) and isinstance(x.get("elementId"), dict) else {}
        link = str(element.get("linkName") or "").strip("/")
        if not link or not re.fullmatch(r"[\w.-]{1,200}", link):
            continue
        category = x.get("category") if isinstance(x.get("category"), dict) else {}
        kind = str(category.get("name") or "")
        names = [str(x.get("name") or link).strip()]
        marks = [str(e.get("name") or "") for e in x.get("elements") or [] if isinstance(e, dict)]
        if S.depicts_minors(names + marks):
            continue                                  # the program's rule
        rating = x.get("rating") if isinstance(x.get("rating"), dict) else {}
        year = next((S._int(e.get("name")) for e in x.get("elements") or []
                     if isinstance(e, dict) and e.get("typeName") == "year"), 0)
        state = _ALLHENTAI_STATUS.get(str(x.get("productionStatus") or "").upper(), "")
        out.append(Item(site="allhentai", key=link, names=names, url=f"{source.site()}/{link}", type=kind,
                        shown_type=kind, year=year, cover=str(x.get("picUrl") or ""),
                        rating=round(S._float(rating.get("rate")), 1), votes=S._int(rating.get("voteCount")),
                        adult=True, status=_STATUS_WORD.get(state, "")))
    total = S._int(data.get("total")) if isinstance(data, dict) else 0
    offset = S._int(data.get("offset")) if isinstance(data, dict) else 0
    return out, offset + len(rows) < total if total else len(rows) >= ALLHENTAI_PAGE


def nhentai_items(data, net=None) -> tuple:
    """(v5.44.97) ([:class:`Item`], whether there is a page more) of a page of nhentai's list or search: a gallery the
    program's rule leaves out — not (its tags by their numbers, their names asked of the site once — ``net``; its
    titles); the name — the title without its brackets (the whole one — the second name), the cover — its thumbnail,
    the status — its pages."""
    items = data.get("result") if isinstance(data, dict) else None
    galleries = [g for g in items or [] if isinstance(g, dict) and S._int(g.get("id"))]
    numbers = [t for g in galleries for t in g.get("tag_ids") or [] if isinstance(t, int)]
    names = N.tag_names(net, numbers) if net is not None else N.known_tags(numbers)
    out = []
    for g in galleries:
        gid = S._int(g.get("id"))
        titles = [" ".join(str(g.get(k) or "").split()) for k in ("english_title", "japanese_title")]
        tags = [t for t in g.get("tag_ids") or [] if isinstance(t, int)]
        if N.forbidden(titles, tags, [names.get(t, "") for t in tags]):
            continue                                  # the program's rule
        shown = N.pretty(titles[0]) or N.pretty(titles[1])
        if len(re.sub(r"[\W_]", "", shown)) < 2:     # «[Artist] ♥ [English]»: the brackets were the most of it
            shown = titles[0] or titles[1] or f"nhentai {gid}"
        pages = S._int(g.get("num_pages"))
        out.append(Item(site="nhentai", key=str(gid), names=[n for n in dict.fromkeys([shown, *titles]) if n],
                        url=N.link_of(gid), type="Манга", shown_type="Хентай", cover=N.thumb_of(g.get("thumbnail"), gid),
                        adult=True, status=f"{pages} стр." if pages else ""))
    page = S._int(data.get("page")) if isinstance(data, dict) else 0
    return out, bool(galleries) and S._int((data or {}).get("num_pages")) > max(1, page)


def _nhentai_page(net, address: str, q: Query) -> tuple:
    """(v5.44.97) A page of nhentai: its answer, then the names of the tags it has not told yet (one request)."""
    try:
        data = net.json(address, what=LABELS["nhentai"], tries=1,
                        headers={"Accept": "application/json", "Referer": N.SITE + "/"})
    except S.SourceError as exc:
        if "429" in str(exc):
            raise S.SourceError("nhentai просит спрашивать реже (у него не больше 10 поисков в минуту) — "
                                "через минуту «Показать ещё» или обновите страницу") from exc
        raise
    number = re.search(r"[?&]page=(\d+)", address)
    if isinstance(data, dict):
        data = {**data, "page": int(number.group(1)) if number else 1}
    return nhentai_items(data, net)


def works_of(hits) -> list:
    """(v5.44.95) [(the hit, its names)] of Хентай-тян's rows, one of each work: a part's name without «глава N» /
    «часть N» (the newest part stands for the work — its card is the whole work)."""
    out, seen = [], set()
    for h in hits:
        name = h.names[0] if h.names else h.key
        work = (S._without_part(name) if S._part_number(name) else name) or name
        mark = S.name_key(work) or h.key
        if mark in seen:
            continue
        seen.add(mark)
        out.append((h, [work, *h.names[1:]]))
    return out


# ── asking the sites ──────────────────────────────────────────────────────────────────────────────────────────────
_LOCK = threading.Lock()
_PAGES: "OrderedDict[str, Got]" = OrderedDict()          # address → what came
_GOING: dict = {}                                          # address → threading.Event
_SHOWN: "OrderedDict[str, dict]" = OrderedDict()          # a query's signature → {page: the name keys it showed}
_ASKED: "OrderedDict[tuple, float]" = OrderedDict()       # (v5.44.98) (signature, page) → when it was first asked
_GIVEN: "OrderedDict[tuple, set]" = OrderedDict()         # (v5.44.98) (signature, page) → the tiles' keys given
_COVERS: "OrderedDict[str, tuple]" = OrderedDict()        # a cover's number → (its address, the title's page)


def forget() -> None:
    with _LOCK:
        _PAGES.clear()
        _GOING.clear()
        _SHOWN.clear()
        _ASKED.clear()
        _GIVEN.clear()
        _COVERS.clear()


def _net():
    from core import manhwa_feed
    return manhwa_feed.FeedNet()


def _headers(site: str) -> dict:
    if site == "allhentai":                           # (v5.44.99) its catalog and its search (as its pages ask them)
        source = S.by_name("allhentai")
        base = source.site() if source is not None else ""
        return {"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest",
                "Referer": base + "/"}
    if site in ("mangalib", "hentailib"):
        source = S.by_name(site)
        return dict(source.headers()) if source is not None else {}
    if site == "remanga":
        source = S.by_name("remanga")
        return {"Accept": "application/json", "Referer": (source.site() if source else "") + "/"}
    from core import video_dubs
    return {"Accept": "application/json", "Referer": video_dubs.SITE + "/"}


_PARSE = {"remanga": _remanga_items, "mangalib": _mangalib_items, "yummy": _yummy_items,
          "hentailib": lambda data, q: _mangalib_items(data, q, "hentailib"), "allhentai": _allhentai_items}
# (v5.44.93) sites of pages, not of API; (v5.44.97) nhentai — its API asked twice a page (the tags' names)
_PAGES_OF = {"hentaichan": _hentaichan_page, "hentasis": _hentasis_page, "nhentai": _nhentai_page}


def _ask(site: str, address: str, q: Query, net, going) -> None:
    from core.redaction import sanitize_text
    label = LABELS.get(site, site)
    try:
        if site in _PAGES_OF:
            from core import manhwa_feed
            items, more = _PAGES_OF[site](net or manhwa_feed.feed_net(S.by_name(site)), address, q)
        else:
            data = (net or _net()).json(address, what=label, headers=_headers(site))
            items, more = _PARSE[site](data, q)
        got = Got(items=items, more=more)
    except S.Missing:
        got = Got()                                   # a page past the last one
    except S.SourceError as exc:
        _LOG.info("catalog: %s did not come: %s", label, exc)
        got = Got(error=sanitize_text(str(exc))[:300] or f"{label} не ответил")
    except Exception as exc:                          # noqa: BLE001 — a page that broke is said, the thread ends
        _LOG.warning("catalog: %s broke: %s", label, exc, exc_info=True)
        got = Got(error=f"{label}: ответ не разобрался")
    with _LOCK:
        _PAGES[address] = got
        _PAGES.move_to_end(address)
        while len(_PAGES) > PAGES_KEPT:
            _PAGES.popitem(last=False)
        _GOING.pop(address, None)
    going.set()


def _site_page(site: str, address: str, q: Query, net, again: bool) -> threading.Event:
    """The event set when the site's page is here (kept, or asked now in a thread of its own)."""
    with _LOCK:
        kept = _PAGES.get(address)
        if kept is not None and not (again and kept.error) and \
                time.time() - kept.at < (FAILED_TTL if kept.error else TTL):
            _PAGES.move_to_end(address)
            done = threading.Event()
            done.set()
            return done
        going = _GOING.get(address)
        if going is None:
            going = _GOING[address] = threading.Event()
            threading.Thread(target=_ask, args=(site, address, q, net, going), daemon=True,
                             name=f"catalog-{site}").start()
    return going


@dataclass
class Result:
    tiles: list = field(default_factory=list)       # dicts: :func:`_tile`
    more: bool = False
    errors: dict = field(default_factory=dict)      # a site's label → why it did not come
    asked: list = field(default_factory=list)       # labels of the sites asked
    skipped: list = field(default_factory=list)     # (label, why) of the sites not asked
    hidden: int = 0                                 # titles the mode («SFW / Всё / NSFW») did not show
    had: int = 0                                    # (v5.44.93) titles not shown: they are in the program's folders
    late: list = field(default_factory=list)        # (v5.44.98) labels of the sites still asked: their tiles come after


def page(q: Query, n: int = 1, *, wait: "float | None" = None, again: bool = False, net=None,
         settings: "dict | None" = None, have: "Have | None" = None, rest: bool = False) -> "Result | None":
    """Page ``n`` of the list of the query; None when a site has not answered in ``wait`` seconds (None — until they
    do): it goes on in its thread, the next ask finds it.  (v5.44.93) ``have`` — the titles of the program's folders
    (:func:`downloaded`): not shown.  (v5.44.98) A site that has not answered :data:`LATE` seconds after the page was
    first asked is not waited for: the others' tiles now, its labels in ``late``; ``rest`` — the page asks for the
    late sites' tiles: only the tiles it was not given yet (None — they are still asked)."""
    n = max(1, min(MAX_PAGE, int(n or 1)))
    wanted = addresses(q, n, settings)
    if again:                                         # (v5.44.98) asked once more: a silent site too
        for site, (address, _why) in wanted.items():
            if address:
                S.forget_silent(address)
    sig = q.sig()
    now = time.monotonic()
    with _LOCK:
        if not rest:
            _ASKED.setdefault((sig, n), now)
        if (sig, n) in _ASKED:
            _ASKED.move_to_end((sig, n))
        while len(_ASKED) > PAGES_ASKED_KEPT:
            _ASKED.popitem(last=False)
        first = _ASKED.get((sig, n), now)
    waits = {site: _site_page(site, address, q, net, again) for site, (address, _why) in wanted.items() if address}
    deadline = None if wait is None else time.monotonic() + wait
    late = []
    for site, going in waits.items():
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not going.wait(left):
            late.append(site)
    if late and (rest or time.monotonic() - first < LATE):
        return None
    result = Result()
    answers = {}
    for site, (address, why) in wanted.items():
        label = LABELS.get(site, site)
        if not address:
            if why:
                result.skipped.append((label, why))
            continue
        result.asked.append(label)
        if site in late:
            result.late.append(label)
            continue
        with _LOCK:
            got = _PAGES.get(address)
        if got is None:
            result.errors[label] = f"{label} не ответил"
            continue
        if got.error:
            result.errors[label] = got.error
            continue
        fits = [i for i in got.items if _tab_fits(i, q)]
        shown = [i for i in fits if _shown_in(i, q)]
        result.hidden += len(fits) - len(shown)
        answers[site] = shown
        result.more = result.more or got.more
    tiles = _name_first(_merge(answers, SITES[q.kind]), q.text)
    if have is not None:
        kept = [tile for tile in tiles if not have.holds(tile["ids"], tile["keys"])]
        result.had = len(tiles) - len(kept)
        tiles = kept
    with _LOCK:
        shown_before = _SHOWN.setdefault(sig, {})
        _SHOWN.move_to_end(sig)
        while len(_SHOWN) > QUERIES_KEPT:
            _SHOWN.popitem(last=False)
        before = set().union(*(keys for k, keys in shown_before.items() if k < n))
        given = set(_GIVEN.get((sig, n), set())) if rest else set()
        fresh, mine = [], set()
        for tile in tiles:
            keys = tile.pop("keys")
            tile.pop("ids", None)
            if keys & before or (given and keys & given):
                continue                                  # shown on a page before — or on this page already
            fresh.append(tile)
            mine |= keys
        shown_before[n] = (shown_before.get(n, set()) | mine) if rest else mine
        _GIVEN[(sig, n)] = given | mine
        _GIVEN.move_to_end((sig, n))
        while len(_GIVEN) > PAGES_ASKED_KEPT:
            _GIVEN.popitem(last=False)
    result.tiles = fresh
    return result


def _name_first(tiles: list, text: str) -> list:
    """(v5.44.92) By a name: the titles of that very name first (Remanga's search and MangaLib's «популярное» put
    «Ненасытный Берсерк» before «Берсерк»), the rest as they were."""
    key = S.name_key(text) if text else ""
    if not key:
        return tiles
    return sorted(tiles, key=lambda t: 0 if key in t["keys"] else 1)


def _tab_fits(item: Item, q: Query) -> bool:
    """(v5.44.99) AllHentai gives every kind on one page (its API takes no kind and no filter — 6.11.2026), the page is
    kept once for both tabs: here — the tab's kinds (of the chosen types); a kind it did not name (its search) — any
    tab; a kind unknown here — «Манга» (most of its titles)."""
    if item.site != "allhentai" or not item.type:
        return True
    if item.type in _ALLHENTAI_KNOWN:
        return any(item.type in names for t, names in ALLHENTAI_KINDS.get(q.kind, {}).items()
                   if not q.types or t in q.types)
    return q.kind == "manga" and not q.types


def _shown_in(item: Item, q: Query) -> bool:
    """The mode of the program («SFW / Всё / NSFW»): what a site marks as erotica itself (MangaLib is asked without
    its «Эротика» already, Remanga's catalog gives none)."""
    if q.mode == "sfw":
        return not item.adult
    if q.mode == "nsfw" and item.site in ("remanga", "yummy"):
        return item.adult
    return True


def _merge(answers: dict, order: tuple) -> list:
    """The sites' titles in turns by their place there (the first of each, the second of each…, by the share of the
    list), one title of two sites — one tile (a name of it, the kind and the year: ``S._fits``)."""
    queue = []
    for o, site in enumerate(order):
        items = answers.get(site) or []
        for rank, item in enumerate(items):
            queue.append((rank / max(1, len(items)), o, item))
    queue.sort(key=lambda x: (x[0], x[1]))
    tiles: list = []
    for _share, _o, item in queue:
        keys = S.names_of(*item.names)
        same = None
        if keys:
            for tile in tiles:
                if item.site in tile["sites_set"]:
                    continue                          # one site's two titles are two titles
                if keys & tile["keys"] and S._fits(item.type, item.year, tile["type"], tile["year"]):
                    same = tile
                    break
        if same is None:
            same = {"names": [], "keys": set(), "sites_set": set(), "items": [], "type": item.type, "year": item.year}
            tiles.append(same)
        same["items"].append(item)
        same["sites_set"].add(item.site)
        same["keys"] |= keys
        same["names"] += [name for name in item.names if name not in same["names"]]
        same["type"] = same["type"] or item.type
        same["year"] = same["year"] or item.year
    return [_tile(t, order) for t in tiles]


def _tile(t: dict, order: tuple) -> dict:
    """A tile of the list; its page, rating and cover — of the site first in ``order`` that has them (a title of both
    — Remanga's: as on the card of «Скачать → По ссылке»)."""
    items = sorted(t["items"], key=lambda i: order.index(i.site) if i.site in order else len(order))
    best = items[0]
    rated = next((i for i in items if i.rating), None)
    covered = next((i for i in items if i.cover), None)
    return {
        "title": t["names"][0] if t["names"] else best.key, "alt": t["names"][1] if len(t["names"]) > 1 else "",
        "type": next((i.shown_type for i in items if i.shown_type), ""), "year": t["year"], "url": best.url,
        "rating": rated.rating if rated else 0.0, "status": next((i.status for i in items if i.status), ""),
        "adult": any(i.adult for i in items), "cover": cover_id(covered.cover, covered.url) if covered else "",
        "sites": [LABELS.get(i.site, i.site) for i in items], "keys": t["keys"],
        "ids": {(i.site, i.key) for i in items},
    }


def hit_tile(hit, label: str) -> dict:
    """A tile of a title a site's own feed gave (``manhwa_feed``: one site, no filters)."""
    return {"title": (hit.names or [hit.key])[0], "alt": hit.names[1] if len(hit.names) > 1 else "",
            "type": hit.type, "year": hit.year, "url": hit.url, "rating": round(hit.rating or 0.0, 1), "status": "",
            "adult": bool(hit.adult), "cover": cover_id(hit.cover, hit.url), "sites": [label]}


# ── what the program's folders hold: core/filter.py (Have, downloaded, forget_have) ──────────────────────────────


# ── covers ────────────────────────────────────────────────────────────────────────────────────────────────────────
def cover_id(address: str, page: str = "") -> str:
    if not address:
        return ""
    number = hashlib.sha1(address.encode("utf-8")).hexdigest()[:16]
    with _LOCK:
        _COVERS[number] = (address, page)
        _COVERS.move_to_end(number)
        while len(_COVERS) > COVERS_KEPT:
            _COVERS.popitem(last=False)
    return number


def cover(number: str, *, net=None) -> "tuple | None":
    """(bytes, type) of a cover a catalog gave — None: not one of them, or it did not come."""
    with _LOCK:
        kept = _COVERS.get(str(number or ""))
    if kept is None:
        return None
    from core import manhwa_search as MS
    return MS.fetch_cover(kept[0], kept[1], net=net, shrink=COVER_WIDTH)
