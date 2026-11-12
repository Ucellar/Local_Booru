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
The program's gates (``core/gates.py``): what a site says of each of its titles is handed to ``gates.title`` — a title
it closes is not a tile; what ``gates.left_out`` names is asked left out of every list of the site.

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
search (ten a minute), 25 a page; no filters, no other order (not asked, said why); a gallery a gate closes (its
tags by their numbers and names, its titles) — not a tile; the tile — «Хентай · N стр.», its
thumbnail, the title without its brackets; downloaded (``nhentai-<n>`` in the manga folder) — not shown.

(v5.44.99) The owner: «https://20.allhen.online/ добавить» — AllHentai in «Манга» and «Манхва» (``manhwa_sources.
AllHentai``, asked 6.11.2026): its catalog API ``/api/catalog/search?offset=&sortType=`` (50 a page: «Популярное» —
POPULARITY, «Новые главы» — DATE_UPDATE, «Новые тайтлы» — DATE_CREATE, «По оценке» — RATING; no filters — not asked
with them, said why), a name — its search ``/search/suggestion`` (one page); its kinds — ``category`` (the tab's:
«Манга», «Комикс», «3D», «Без текста» / «Манхва», «Маньхуа»); a title a gate closes (its names, its ``elements`` —
genres and tags) — not a tile.

(v5.44.148) The owner: «я не могу написать тег tentaclies мне его просто не выдает должно показывать любой тег … найти
автора или нищевый тег просто нереально».  nhentai takes the filters now — its own search words (``tag:"…"``,
``-tag:"…"``, asked 11.11.2026: «tag:"tentacles"» — 17 508 galleries, «artist:"…"» the same way): a genre or tag of
the feed it names its own way (``core/catalog_names.py`` — «Тентакли» is its «tentacles») is asked of it too, and any
tag, artist, character, parody or group of its own — a **site's own tag** of the query (:data:`EXTRA_TYPES`,
``Query.extra`` / ``unextra``: «nhentai:artist:name»; the address's ``sg`` / ``sx``): only that site is asked then.
What to choose from — its own tag search (``POST /api/v2/tags/search`` {type, query, limit}; 14 in a row answered in
3,7 s) — :func:`suggest`; «Найти тег» looks in the other names of the feed's tags too (:func:`also_names`: the Lib
API's ``alt_name``, nhentai's).  A name a gate closes is offered by nothing here, and a query with one asks no site.

(v5.44.149) The authors of MangaLib and HentaiLib — a site's own tag too («lib:people:<number> <name>»: one base of
people for both sites of the Lib API): its search of people (``/api/people?q=&site_id[]=``: id, name, rus_name,
``titles_count_details`` {site: titles}) and the catalogue as the site's own page of a person asks it —
``/api/manga?…&target_id=<number>&target_model=people`` (asked 11.11.2026: «Boichi» — 20 titles at MangaLib, 9 at
HentaiLib; with the other filters too).  One author at a time (the catalogue takes one ``target_id``), no «none of
these».  None of the nine parameters tried (``authors[]``, ``people[]``…) filters anything — only this pair.

(v5.44.155) The owner sent the sites' pages of tags («ты там теги просил я кинул куда сказал»): AllHentai's list — 188
tags, Nude-Moon's — 97.  AllHentai's catalog API does take filters (:func:`allhentai_url`: the numbers of its genres,
tags and kinds — ``core/catalog_names.py``; all of these, none of these, the tab's kinds, the statuses, the years, a
name with pages): it is one of the sites that filter now, its 213 genres and tags are the feed's (56 of them it alone
has).  Nude-Moon's six tags its table had not are there (:data:`NUDEMOON_TAGS`).  «Найти тег» finds a tag by the
site's own word of it (:func:`also_names`)."""
from __future__ import annotations

import hashlib
import html as _html
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import zip_longest
from urllib.parse import quote, unquote, urljoin, urlparse

from core import catalog_names as NAMES
from core import gates as G
from core import manhwa_sources as S
from core import nhentai as N
# what the program's folders hold (``core/catalog_have.py``); their names here for the code that uses them
from core.catalog_have import HAVE_TTL, Have, downloaded, forget_have  # noqa: F401

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
HENTASIS_FIRST_YEAR = 1990        # (v5.44.137) years «до …» without «от» — from this one (its oldest page of a year: 1994)
# (v5.44.138) HentaiHaven: its pages (a Next.js site) and its catalogue — the API of its WordPress (asked from the owner's
# computer 9.11.2026: not reached straight there — through the system's proxy, as ``manhwa_sources.Net`` turns)
HAVEN_SITE = "https://hentaihaven.xxx"
HAVEN_API = "https://cms.hentaihaven.xxx/wp-json/wp/v2"
HAVEN_IMAGES = "https://img.hentaihaven.xxx/"
HAVEN_PAGE = 24
# (v5.44.139) Hentai-XS (a site of its own: ten titles a page, newest first, a page of each year, a search — a form,
# no pages of genres: its rows tell them) and HFShare (DataLife Engine, as Hentasis: «Хентай аниме» twelve a page, its
# genres ``/xfsearch/<value>/``, its tag «без цензуры», its section of 3D, a search; its titles' pages — v2.hfshare.me)
XS_SITE = "https://hentai-xs.com"
XS_WALK = 4                       # its pages read for one page of «Лента» when a genre is chosen (its rows tell them)
XS_FIRST_YEAR = 1995              # years «до …» without «от» — from this one (the first of its menu of years)
HFSHARE_SITE = "https://hfshare.me"
HFSHARE_FOUND_PAGE = 10           # titles of its search for one page of «Лента» (each one's page tells its genres)
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
# (v5.44.96) HentaiLib's genres (site 4 of the same API; asked 6.11.2026): MangaLib's numbers, without «Эротика»
HENTAILIB_GENRES = {
    "Арт": 32, "Безумие": 91, "Боевик": 34, "Боевые искусства": 35, "Вампиры": 36, "Военное": 89, "Гарем": 37,
    "Гендерная интрига": 38, "Героическое фэнтези": 39, "Демоны": 81, "Детектив": 40, "Дзёсэй": 41, "Драма": 43,
    "Игра": 44, "Исекай": 79, "История": 45, "Киберпанк": 46, "Комедия": 47, "Космос": 83, "Магия": 85,
    "Махо-сёдзё": 48, "Машины": 90, "Меха": 49, "Мистика": 50, "Музыка": 80, "Научная фантастика": 51,
    "Омегаверс": 77, "Пародия": 86, "Повседневность": 52, "Полиция": 82, "Постапокалиптика": 53, "Приключения": 54,
    "Психология": 55, "Романтика": 56, "Самурайский боевик": 57, "Сверхъестественное": 58, "Сёдзё": 59,
    "Сёдзё-ай": 60, "Сёнэн": 61, "Сёнэн-ай": 62, "Спорт": 63, "Супер сила": 87, "Сэйнэн": 64, "Трагедия": 65,
    "Триллер": 66, "Ужасы": 67, "Фантастика": 68, "Фэнтези": 69, "Этти": 72, "Юри": 73, "Яой": 74}
# …its tags of site 4
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
# (v5.44.135; the owner: «не ищет по тегам нормально, особенно с нового сайта») Nude-Moon's tags — its pages
# ``/tags/<слово>``, «all of these» as ``/tags/a+b`` (asked from the owner's computer 8.11.2026: «большие_груди+ahegao» —
# 20 of 20 titles with both; the column of every tag of its lists and its entries' tags).  The feed's name → its word; a
# tag the other sites have under another
# name is under theirs («большие груди» — «Большая грудь»), its own words — :data:`NUDEMOON_NAMES`.  Its genre-like tags
# are the feed's genres (:data:`NUDEMOON_GENRES`; «Ужасы» is a tag of HentaiLib too — in both tables).
NUDEMOON_GENRES = {"Гарем": "гарем", "Гендерная интрига": "gender_bender", "Драма": "драма", "Комедия": "юмор",
                   "Романтика": "романтика", "Студенты": "студенты", "Ужасы": "ужасы", "Фэнтези": "фэнтези",
                   "Юри": "юри", "Яой": "яой"}
NUDEMOON_TAGS = {
    "3D": "3D", "Ахэгао": "ahegao", "БДСМ": "bdsm", "Гяру и Гангуро": "ganguro", "Дыра в стене": "glory_hole",
    "Инсеки": "inseki", "В очках": "megane", "Психический слом": "mind_break", "Монстродевушки": "monstergirl",
    "Нетораре": "netorare", "Проникновение в соски": "nipple_penetration", "Оякодон": "oyakodon",
    "Skinsuit": "skinsuit", "Пайзури": "titsfuck", "Vore": "vore", "X-Ray": "x-ray", "Анал": "анал",
    "Анимация (GIF)": "анимация", "Без цензуры": "без_цензуры", "Беременность": "беременные", "Близнецы": "близняшки",
    "Большая грудь": "большие_груди", "В бассейне": "в_бассейне", "Больница": "в_больнице", "В ванной": "в_ванной",
    "В общественном месте": "в_общественном_месте", "В первый раз": "в_первый_раз", "В транспорте": "в_транспорте",
    "В туалете": "в_туалете", "Гипноз": "гипноз", "Горничные": "горничные", "Горячий источник": "горячий_источник",
    "Групповой секс": "групповой_секс", "Запредельное": "запредельное", "Золотой дождь": "золотой_дождь",
    "Зрелые женщины": "зрелые_женщины", "Идолы": "идолы", "Извращение": "извращение", "Измена": "измена",
    "Имеют парня": "имеют_парня", "Инцест": "инцест", "Клизма": "клизма", "Колготки": "колготки", "Комикс": "комикс",
    "Косплей": "косплей", "Мастурбация": "мастурбация", "Мерзкий дядька": "мерзкий_мужик",
    "Много спермы": "много_спермы", "Молоко": "молоко", "Монашка": "монашки", "Монстры": "монстры",
    "Камера": "на_камеру", "На природе": "на_природе", "Насекомые": "насекомые",
    "Небритые подмышки": "небритые_подмышки", "Сгенерировано ИИ": "нейросеть", "Обычный секс": "обычный_секс",
    "Огромный член": "огромный_член", "Пляж": "пляж", "Подглядывание": "подглядывание",
    "Пояс верности": "пояс_верности", "Принуждение": "принуждение", "Проституция": "продажность", "Пьяные": "пьяные",
    "Рабы": "рабыни", "Ушастые": "с_ушками", "Секс-игрушки": "секс_игрушки", "Спящие": "спящие",
    "Страпон": "страпон", "Суккуб": "суккуб", "Тентакли": "тентакли", "Толстушки": "толстушки", "Трап": "трапы",
    "Ужасы": "ужасы", "Униформа": "униформа", "Фемдом": "фемдом", "Фетиш": "фетиш", "Фурри": "фурри",
    "Футанари": "футанари", "Footjob": "футджоб", "В цвете": "цветная", "Чикан": "чикан", "Чулки": "чулки",
    "Эксгибиционизм": "эксгибиционизм", "Эльфы": "эльфы",
    # (v5.44.155) its own list of tags (its page «Настройка тегов», saved by the owner 11.11.2026: 97) had six more
    "Гуро": "гуро", "Зоофилия": "зоо", "Манхва": "манхва", "Недоперевод": "недоперевод", "Scat": "копро",
    "Shemale": "шимейл"}


def _nudemoon_names(table: dict, also: dict) -> dict:
    """{a name Nude-Moon writes (its tag's words, «_» as a space): the feed's name} where they differ."""
    out = {}
    for shown, word in table.items():
        for said in (word.replace("_", " "), *[k for k, v in also.items() if v == word]):
            if S.name_key(said) != S.name_key(shown):
                out[said] = shown
    return out


NUDEMOON_NAMES = {**_nudemoon_names(NUDEMOON_TAGS, {"3D арт": "3D"}), **_nudemoon_names(NUDEMOON_GENRES, {})}
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
    "Кулинария": 16, "Культивация": 18, "Магическая академия": 78, "Магия": 22, "Медицина": 17,
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
    "Магическая академия": 258, "Магия": 170, "Матриархат": 705, "Мафия": 172, "Медицина": 159,
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
# «Видео → Лента», «Новые» without a status chosen: out already or coming out (YummyAnime's words) — the announcements
# of years ahead would be the whole first page; a status chosen in the filters — that one
VIDEO_NEW_STATUSES = ("ongoing", "released")
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


# (v5.44.148) …and nhentai's own names of them (``core/catalog_names.py``): asked of it as ``tag:"…"``; (v5.44.155)
# AllHentai's — the numbers of its catalog's elements (the same file)
MANGA_GENRES = _merged(("remanga", REMANGA_GENRES), ("mangalib", MANGALIB_GENRES), ("hentailib", HENTAILIB_GENRES),
                      ("nudemoon", NUDEMOON_GENRES), ("nhentai", NAMES.NHENTAI_GENRES),
                      ("allhentai", NAMES.ALLHENTAI_GENRES), same=_SAME)
MANGA_TAGS = _merged(("remanga", REMANGA_TAGS), ("mangalib", MANGALIB_TAGS), ("hentailib", HENTAILIB_TAGS),
                     ("nudemoon", NUDEMOON_TAGS), ("nhentai", NAMES.NHENTAI_TAGS),
                     ("allhentai", NAMES.ALLHENTAI_TAGS), same=_SAME_TAGS)
# (v5.44.137; the owner: «почему в видео нет фильтров») Hentasis's genres: its pages ``/xfsearch/<слово>/`` (its menu's
# words; one it does not name there — by the word of its rows: «Юри» — asked from the owner's computer 9.11.2026, 25
# pages); the feed's name → (its address word, its rows' word).  A genre YummyAnime has too is under its name.
HENTASIS_GENRES = {
    "Анал": ("Anal", "Анал"), "БДСМ": ("BDSM", "БДСМ"), "Большая грудь": ("Big", "Большие сиськи"),
    "Бондаж": ("bondage", "Бондаж"), "Вибраторы": ("Vibrators", "Вибраторы"), "Гарем": ("Harem", "Гарем"),
    "Гипноз": ("Гипноз", "Гипноз"), "Групповой секс": ("Group", "Групповуха"), "Девственницы": ("Virgin", "Девственницы"),
    "Демоны": ("Demons", "Демоны"), "Домохозяйки": ("Housewives", "Домохозяйки"), "Драма": ("Drama", "Драма"),
    "Изнасилование": ("Изнасилование", "Изнасилование"), "Инцест": ("Incest", "Инцест"), "Комедия": ("Comedy", "Комедия"),
    "Магия": ("Magic", "Магия"), "Маленькая грудь": ("small", "Маленькие сиськи"), "Медсёстры": ("Nurse", "Медсестры"),
    "Милфы": ("Милфы", "Милфы"), "Минет": ("Oral", "Минет"), "Мистика": ("Mystic", "Мистика"),
    "Монстры": ("Monster", "Монстры"), "Нетораре": ("Netorare", "Нетораре"), "Пайзури": ("paizuri", "Пайзури"),
    "Романтика": ("Romance", "Романтика"), "Служанки": ("Maid", "Служанки"), "Студенты": ("student", "Студенты"),
    "Суккубы": ("Succubus", "Сукубы"), "Тентакли": ("Tentacles", "Щупальцы"), "Ужасы": ("Horror", "Ужасы"),
    "Фантастика": ("Sci-Fi", "Скай Фай"), "Фэнтези": ("Fantasy", "Фэнтези"), "Эльфы": ("elf", "Эльфы"),
    "Этти": ("Этти", "Этти"), "Юри": ("Юри", "Юри")}
# …and the other words of its rows for one of them («Фентези» — as some of its rows write it)
HENTASIS_ALSO = {"Фентези": "Фэнтези"}


def _site_words(table: dict, also: dict) -> dict:
    """{a site's word for a genre: the feed's name} where they differ (the chips of a title's page — ``feed_filter``)."""
    out = {said: shown for shown, (_address, said) in table.items() if S.name_key(said) != S.name_key(shown)}
    out.update(also)
    return out


HENTASIS_NAMES = _site_words(HENTASIS_GENRES, HENTASIS_ALSO)
# (v5.44.138) HentaiHaven's genres (the numbers of its WordPress; its Russian names, 9.11.2026): the feed's name → (its
# number, its own name); of its several numbers for one genre («Большая грудь» — three) the one most titles carry
HAVEN_GENRES = {
    "3D": (2245, "3D Хентай"), "Ахэгао": (11709, "Ахегао"), "Анал": (222, "Анальный"), "БДСМ": (133, "БДСМ"),
    "Без цензуры": (107, "Нецензурированное хентай"), "Большая грудь": (11719, "Большая грудь"), "Гарем": (105, "Гарем"),
    "Гендерная интрига": (2582, "Гендербендер Хентай"), "Групповой секс": (11740, "Групповой секс"),
    "Девственницы": (11745, "Virgin"), "Домохозяйки": (11743, "Housewife"), "Зоофилия": (252, "Зоофилия"),
    "Изнасилование": (65, "Рейп"), "Инцест": (64, "Инцест Хентай"), "Комедия": (11725, "Комедия"),
    "Кримпай": (11710, "Кримпай"), "Милфы": (208, "Милф"), "Минет": (11720, "Минет"), "Монстры": (290, "Монстры"),
    "Нетораре": (11728, "Ntr"), "Пайзури": (11722, "Пайзури"), "Романтика": (103, "Романтика"),
    "Софткор": (2627, "Софткор"), "Тентакли": (244, "Тентакли"), "Толстушки": (2433, "BBW"), "Ужасы": (1606, "Ужасы"),
    "Фембой": (2673, "Фембой"), "Фемдом": (2676, "Фемдом"), "Фурри": (165, "Фурри"), "Футанари": (229, "Футанари"),
    "Фэнтези": (11716, "Фэнтези"), "Цундере": (106, "Цундере"), "Эльфы": (11734, "Эльф"), "Этти": (2594, "Этти"),
    "Юри": (102, "Юри"), "Яой": (104, "Яой")}
HAVEN_YEARS = {1991: 2625, 1992: 2575, 1994: 2759, 1995: 2604, 1996: 1614, 1997: 1579, 1998: 2643, 1999: 828, 2000: 2610,
               2001: 781, 2002: 1118, 2003: 1018, 2004: 882, 2005: 1026, 2006: 1199, 2007: 318, 2008: 261, 2009: 68,
               2010: 110, 2011: 114, 2012: 361, 2013: 112, 2014: 166, 2015: 111, 2016: 113, 2017: 142, 2018: 198,
               2019: 879, 2020: 1930, 2021: 2593, 2022: 2656, 2023: 2681, 2024: 11308, 2025: 11700, 2026: 11708}
_HAVEN_YEAR_OF = {number: year for year, number in HAVEN_YEARS.items()}
HAVEN_NAMES = {said: shown for shown, (_number, said) in HAVEN_GENRES.items()
               if S.name_key(said) != S.name_key(shown)}
# (v5.44.139) Hentai-XS's genres: the words of its rows (every title of it, 828, read from the owner's computer
# 9.11.2026 — Russian and English, in any case: compared by their letters) → the feed's name.  It has no page of a
# genre: a genre is looked for in its rows (:func:`_xs_page` reads several of its pages for one page of «Лента»);
# «Без цензуры» — its rows' «Цензура: нет».
XS_GENRES = {
    "Анал": ("Анал", "Anal", "Anal sex"), "Ахэгао": ("Ahegao", "Ахегао"), "БДСМ": ("BDSM",),
    "Без цензуры": ("Без цензуры",), "Беременные": ("Pregnant", "Pregnancy", "Беременные"),
    "Большая грудь": ("Большие сиськи", "Big tits", "Big Boobs", "Large Breasts", "Huge Boobs", "Gigantic Breasts",
                      "Big Breasts", "Больше сиськи", "большие груди", "бакуню", "Busty"),
    "Бондаж": ("Bondage", "Бондаж", "Shibari", "Sibari", "сибари"), "Буккаке": ("Bukkake", "буккакэ", "Bukakke"),
    "Вибраторы": ("Vibrators", "Вибраторы", "Дидло", "Dildos"), "Гарем": ("Harem", "Гарем"),
    "Гипноз": ("Гипноз", "Mind control", "Gypnos", "Hypnos", "Hypnosis"),
    "Групповой секс": ("Group sex", "Групповуха", "Групповой секс", "Гоупповуха", "Group", "Orgy"),
    "Гэнгбэнг": ("Gangbang", "Gangband"), "Гяру": ("Gyaru",),
    "Двойное проникновение": ("Double Penetration", "Двойное проникновение", "DP", "DAP"),
    "Девственницы": ("Virgin", "Virgins", "Девственницы", "Defloration"), "Демоны": ("Demons", "Демоны", "Demon"),
    "Домохозяйки": ("Housewives", "Домохозяйки", "Housewife", "Жены"), "Драма": ("Drama", "Драма"),
    "Игрушки": ("Toys", "Sex toys", "Toy"), "Изнасилование": ("Rape", "Изнасилование", "Forced Sex", "Рэйп"),
    "Инцест": ("Incest", "Инцест", "Инцecт"), "Комедия": ("Comedy", "Комедия"), "Косплей": ("Cosplay",),
    "Кримпай": ("Creampie", "Кремпай", "Nakadashi", "Internal Cumshot", "Creampai"),
    "Лактация": ("Lactation", "Лактация", "Milk", "Lactating"), "Магия": ("Magic", "Магия"),
    "Маленькая грудь": ("Small tits", "Маленькие сиськи", "Small Boobs", "Small Breast", "Small Breasts"),
    "Мастурбация": ("Masturbation", "Мастурбация"), "Медсёстры": ("Nurse", "Медсестры", "Nurses"),
    "Милфы": ("MILF", "Милф", "Mature"),
    "Минет": ("Минет", "Oral sex", "Blowjob", "Oral", "Fellatio", "Minet", "Орал", "Оральный секс"),
    "Мистика": ("Mystic", "Мистика", "Supernatural"), "Монстродевушки": ("Monster Girl",),
    "Монстры": ("Monsters", "Монстры", "Monster"), "Неко": ("Neko", "Кошкодевушки"),
    "Нетораре": ("Нетораре", "Netorare", "NTR", "нэторарэ", "Cheating"),
    "Пайзури": ("Paizuri", "Tittyfuck", "Titsjob", "Пайзури", "Секс в сиськи", "Titfuck", "Paizury", "Titjob"),
    "Рентген": ("X-Ray", "Икс Рэй", "Икс Рей", "Икс-Рэй", "Рентген"), "Романтика": ("Romance", "Романтика"),
    "Служанки": ("Maids", "Служанки", "Maid"),
    "Студенты": ("Students", "Student Girl", "Студенты", "Female Students", "Студентки", "University"),
    "Суккубы": ("Суккубы", "Succubus"),
    "Тентакли": ("Tentacles", "Щупальцы", "Тентакли", "Щупальца", "Tentacle", "Механические щупальцы"),
    "Ужасы": ("Horror", "Ужасы"), "Учителя": ("Teachers", "Teacher", "Учительницы"),
    "Фантастика": ("Sci-Fi", "Скай Фай", "Скай-Фай", "Фантастика"), "Футанари": ("Futanari", "Футанари"),
    "Фэнтези": ("Fantasy", "Фэнтези", "Фентези"), "Чикан": ("Chikan", "Чикан", "Train Molestation", "Molester"),
    "Чулки": ("Stockings",), "Эльфы": ("Elf", "Эльфы"), "Этти": ("Ecchi", "Эччи", "Этти"),
    "Юри": ("Yuri", "Юри", "Лесбиянки", "Lesbians")}
# (v5.44.139) HFShare's genres: the feed's name → (its page — the value of ``/xfsearch/<value>/``, or a page of its own:
# «3D» — its section, «Без цензуры» — its tag; the words of its rows' genres for it).  Its menu's words (as it shows
# them in Russian) and its 250 genres (``/katalog.html``), read from the owner's computer 9.11.2026.
HFSHARE_GENRES = {
    "3D": ("/3d-hentay/", ("3D", "3DCG", "SFM", "MMD", "Koikatsu", "Blender")),
    "Без цензуры": ("/tags/без цензуры/", ()),          # its tag: not in the rows — its own page alone
    "Ахэгао": ("Ahegao", ("Ahegao",)), "Анал": ("Anal+sex", ("Anal sex", "Anal")), "БДСМ": ("BDSM", ("BDSM",)),
    "Беременные": ("Pregnant", ("Pregnant", "Pregnancy", "impregnation")),
    "Большая грудь": ("Big+tits", ("Big tits", "Large Breasts", "Gigantic Breasts")),
    "Бондаж": ("Bondage", ("Bondage", "shibari")), "Буккаке": ("Bukkake", ("Bukkake", "Bukakke", "Bukake")),
    "Вибраторы": ("Vibrators", ("Vibrators",)), "Гарем": ("Harem", ("Harem",)),
    "Гендерная интрига": ("Gender+Bender", ("Gender Bender",)),
    "Гипноз": ("Mind+control", ("Mind control", "Hypnos", "Gypnos", "Hypnosis")),
    "Групповой секс": ("Group+sex", ("Group sex", "Group", "Orgy Sex")), "Гэнгбэнг": ("Gangbang", ("Gangbang",)),
    "Гяру": ("Gyaru", ("Gyaru", "Gal")), "Двойное проникновение": ("Double+penetration", ("Double penetration", "dp")),
    "Девственницы": ("Virgin", ("Virgin", "Defloration")), "Демоны": ("Demons", ("Demons",)),
    "Домохозяйки": ("Housewives", ("Housewives", "Housewife", "Married Woman")), "Драма": ("Drama", ("Drama",)),
    "Зоофилия": ("Bestiality", ("Bestiality",)), "Игрушки": ("Toys", ("Toys", "Sex toys", "Dildos")),
    "Изнасилование": ("Rape", ("Rape", "Violation")), "Инцест": ("Incest", ("Incest",)),
    "Комедия": ("Comedy", ("Comedy",)), "Косплей": ("Cosplay", ("Cosplay",)),
    "Кримпай": ("Creampie", ("Creampie", "Internal Cumshot", "Nakadashi")),
    "Лактация": ("Lactation", ("Lactation", "Milk")), "Магия": ("Magic", ("Magic",)),
    "Маленькая грудь": ("Small+tits", ("Small tits", "Tiny tits")), "Мастурбация": ("Masturbation", ("Masturbation",)),
    "Медсёстры": ("Nurse", ("Nurse", "Nurses")), "Милфы": ("Milf", ("MILF", "Mature")),
    "Минет": ("Oral+sex", ("Oral sex", "Oral", "Blowjob", "Deep throat")), "Мистика": ("Mystic", ("Mystic",)),
    "Монстродевушки": ("Monster+girl", ("Monster girl",)), "Монстры": ("Monster", ("Monster",)),
    "Неко": ("Neko", ("Neko", "Nekomimi", "Cat Girl")),
    "Нетораре": ("Netorare", ("Netorare", "NTR", "Cuckoldry", "Cheating")),
    "Пайзури": ("Paizuri", ("Paizuri", "Titsjob", "Titfuck", "Titsfuck")), "Пародия": ("Parody", ("Parody",)),
    "Рентген": ("X-ray", ("X-ray",)), "Романтика": ("Romance", ("Romance",)), "Служанки": ("Maids", ("Maids",)),
    "Софткор": ("Softcore", ("Softcore",)), "Студенты": ("Students", ("Students", "female students")),
    "Суккубы": ("Succubus", ("Succubus",)), "Тентакли": ("Tentacles", ("Tentacles", "Tentacle")),
    "Ужасы": ("Horror", ("Horror",)), "Учителя": ("Teachers", ("Teachers", "Female Teachers")),
    "Фантастика": ("Sci-fi", ("Sci-fi",)), "Фембой": ("Trap", ("Trap",)), "Фемдом": ("Femdom", ("Femdom",)),
    "Футанари": ("Futanari", ("Futanari",)), "Фурри": ("Furry", ("Furry",)), "Фэнтези": ("Fantasy", ("Fantasy",)),
    "Цундере": ("Tsundere", ("Tsundere",)), "Чикан": ("Chikan", ("Chikan", "Molester")),
    "Чулки": ("Stockings", ("Stockings",)), "Эльфы": ("Elf", ("Elf",)), "Этти": ("Ecchi", ("Ecchi", "Ecchie")),
    "Юри": ("Yuri", ("Yuri", "Lesbians"))}


def _row_words(table: dict) -> dict:
    """{the letters of a word of a site's rows: the feed's genre}."""
    return {S.name_key(word): shown for shown, words in table.items() for word in words}


_XS_ROW = _row_words(XS_GENRES)
_HFSHARE_ROW = _row_words({shown: words for shown, (_address, words) in HFSHARE_GENRES.items()})
XS_NAMES = {word: shown for shown, words in XS_GENRES.items() for word in words
            if S.name_key(word) != S.name_key(shown)}
HFSHARE_NAMES = {word: shown for shown, (_address, words) in HFSHARE_GENRES.items() for word in words
                 if S.name_key(word) != S.name_key(shown)}
VIDEO_GENRES = _merged(("yummy", {title: value for value, title, _g in YUMMY_GENRES}),
                       ("hentasis", {shown: address for shown, (address, _said) in HENTASIS_GENRES.items()}),
                       ("haven", {shown: number for shown, (number, _said) in HAVEN_GENRES.items()}),
                       ("hentaixs", {shown: words[0] for shown, words in XS_GENRES.items()}),
                       ("hfshare", {shown: address for shown, (address, _words) in HFSHARE_GENRES.items()}), same={})
VIDEO_NSFW_TOP = ("Большая грудь", "Минет", "Анал", "Романтика", "Гарем", "Юри", "Нетораре", "Тентакли", "Фэнтези",
                  "Комедия", "Групповой секс", "Монстры")


def genres(kind: str) -> "OrderedDict[str, dict]":
    return VIDEO_GENRES if kind == "video" else MANGA_GENRES


def tags(kind: str) -> "OrderedDict[str, dict]":
    return OrderedDict() if kind == "video" else MANGA_TAGS


def offered(table: "OrderedDict[str, dict]", kind: str, mode: str, *, of_genres: bool = False) -> list:
    """(v5.44.96) The names of a table of genres (``of_genres``) or tags the page offers in this mode: those of a site
    the list can ask (``sites_shown``) — not a tag of HentaiLib alone in «SFW», not one of Remanga alone in «NSFW»; a
    genre a site has as a tag (:data:`GENRE_AS_TAG`) — its; a genre the site's lists are asked without in the mode
    (``gates.left_out``) — not its."""
    shown = sites_shown(kind, mode)
    left = {site: set(G.left_out(site, mode).genres) for site in shown} if of_genres else {}

    def takes(name: str, ids: dict, site: str) -> bool:
        if not of_genres:
            return site in ids
        if site in ids:
            return ids[site] not in left[site]
        return kind != "video" and name in GENRE_AS_TAG.get(site, {})
    return [name for name, ids in table.items() if any(takes(name, ids, site) for site in shown)]


def top_genres(kind: str, mode: str = "all") -> tuple:
    """The genres shown first; (v5.44.137) the videos' in «NSFW» — those of the sites of hentai."""
    if kind == "video":
        return VIDEO_NSFW_TOP if mode == "nsfw" else VIDEO_TOP
    return MANGA_TOP


# (v5.44.129; the owner: «при открытии страницы с интернета (в ленте) теги и все плитки связанные с каким либо поиском
# не кликабельны») a title's genre or tag, as a site names it → the filter of the feed that shows such titles
_FILTER_NAMES: dict = {}


def _filter_names(kind: str) -> "tuple[dict, dict]":
    """({letters of a name: the feed's genre}, {…: the feed's tag}) — every name of the tables, and the other names
    of one genre or tag (:data:`_SAME`, :data:`_SAME_TAGS`; (v5.44.148) :func:`also_names` — the sites' own other
    names: «Tentacles» is «Тентакли»)."""
    group = "video" if kind == "video" else "manga"
    got = _FILTER_NAMES.get(group)
    if got is None:
        def table(names, same: dict, also: dict) -> dict:
            out = {S.name_key(n): n for n in names}
            for other, shown in same.items():
                if shown in names:
                    out.setdefault(S.name_key(other), shown)
            for shown, others in also.items():            # after the names themselves: a name is its own first
                for other in others:
                    if shown in names:
                        out.setdefault(S.name_key(other), shown)
            return out
        got = (table(genres(kind), {**HENTASIS_NAMES, **HAVEN_NAMES, **XS_NAMES, **HFSHARE_NAMES} if group == "video"
                     else {**_SAME, **NUDEMOON_NAMES, **NAMES.ALLHENTAI_NAMES},
                     {} if group == "video" else also_names(kind, of_genres=True)),
               table(tags(kind), {} if group == "video" else {**_SAME_TAGS, **NUDEMOON_NAMES, **NAMES.ALLHENTAI_NAMES},
                     {} if group == "video" else also_names(kind)))
        _FILTER_NAMES[group] = got
    return got


def also_names(kind: str, *, of_genres: bool = False) -> dict:
    """(v5.44.148) {a genre or tag of the feed: its other names} — as the sites themselves call it besides
    (``core/catalog_names.py``: the Lib API's ``alt_name``, nhentai's name): «Найти тег» finds «Тентакли» by
    «tentacles», a chip «NTR» of a title's page is the feed's «Нетораре».  (v5.44.155) …and Nude-Moon's and
    AllHentai's own words (:data:`NUDEMOON_NAMES`, ``ALLHENTAI_NAMES``): «рентген зрение» finds «X-Ray», «копро» —
    «Scat».  A name a gate closes — not."""
    if kind == "video":
        return {}
    table = NAMES.ALSO_GENRES if of_genres else NAMES.ALSO_TAGS
    known = genres(kind) if of_genres else tags(kind)
    out = {shown: list(others) for shown, others in table.items() if shown in known}
    for said, shown in (*NUDEMOON_NAMES.items(), *NAMES.ALLHENTAI_NAMES.items()):
        have = out.setdefault(shown, []) if shown in known else None
        if have is not None and S.name_key(said) not in {S.name_key(x) for x in (shown, *have)}:
            have.append(said)
    return {shown: tuple(n for n in others if not G.chip(n)) for shown, others in out.items() if others}


def feed_filter(kind: str, name: str, *, tag: bool = False) -> "tuple[str, str]":
    """A title's genre (``tag`` — its tag) as a site names it → the filter of the feed of ``kind`` that shows such
    titles: ("g", the feed's genre) or ("tg", the feed's tag) — the table of its own kind first («Боевик» of MangaLib —
    «Экшен», «Культивирование» — «Культивация»); ("never", "") — a name the page makes no search of at all
    (``gates.chip``); ("", "") — the feed has no such filter."""
    key = S.name_key(name)
    if not key:
        return "", ""
    by_genre, by_tag = _filter_names(kind)
    for table, field in (((by_tag, "tg"), (by_genre, "g")) if tag else ((by_genre, "g"), (by_tag, "tg"))):
        if key in table:
            return field, table[key]
    if G.chip(name):
        return "never", ""
    return "", ""


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
    "manga": (("ongoing", "Выходит", {"remanga": (2,), "mangalib": (1,), "hentailib": (1,),
                                      "allhentai": ("PROGRESS",)}),
              ("done", "Завершён", {"remanga": (1,), "mangalib": (2,), "hentailib": (2,), "allhentai": ("FINISHED",)}),
              ("announce", "Анонс", {"remanga": (5,), "mangalib": (3,), "hentailib": (3,), "allhentai": ("PLANNED",)}),
              ("frozen", "Заморожен", {"remanga": (3,), "mangalib": (4, 5), "hentailib": (4, 5),
                                       "allhentai": ("POSTPONED", "CANCELED")})),
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
SITES = {"manga": ("remanga", "mangalib", "hentailib", "hentaichan", "allhentai", "nhentai", "nudemoon"),
         "manhwa": ("remanga", "mangalib", "hentailib", "allhentai"),
         "video": ("yummy", "hentasis", "haven", "hentaixs", "hfshare")}                    # (v5.44.139)
LABELS = {"remanga": "Remanga", "mangalib": "MangaLib", "yummy": "YummyAnime", "hentaichan": "Хентай-тян",
          "hentasis": "Hentasis", "hentailib": "HentaiLib", "nhentai": "nhentai", "allhentai": "AllHentai",
          "nudemoon": "Nude-Moon", "haven": "HentaiHaven", "hentaixs": "Hentai-XS", "hfshare": "HFShare"}
# (v5.44.93) sites of hentai alone; (v5.44.96) HentaiLib; (v5.44.97) nhentai; (v5.44.99) AllHentai; (v5.44.131)
# Nude-Moon
ADULT_SITES = ("hentaichan", "hentasis", "hentailib", "nhentai", "allhentai", "nudemoon", "haven",  # (v5.44.138)
               "hentaixs", "hfshare")                                                            # (v5.44.139)
FILTERING = ("remanga", "mangalib", "yummy", "hentailib", "nudemoon",  # the sites that take the filters (Nude-Moon —
             "hentasis", "haven", "hentaixs", "hfshare", "nhentai",     # its tags and genres alone: v5.44.135;
             "allhentai")           # (v5.44.148) nhentai — its tags; (v5.44.155) AllHentai — its catalog API's
# (v5.44.148) a site's own tags of a query — what the site itself searches by besides the feed's genres and tags:
# {site: the kinds it has}; a query's ``extra`` / ``unextra`` — «site:kind:name» (the address's ``sg`` / ``sx``)
EXTRA_TYPES = {"nhentai": ("tag", "artist", "character", "parody", "group", "language", "category"),
               "lib": ("people",)}                 # (v5.44.149) the people of the Lib API: «<number> <name>»
EXTRA_WORDS = {"tag": "тег", "artist": "автор", "character": "персонаж", "parody": "пародия", "group": "группа",
               "language": "язык", "category": "вид", "people": "автор"}
# (v5.44.149) the sites of «Лента» a site's own tag is asked of («lib» — one base of people for both its sites), the
# name it is shown under, and whether its lists can be asked without it («none of these»)
EXTRA_SITES = {"nhentai": ("nhentai",), "lib": ("mangalib", "hentailib")}
EXTRA_LABELS = {"nhentai": "nhentai", "lib": "MangaLib"}
EXTRA_LEFT_OUT = ("nhentai",)
EXTRA_NAME = 80                    # characters of a site's own tag at most
SUGGEST_MIN = 2                    # letters typed before a site's own tags are asked
SUGGEST_SHOWN = 30                 # a site's own tags offered for the words typed
SUGGEST_TTL = 10 * 60
SUGGEST_KEPT = 400
# (v5.44.93) the orders of the lists of a site of hentai: its own list for each (none — it is not asked)
# — Хентай-тян's «Популярное» is empty now (19 bytes, 6.11.2026: ``Hentaichan.popular_empty``): «Обновления» for it too
HENTAICHAN_ORDER = {"popular": "new", "updates": "new", "new": "new"}
HENTASIS_ORDERS = ("popular", "new")         # its one list — the newest first: as «Популярное» too (said on the page)
NEWEST_ORDERS = HENTASIS_ORDERS              # (v5.44.139) Hentai-XS and HFShare: the same — their lists, newest first
# (v5.44.131) Nude-Moon: its lists by the date and by the views (``manhwa_sources.NudeMoon.FEED``)
NUDEMOON_ORDER = {"popular": "popular", "updates": "new", "new": "new"}
# (v5.44.97) nhentai: «Популярное» — its search by the favourites of all time, the new — its list of the newest
NHENTAI_ORDER = {"popular": "popular", "updates": "date", "new": "date"}
# (v5.44.99) AllHentai: its catalog's orders (an order it does not know is its default — RATING: not asked by that);
# its kinds (``category``) of each tab, and of each type the tab offers ((v5.44.155) every title has one of its eight
# kinds — 30 171 «Манга», 4 562 «Комикс», 1 758 «Манхва», 694 «3D», 572 «Без текста», 75 «Руманга», 71 «Маньхуа», 27
# «Анимация», 11.11.2026; their numbers — ``catalog_names.ALLHENTAI_KIND_IDS``); its words of a title's state
# (``productionStatus``: PLANNED, PROGRESS, POSTPONED, CANCELED, FINISHED, NON_FINISHED)
ALLHENTAI_ORDER = {"popular": "POPULARITY", "updates": "DATE_UPDATE", "new": "DATE_CREATE", "rating": "RATING"}
ALLHENTAI_PAGE = 50
ALLHENTAI_KINDS = {"manga": {"manga": ("Манга", "3D", "Без текста", "Анимация"), "comics": ("Комикс",)},
                   "manhwa": {"manhwa": ("Манхва",), "manhua": ("Маньхуа",), "russian": ("Руманга",)}}
_ALLHENTAI_KNOWN = frozenset(k for tab in ALLHENTAI_KINDS.values() for names in tab.values() for k in names)
_ALLHENTAI_STATUS = {"PROGRESS": "ongoing", "FINISHED": "done", "PLANNED": "announce", "POSTPONED": "frozen",
                     "CANCELED": "frozen"}
ALLHENTAI_YEARS = (1900, 2100)     # its ``years=`` wants both ends: the open one
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


def typed(kind: str, mode: str) -> bool:
    """(v5.44.137) Whether a site of the list tells the types and the statuses of its titles: the videos — YummyAnime
    alone (the sites of hentai say none; in «NSFW» the page offers neither)."""
    if kind == "video":
        return "yummy" in sites_shown(kind, mode)
    return True


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
    extra: tuple = ()                # (v5.44.148) a site's own tags, all of them: «nhentai:artist:name»
    unextra: tuple = ()              # …none of them

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
        def here(value: str) -> bool:                # a site of the tag is among the mode's sites of the kind
            return bool(value) and any(s in mine for s in EXTRA_SITES.get(value.split(":")[0], ()))
        chosen = tuple(dict.fromkeys(x for x in map(extra_of, self.extra) if here(x)))
        object.__setattr__(self, "extra", chosen[:MAX_CHOSEN])
        object.__setattr__(self, "unextra", tuple(dict.fromkeys(
            x for x in map(extra_of, self.unextra)
            if here(x) and x.split(":")[0] in EXTRA_LEFT_OUT and x not in chosen))[:MAX_CHOSEN])

    def sig(self) -> str:
        return hashlib.sha1(repr(self).encode("utf-8")).hexdigest()[:16]

    def asked(self) -> tuple:
        """The sites of the query (of those the mode shows — :func:`sites_for`); (v5.44.148) with a site's own tag
        chosen — that site alone (no other has such a tag)."""
        mine = self.sites or sites_for(self.kind, self.mode)
        own = {site for x in self.extra for site in EXTRA_SITES.get(x.split(":")[0], ())}
        if not own:
            return mine
        return (tuple(s for s in mine if s in own)       # (v5.44.149) …of the sites picked; none of them is its site —
                or tuple(s for s in sites_for(self.kind, self.mode) if s in own))    # the tag's own sites of the mode

    def plain(self) -> bool:
        """Nothing chosen but the order (and the mode)."""
        return not (self.text or self.genres or self.without or self.tags or self.untags or self.types or self.status
                    or any(self.chapters) or any(self.rating) or any(self.years) or self.sites or self.extra
                    or self.unextra)


def extra_of(value) -> str:
    """(v5.44.148) «site:kind:name» of a site's own tag as an address says it — the site and the kind it has, the name
    in small letters without quotes; "" — not one."""
    site, _, rest = str(value or "").partition(":")
    kind, _, name = rest.partition(":")
    name = " ".join(name.replace('"', " ").replace("\\", " ").split())
    if site == "lib":                                # (v5.44.149) «<the person's number> <the name shown>», as written
        found = re.fullmatch(r"(\d{1,9})(?: (.*))?", name)
        name = f"{int(found.group(1))} {found.group(2) or ''}".strip()[:EXTRA_NAME].strip() if found else ""
    else:
        name = name.lower()[:EXTRA_NAME].strip()
    if kind not in EXTRA_TYPES.get(site, ()) or not name:
        return ""
    return f"{site}:{kind}:{name}"


def extra_parts(value: str) -> tuple:
    """(site, kind, name) of «site:kind:name»."""
    site, kind, name = (str(value).split(":", 2) + ["", ""])[:3]
    return site, kind, name


def extra_shown(value: str) -> str:
    """The name a site's own tag is shown under: nhentai's — its name; a person of the Lib API — the name after the
    number («№ 229» — none was given)."""
    site, _kind, name = extra_parts(value)
    if site == "lib":
        number, _, shown = name.partition(" ")
        return shown or f"№ {number}"
    return name


def extra_where(value: str, kind: str = "", mode: str = "") -> str:
    """Whose tag it is, in words: «nhentai»; the people of the Lib API — its sites the mode of the kind shows
    («MangaLib + HentaiLib»; no kind given — «MangaLib»)."""
    site = extra_parts(value)[0]
    shown = [LABELS[s] for s in EXTRA_SITES.get(site, ()) if s in sites_shown(kind, mode)] if kind else []
    return " + ".join(shown) or EXTRA_LABELS.get(site, site)


def extra_label(value: str, kind: str = "", mode: str = "") -> str:
    """«автор: name · nhentai» — a site's own tag in words."""
    _site, what, _name = extra_parts(value)
    return f"{EXTRA_WORDS.get(what, what)}: {extra_shown(value)} · {extra_where(value, kind, mode)}"


def extra_single(value: str) -> bool:
    """(v5.44.149) One of its kind at a time, and no «none of these»: a person of the Lib API (its catalogue takes one
    ``target_id``)."""
    return extra_parts(value)[0] not in EXTRA_LEFT_OUT


def extra_closed(site: str, name: str) -> bool:
    """Whether a gate of the program closes a site's own tag of this name: the site's lists are always asked without
    it (``gates.left_out``), or every title with it is closed (``gates.title``) — it is offered by nothing, and a
    query with it asks no site."""
    key = S.name_key(name)
    return bool(any(key in {S.name_key(n) for n in G.left_out(s).names} for s in EXTRA_SITES.get(site, (site,)))
                or G.title(site, tags=[name]))


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
    for site in ("remanga", "mangalib", "hentailib", "nudemoon", "nhentai", "allhentai"):
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
    for name in q.genres:
        got = _genre_of(q, name, site)
        if got is None:
            return [], [], f"у {label} нет жанра «{name}»"
        refused = G.genre(site, q.mode, got[1], name) if got[0] == "g" else ""
        if refused:                                 # (v5.44.96) a gate: the genre is not asked of the site
            return [], [], refused
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
    always = G.left_out(site, q.mode)                            # what its lists are always asked without
    left += [("t", n) for n in always.tags]
    left += [("g", n) for n in always.genres]
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
        return None, "MangaLib показывает хентай только со входом («Настройки → Манга и манхва»)"
    return _lib_url(q, page, "mangalib")


def hentailib_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.96) HentaiLib: the API and the filters of MangaLib, its site 4 — every title of it is hentai; what its
    lists are always asked without — ``_wanted`` (``gates.left_out``)."""
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
    people = [x for x in q.extra if extra_parts(x)[:2] == ("lib", "people")]
    if len(people) > 1:                              # (v5.44.149) its catalogue takes one ``target_id``
        return None, f"у {label} автор выбирается один"
    if people:                                       # …the titles of a person, as the site's own page asks them
        if extra_closed("lib", extra_shown(people[0])):
            return None, f"по «{extra_shown(people[0])}» {label} ничего не показывает"
        parts += [f"target_id={extra_parts(people[0])[2].split(' ', 1)[0]}", "target_model=people"]
    if q.text:
        parts.append(f"q={quote(q.text)}")
    query = "&".join(parts) + _bounds([("chap_count_min", q.chapters[0]), ("chap_count_max", q.chapters[1]),
                                       ("rating_min", float(q.rating[0])), ("rating_max", float(q.rating[1])),
                                       ("year_min", q.years[0]), ("year_max", q.years[1])])
    return f"{source.api()}/api/manga?{query}", ""


def yummy_url(q: Query, page: int, api: str) -> "tuple[str | None, str]":
    _wanted_genres, _left, why = _wanted(q, "yummy")    # (v5.44.137) a genre of the sites of hentai alone («Минет»)
    if why:
        return None, why
    sort, forward = dict((k, s) for k, _l, s in orders("video"))[q.order]["yummy"]
    parts = [f"limit={VIDEO_PAGE}", f"offset={VIDEO_PAGE * (page - 1)}", f"sort={sort}"]
    if forward:
        parts.append(f"sort_forward={forward}")
    if q.genres:
        parts.append("genres=" + ",".join(str(VIDEO_GENRES[g]["yummy"]) for g in q.genres))
    unwanted = [str(VIDEO_GENRES[g]["yummy"]) for g in q.without if "yummy" in VIDEO_GENRES[g]]
    if unwanted:
        parts.append("exclude_genres=" + ",".join(unwanted))
    chosen = _ids(types("video"), q.types, "yummy")
    if chosen:
        parts.append("types=" + ",".join(chosen))
    said = _ids(statuses("video"), q.status, "yummy")
    if not said and q.order == "new":
        said = list(VIDEO_NEW_STATUSES)            # no announcements without a status chosen
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


def nudemoon_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.131) Nude-Moon: its lists, its search — one page.  (v5.44.135) Its genres and tags
    (:data:`NUDEMOON_GENRES`, :data:`NUDEMOON_TAGS`): «all of these» — its pages of those tags (``/tags/a+b`` — the site
    joins them), «none of these» and a search by a name with tags — the tags of the rows of its page
    (:func:`_nudemoon_page`); a genre or tag it has not — not asked (as the other sites), nor with the filters it has
    not (status, chapters, rating, years)."""
    source = S.by_name("nudemoon")
    if source is None:
        return None, ""
    if q.status or any(q.chapters) or any(q.rating) or any(q.years):
        return None, "у Nude-Moon из фильтров только жанры и теги"
    if q.types and "manga" not in q.types:
        return None, "у Nude-Moon только манга"
    wanted, _left, why = _wanted(q, "nudemoon")
    if why:
        return None, why
    how = NUDEMOON_ORDER.get(q.order)
    if q.text:
        return (source.search_url(q.text) if page == 1 else None), ""
    if how is None:
        return None, "Nude-Moon так не сортирует"
    if wanted:
        return source.tag_url([word for _kind, word in wanted], page, how), ""
    return source.feed_url(how, page), ""


def nhentai_words(q: Query) -> "tuple[str, str]":
    """(v5.44.148) (the words of nhentai's search for what the query chose, why it has no such gallery): a genre or tag
    of the feed — nhentai's tag of it (``catalog_names``; none — it has no such gallery), its own tags
    (``Query.extra``) — ``kind:"name"``; «none of these» — the same with «-» (one it does not name — nothing to leave
    out there).  One a gate closes (:func:`extra_closed`) — no gallery is asked."""
    yes, no = [], []
    for name in (*q.genres, *q.tags):
        got = _genre_of(q, name, "nhentai") if name in q.genres else None
        word = got[1] if got else tags(q.kind).get(name, {}).get("nhentai")
        if not word:
            return "", f"у nhentai нет {'жанра' if name in q.genres else 'тега'} «{name}»"
        yes.append(("tag", str(word)))
    for name in (*q.without, *q.untags):
        got = _genre_of(q, name, "nhentai") if name in q.without else None
        word = got[1] if got else tags(q.kind).get(name, {}).get("nhentai")
        if word:
            no.append(("tag", str(word)))
    for value in q.extra:
        site, kind, name = extra_parts(value)
        if site != "nhentai":
            continue
        if extra_closed(site, name):
            return "", f"по «{name}» nhentai ничего не показывает"
        yes.append((kind, name))
    for value in q.unextra:
        site, kind, name = extra_parts(value)
        if site == "nhentai" and not extra_closed(site, name):    # one a gate closes is left out of every list anyway
            no.append((kind, name))
    words = [f'{kind}:"{name}"' for kind, name in dict.fromkeys(yes)]
    words += [f'-{kind}:"{name}"' for kind, name in dict.fromkeys(no) if (kind, name) not in yes]
    return " ".join(words), ""


def nhentai_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.97) nhentai: its newest or its search (by a name, «Популярное»).  (v5.44.148) Its filters — its tags:
    the feed's genres and tags it names its own way and its own tags (:func:`nhentai_words` — the words of its
    search); the filters it has not (status, chapters, rating, years) — not asked with them, said why."""
    if q.status or any(q.chapters) or any(q.rating) or any(q.years):
        return None, "у nhentai из фильтров только теги"
    if q.types and "manga" not in q.types:
        return None, "у nhentai только манга"
    words, why = nhentai_words(q)
    if why:
        return None, why
    how = NHENTAI_ORDER.get(q.order)
    if how is None:
        return None, "nhentai так не сортирует"
    return N.list_url(how, page, q.text, words), ""


def allhentai_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.99) AllHentai: its catalog API.  (v5.44.155; the owner: «должно показывать любой тег», and its page of
    tags sent 11.11.2026) The API takes the filters — as the open reader apps ask it, tried from the owner's computer
    11.11.2026: ``includeElementIds`` — a genre, tag or kind a title has (several — all of them: 370 titles of one
    tag, 13 of two), ``excludeElementIds`` — none of these (37 560 = all without those 370);
    ``includeProductionStatuses`` (several — any of them), ``years=<from>,<to>`` (both ends, else it is not a
    filter), ``q`` — a name (the other names of a title too; pages; with the other filters).  The tab's kinds (of
    the types chosen) — by leaving the other kinds out: «Манхва» is asked for its 1 829 titles, not shown the two
    manhwa of each fifty of everything.  What its lists are always asked without (``gates.left_out`` —
    :func:`_wanted`) is left out of every address.  What it has not (the number of chapters, a rating) — not asked
    with them, said why."""
    source = S.by_name("allhentai")
    if source is None:
        return None, ""
    if any(q.chapters) or any(q.rating):
        return None, "у AllHentai нет фильтров по числу глав и по оценке"
    kinds = ALLHENTAI_KINDS.get(q.kind, {})
    if q.types and not any(t in kinds for t in q.types):
        return None, "у AllHentai таких нет"
    wanted, left, why = _wanted(q, "allhentai")
    if why:
        return None, why
    how = ALLHENTAI_ORDER.get(q.order) or ("POPULARITY" if q.text else None)
    if how is None:
        return None, "AllHentai так не сортирует"
    mine = {name for t, names in kinds.items() if not q.types or t in q.types for name in names}
    yes = list(dict.fromkeys(number for _what, number in wanted))
    no = list(dict.fromkeys([*(number for _what, number in left),
                             *(number for name, number in NAMES.ALLHENTAI_KIND_IDS.items() if name not in mine)]))
    if set(yes) & set(no):                               # «3D» of the tab «Манхва»: a kind of the other tab
        return None, "у AllHentai таких нет"
    parts = [f"offset={ALLHENTAI_PAGE * (max(1, page) - 1)}", f"sortType={how}"]
    if q.text:
        parts.append(f"q={quote(q.text)}")
    parts += [f"includeElementIds={number}" for number in yes] + [f"excludeElementIds={number}" for number in no]
    parts += [f"includeProductionStatuses={word}" for word in _ids(statuses(q.kind), q.status, "allhentai")]
    if any(q.years):
        parts.append(f"years={q.years[0] or ALLHENTAI_YEARS[0]},{q.years[1] or ALLHENTAI_YEARS[1]}")
    return f"{source.site()}/api/catalog/search?" + "&".join(parts), ""


def hentasis_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.93) Hentasis: its list, the newest first.  (v5.44.137; the owner: «почему в видео нет фильтров») Its
    genres (:data:`HENTASIS_GENRES`): «all of these» — the first one's page ``/xfsearch/<слово>/`` (the others and «none
    of these» — by the genres of its rows: :func:`_hentasis_page`); one year — its page ``/tags/<год>/``, years and a
    rating — by its rows; its search (``index.php?do=search``, ten a page; the genres and the year of each found title —
    from its page); what it has not (the types, the statuses, another order) — not asked, said why."""
    if q.types or q.status:
        return None, "у Hentasis нет типов и статусов — только жанры, годы, оценка и поиск"
    wanted, _left, why = _wanted(q, "hentasis")
    if why:
        return None, why
    if q.text:
        return (f"{HENTASIS_SITE}/index.php?do=search&subaction=search&search_start={max(1, page)}&full_search=0"
                f"&story={quote(q.text)}"), ""
    if q.order not in HENTASIS_ORDERS:
        return None, "Hentasis так не сортирует"
    if wanted:
        base = f"{HENTASIS_SITE}/xfsearch/{quote(str(wanted[0][1]), safe='')}/"
    elif q.years[0] and q.years[0] == q.years[1]:
        base = f"{HENTASIS_SITE}/tags/{q.years[0]}/"
    elif any(q.years):                                # several years: their pages one after another (_hentasis_years)
        low, high = q.years[0] or HENTASIS_FIRST_YEAR, q.years[1] or time.localtime().tm_year + 1
        return f"{HENTASIS_SITE}/tags/{high}/#years={low}-{high}&n={max(1, page)}", ""
    else:
        base = HENTASIS_SITE + "/"
    return base + ("" if page <= 1 else f"page/{page}/"), ""


HAVEN_ORDER = {"popular": "date", "new": "date", "title": "title"}   # (v5.44.138) its WordPress: by date, by title
HAVEN_FIELDS = "id,slug,title,meta,wp-manga-genre,wp-manga-tag,wp-manga-release"


def haven_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.138; the owner: «в хентае только хентасис а не другие из поддерживаемых») HentaiHaven: the catalogue of its
    WordPress (``/wp-json/wp/v2/wp-manga``, asked from the owner's computer 9.11.2026 — 1056 titles): its genres
    (:data:`HAVEN_GENRES`; «all of these» — ``[operator]=AND``, «none of these» — ``_exclude``), its years (the
    taxonomy ``wp-manga-release``: :data:`HAVEN_YEARS`), its search; what its lists are always asked without — left out
    (``_wanted``); «Популярное» — the newest (it counts no views there); what it has not (types, statuses, a rating) —
    not asked, said why."""
    if q.types or q.status or any(q.rating):
        return None, "у HentaiHaven из фильтров только жанры, годы и поиск"
    wanted, left, why = _wanted(q, "haven")
    if why:
        return None, why
    how = HAVEN_ORDER.get(q.order)
    if how is None:
        return None, "HentaiHaven так не сортирует"
    parts = [f"per_page={HAVEN_PAGE}", f"page={max(1, page)}", f"_fields={HAVEN_FIELDS}"]
    if how == "title":
        parts += ["orderby=title", "order=asc"]
    chosen = [str(n) for kind, n in wanted if kind == "g"]
    if len(chosen) == 1:
        parts.append(f"wp-manga-genre={chosen[0]}")
    elif chosen:
        parts += [f"wp-manga-genre[terms]={','.join(chosen)}", "wp-manga-genre[operator]=AND"]
    unwanted = [str(n) for kind, n in left if kind == "g"]
    if unwanted:
        parts.append("wp-manga-genre_exclude=" + ",".join(unwanted))
    untagged = [str(n) for kind, n in left if kind == "t"]
    if untagged:
        parts.append("wp-manga-tag_exclude=" + ",".join(untagged))
    if any(q.years):
        low, high = q.years[0] or 0, q.years[1] or 9999
        years = [str(n) for year, n in HAVEN_YEARS.items() if low <= year <= high]
        if not years:
            return None, "у HentaiHaven нет тайтлов этих лет"
        parts.append("wp-manga-release=" + ",".join(years))
    if q.text:
        parts.append(f"search={quote(q.text)}")
    return f"{HAVEN_API}/wp-manga?" + "&".join(parts), ""


def xs_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.139; the owner: «в хентае только хентасис а не другие из поддерживаемых») Hentai-XS: its list (ten a page,
    the newest first; ``/animepage.php?id=N``), a year — its page ``/age.php?age=<год>``, several — their pages one
    after another (the newest year first), its search (a form: :func:`_xs_page` sends it); its genres — by its rows
    (:data:`XS_GENRES`; with a genre chosen :data:`XS_WALK` of its pages are one page of «Лента»).  What it has not
    (types, statuses, a rating, another order) — not asked, said why."""
    if q.types or q.status or any(q.rating):
        return None, "у Hentai-XS из фильтров только жанры, годы и поиск"
    _wanted_ids, _left, why = _wanted(q, "hentaixs")
    if why:
        return None, why
    if q.text:                                        # its search: every title found in one answer — one page
        return (f"{XS_SITE}/search.php#search={quote(q.text)}", "") if page <= 1 else (None, "")
    if q.order not in NEWEST_ORDERS:
        return None, "Hentai-XS так не сортирует"
    if q.years[0] and q.years[0] == q.years[1]:
        return f"{XS_SITE}/age.php?age={q.years[0]}" + ("" if page <= 1 else f"&page={page}"), ""
    if any(q.years):
        low, high = q.years[0] or XS_FIRST_YEAR, q.years[1] or time.localtime().tm_year + 1
        return f"{XS_SITE}/age.php#years={low}-{high}&n={max(1, page)}", ""
    if q.genres or q.without:                         # its rows alone tell the genres: several of its pages
        return f"{XS_SITE}/animepage.php#walk={XS_WALK}&n={max(1, page)}", ""
    return (XS_SITE + "/" if page <= 1 else f"{XS_SITE}/animepage.php?id={page}"), ""


def _hfshare_base(q: Query) -> str:
    """(v5.44.139) The genre whose own page of HFShare the list is (its others — by the rows): «Без цензуры» (its tag
    is not in the rows), «3D» (its section), else the first chosen."""
    for name in ("Без цензуры", "3D"):
        if name in q.genres:
            return name
    return q.genres[0] if q.genres else ""


def hfshare_url(q: Query, page: int) -> "tuple[str | None, str]":
    """(v5.44.139) HFShare: «Хентай аниме» (``/hentai-anime/``, twelve a page, the newest first); a genre — its pages
    ``/xfsearch/<value>/page/N/`` (the first — ``index.php?do=xfsearch``; :data:`HFSHARE_GENRES`; «3D» — its section,
    «Без цензуры» — its tag), the others, «none of these», years, a rating (its people's «Нравится») — by its rows;
    its search (DataLife Engine's: every title found in one answer, no genres — each one's page tells them).  What it
    has not — not asked, said why."""
    if q.types or q.status:
        return None, "у HFShare нет типов и статусов — только жанры, годы, оценка и поиск"
    _wanted_ids, _left, why = _wanted(q, "hfshare")
    if why:
        return None, why
    if "Без цензуры" in q.without:
        return None, "HFShare не говорит в списках, есть ли цензура, — «без этих» для «Без цензуры» он не умеет"
    if q.text:
        return (f"{HFSHARE_SITE}/index.php?do=search&subaction=search&search_start=0&full_search=0&result_from=1"
                f"&story={quote(q.text)}#n={max(1, page)}"), ""
    if q.order not in NEWEST_ORDERS:
        return None, "HFShare так не сортирует"
    base = HFSHARE_SITE + "/hentai-anime/"
    first = _hfshare_base(q)
    if first:
        address = HFSHARE_GENRES[first][0]
        if not address.startswith("/") and page <= 1:
            # the first page of a genre — DataLife Engine's own address of it: ``/xfsearch/<value>/`` of «Юри»,
            # «Футанари», «Групповой секс»… is the site's «Ошибка 404» (9.11.2026), their next pages are not
            return f"{HFSHARE_SITE}/index.php?do=xfsearch&xfname=janre&xf={address}", ""
        base = HFSHARE_SITE + (quote(address) if address.startswith("/") else f"/xfsearch/{address}/")
    return base + ("" if page <= 1 else f"page/{page}/"), ""


def addresses(q: Query, n: int, settings: "dict | None" = None) -> "dict[str, tuple]":
    """{site: (its address for page ``n`` of the query or None, why not)} — the query's sites."""
    out = {}
    for site in q.asked():
        foreign = [x for x in q.extra if site not in EXTRA_SITES.get(extra_parts(x)[0], ())]
        if foreign:                                   # (v5.44.148) another site's own tag: this one has no such
            _site, kind, _name = extra_parts(foreign[0])
            out[site] = (None, f"«{extra_shown(foreign[0])}» — {EXTRA_WORDS.get(kind, 'тег')} "
                               f"{EXTRA_LABELS.get(_site, _site)}")
            continue
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
        elif site == "nudemoon":
            out[site] = nudemoon_url(q, n)
        elif site == "haven":
            out[site] = haven_url(q, n)
        elif site == "hentaixs":                     # (v5.44.139)
            out[site] = xs_url(q, n)
        elif site == "hfshare":
            out[site] = hfshare_url(q, n)
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
        if G.title("remanga", names=[str(n) for n in names if n], adult=bool(x.get("is_erotic"))):
            continue                                  # (v5.44.143) a gate closed it — by the name too, as MangaLib's
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
        if G.title(site, names=names, adult=adult):               # (v5.44.96) a gate closed it — by the name too
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


def hentasis_items(text: str, page: int = 1, base: str = "", q: "Query | None" = None) -> tuple:
    """(v5.44.93) ([:class:`Item`], whether there is a page more) of a page of Hentasis's list: ``div.short-item`` —
    the link ``a.short-link`` «Русское / Romaji (2026г.)», the picture of ``.short-img``, the rating (the width of
    ``.current-rating``, %) and the votes, the lines of ``.mov-lines`` («Год выпуска», «Жанр», «Эпизоды»…).  A post
    of the site's own (one name, no year: «Есть свет в конце тонеля…» — the first of its first page) and a title that
    a gate closes (its genres, its names: ``gates.title``) — not.  ``base`` — the page's
    address (its addresses are read against it).  (v5.44.137) ``q`` — a row the query's genres, years or rating leave
    out (:func:`_hentasis_fits`) — not."""
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
        genres = [g.strip() for g in re.split(r"[,.]\s", lines.get("Жанр", "") + " ") if g.strip()]
        if G.title("hentasis", names=names, genres=genres):
            continue                                   # a gate closed it
        rate = re.search(r'current-rating["\'][^>]*width:\s*(\d+(?:\.\d+)?)%', block)
        rating = round(float(rate.group(1)) / 10, 1) if rate else 0.0
        if q is not None and not _hentasis_fits(genres, lines.get("Год выпуска", ""), rating, q):
            continue                                   # (v5.44.137) the query's other genres, «none of these», years
        year = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", lines.get("Год выпуска", ""))
        picture = re.search(r'class=["\'][^"\']*\bshort-img\b[^"\']*["\'][^>]*>\s*<img\b([^>]*)>', block)
        src = (re.search(r'\bdata-src=["\']([^"\']+)["\']', picture.group(1))
               or re.search(r'\bsrc=["\']([^"\']+)["\']', picture.group(1))) if picture else None
        cover = urljoin(base, _html.unescape(src.group(1).strip())) if src else ""
        votes = re.search(r'data-vote-num-id=["\']\d+["\'][^>]*>\s*([\d\s]+)<', block)
        episodes = lines.get("Эпизоды", "").replace("-", "–")
        seen.add(number.group(1))
        out.append(Item(site="hentasis", key=number.group(1), names=names, url=url, shown_type="Хентай",
                        year=int(year.group(0)) if year else 0, cover=cover, rating=rating,
                        votes=int(re.sub(r"\D", "", votes.group(1)) or 0) if votes else 0, adult=True,
                        status=(f"серии {episodes}" if re.search(r"\D", episodes) else
                                f"{episodes} серия" if episodes == "1" else f"{episodes} серий" if episodes else "")))
    pages = [int(x) for x in re.findall(r'href=["\'][^"\']*/page/(\d+)/?["\']', text)]
    return out, any(x > page for x in pages)


# (v5.44.137) a row's genre as Hentasis writes it → the feed's name (:data:`HENTASIS_GENRES`, :data:`HENTASIS_ALSO`)
_HENTASIS_ROW = {**{S.name_key(said): shown for shown, (_address, said) in HENTASIS_GENRES.items()},
                 **{S.name_key(said): shown for said, shown in HENTASIS_ALSO.items()}}


def _hentasis_fits(genres: list, years: str, rating: float, q: Query) -> bool:
    """(v5.44.137) A title of Hentasis by its genres (its rows' words), its years («2022-2025»), its rating (0…10) — what
    the query asks: every genre chosen, none of «none of these», a year of its in the years, its rating in the bounds (a
    title without one — not, when a rating is asked)."""
    return _rows_fit(genres, years, rating, q, _HENTASIS_ROW)


def _rows_fit(genres: list, years: str, rating: float, q: Query, row: dict, served: tuple = ()) -> bool:
    """(v5.44.139) A title of a site whose rows the feed narrows (:data:`LOCAL_FILTERS`) — what the query asks (as
    :func:`_hentasis_fits`): ``row`` — the site's words → the feed's genres; ``served`` — genres the list asked is of
    already (HFShare's tag «без цензуры»: its rows do not say it)."""
    shown = {S.name_key(row.get(S.name_key(g), g)) for g in genres}
    if any(S.name_key(g) not in shown for g in q.genres if g not in served) or \
            any(S.name_key(g) in shown for g in q.without):
        return False
    if any(q.years):
        found = [int(y) for y in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(years or ""))]
        low, high = q.years[0] or 0, q.years[1] or 9999
        if not found or not any(low <= y <= high for y in range(min(found), max(found) + 1)):
            return False
    if any(q.rating):
        low, high = float(q.rating[0] or 0), float(q.rating[1] or 10)
        if not rating or not low <= rating <= high:
            return False
    return True


def hentasis_found(text: str, page: int = 1, base: str = "") -> tuple:
    """(v5.44.137) ([:class:`Item`], whether there is a page more) of a page of Hentasis's search (ten a page):
    ``a.sres-wrap`` — its picture (``.sres-img img``), its name (``.sres-text h2`` «Русское / Romaji (2026г.)»); its
    pages — ``list_submit(N)``.  The rows say no genres: :func:`_hentasis_page` reads them from each title's page."""
    base = base or HENTASIS_SITE + "/"
    out, seen = [], set()
    for attrs, inner in re.findall(r"<a\b([^>]*)>(.*?)</a>", text, re.S):
        if not re.search(r'class=["\'][^"\']*\bsres-wrap\b', attrs):
            continue
        href = re.search(r'href=["\']([^"\']+)["\']', attrs)
        if not href:
            continue
        url = urljoin(base, _html.unescape(href.group(1)))
        number = re.search(r"/(\d+)-[^/]*\.html?$", urlparse(url).path)
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", inner, re.S)
        if not number or not heading or number.group(1) in seen:
            continue
        full = _plain(heading.group(1))
        years = re.search(r"\(\s*((?:19|20)\d{2})(?:\s*[-–]\s*\d{4})?\s*г?\.?\s*\)\s*$", full)
        title = re.sub(r"\s*\(\s*\d{4}(?:\s*[-–]\s*\d{4})?\s*г?\.?\s*\)\s*$", "", full)
        names = [x.strip() for x in title.split(" / ") if x.strip()]
        if len(names) < 2 and not years:
            continue                                   # the site's own post, not a title
        if not S._cyrillic(names[0]) and any(S._cyrillic(x) for x in names[1:]):
            names.sort(key=lambda x: not S._cyrillic(x))
        if G.title("hentasis", names=names):
            continue                                   # a gate closed it: by its names (its genres — after)
        picture = re.search(r"<img\b([^>]*)>", inner)
        src = (re.search(r'\bdata-src=["\']([^"\']+)["\']', picture.group(1))
               or re.search(r'\bsrc=["\']([^"\']+)["\']', picture.group(1))) if picture else None
        seen.add(number.group(1))
        out.append(Item(site="hentasis", key=number.group(1), names=names, url=url, shown_type="Хентай",
                        year=int(years.group(1)) if years else 0, adult=True,
                        cover=urljoin(base, _html.unescape(src.group(1).strip())) if src else ""))
    pages = [int(x) for x in re.findall(r"list_submit\((\d+)\)", text)]
    return out, any(x > page for x in pages)


HENTASIS_FACTS_KEPT = 3000             # (v5.44.137) titles whose genres a search read from their pages, kept
_HENTASIS_FACTS: "OrderedDict[tuple, tuple]" = OrderedDict()     # (site, key) → (genres, years, rating)
_HENTASIS_FACTS_LOCK = threading.Lock()
_PAGE_HEADERS = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def _site_of_page(site: str) -> str:
    """(v5.44.139) The address the program asks a site of pages at (its Referer)."""
    return {"hentasis": HENTASIS_SITE, "hentaixs": XS_SITE, "hfshare": HFSHARE_SITE}.get(site, "")


def likes_of(text: str) -> tuple:
    """(v5.44.139) (rating 0…10, votes) of HFShare's «Нравится / Не нравится» of a title (its first ``data-hr`` box —
    a row's, or its page's own): the share of «Нравится»; (0.0, 0) — none, or nobody voted."""
    box = re.search(r"data-hr=[\"']\d+[\"']", str(text or ""))
    if not box:
        return 0.0, 0
    part = text[box.start():box.start() + 3000]
    pct = re.search(r"data-hr-pct[^>]*>\s*(\d{1,3})\s*%", part)
    votes = sum(int(x) for x in re.findall(r"data-hr-(?:up|down)\b[^>]*>\s*(\d{1,9})\s*<", part))
    return (round(min(100, int(pct.group(1))) / 10, 1), votes) if pct and votes else (0.0, 0)


def _page_rating(site: str, text: str) -> tuple:
    """(rating 0…10, votes) a title's page or row tells: Hentasis's stars (the width of ``.current-rating``, %), (v5.44.139)
    HFShare's «Нравится» of its people (:func:`likes_of`)."""
    if site == "hfshare":
        return likes_of(text)
    rate = re.search(r'current-rating["\'][^>]*width:\s*(\d+(?:\.\d+)?)%', text)
    return (round(float(rate.group(1)) / 10, 1) if rate else 0.0), 0


def _hentasis_facts(net, item: Item) -> "tuple | None":
    """(v5.44.137) (genres, years, rating) of a title of Hentasis — (v5.44.139) or of HFShare — from its own page (a
    search's rows say none); kept — a title's genres do not change; None — its page did not come (the title is not
    shown: its genres are not known)."""
    with _HENTASIS_FACTS_LOCK:
        kept = _HENTASIS_FACTS.get((item.site, item.key))
    if kept is not None:
        return kept
    from core import video_meta
    label = LABELS.get(item.site, item.site)
    try:
        body, ctype = net.fetch(item.url, what=label, headers=dict(_PAGE_HEADERS, Referer=_site_of_page(item.site) + "/"))
    except S.SourceError as exc:
        _LOG.info("catalog: %s: the page of %s did not come: %s", label, item.key, exc)
        return None
    text = S._decoded(body, ctype)
    meta = video_meta.parse_page(text, item.url)
    genres = [str(g).strip() for g in meta.get("genres") or [] if str(g).strip()]
    facts = (genres, str(meta.get("year") or item.year or ""), _page_rating(item.site, text)[0])
    with _HENTASIS_FACTS_LOCK:
        _HENTASIS_FACTS[(item.site, item.key)] = facts
        while len(_HENTASIS_FACTS) > HENTASIS_FACTS_KEPT:
            _HENTASIS_FACTS.popitem(last=False)
    return facts


def _with_facts(net, items: list, q: Query, row: dict) -> list:
    """(v5.44.137) The titles a search found whose rows tell no genres: each one's page read (four at once, kept) —
    the gate asked with them (``gates.title``: the title's site, names and these genres), then the
    query's genres, years, rating (``row`` — the site's words → the feed's genres); a page that did not come — the
    title is not shown."""
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="catalog-facts") as pool:
        facts = list(pool.map(lambda item: _hentasis_facts(net, item), items))
    kept = []
    for item, got in zip(items, facts):
        if got is None or G.title(item.site, names=item.names, genres=got[0]):
            continue                                   # a gate closed it: by its genres too
        if _rows_fit(*got, q, row):
            item.rating = item.rating or got[2]
            kept.append(item)
    return kept


HENTASIS_YEARS_TTL = 30 * 60          # (v5.44.137) seconds the number of pages of a year of Hentasis is kept
_HENTASIS_YEARS: dict = {}             # (site, year) → (its pages, its first page's text, when)


def _hentasis_text(net, address: str, site: str = "hentasis") -> str:
    body, ctype = net.fetch(address, what=LABELS[site], headers=dict(_PAGE_HEADERS, Referer=_site_of_page(site) + "/"))
    return S._decoded(body, ctype)


def _year_url(site: str, year: int, page: int = 1) -> str:
    """The address of page ``page`` of a year's own list: Hentasis's ``/tags/<год>/``, (v5.44.139) Hentai-XS's
    ``/age.php?age=<год>``."""
    if site == "hentaixs":
        return f"{XS_SITE}/age.php?age={year}" + ("" if page <= 1 else f"&page={page}")
    return f"{HENTASIS_SITE}/tags/{year}/" + ("" if page <= 1 else f"page/{page}/")


def _hentasis_year(net, year: int, site: str = "hentasis") -> tuple:
    """(its pages, its first page's text) of a year of Hentasis (``/tags/<год>/``; (v5.44.139) of Hentai-XS —
    ``/age.php?age=<год>``; none — 0)."""
    with _HENTASIS_FACTS_LOCK:
        kept = _HENTASIS_YEARS.get((site, year))
    if kept is not None and time.time() - kept[2] < HENTASIS_YEARS_TTL:
        return kept[0], kept[1]
    try:
        text = _hentasis_text(net, _year_url(site, year), site)
    except S.Missing:
        text = ""
    if site == "hentaixs":
        numbers = [int(x) for x in re.findall(r"age\.php\?age=\d{4}&(?:amp;)?page=(\d+)", text)]
        pages = max(numbers or [1]) if _XS_ITEM.search(text) else 0
    else:
        numbers = [int(x) for x in re.findall(r'href=["\'][^"\']*/tags/\d{4}/page/(\d+)/?["\']', text)]
        pages = max(numbers or [1]) if _HENTASIS_ITEM.search(text) else 0
    with _HENTASIS_FACTS_LOCK:
        _HENTASIS_YEARS[(site, year)] = (pages, text, time.time())
    return pages, text


def _hentasis_years(net, address: str, q: Query, site: str = "hentasis") -> tuple:
    """(v5.44.137) Page ``n`` of several years of Hentasis — (v5.44.139) or of Hentai-XS: its pages of the years one
    after another, the newest year first (the main list is the newest first — its first pages would have none of the
    years of long ago)."""
    found = re.search(r"#years=(\d+)-(\d+)&n=(\d+)", address)
    low, high, left = int(found.group(1)), int(found.group(2)), int(found.group(3))
    items_of = xs_items if site == "hentaixs" else hentasis_items
    for year in range(high, low - 1, -1):
        pages, first = _hentasis_year(net, year, site)
        if left <= pages:
            url = _year_url(site, year, left)
            text = first if left == 1 else _hentasis_text(net, url, site)
            items, _more = items_of(text, left, url, q)
            return items, left < pages or year > low
        left -= pages
    return [], False


def _hentasis_page(net, address: str, q: Query) -> tuple:
    if "#years=" in address:
        return _hentasis_years(net, address, q)
    text = _hentasis_text(net, address)
    if "do=search" in address:                         # (v5.44.137) its search: the genres from each title's page
        number = re.search(r"search_start=(\d+)", address)
        items, more = hentasis_found(text, int(number.group(1)) if number else 1, address)
        return _with_facts(net, items, q, _HENTASIS_ROW), more
    number = re.search(r"/page/(\d+)/?$", address)
    return hentasis_items(text, int(number.group(1)) if number else 1, address, q)


# ── (v5.44.139) Hentai-XS ─────────────────────────────────────────────────────────────────────────────────────────
_XS_ITEM = re.compile(r"<div\b[^>]*\bclass=[\"']mini_anime[\"'][^>]*>")


def _episodes_word(count: int) -> str:
    """«1 серия», «3 серии», «12 серий» ("" — not known)."""
    if count <= 0:
        return ""
    tail = count % 100
    word = ("серия" if count % 10 == 1 and tail != 11 else
            "серии" if 2 <= count % 10 <= 4 and not 12 <= tail <= 14 else "серий")
    return f"{count} {word}"


def xs_items(text: str, page: int = 1, base: str = "", q: "Query | None" = None) -> tuple:
    """(v5.44.139) ([:class:`Item`], whether there is a page more) of a page of Hentai-XS's lists (its main one, a
    year's, its search): ``div.mini_anime`` — the link ``.name_anime a`` «Русское|Romaji» to ``anime.php?id=N``, its
    picture, the lines «Год производства», «Жанр», «Серии», «Цензура» («нет» — the feed's «Без цензуры»).  A title a
    gate closes (its genres, its names — ``gates.title``) — not; ``q`` — a row the query's
    genres, «none of these», years leave out (:func:`_rows_fit`) — not.  Its pages — ``animepage.php?id=N``,
    ``…&page=N``."""
    starts = list(_XS_ITEM.finditer(text))
    out, seen = [], set()
    for n, m in enumerate(starts):
        block = text[m.end():starts[n + 1].start() if n + 1 < len(starts) else m.end() + 30000]
        link = re.search(r"class=[\"']name_anime[\"'][^>]*>\s*<a\b[^>]*\bhref=[\"'][^\"']*?anime\.php\?id=(\d{1,9})[\"'][^>]*>"
                         r"(.*?)</a>", block, re.S)
        if not link or str(int(link.group(1))) in seen:
            continue
        number = str(int(link.group(1)))
        names = [x.strip(" /") for part in _plain(link.group(2)).split("|") for x in part.split(" / ") if x.strip(" /")]
        if not names:
            continue
        lines = {}
        for label, value in re.findall(r"<p>\s*<span\b[^>]*>\s*([^<]{1,60}?)\s*</span>(.*?)</p>", block, re.S):
            lines.setdefault(_plain(label).strip(" :"), _plain(value))
        genres = [g.strip(" .") for g in re.split(r"\s*[,;]\s*|\.\s+", lines.get("Жанр", "")) if g.strip(" .")]
        if re.fullmatch(r"нет\.?", lines.get("Цензура", "").strip(), re.I):
            genres.append("Без цензуры")
        if G.title("hentaixs", names=names, genres=genres):
            continue                                   # a gate closed it
        if q is not None and not _rows_fit(genres, lines.get("Год производства", ""), 0.0, q, _XS_ROW):
            continue
        year = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", lines.get("Год производства", ""))
        picture = re.search(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", block)
        count = re.search(r"\d{1,4}", lines.get("Серии", ""))
        seen.add(number)
        out.append(Item(site="hentaixs", key=number, names=names, url=f"{XS_SITE}/anime.php?id={number}",
                        shown_type="Хентай", year=int(year.group(0)) if year else 0, adult=True,
                        cover=urljoin(XS_SITE + "/", _html.unescape(picture.group(1).strip())) if picture else "",
                        status=_episodes_word(int(count.group(0))) if count else ""))
    pages = [int(x) for x in re.findall(r"(?:animepage\.php\?id=|[?&](?:amp;)?page=)(\d{1,5})", text)]
    return out, any(x > page for x in pages)


def _xs_list(page: int) -> str:
    return XS_SITE + "/" if page <= 1 else f"{XS_SITE}/animepage.php?id={page}"


def _xs_page(net, address: str, q: Query) -> tuple:
    """(v5.44.139) A page of Hentai-XS for «Лента»: its search (a form — sent here), its years, its list — with a
    genre chosen, :data:`XS_WALK` of its pages (its rows alone tell the genres)."""
    if "/search.php#search=" in address:
        words = re.search(r"#search=([^&]*)", address)
        body, ctype = net.fetch(XS_SITE + "/search.php", method="POST", what=LABELS["hentaixs"],
                                data={"search": unquote(words.group(1)) if words else "", "submit_s": ""},
                                headers=dict(_PAGE_HEADERS, Referer=XS_SITE + "/"))
        items, _more = xs_items(S._decoded(body, ctype), 1, XS_SITE + "/search.php", q)
        return items, False                             # every title found — in one answer
    if "#years=" in address:
        return _hentasis_years(net, address, q, "hentaixs")
    walk = re.search(r"#walk=(\d+)&n=(\d+)", address)
    if walk:
        size, n = max(1, int(walk.group(1))), max(1, int(walk.group(2)))
        items, last, seen = [], 0, set()
        for k in range((n - 1) * size + 1, n * size + 1):
            if last and k > last:
                break
            try:
                text = _hentasis_text(net, _xs_list(k), "hentaixs")
            except S.Missing:
                break                                   # past its last page
            got, _more = xs_items(text, k, _xs_list(k), q)
            items += [i for i in got if i.key not in seen]
            seen |= {i.key for i in got}
            last = max([int(x) for x in re.findall(r"animepage\.php\?id=(\d{1,5})", text)] + [k])
        return items, n * size < last
    text = _hentasis_text(net, address, "hentaixs")
    number = re.search(r"(?:animepage\.php\?id=|[?&]page=)(\d+)", address)
    return xs_items(text, int(number.group(1)) if number else 1, address, q)


# ── (v5.44.139) HFShare ───────────────────────────────────────────────────────────────────────────────────────────
_HFSHARE_ITEM = re.compile(r"<div\b[^>]*\bclass=[\"'][^\"']*\bkino-item\b[^\"']*[\"'][^>]*>")


def _hfshare_names(text: str) -> list:
    """Its names of a title («Romaji / Русское»), the Russian first, without the brackets after them («… 01-02
    [720px][Demosaic Model H2Dv1.1][Preview]»)."""
    names = [re.sub(r"(?:\s*\[[^\[\]]*\])+\s*$", "", x).strip() for x in _plain(text).split(" / ")]
    names = [x for x in names if x]
    if names and not S._cyrillic(names[0]) and any(S._cyrillic(x) for x in names[1:]):
        names.sort(key=lambda x: not S._cyrillic(x))     # «Muramata-san no Aijou / Любовь Мураматы-сан»: Russian first
    return names


def hfshare_items(text: str, page: int = 1, base: str = "", q: "Query | None" = None, served: tuple = ()) -> tuple:
    """(v5.44.139) ([:class:`Item`], whether there is a page more) of a page of HFShare's lists: ``div.kino-item`` — the
    link ``a.kino-h`` «Romaji / Русское» to ``<номер>-<slug>.html``, the picture of ``.kino-img``, its genres
    (``.xf-chips[data-xf=janre]`` — a row of another field, its manga's ``manj``, — not a video: not), its people's
    «Нравится» (``data-hr-pct``), the lines «Год выпуска», «Продолжительность» («2эп. 19мин.»).  A title a gate
    closes (``gates.title``) — not; ``q`` — what the query's genres (but ``served`` — the list is of
    them), «none of these», years, rating leave out (:func:`_rows_fit`) — not."""
    base = base or HFSHARE_SITE + "/"
    starts = list(_HFSHARE_ITEM.finditer(text))
    out, seen = [], set()
    for n, m in enumerate(starts):
        block = text[m.end():starts[n + 1].start() if n + 1 < len(starts) else m.end() + 30000]
        link = next((a for a in re.finditer(r"<a\b([^>]*)>(.*?)</a>", block, re.S)
                     if re.search(r'class=["\'][^"\']*\bkino-h\b', a.group(1))), None)
        href = re.search(r'href=["\']([^"\']+)["\']', link.group(1)) if link else None
        if not href:
            continue
        url = urljoin(base, _html.unescape(href.group(1)))
        number = re.search(r"/(\d{1,9})-[^/]*\.html?$", urlparse(url).path)
        if not number or number.group(1) in seen:
            continue
        said = re.search(r'title=["\']([^"\']*)["\']', link.group(1))       # whole (its heading is cut short)
        names = _hfshare_names(_html.unescape(said.group(1)) if said else "") or _hfshare_names(link.group(2))
        fields = dict(re.findall(r'data-xf=["\'](\w+)["\'][^>]*>(.*?)</div>', block, re.S))
        if not names or (fields and "janre" not in fields):
            continue                                   # a row of its manga (``manj``), not a video
        genres = [g.strip(" .") for g in re.split(r"\s*,\s*", _plain(fields.get("janre", ""))) if g.strip(" .")]
        if G.title("hfshare", names=names, genres=genres):
            continue                                   # a gate closed it
        lines = {_plain(label).strip(" :"): _plain(value) for label, value in
                 re.findall(r"<strong>\s*([^<]{1,60}?)\s*</strong>\s*</div>(.*?)</li>", block, re.S)}
        rating, votes = _page_rating("hfshare", block)
        if q is not None and not _rows_fit(genres, lines.get("Год выпуска", ""), rating, q, _HFSHARE_ROW, served):
            continue
        year = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", lines.get("Год выпуска", ""))
        picture = re.search(r'class=["\'][^"\']*\bkino-img\b[^"\']*["\'][^>]*>\s*<img\b([^>]*)>', block)
        src = (re.search(r'\bdata-src=["\']([^"\']+)["\']', picture.group(1))
               or re.search(r'\bsrc=["\']([^"\']+)["\']', picture.group(1))) if picture else None
        count = re.search(r"(\d{1,4})\s*эп", lines.get("Продолжительность", ""))
        seen.add(number.group(1))
        out.append(Item(site="hfshare", key=number.group(1), names=names, url=url, shown_type="Хентай",
                        year=int(year.group(0)) if year else 0, adult=True, rating=rating, votes=votes,
                        cover=urljoin(base, _html.unescape(src.group(1).strip())) if src else "",
                        status=_episodes_word(int(count.group(1))) if count else ""))
    pages = [int(x) for x in re.findall(r'href=["\'][^"\']*/page/(\d{1,5})/?["\']', text)]
    return out, any(x > page for x in pages)


def hfshare_found(text: str, base: str = "") -> list:
    """(v5.44.139) [:class:`Item`] of HFShare's search (DataLife Engine's, as Hentasis's: ``a.sres-wrap`` — its picture,
    its name in ``h2``; every title found in one answer); the rows say no genres (each title's page tells them)."""
    base = base or HFSHARE_SITE + "/"
    out, seen = [], set()
    for attrs, inner in re.findall(r"<a\b([^>]*)>(.*?)</a>", text, re.S):
        if not re.search(r'class=["\'][^"\']*\bsres-wrap\b', attrs):
            continue
        href = re.search(r'href=["\']([^"\']+)["\']', attrs)
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", inner, re.S)
        if not href or not heading:
            continue
        url = urljoin(base, _html.unescape(href.group(1)))
        number = re.search(r"/(\d{1,9})-[^/]*\.html?$", urlparse(url).path)
        names = _hfshare_names(heading.group(1))
        if not number or not names or number.group(1) in seen:
            continue
        if G.title("hfshare", names=names):
            continue                                   # a gate closed it: by its names (its genres — after)
        picture = re.search(r"<img\b([^>]*)>", inner)
        src = (re.search(r'\bdata-src=["\']([^"\']+)["\']', picture.group(1))
               or re.search(r'\bsrc=["\']([^"\']+)["\']', picture.group(1))) if picture else None
        seen.add(number.group(1))
        out.append(Item(site="hfshare", key=number.group(1), names=names, url=url, shown_type="Хентай", adult=True,
                        cover=urljoin(base, _html.unescape(src.group(1).strip())) if src else ""))
    return out


HFSHARE_FOUND_TTL = 10 * 60             # seconds a search of HFShare is kept (its answer has every title found)
_HFSHARE_FOUND: "OrderedDict[str, tuple]" = OrderedDict()   # its address → (the titles, when)


def _hfshare_page(net, address: str, q: Query) -> tuple:
    """(v5.44.139) A page of HFShare for «Лента»: its list (narrowed by the rows), its search — ten titles of it a page,
    each one's genres from its page (:func:`_with_facts`)."""
    if "do=search" in address:
        bare, _hash, rest = address.partition("#n=")
        n = max(1, int(rest)) if rest.isdigit() else 1
        with _HENTASIS_FACTS_LOCK:
            kept = _HFSHARE_FOUND.get(bare)
        if kept is None or time.time() - kept[1] > HFSHARE_FOUND_TTL:
            kept = (hfshare_found(_hentasis_text(net, bare, "hfshare"), bare), time.time())
            with _HENTASIS_FACTS_LOCK:
                _HFSHARE_FOUND[bare] = kept
                while len(_HFSHARE_FOUND) > QUERIES_KEPT:
                    _HFSHARE_FOUND.popitem(last=False)
        found = kept[0]
        shown = [Item(**vars(i)) for i in found[(n - 1) * HFSHARE_FOUND_PAGE:n * HFSHARE_FOUND_PAGE]]
        return _with_facts(net, shown, q, _HFSHARE_ROW), n * HFSHARE_FOUND_PAGE < len(found)
    text = _hentasis_text(net, address, "hfshare")
    number = re.search(r"/page/(\d+)/?$", address)
    served = tuple(name for name in ("Без цензуры", "3D") if name == _hfshare_base(q))
    return hfshare_items(text, int(number.group(1)) if number else 1, address, q, served)


def haven_items(data) -> list:
    """(v5.44.138) [:class:`Item`] of a page of HentaiHaven's catalogue (its WordPress' entries: ``slug``,
    ``title.rendered``, ``meta.vraven_remote_thumbnail`` — the cover on ``img.hentaihaven.xxx``, the numbers of its
    genres, tags and year); a title a gate closes (its genres, tags, name — ``gates.title``) — not."""
    out = []
    for x in data if isinstance(data, list) else []:
        if not isinstance(x, dict):
            continue
        slug = str(x.get("slug") or "")
        name = _html.unescape(str(_dict(x.get("title")).get("rendered") or "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,199}", slug) or not name:
            continue
        if G.title("haven", names=[name], genre_ids=x.get("wp-manga-genre") or [],
                   tag_ids=x.get("wp-manga-tag") or []):
            continue                                       # a gate closed it
        thumb = str(_dict(x.get("meta")).get("vraven_remote_thumbnail") or "").strip()
        year = next((_HAVEN_YEAR_OF[n] for n in x.get("wp-manga-release") or [] if n in _HAVEN_YEAR_OF), 0)
        out.append(Item(site="haven", key=slug, names=[name], url=f"{HAVEN_SITE}/ru/watch/{slug}/", shown_type="Хентай",
                        year=year, cover=urljoin(HAVEN_IMAGES, thumb) if thumb else "", adult=True))
    return out


def _haven_page(net, address: str, q: Query) -> tuple:
    page = re.search(r"[?&]page=(\d+)", address)
    try:
        data = net.json(address, what=LABELS["haven"], headers={"Accept": "application/json",
                                                                 "Referer": HAVEN_SITE + "/"})
    except S.SourceError as exc:
        if page and int(page.group(1)) > 1 and re.search(r"\b400\b", str(exc)):
            return [], False                               # a page past the last: its WordPress says 400
        raise
    return haven_items(data), isinstance(data, list) and len(data) >= HAVEN_PAGE


def haven_said(net, slug: str) -> "dict | None":
    """(v5.44.138) What HentaiHaven's catalogue says of a title by its slug — its name and the numbers of its genres
    and tags (the page «Смотреть» opens shows its genres by name only): ``{names, genre_ids, tag_ids}`` for
    ``gates.title("haven", **…)``; None — not known (no such slug, no answer)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,199}", str(slug or "")):
        return None
    try:
        data = net.json(f"{HAVEN_API}/wp-manga?slug={slug}&_fields={HAVEN_FIELDS}", what=LABELS["haven"],
                        headers={"Accept": "application/json", "Referer": HAVEN_SITE + "/"})
    except S.SourceError as exc:
        _LOG.info("catalog: HentaiHaven did not say of %s: %s", slug, exc)
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    x = data[0]
    name = _html.unescape(str(_dict(x.get("title")).get("rendered") or "")).strip()
    return {"names": [name], "genre_ids": list(x.get("wp-manga-genre") or []),
            "tag_ids": list(x.get("wp-manga-tag") or [])}


def _hentaichan_page(net, address: str, q: Query) -> tuple:
    """(v5.44.93) A page of Хентай-тян's lists or its search (``manhwa_sources.Hentaichan.listing``: a title a gate
    closes is not in it); (v5.44.95) the parts of one work — one tile, named as the work («Укрощение строптивого - часть
    22» six times on its «Обновления» of 6.11.2026; the card of any part is the whole work)."""
    source = S.by_name("hentaichan")
    hits, more = source.listing(net, address)
    return [Item(site="hentaichan", key=h.key, names=names, url=h.url, type="Манга", shown_type="Хентай", cover=h.cover,
                 adult=True) for h, names in works_of(hits)], more


def _nudemoon_page(net, address: str, q: Query) -> tuple:
    """(v5.44.131) A page of Nude-Moon's lists or its search (``manhwa_sources.NudeMoon.listing``: a title the program's
    rule leaves out is not in it); the chapters of one work — one tile, named as the work (as Хентай-тян's parts).
    (v5.44.135) The genres and tags of the query — by the tags its rows carry: all the wanted ones, none of the others
    (a page of its tags has them already; its search and its lists — not)."""
    source = S.by_name("nudemoon")
    wanted, left, _why = _wanted(q, "nudemoon")
    hits, more = source.listing(net, address, need=[word for _kind, word in wanted],
                                unwanted=[word for _kind, word in left])
    return [Item(site="nudemoon", key=h.key, names=names, url=h.url, type="Манга", shown_type="Хентай", cover=h.cover,
                 adult=True) for h, names in works_of(hits)], more


def _allhentai_items(data, q: Query) -> tuple:
    """(v5.44.99) ([:class:`Item`], whether there is a page more) of AllHentai's catalog — by a name too (v5.44.155:
    ``q=`` of the same API); a title a gate closes (its names; its genres and tags — their names and their numbers at
    the site) — not."""
    source = S.by_name("allhentai")
    out = []
    rows = (data.get("list") if isinstance(data, dict) else None) or []
    for x in rows:
        element = x.get("elementId") if isinstance(x, dict) and isinstance(x.get("elementId"), dict) else {}
        link = str(element.get("linkName") or "").strip("/")
        if not link or not re.fullmatch(r"[\w.-]{1,200}", link):
            continue
        category = x.get("category") if isinstance(x.get("category"), dict) else {}
        kind = str(category.get("name") or "")
        names = [str(x.get("name") or link).strip()]
        if G.title("allhentai", names=names, **S.AllHentai.said(x)):
            continue                                  # a gate closed it
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
    """(v5.44.97) ([:class:`Item`], whether there is a page more) of a page of nhentai's list or search: a gallery a
    gate closes — not (its tags by their numbers, their names asked of the site once — ``net``; its titles); the name — the title without its brackets (the whole one — the second name), the cover — its thumbnail,
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
        if G.title("nhentai", names=titles, tags=[names.get(t, "") for t in tags], tag_ids=tags):
            continue                                  # a gate closed it
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
        _SUGGESTED.clear()
    with _HENTASIS_FACTS_LOCK:                        # (v5.44.139) the pages of years, titles' facts, searches kept
        _HENTASIS_FACTS.clear()
        _HENTASIS_YEARS.clear()
        _HFSHARE_FOUND.clear()


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


# ── a site's own tags for the words typed ─────────────────────────────────────────────────────────────────────────
_SUGGESTED: "OrderedDict[tuple, tuple]" = OrderedDict()     # (site, the words) → (the rows, when)


def suggest(kind: str, mode: str, words: str, *, net=None) -> list:
    """(v5.44.148) The sites' own tags whose names begin with the words typed — what «Найти тег» offers besides the
    feed's genres and tags: [{value «site:kind:name», name, kind, word («автор»), site (its label), count, one — one
    of its kind at a time (:func:`extra_single`), exact — its name is the words typed}] (:func:`suggested` without
    the sites that did not answer)."""
    return suggested(kind, mode, words, net=net)[0]


def suggested(kind: str, mode: str, words: str, *, net=None) -> "tuple[list, list]":
    """(the rows of :func:`suggest`, the labels of the sites that did not answer).  nhentai's own tag search
    (``POST /api/v2/tags/search``, every kind at once: tags, artists, characters, parodies, groups); (v5.44.149) the
    people of the Lib API (``/api/people?q=``: the authors of MangaLib and HentaiLib, ``one`` — one at a time) —
    both asked at once.  A tag the feed has under a name of its own (``catalog_names``: «tentacles» — «Тентакли») is
    not repeated; one a gate closes (:func:`extra_closed`) is not offered.  A site's rows are kept
    :data:`SUGGEST_TTL`; a site that did not answer — nothing of it (it is asked again the next time).  The rows —
    the sites in turn (each one's most used first), a name that is the words typed — before the others."""
    words = " ".join(str(words or "").replace('"', " ").split()).lower()[:60]
    if kind == "video" or len(words) < SUGGEST_MIN:
        return [], []
    shown = sites_shown(kind, mode)
    asked = []                                        # (the key its rows are kept by, its label, who asks)
    if kind == "manga" and "nhentai" in shown:
        asked.append((("nhentai", words), LABELS["nhentai"], _nhentai_suggested))
    people = tuple(s for s in EXTRA_SITES["lib"] if s in shown and S.by_name(s) is not None)
    if people:
        asked.append((("lib", words, people), " и ".join(LABELS[s] for s in people),
                      lambda words, net, people=people: _lib_people(words, people, net)))
    now = time.time()
    got, going = {}, []
    net = net or _net()
    for key, _label, ask in asked:
        with _LOCK:
            kept = _SUGGESTED.get(key)
            if kept and now - kept[1] < SUGGEST_TTL:
                _SUGGESTED.move_to_end(key)
                got[key] = [dict(row) for row in kept[0]]
                continue

        def run(key=key, ask=ask):
            try:
                rows = ask(words, net)
            except S.SourceError as exc:
                _LOG.info("catalog: %s's tags for %r did not come: %s", key[0], words, exc)
                return
            with _LOCK:
                _SUGGESTED[key] = (rows, time.time())
                while len(_SUGGESTED) > SUGGEST_KEPT:
                    _SUGGESTED.popitem(last=False)
            got[key] = [dict(row) for row in rows]
        thread = threading.Thread(target=run, name="catalog-suggest", daemon=True)
        thread.start()
        going.append(thread)
    for thread in going:
        thread.join()
    lists = [got[key] for key, _label, _ask in asked if key in got]
    rows = [row for turn in zip_longest(*lists) for row in turn if row is not None]
    rows.sort(key=lambda row: not row["exact"])       # stable: a name that is the words typed — first
    return rows, [label for key, label, _ask in asked if key not in got]


def _nhentai_suggested(words: str, net) -> list:
    # (v5.44.150) two tries: the first may be the straight way that does not get through — the net turns to the
    # system's proxy and has nothing left to ask with (seen 11.11.2026: «HentaiLib: нет ответа» once in seven asks)
    data = net.json(
        N.API + "/tags/search", method="POST", what=LABELS["nhentai"], tries=2, paced=False, timeout=(4, 8),
        headers={"Accept": "application/json", "Content-Type": "application/json", "Referer": N.SITE + "/"},
        data=json.dumps({"type": None, "query": words, "limit": SUGGEST_SHOWN}))
    mine = {S.name_key(str(name)) for name in (*NAMES.NHENTAI_TAGS.values(), *NAMES.NHENTAI_GENRES.values())}
    rows = []
    for tag in data if isinstance(data, list) else []:
        if not isinstance(tag, dict):
            continue
        what = str(tag.get("type") or "")
        value = extra_of(f"nhentai:{what}:{tag.get('name') or ''}")
        if not value:
            continue
        name = extra_parts(value)[2]
        if what == "tag" and S.name_key(name) in mine or extra_closed("nhentai", name):
            continue
        rows.append({"value": value, "name": name, "kind": what, "word": EXTRA_WORDS[what], "site": LABELS["nhentai"],
                     "count": S._int(tag.get("count")), "one": False, "exact": S.name_key(name) == S.name_key(words)})
    rows.sort(key=lambda row: -row["count"])
    return rows


PEOPLE_PAGES = 2                    # pages of the Lib API's people asked for the words typed (fifty people a page)


def _words_set(text) -> frozenset:
    """The words of a name, whatever their order («Inagaki Riichiro» is «Riichiro Inagaki»)."""
    return frozenset(key for key in (S.name_key(part) for part in re.split(r"[\s,./]+", str(text or ""))) if key)


def _lib_people(words: str, sites: tuple, net) -> list:
    """(v5.44.149) The people of the Lib API whose names hold the words (one base for its sites; asked 11.11.2026:
    ``/api/people?q=boichi`` — {id 229, name «Boichi», rus_name, alt_name, titles_count_details {"1": 24, "4": 9, …}};
    fifty a page, ``sort_by=titles_count`` — the ones with the most titles first, of every site of it: the anime's
    too; it has no filter by a site) — those with titles at the sites shown: «автор Boichi · MangaLib + HentaiLib ·
    33».  A full page with few of them — the next one too (:data:`PEOPLE_PAGES`).  Words that found nobody and are a
    name of several words — asked by its longest word (the other order of a name: «Inagaki Riichiro»)."""
    source = S.by_name(sites[0])
    wanted = _words_set(words)

    def ask(text: str) -> list:
        rows = []
        for page in range(1, PEOPLE_PAGES + 1):
            data = net.json(f"{source.api()}/api/people?q={quote(text)}&site_id[]={source.SITE_ID}"
                            f"&sort_by=titles_count&sort_type=desc&page={page}", what=source.label,
                            headers=_headers(sites[0]), tries=2, paced=False, timeout=(4, 8))
            people = [x for x in (data.get("data") if isinstance(data, dict) else None) or [] if isinstance(x, dict)]
            for person in people:
                row = _person_row(person, sites, wanted)
                if row is not None and row["value"] not in {r["value"] for r in rows}:
                    rows.append(row)
            if len(people) < 50 or len(rows) >= SUGGEST_SHOWN:
                break
        return rows

    rows = ask(words)
    longest = max(re.split(r"[\s,./]+", words), key=len, default="")
    if not rows and len(wanted) > 1 and len(longest) >= 3:
        rows = [row for row in ask(longest) if row["exact"]]
    rows.sort(key=lambda row: -row["count"])
    return rows[:SUGGEST_SHOWN]


def _person_row(person: dict, sites: tuple, wanted: frozenset) -> "dict | None":
    """A person of the Lib API as a row of :func:`suggest`; None — no titles at the sites shown (ranobe, anime alone),
    or a gate closes a name of theirs."""
    number = S._int(person.get("id"))
    counts = person.get("titles_count_details") if isinstance(person.get("titles_count_details"), dict) else {}
    where = [(site, S._int(counts.get(str(S.by_name(site).SITE_ID)))) for site in sites]
    where = [(site, count) for site, count in where if count > 0]
    if number <= 0 or not where:
        return None
    said = [" ".join(str(person.get(key) or "").split()) for key in ("name", "rus_name", "alt_name")]
    name, local = said[0], said[1]
    shown = f"{name} ({local})" if name and local and S.name_key(name) != S.name_key(local) else name or local
    value = extra_of(f"lib:people:{number} {shown}")
    if not value or not shown or any(extra_closed("lib", one) for one in said if one):
        return None
    return {"value": value, "name": extra_shown(value), "kind": "people", "word": EXTRA_WORDS["people"],
            "site": " + ".join(LABELS[site] for site, _count in where),
            "count": sum(count for _site, count in where), "one": True,
            "exact": bool(wanted) and wanted in {_words_set(one) for one in said if one}}


_PARSE = {"remanga": _remanga_items, "mangalib": _mangalib_items, "yummy": _yummy_items,
          "hentailib": lambda data, q: _mangalib_items(data, q, "hentailib"), "allhentai": _allhentai_items}
# (v5.44.93) sites of pages, not of API; (v5.44.97) nhentai — its API asked twice a page (the tags' names)
_PAGES_OF = {"hentaichan": _hentaichan_page, "hentasis": _hentasis_page, "nhentai": _nhentai_page,
             "nudemoon": _nudemoon_page,                    # (v5.44.131)
             "haven": _haven_page,                          # (v5.44.138)
             "hentaixs": _xs_page, "hfshare": _hfshare_page}  # (v5.44.139)


# (v5.44.137) sites whose pages the query's own filters narrow here, by their rows (Hentasis: the second genre, «none
# of these», years, a rating; Nude-Moon: «none of these», its search with tags): one address, different queries —
# a page of each kept apart (before, the first query's page served the next: «none of these» lost, or added)
LOCAL_FILTERS = ("hentasis", "nudemoon", "hentaixs", "hfshare")     # (v5.44.139) Hentai-XS, HFShare


def _page_key(site: str, address: str, q: Query) -> str:
    """The key a site's page is kept by: its address — and the query's filters, for a site that filters here."""
    if site not in LOCAL_FILTERS:
        return address
    return address + "#" + repr((q.genres, q.without, q.tags, q.untags, q.years, q.rating))


def _ask(site: str, address: str, key: str, q: Query, net, going) -> None:
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
        _PAGES[key] = got
        _PAGES.move_to_end(key)
        while len(_PAGES) > PAGES_KEPT:
            _PAGES.popitem(last=False)
        _GOING.pop(key, None)
    going.set()


def _site_page(site: str, address: str, q: Query, net, again: bool) -> threading.Event:
    """The event set when the site's page is here (kept, or asked now in a thread of its own)."""
    key = _page_key(site, address, q)
    with _LOCK:
        kept = _PAGES.get(key)
        if kept is not None and not (again and kept.error) and \
                time.time() - kept.at < (FAILED_TTL if kept.error else TTL):
            _PAGES.move_to_end(key)
            done = threading.Event()
            done.set()
            return done
        going = _GOING.get(key)
        if going is None:
            going = _GOING[key] = threading.Event()
            threading.Thread(target=_ask, args=(site, address, key, q, net, going), daemon=True,
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
            got = _PAGES.get(_page_key(site, address, q))
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
    """(v5.44.99) AllHentai's kinds: here — the tab's (of the chosen types); (v5.44.155) its catalog is asked without
    the other tabs' kinds already (:func:`allhentai_url`) — what is left for this is a kind unknown here, one the site
    made since: «Манга» (most of its titles); no kind named — any tab."""
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
        "adult": any(i.adult for i in items),
        "cover": _chosen_cover([(i.site, i.key) for i in items]) or (cover_id(covered.cover, covered.url) if covered
                                                                    else ""),
        "sites": [LABELS.get(i.site, i.site) for i in items], "keys": t["keys"],
        "ids": {(i.site, i.key) for i in items},
    }


def _chosen_cover(ids: list) -> str:
    """(v5.44.123) The cover the owner chose for the title (the menu of covers: ``core/title_covers``) — its number for
    ``/api/catalog/cover``; "" — none chosen."""
    from core import title_covers
    entry = title_covers.chosen([f"{site}:{key}" for site, key in ids])
    return title_covers.catalog_id(entry) if entry is not None else ""


def hit_tile(hit, label: str) -> dict:
    """A tile of a title a site's own feed gave (``manhwa_feed``: one site, no filters)."""
    return {"title": (hit.names or [hit.key])[0], "alt": hit.names[1] if len(hit.names) > 1 else "",
            "type": hit.type, "year": hit.year, "url": hit.url, "rating": round(hit.rating or 0.0, 1), "status": "",
            "adult": bool(hit.adult), "cover": _chosen_cover([(hit.site, hit.key)]) or cover_id(hit.cover, hit.url),
            "sites": [label]}


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
    from core import manhwa_search as MS, title_covers
    if kept[0].startswith(title_covers.OWN_MARK):     # (v5.44.123) a picture of the owner's own, chosen for a title
        return title_covers.own_picture(kept[0], shrink=COVER_WIDTH)
    return MS.fetch_cover(kept[0], kept[1], net=net, shrink=COVER_WIDTH)
