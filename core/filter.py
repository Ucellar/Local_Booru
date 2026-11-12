"""Правило программы о несовершеннолетних — всё целиком в этом файле.

Хентай, в котором изображены несовершеннолетние, программа не показывает, не открывает и не скачивает — в любом
режиме («SFW / Всё / NSFW»), на любом сайте, без выключателя.  Правило — о хентае: тайтл, который сайт не помечает
как 18+, оно не закрывает никогда.

Всё, что относится к правилу, лежит здесь и только здесь: каждое слово, каждый номер тега или жанра сайта, каждое
решение и каждая фраза, которую программа говорит, когда что-то закрыто.  Остальная программа этот файл не называет
и о правиле ничего не знает: она задаёт общие вопросы (``core/gates.py`` — «можно ли показать этот тайтл», «что
убрать из списков сайта»…) и исполняет ответ.  Ответы на эти вопросы этот файл подключает сам (раздел 9), его
проверки — тоже здесь (раздел 10).

Слова и числа правила владелец разрешил добавлять; убирать — нет.

ОПИСЬ — что здесь лежит и за что отвечает

1. Фразы программы — что она говорит, когда правило что-то закрыло.
   * ``EXCLUDED`` — тайтл сайта («Скачать», страница тайтла, «Читать», «Смотреть», «Скачать с сайта»).
   * ``GALLERY_EXCLUDED`` — галерея nhentai.
   * ``COMIC_EXCLUDED`` — сборка-комикс галереи, которую не переносят в «Мангу».
   * ``PAGES_REFUSED`` — папка парсера, которую не берут в «Мангу» (журнал и блок «Манга в исходной папке»).
   * ``POSTS_WHY`` — посты избранного аккаунта, которые не скачиваются (строка журнала «не скачиваю постов — N: …»).
   * ``GENRE_REFUSED`` — жанр, выбранный в «Ленте», который у сайта не спрашивается.
   * ``RULE_NAME`` — короткое название правила там, где фраза не помещается (колонка «почему», счётчик).

2. Слова, по которым тайтл считается изображающим несовершеннолетних — в его названиях, жанрах и тегах
   (``depicts_minors``).
   * ``MINOR_WORDS`` — целые слова.
   * ``MINOR_STEMS`` — начала слов («школ…», «school…», «loli…»).
   * ``MINOR_PARTS`` — куски внутри японского текста (в нём нет пробелов между словами).

3. Теги и жанры сайтов такого рода — по их номерам на каждом сайте.
   * ``REMANGA_NEVER_TAGS``, ``MANGALIB_NEVER_TAGS`` — тег «Лоли» у Remanga и MangaLib.
   * ``HENTAILIB_NEVER_TAGS``, ``HENTAILIB_NEVER_GENRES`` — теги и жанры HentaiLib.
   * ``HAVEN_NEVER_GENRES``, ``HAVEN_NEVER_TAGS`` — жанры и теги HentaiHaven.
   * ``NHENTAI_NEVER_TAGS`` (названия) и ``NHENTAI_NEVER_IDS`` (номера) — теги nhentai.
   * ``ALLHENTAI_NEVER_TAGS`` — теги AllHentai (номера его каталога): два его собственных слова из раздела 4 и три
     тега, которые ловят слова раздела 2.
   * ``NEVER_TAG_IDS``, ``NEVER_GENRE_IDS`` — всё это по сайтам.
   * ``never_tag_ids``, ``never_genres``, ``never_tag_names`` — что списки сайта всегда просят убрать («Лента»
     никогда не предлагает эти теги и жанры в фильтрах и в каждом запросе просит сайт их исключить; поиск nhentai
     всегда идёт с «-tag:"…"» для каждого из названий).
   * ``list_exclusions`` — то же для собственных поиска и ленты сайта в «Скачать» (HentaiLib, AllHentai).
   * ``genre_refused`` — фраза, когда такой жанр выбрали в «Ленте».

4. Собственные слова сайтов, которых общие слова не ловят.
   * ``XS_NEVER_WORDS`` — Hentai-XS, ``HFSHARE_NEVER_WORDS`` — HFShare, ``ALLHENTAI_NEVER_WORDS`` — AllHentai (так
     сайт сам называет свои теги «лоли» и «сёта»); ``NEVER_WORDS`` — они по сайтам.
   * ``never_word`` — является ли название жанра одним из таких слов.

5. ГЛАВНАЯ ПРОВЕРКА ТАЙТЛА — ``title_forbidden``.  Тайтл закрыт, если сайт помечает его как 18+ и при этом: его жанр
   или тег — один из номеров раздела 3; или его названия, жанры, теги содержат слова раздела 2; или среди них есть
   собственное слово сайта из раздела 4.  Действует везде, где программа показывает, открывает или скачивает тайтл:
   плитки «Ленты» и поиска, страница тайтла, «Скачать», «Читать», ночная проверка новых глав, копия главы с другого
   сайта, «Смотреть», «Скачать с сайта», галереи nhentai.

6. Плитка жанра или тега на странице тайтла — ``chip_never``: слово правила не становится ссылкой-поиском.

7. Страницы booru-сайтов, которые берутся в «Мангу» (комиксы галереи, манга из папки парсера).
   * ``SITE_MINOR_TAGS`` — собственные теги booru-сайтов такого рода.
   * ``SAFE_RATINGS`` — оценки сайтов, означающие «безопасно».
   * ``tags_speak_of_minors``, ``page_forbidden``, ``pages_forbidden`` — страница, теги которой говорят о
     несовершеннолетних и которую ни один сайт не назвал безопасной, в «Мангу» не берётся; с ней — вся сборка.

8. Посты избранного аккаунта на booru-сайтах («Скачать избранное»).
   * ``SAFE_BY_SITE`` — какое слово сайта значит «безопасно» (у Danbooru, ATF и Gelbooru — только «general»).
   * ``post_forbidden`` — пост, теги которого говорят о несовершеннолетних и который его сайт не назвал безопасным,
     не скачивается.

9. Подключение к программе — ответы на её вопросы (``_title``, ``_pages``, ``_post``, ``_chip``, ``_left_out``,
   ``_genre``, ``_name``).  Строка ``__keeper__ = True`` говорит программе, что этот файл отвечает на её вопросы;
   ``__own__`` — слова, которые может содержать только этот файл (проверка проекта падает, если они есть где-то ещё
   или если другой файл называет этот).

10. Проверка самого себя — ``self_check``: каждое решение правила на его же данных.
"""
from __future__ import annotations

import html as _html
import re
import unicodedata
from urllib.parse import unquote

from core import gates

__keeper__ = True
# what only this file holds (regular expressions, case ignored) — no other file of the project has them
__own__ = (r"несовершеннолет", r"малолет", r"\bminors\b", r"depicts?[ _]minors?", r"speak[ _]of[ _]minors?",
           r"\bminor[ _](words?|stems?|parts?|tags?)\b", r"\bloli(?!booru)", r"\bлоли", r"\bshota", r"\bшота", r"\bс[её]та",
           r"\blolcon\b", r"ロリ", r"ショタ", r"\bдевчул", r"\bпарниш")


# ── 1. the program's words when the rule leaves something out ────────────────────────────────────────────────────
EXCLUDED = "Этот тайтл программа не открывает и не скачивает: в нём изображены несовершеннолетние"
GALLERY_EXCLUDED = "Эту галерею программа не показывает и не скачивает: в ней изображены несовершеннолетние"
COMIC_EXCLUDED = "Эту сборку программа в мангу не переносит: на её страницах изображены несовершеннолетние"
PAGES_REFUSED = "e621: на страницах — несовершеннолетние, не «safe»: в «Мангу» не беру"
POSTS_WHY = "на них несовершеннолетние, сайт не назвал их безопасными"
GENRE_REFUSED = "хентай с жанром «{name}» программа не показывает"
RULE_NAME = "правило о несовершеннолетних"

# ── 2. the words that say a title depicts minors — in its names, genres, tags ────────────────────────────────────
MINOR_WORDS = frozenset({"loli", "lolicon", "lolikon", "lolcon", "лоли", "лоликон", "лолькон", "shota", "shotacon",
                         "shotakon", "сёта", "сета", "сётакон", "сетакон", "шота", "шотакон", "schoolgirl",
                         "schoolgirls", "schoolboy", "schoolboys", "малолетка", "малолетки", "девочка", "девочки",
                         "девочек", "дети", "ребенок", "ребёнок",
                         # HentaiLib's tag «Юные» and genre «Кодомо» (children's manga)
                         "юные", "юная", "юный", "юных", "кодомо", "kodomo",
                         # nhentai's English tags and titles: «kindergarten uniform», «randoseru», «JK»…
                         "kindergarten", "randoseru", "jk", "jc", "js"})
# the beginnings of such words: «lolibaba», «oppai loli», «school swimsuit», «school gym uniform» (nhentai's tags — as
# HentaiLib's genre «Школа» is left out), «kodomokai»
MINOR_STEMS = ("школ", "учени", "loli", "shota", "school", "kodomo")
# …and of Japanese titles, which have no spaces between words: inside the text
MINOR_PARTS = ("ロリ", "ショタ", "幼女", "幼児", "園児", "小学生", "中学生", "高校生", "女子高生", "女子校生", "ランドセル",
               "子供", "こども", "児童")


def depicts_minors(tags) -> bool:
    """Whether names, genres or tags (any of them) say that a title depicts minors: one of :data:`MINOR_WORDS`, a
    word beginning as :data:`MINOR_STEMS`, a part of :data:`MINOR_PARTS` inside."""
    for tag in tags or ():
        text = unicodedata.normalize("NFKC", _html.unescape(unquote(str(tag or "")))).casefold()
        if any(part in text for part in MINOR_PARTS):
            return True
        for word in re.split(r"[\W_]+", text):
            if word and (word in MINOR_WORDS or word.startswith(MINOR_STEMS)):
                return True
    return False


# ── 3. the sites' tags and genres of that kind, by their numbers at each site ────────────────────────────────────
REMANGA_NEVER_TAGS = (108,)                       # «Лоли»
MANGALIB_NEVER_TAGS = (187,)                      # «Лоли»
# HentaiLib (site 4 of the Lib sites): «Лоликон», «Сетакон», «Юные», «Девочки-волшебницы» (its own), «Лоли» (MangaLib's:
# its titles carry those numbers too, 6.11.2026); …and the genres «Школа», «Кодомо»
HENTAILIB_NEVER_TAGS = (372, 373, 145, 50, 187)
HENTAILIB_NEVER_GENRES = (70, 76)
# HentaiHaven (the numbers of its WordPress, asked from the owner's computer 9.11.2026): its genres «Хентай-школа»,
# «Школа», «Школьница», «Подростковое хентай», «Молодое Хентай» — and its tags of school, of teens and the young,
# shota, little girls and boys, «under-hentai»
# (three quarters of its titles carry them: 270 of 1056 were left, 9.11.2026; its tag «under-hentai» — 330 titles — is
# on stories of adults too, the office romance «Muramata-san no Aijou»: taking it out of the rule — at the owner's word
# alone)
HAVEN_NEVER_GENRES = (815, 11714, 11732, 66, 67)
HAVEN_NEVER_TAGS = (822, 2758, 83, 74, 2753, 146, 79, 108, 71, 2639, 72, 921, 922, 152, 2671)
# nhentai: its tags of that kind (their names on the site — left out of every search by name; their numbers,
# 6.11.2026 — a gallery with one is never shown)
NHENTAI_NEVER_TAGS = ("lolicon", "shotacon", "oppai loli", "low lolicon", "low shotacon", "lolibaba", "kodomo doushi",
                      "kodomo only", "schoolgirl uniform", "schoolboy uniform", "school swimsuit", "school gym uniform")
NHENTAI_NEVER_IDS = frozenset({19440, 32341, 25663, 14362, 27598, 166714, 152252, 152505, 10314, 7752, 29565, 132870})
# AllHentai (the numbers of its catalog's elements — ``/api/catalog/elementsByType?type=40``, asked from the owner's
# computer 11.11.2026: 188 tags).  The first two are its own names for «лоли» and «сёта» — «девчуля» (6205) and
# «парниша» (6204), words nothing above catches (:data:`ALLHENTAI_NEVER_WORDS`): the site keeps the plain «девушки» and
# «парни» beside them; of the 1603 titles of the first 77 % carry its «маленькая грудь» (6.8 % of all its titles do) and
# 0.3 % its «милфа» (3.6 % of all); the 453 of the second go with «милфа» four and a half times oftener than its
# titles do.  The other three are its tags the words of section 2 catch by their names (two of the school — 6015,
# 6048 — and its «девочки-…», 292): by the number its lists are asked without them, and a title is closed whatever
# the site calls its tag tomorrow.
ALLHENTAI_NEVER_TAGS = (6205, 6204, 292, 6015, 6048)
NEVER_TAG_IDS = {"remanga": REMANGA_NEVER_TAGS, "mangalib": MANGALIB_NEVER_TAGS, "hentailib": HENTAILIB_NEVER_TAGS,
                 "haven": HAVEN_NEVER_TAGS, "nhentai": tuple(sorted(NHENTAI_NEVER_IDS)),
                 "allhentai": ALLHENTAI_NEVER_TAGS}
NEVER_GENRE_IDS = {"hentailib": HENTAILIB_NEVER_GENRES, "haven": HAVEN_NEVER_GENRES}


def never_tag_ids(site: str) -> tuple:
    """The numbers of a site's tags of that kind: «Лента» asks them left out of every list of the site."""
    return tuple(NEVER_TAG_IDS.get(site, ()))


def never_genres(site: str, mode: str) -> tuple:
    """The genres a site's list leaves out always in ``mode``: HentaiLib's, HentaiHaven's; MangaLib in «NSFW» — its
    «Эротика» alone — HentaiLib's (the Lib sites number their genres alike): hentai «Школа» or «Кодомо»."""
    if site == "mangalib" and mode == "nsfw":
        return NEVER_GENRE_IDS["hentailib"]
    return tuple(NEVER_GENRE_IDS.get(site, ()))


def genre_refused(site: str, mode: str, number, name: str) -> str:
    """«Лента»: the words why a genre chosen (``name``; its ``number`` at ``site``) is not asked of the site in
    ``mode`` — one of its genres of that kind; "" — it may be asked."""
    return GENRE_REFUSED.format(name=name) if number in never_genres(site, mode) else ""


def list_exclusions(site: str) -> tuple:
    """(tags' numbers, genres' numbers) the site's own lists in «Скачать» (its search and its feed) ask left out:
    HentaiLib's and AllHentai's (sites of hentai alone); another site's — none (a title of that kind is not opened —
    :func:`title_forbidden`)."""
    if site in ("hentailib", "allhentai"):
        return never_tag_ids(site), never_genres(site, "nsfw")
    return (), ()


def never_tag_names(site: str) -> tuple:
    """The names of a site's tags of that kind, for a search that leaves tags out by name: nhentai's."""
    return NHENTAI_NEVER_TAGS if site == "nhentai" else ()


# ── 4. Hentai-XS, HFShare, AllHentai: their own words of that kind the words above do not catch ──────────────────
# (read from the owner's computer 9.11.2026 — every title of Hentai-XS, 828, and the 250 genres of HFShare with the
# titles of each): a lyceum (a school of Russia), teens, youth, the school's uniform and its gym bloomers, flat chests
# («DFC»), boys («Small pipi», «oneshota»), magical girls (as HentaiLib's «Девочки-волшебницы»).  Whole words or
# phrases of a genre or of a name; a title with one is neither shown nor opened.
XS_NEVER_WORDS = ("лицей", "лицея", "лицее", "лицеистка", "лицеистки", "лицеисты", "юность", "teen", "teens", "dfc",
                  "magical girl", "student uniform")
HFSHARE_NEVER_WORDS = ("dfc", "small pipi", "bloomers", "student uniform", "magical girl", "mahou shoujo", "oneshota")
# AllHentai's own names of its two tags of that kind (:data:`ALLHENTAI_NEVER_TAGS` — how they were told), in the forms
# a title's page or name may write them: where the site gives a tag's name and not its number (a title's page, its
# search) the name closes the title
ALLHENTAI_NEVER_WORDS = ("девчуля", "девчули", "девчулю", "девчуль", "парниша", "парниши", "парнишу", "парниш")
NEVER_WORDS = {"hentaixs": XS_NEVER_WORDS, "hfshare": HFSHARE_NEVER_WORDS, "allhentai": ALLHENTAI_NEVER_WORDS}


def _phrases(text) -> str:
    """A text as its words, one case, spaces around (a phrase is looked for whole: « small pipi »)."""
    words = re.split(r"[\W_]+", unicodedata.normalize("NFKC", _html.unescape(unquote(str(text or "")))).casefold())
    return " " + " ".join(w for w in words if w) + " "


def never_word(text) -> bool:
    """Whether a genre's name is one of the sites' own words of that kind (of any site: a chip of a title's page)."""
    phrases = _phrases(text)
    return any(f" {w} " in phrases for words in NEVER_WORDS.values() for w in words)


# ── 5. THE check of a title ───────────────────────────────────────────────────────────────────────────────────────
def _number(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def title_forbidden(site: str, *, names=(), genres=(), tags=(), genre_ids=(), tag_ids=(), adult: bool = True) -> bool:
    """Whether the rule leaves a title of a site out — it is not shown, not opened, not downloaded.

    ``site`` — the site's name in the program; what the site says of the title: ``names`` (its name, its other names,
    the works it is of), ``genres``, ``tags`` (their names), ``genre_ids``, ``tag_ids`` (their numbers at the site);
    ``adult`` — whether it is hentai (every title of a site of hentai alone; another site's — by its own mark): the
    rule is about hentai — a title that is not is never left out by it.  Left out:

    1. a genre or tag that is one of the site's of that kind by its number (:func:`never_genres` — as in «NSFW»,
       :func:`never_tag_ids`);
    2. names, genres or tags that say it depicts minors (:func:`depicts_minors`);
    3. one of the site's own words of that kind among them (:data:`NEVER_WORDS`: Hentai-XS, HFShare, AllHentai)."""
    if not adult:
        return False
    if {_number(n) for n in genre_ids or ()} & set(never_genres(site, "nsfw")) or \
            {_number(n) for n in tag_ids or ()} & set(never_tag_ids(site)):
        return True
    words = [str(x) for x in (*(names or ()), *(genres or ()), *(tags or ())) if x]
    if depicts_minors(words):
        return True
    own = NEVER_WORDS.get(site, ())
    if own:
        phrases = [_phrases(text) for text in words]
        if any(f" {w} " in p for p in phrases for w in own):
            return True
    return False


# ── 6. a genre or tag on a title's page ───────────────────────────────────────────────────────────────────────────
def chip_never(name: str) -> bool:
    """Whether a genre or tag on a title's page is a word of the rule (:func:`depicts_minors`; any site's own words —
    :func:`never_word`): the page makes no search of it."""
    return depicts_minors([name]) or never_word(name)


# ── 7. the pages of booru sites taken into «Манга» ────────────────────────────────────────────────────────────────
SITE_MINOR_TAGS = frozenset({"young", "cub", "child", "toddler", "infant", "preteen", "loli", "shota"})  # e621's own
SAFE_RATINGS = ("s", "g")       # the sites' ratings of a safe picture (e621 «s», Danbooru «g» and «s»)


def tags_speak_of_minors(names) -> bool:
    """A picture's tags speak of minors: :func:`depicts_minors`, or a booru site's own tag of that kind
    (:data:`SITE_MINOR_TAGS`)."""
    names = [str(n) for n in names or () if n]
    return depicts_minors(names) or any(n.strip().lower() in SITE_MINOR_TAGS for n in names)


def page_forbidden(names, ratings) -> bool:
    """A page never taken into «Манга»: its tags speak of minors and the sites did not rate it safe — ``ratings``:
    what they said of it (:data:`SAFE_RATINGS`; none said — not rated, not safe)."""
    if not tags_speak_of_minors(names):
        return False
    said = {str(r or "") for r in ratings or ()} or {""}
    return not said <= set(SAFE_RATINGS)


def pages_forbidden(pages) -> bool:
    """Whether pages of booru sites (a comic of the gallery, the manga of the parser's folder) are never taken into
    «Манга»: one of them is (:func:`page_forbidden`); ``pages`` — (its tags' names, what the sites rated it) of each."""
    return any(page_forbidden(names, ratings) for names, ratings in pages)


# ── 8. a post of an account's favorites on a booru site ───────────────────────────────────────────────────────────
# Each site's own word of «safe»: Danbooru's, ATF's and Gelbooru's «general» (their «sensitive» — «s» — is suggestive,
# not safe); e621's and rule34.xxx's «safe» (:data:`SAFE_RATINGS` for the others).
SAFE_BY_SITE = {"danbooru.donmai.us": ("g",), "booru.allthefallen.moe": ("g",), "gelbooru.com": ("g",)}


def post_forbidden(site: str, tags, rating) -> bool:
    """Whether a post of a booru site is never downloaded from an account's favorites: its tags speak of minors and
    the site did not rate it safe — ``rating`` as the site gives it («explicit», «e», «general»…; none — not safe)."""
    if not tags_speak_of_minors(tags):
        return False
    return str(rating or "").strip().lower()[:1] not in SAFE_BY_SITE.get(str(site or "").lower(), SAFE_RATINGS)


# ── 9. the answers to the program's questions (``core/gates.py``) ─────────────────────────────────────────────────
@gates.keeper("title")
def _title(site, *, names, genres, tags, genre_ids, tag_ids, adult, what) -> str:
    if not title_forbidden(site, names=names, genres=genres, tags=tags, genre_ids=genre_ids, tag_ids=tag_ids,
                           adult=adult):
        return ""
    return GALLERY_EXCLUDED if what == "gallery" else EXCLUDED


@gates.keeper("pages")
def _pages(said, *, what) -> str:
    if not pages_forbidden(said):
        return ""
    return PAGES_REFUSED if what == "folder" else COMIC_EXCLUDED


@gates.keeper("post")
def _post(site, tags, rating) -> str:
    return POSTS_WHY if post_forbidden(site, tags, rating) else ""


@gates.keeper("chip")
def _chip(name) -> bool:
    return chip_never(name)


@gates.keeper("left_out")
def _left_out(site, mode, where) -> tuple:
    if where == "site":
        tags, genres = list_exclusions(site)
        return tags, genres, ()
    return never_tag_ids(site), never_genres(site, mode), never_tag_names(site)


@gates.keeper("genre")
def _genre(site, mode, number, name) -> str:
    return genre_refused(site, mode, number, name)


@gates.keeper("name")
def _name() -> str:
    return RULE_NAME


# ── 10. the check of itself ───────────────────────────────────────────────────────────────────────────────────────
def self_check() -> list:
    """Every decision of the rule on its own data; [] — all as it must be, else the lines of what is not."""
    bad: list = []

    def must(what: str, ok) -> None:
        if not ok:
            bad.append(what)

    # 2. the words
    for words in (["Лоли"], ["lolicon"], ["Shotacon"], ["сётакон"], ["Школьницы"], ["School Days"], ["schoolgirl"],
                  ["ученица"], ["Девочки-волшебницы"], ["Юные"], ["Кодомо"], ["kodomokai"], ["oppai loli"],
                  ["lolibaba"], ["kindergarten uniform"], ["randoseru"], ["JK Diary"], ["ロリっ子"], ["女子高生の日常"],
                  ["小学生"], ["Соседка-лоли"], ["school%20swimsuit"], ["school&amp;gym"], ["Ｓｃｈｏｏｌ"]):
        must(f"слова: {words} не закрыты", depicts_minors(words))
    for words in (["Романтика", "Драма"], ["Big tits"], ["Students"], ["Перерождение"], ["jkl"], ["Lola"],
                  ["scholar"], [""], [None], ["Весенний дождь"]):
        must(f"слова: {words} закрыты", not depicts_minors(words))
    # 5. the check of a title: the mark 18+, the numbers of each site, the sites' own words
    must("тайтл не 18+ закрыт по слову", not title_forbidden("remanga", names=["Соседка-лоли"], adult=False))
    must("тайтл 18+ не закрыт по слову", title_forbidden("remanga", names=["Соседка-лоли"], adult=True))
    must("тайтл не 18+ закрыт по номеру", not title_forbidden("mangalib", tag_ids=[187], adult=False))
    for site, numbers in NEVER_TAG_IDS.items():
        for n in numbers:
            must(f"{site}: тег {n} не закрывает", title_forbidden(site, tag_ids=[n]))
            must(f"{site}: тег {n} строкой не закрывает", title_forbidden(site, tag_ids=[str(n)]))
    for site, numbers in NEVER_GENRE_IDS.items():
        for n in numbers:
            must(f"{site}: жанр {n} не закрывает", title_forbidden(site, genre_ids=[n]))
    must("чужой номер закрывает", not title_forbidden("hentailib", tag_ids=[154, 999999], genre_ids=[56]))
    must("номер тега как номер жанра", not title_forbidden("hentailib", genre_ids=[372]))
    must("MangaLib 18+: жанры HentaiLib", title_forbidden("mangalib", genre_ids=[70]) and
         title_forbidden("mangalib", genre_ids=[76]))
    must("сайт без номеров закрыт номером", not title_forbidden("hentaichan", tag_ids=[187], genre_ids=[70]))
    must("чужой номер закрывает AllHentai", not title_forbidden("allhentai", tag_ids=[187, 837], genre_ids=[70]))
    must("AllHentai: свои слова", title_forbidden("allhentai", tags=["большая грудь", "Девчуля"])
         and title_forbidden("allhentai", names=["Парниша и соседка"]) and title_forbidden("allhentai", tag_ids=[6204])
         and not title_forbidden("allhentai", names=["Весенний дождь"], tags=["девушки", "парни", "милфа"],
                                 tag_ids=[84, 83, 6011]))
    must("слова AllHentai у другого сайта", not title_forbidden("hentasis", names=["Парниша и соседка"]))
    for site, words in NEVER_WORDS.items():
        for w in words:
            must(f"{site}: «{w}» не закрывает", title_forbidden(site, genres=[w.title()]))
    must("Hentai-XS: название", title_forbidden("hentaixs", genres=["Romance"], names=["Лицей у моря"]))
    must("Hentai-XS: Teens", title_forbidden("hentaixs", genres=["Teens"], names=["Natsu no Hi"]))
    must("Hentai-XS: Students", not title_forbidden("hentaixs", genres=["Students", "Big tits"], names=["Песня ангела"]))
    must("HFShare: Small pipi", title_forbidden("hfshare", genres=["Small pipi", "Big tits"]))
    must("HFShare: Bloomers", title_forbidden("hfshare", genres=["Bloomers"]))
    must("HFShare: Magical Girl", title_forbidden("hfshare", genres=["Magical Girl"]))
    must("HFShare: название", title_forbidden("hfshare", names=["Oneshota The Animation"]))
    must("HFShare: Students", not title_forbidden("hfshare", genres=["Students", "Romance"], names=["Hatsukoi Jikan"]))
    must("слова одного сайта у другого", not title_forbidden("hentasis", genres=["Bloomers"]))
    must("nhentai: тег по названию", title_forbidden("nhentai", names=["[Ash] Summer Pool"], tags=["school swimsuit"]))
    must("nhentai: слова названия", title_forbidden("nhentai", names=["[Kit] JK Diary [English]"]))
    must("nhentai: обычная галерея", not title_forbidden("nhentai", names=["[Pen] Night Office"],
                                                         tags=["big breasts", "english"], tag_ids=[2937, 12227]))
    # 3. what the lists of a site leave out
    must("списки: Remanga", never_tag_ids("remanga") == (108,) and never_genres("remanga", "nsfw") == ())
    must("списки: MangaLib «Всё»", never_tag_ids("mangalib") == (187,) and never_genres("mangalib", "all") == ())
    must("списки: MangaLib «NSFW»", never_genres("mangalib", "nsfw") == (70, 76))
    must("списки: HentaiLib", never_tag_ids("hentailib") == (372, 373, 145, 50, 187)
         and never_genres("hentailib", "all") == (70, 76))
    must("списки: свои списки сайта", list_exclusions("hentailib") == ((372, 373, 145, 50, 187), (70, 76))
         and list_exclusions("mangalib") == ((), ()) and list_exclusions("remanga") == ((), ()))
    must("списки: названия тегов nhentai", never_tag_names("nhentai") == NHENTAI_NEVER_TAGS
         and never_tag_names("remanga") == ())
    must("списки: AllHentai", never_tag_ids("allhentai") == ALLHENTAI_NEVER_TAGS
         and never_genres("allhentai", "nsfw") == () and list_exclusions("allhentai") == (ALLHENTAI_NEVER_TAGS, ()))
    must("жанр в «Ленте»", genre_refused("mangalib", "nsfw", 70, "Школа") == "хентай с жанром «Школа» программа не "
         "показывает" and genre_refused("mangalib", "all", 70, "Школа") == ""
         and genre_refused("hentailib", "all", 76, "Кодомо") != "" and genre_refused("hentailib", "nsfw", 56, "Яой") == "")
    # 6. a chip of a title's page
    must("плитка: слово правила", chip_never("Лоли") and chip_never("Teens") and chip_never("Small pipi")
         and chip_never("Школа") and chip_never("девчуля") and chip_never("Парниша"))
    must("плитка: обычное слово", not chip_never("Романтика") and not chip_never("Big tits") and not chip_never(""))
    # 7. the pages of booru sites
    must("страницы: тег и оценка", page_forbidden(["young", "fox"], ["e"]) and page_forbidden(["cub"], ["q"])
         and page_forbidden(["loli"], []) and page_forbidden(["child"], ["s", "e"]))
    must("страницы: безопасная", not page_forbidden(["young", "fox"], ["s"]) and not page_forbidden(["cub"], ["g", "s"]))
    must("страницы: обычные теги", not page_forbidden(["fox", "comic"], ["e"]))
    must("страницы: одна из многих", pages_forbidden([(["fox"], ["e"]), (["young"], ["e"])])
         and not pages_forbidden([(["fox"], ["e"]), (["young"], ["s"])]) and not pages_forbidden([]))
    # 8. a post of an account's favorites
    must("пост: explicit", post_forbidden("rule34.xxx", ["young", "1girl"], "explicit")
         and post_forbidden("e621.net", ["cub"], "e") and post_forbidden("gelbooru.com", ["loli"], "sensitive")
         and post_forbidden("danbooru.donmai.us", ["shota"], "s") and post_forbidden("e621.net", ["young"], "")
         and post_forbidden("rule34.xxx", ["loli"], "") and post_forbidden("danbooru.donmai.us", ["loli", "solo"], "s"))
    must("пост: безопасный", not post_forbidden("e621.net", ["young"], "s")
         and not post_forbidden("rule34.xxx", ["child"], "safe") and not post_forbidden("danbooru.donmai.us", ["child"], "g")
         and not post_forbidden("gelbooru.com", ["young"], "general"))
    must("пост: обычные теги", not post_forbidden("rule34.xxx", ["1girl", "solo"], "explicit"))
    # 9. the answers the program gets
    must("ответ: тайтл", gates.title("hentailib", names=["Тайный урок"], genre_ids=[70]) == EXCLUDED
         and gates.title("nhentai", tags=["lolicon"], what="gallery") == GALLERY_EXCLUDED
         and gates.title("hentailib", names=["Свежая листва"], tags=["Романтика"]) == ""
         and gates.title("mangalib", names=["Соседка-лоли"], adult=False) == "")
    must("ответ: страницы", gates.pages([(["young"], ["e"])]) == COMIC_EXCLUDED
         and gates.pages([(["young"], ["e"])], what="folder") == PAGES_REFUSED and gates.pages([(["fox"], ["e"])]) == "")
    must("ответ: пост", gates.post("e621.net", ["cub"], "e") == POSTS_WHY and gates.post("e621.net", ["fox"], "e") == "")
    must("ответ: плитка", gates.chip("Сётакон") is True and gates.chip("Комедия") is False)
    left = gates.left_out("hentailib", "nsfw")
    must("ответ: списки", set(HENTAILIB_NEVER_TAGS) <= set(left.tags) and set(HENTAILIB_NEVER_GENRES) <= set(left.genres)
         and set(NHENTAI_NEVER_TAGS) <= set(gates.left_out("nhentai").names)
         and set(gates.left_out("mangalib", "all", where="site").tags) & set(MANGALIB_NEVER_TAGS) == set()
         and set(HENTAILIB_NEVER_TAGS) <= set(gates.left_out("hentailib", "nsfw", where="site").tags)
         and set(ALLHENTAI_NEVER_TAGS) <= set(gates.left_out("allhentai", "nsfw").tags)
         and set(ALLHENTAI_NEVER_TAGS) <= set(gates.left_out("allhentai", "nsfw", where="site").tags))
    must("ответ: жанр", gates.genre("mangalib", "nsfw", 76, "Кодомо") == GENRE_REFUSED.format(name="Кодомо")
         and gates.genre("mangalib", "all", 76, "Кодомо") == "")
    must("ответ: название", RULE_NAME in gates.name())
    # the program asks: the lists of the sites carry the numbers, the searches — the names
    try:
        bad.extend(_self_check_program())
    except Exception as exc:                          # noqa: BLE001 — said, not raised: a check
        bad.append(f"программа: {type(exc).__name__}: {exc}")
    return bad


def _self_check_program() -> list:
    """What the program builds with the answers: the addresses of «Лента» and the searches of the sites."""
    from urllib.parse import unquote as plain
    from core import catalog as C, nhentai as N
    bad: list = []

    def must(what: str, ok) -> None:
        if not ok:
            bad.append(what)

    # «Лента» never offers a tag or a genre of that kind in its filters: its tables have none of the numbers
    for table, never in ((C.MANGA_TAGS, lambda site: never_tag_ids(site)), (C.MANGA_GENRES, lambda site: never_genres(site, "all")),
                         (C.VIDEO_GENRES, lambda site: never_genres(site, "all"))):
        for name, ids in table.items():
            for site, number in ids.items():
                must(f"«Лента» предлагает «{name}» ({site})", number not in never(site))
    must("«Лента» предлагает теги правила", not {"Лоли", "Лоликон", "Сетакон", "Юные", "Девочки-волшебницы"} & set(C.MANGA_TAGS))
    never_names = {str(n).casefold() for n in NHENTAI_NEVER_TAGS}
    for site in ("nudemoon", "nhentai"):             # the sites whose tags of «Лента» are words, not numbers
        must(f"«Лента» предлагает слово правила у {site}", not any(
            depicts_minors([name, ids[site]]) or str(ids[site]).casefold() in never_names
            for table in (C.MANGA_TAGS, C.MANGA_GENRES) for name, ids in table.items() if site in ids))
    must("«Найти тег» находит слово правила по другому названию", not any(
        depicts_minors([other]) or str(other).casefold() in never_names
        for of_genres in (False, True) for others in C.also_names("manga", of_genres=of_genres).values()
        for other in others))
    offered = C.offered(C.genres("manga"), "manga", "nsfw", of_genres=True)
    must("«Лента» в «NSFW» предлагает жанры", "Школа" not in offered and "Кодомо" not in offered)
    must("«Лента» во «Всё» без общих жанров", "Школа" in C.offered(C.genres("manga"), "manga", "all", of_genres=True))
    # every list asked of a site leaves the numbers out
    for site, build, mode in (("remanga", C.remanga_url, "all"), ("mangalib", C.mangalib_url, "all"),
                              ("hentailib", C.hentailib_url, "nsfw"), ("haven", C.haven_url, "nsfw"),
                              ("allhentai", C.allhentai_url, "nsfw")):
        kind = "video" if site == "haven" else "manga"
        url, why = build(C.Query(kind, mode=mode), 1)
        text = plain(str(url or ""))
        numbers = re.findall(r"\d+", text)
        for n in never_tag_ids(site):
            must(f"{site}: список без исключения тега {n} ({why})", str(n) in numbers)
        for n in never_genres(site, mode):
            must(f"{site}: список без исключения жанра {n} ({why})", str(n) in numbers)
    # AllHentai: the same with its filters chosen, by a name, on the other tab — and its own lists in «Скачать»
    for q in (C.Query("manga", mode="nsfw", tags=("Чулки",), without=("Драма",), text="office", years=(2020, 0)),
              C.Query("manhwa", mode="nsfw", order="updates"), C.Query("manga", mode="all", types=("comics",))):
        url, why = C.allhentai_url(q, 2)
        asked = re.findall(r"excludeElementIds=(\d+)", plain(str(url or "")))
        included = re.findall(r"includeElementIds=(\d+)", plain(str(url or "")))
        must(f"AllHentai: список с фильтрами без исключений ({why})",
             bool(url) and {str(n) for n in ALLHENTAI_NEVER_TAGS} <= set(asked)
             and not {str(n) for n in ALLHENTAI_NEVER_TAGS} & set(included))
    from core import manhwa_sources as S
    never = S.AllHentai()._never()
    must("AllHentai: свои списки без исключений",
         {str(n) for n in ALLHENTAI_NEVER_TAGS} <= set(re.findall(r"excludeElementIds=(\d+)", never)))
    # …and the list of an author's titles (v5.44.149: the Lib API's own tag of a query) — the same numbers left out
    for site, build, mode in (("mangalib", C.mangalib_url, "all"), ("hentailib", C.hentailib_url, "nsfw")):
        url, why = build(C.Query("manga", mode=mode, extra=("lib:people:1 Somebody",)), 1)
        text = plain(str(url or ""))
        numbers = re.findall(r"\d+", text.replace("target_id=1", ""))
        must(f"{site}: список автора не спрошен ({why})", "target_id=1" in text and "target_model=people" in text)
        for n in (*never_tag_ids(site), *never_genres(site, mode)):
            must(f"{site}: список автора без исключения {n} ({why})", str(n) in numbers)
    asked = C._wanted(C.Query("manga", mode="nsfw", genres=("Школа",)), "mangalib")
    must("MangaLib «NSFW»: жанр спрошен", asked == ([], [], GENRE_REFUSED.format(name="Школа")))
    must("MangaLib «Всё»: жанр не спрошен", C._wanted(C.Query("manga", mode="all", genres=("Школа",)), "mangalib")[2] == "")
    # nhentai's search — without its tags of that kind, each by its name
    words = N.search_words("office")
    must("nhentai: поиск без исключений", all(f'-tag:"{name}"' in words for name in NHENTAI_NEVER_TAGS)
         and words.startswith("office"))
    # …with the filters of «Лента» chosen too (v5.44.148: nhentai takes them — its own words): the same exclusions
    url, why = C.nhentai_url(C.Query("manga", mode="nsfw", tags=("Тентакли",), extra=("nhentai:artist:somebody",)), 1)
    words = plain(str(url or "")).replace("+", " ")
    must(f"nhentai: поиск с фильтрами без исключений ({why})",
         bool(url) and all(f'-tag:"{name}"' in words for name in NHENTAI_NEVER_TAGS)
         and 'tag:"tentacles"' in words and 'artist:"somebody"' in words)
    # a site's own tag of that kind: offered by nothing, and a query with it asks no site
    for name in NHENTAI_NEVER_TAGS:
        must(f"«Лента»: свой тег сайта «{name}» спрошен",
             C.extra_closed("nhentai", name)
             and C.nhentai_url(C.Query("manga", mode="nsfw", extra=(f"nhentai:tag:{name}",)), 1)[0] is None)
    # a chip of a title's page: no search of a word of the rule
    must("плитка «Ленты»", C.feed_filter("manga", "Лоли", tag=True) == ("never", "")
         and C.feed_filter("video", "Teens") == ("never", "") and C.feed_filter("video", "Small pipi") == ("never", "")
         and C.feed_filter("manga", "Романтика")[0] == "g")
    return bad
