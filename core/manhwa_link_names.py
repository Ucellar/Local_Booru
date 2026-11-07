"""(v5.44.72) A title by the name in a link of a site the program does not ask — com-x.life (the owner, 5.11.2026:
«тоже должно быть доступно»; every page of it is a check for robots first, which the program does not get past —
``core/manhwa_sources.ComX``), or a site that answered the link with such a check or a refusal
(``manhwa_download._by_link_name``).

An address of com-x.life is «/9504-latna-saga-survival-of-a-sword-king-korol-mecha.html»: the site's engine (DataLife
Engine) writes the title's names in Latin letters, one after another — «Latna Saga: Survival of a Sword King» and
«Король меча».  The sites the program reads are searched (their own searches, ``Source.search``) by the whole of it and
by its halves where an English name most likely meets a Russian one (:func:`link_queries` — the Russian half in
Russian letters: the searches do not know «korol mecha»; Remanga's finds «корол меча», looked at from the owner's
computer); an answer is taken when its names make at least half of the name in the address (:func:`link_cover`: runs
of its words that are a whole name — :func:`loose_key`: Russian letters as the address writes them, the spellings of
one letter one), on the first site in the owner's order that has one (:func:`find_by_words`).
"""
from __future__ import annotations

import logging
import re
import unicodedata

from core.manhwa_sources import Net, SourceError

_LOG = logging.getLogger("local_booru.manhwa_get")

# Russian letters as an address writes them in Latin ones (DataLife Engine's own table: «Король меча» — «korol-mecha»)
_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
             "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
             "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
             "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g"}
# the spellings of one Russian letter that other tables use, as one («shh», «shch» — «sch»; «ts» — «c»; «j» — «y»…)
_LOOSE = (("shch", "s"), ("sch", "s"), ("shh", "s"), ("sh", "s"), ("ch", "c"), ("ts", "c"), ("tz", "c"), ("cz", "c"),
          ("kh", "h"), ("zh", "z"), ("yu", "u"), ("ju", "u"), ("iu", "u"), ("ya", "a"), ("ja", "a"), ("ia", "a"),
          ("yo", "o"), ("jo", "o"), ("ye", "e"), ("je", "e"), ("y", "i"), ("j", "i"), ("w", "v"), ("x", "ks"),
          ("q", "k"))
# back to Russian letters, for a site's search (the longest first; «y» at the end of a word after a vowel — «й»)
_TO_CYRILLIC = (("shch", "щ"), ("sch", "щ"), ("shh", "щ"), ("zh", "ж"), ("kh", "х"), ("ch", "ч"), ("sh", "ш"),
                ("yu", "ю"), ("ya", "я"), ("yo", "ё"), ("ts", "ц"), ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"),
                ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"), ("j", "й"), ("k", "к"), ("l", "л"), ("m", "м"),
                ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"),
                ("h", "х"), ("c", "ц"), ("y", "ы"), ("w", "в"), ("x", "кс"), ("q", "к"))
# which half of an address is English and which is Russian in Latin letters (a guess: only the order of the searches)
_ENGLISH = frozenset(
    "a an the of in on to and is at for with from by you your me his her it be as or not who how when after into over "
    "under this that are was all one no new last first only just again back return returns returned reborn rebirth "
    "regression regressor reincarnation reincarnated level leveling levelling up solo max maxed rank ranker player "
    "players game system hunter hunters master lord king kings queen prince princess emperor empress duke duchess lady "
    "knight knights hero heroes villain villainess demon devil god gods dragon sword swords swordsman magic mage "
    "wizard sorcerer academy tower dungeon world life story tale legend legendary martial arts saint survival survive "
    "record records chronicles class genius monster monsters dead death night star heavenly heaven sky blood shadow "
    "dark black white red blue love wife husband son daughter family school high boy girl man woman beast warrior war "
    "battle".split())
_RUSSIAN = frozenset(
    "v s k u o na ne po za iz ot do vo so ko pod nad bez pri kak chto eto dlya moy moya svoy korol koroleva mech mecha "
    "mechey geroy geroya bog boga mag maga mir mira mire put sila igra igrok syn doch zhena muzh voin zver rytsar "
    "ohotnik imperator printsessa akademiya bashnya istoriya legenda vtoroy novyy posledniy nebesnyy temnyy chernyy "
    "krovi smerti zhizni lyubov".split())
LINK_SEARCHES = 6            # queries of one site for the name in a link
LINK_LOOKS = 3               # titles of a site's answers asked for
LINK_COVER = 0.5             # the share of the name in a link a title's names must make


def loose_key(text) -> str:
    """A name as an address writes it: Russian letters in Latin ones, letters and digits alone, one case, the
    spellings of one Russian letter one — «Король меча» and «korol-mecha» are one."""
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    key = "".join(ch for ch in "".join(_TRANSLIT.get(ch, ch) for ch in text) if ch.isalnum())
    for a, b in _LOOSE:
        key = key.replace(a, b)
    return key


def link_cover(words: list, names) -> float:
    """How much of the name in a link (its ``words``) the names of a title make: the share of its letters in runs of
    its words that are a whole name of the title (``loose_key``) — 1.0 when they make all of it."""
    runs: dict = {}
    for i in range(len(words)):
        for j in range(i + 1, len(words) + 1):
            runs.setdefault(loose_key("".join(words[i:j])), (i, j))
    covered: set = set()
    for name in names:
        key = loose_key(name)
        if len(key) >= 4 and key in runs:
            covered.update(range(*runs[key]))
    total = sum(len(w) for w in words)
    return sum(len(words[n]) for n in covered) / total if total else 0.0


def _lean(word: str) -> int:
    """+1 — an English word, -1 — a Russian one in Latin letters, 0 — either."""
    if word in _ENGLISH or re.search(r"[wqx]|th", word):
        return 1
    if word in _RUSSIAN or re.search(r"zh|kh|shch|sch|shh|yu|ya|yo|(?:ogo|ego|aya|ykh|ikh|yj|ij|yy|iy)$", word):
        return -1
    return 0


def _cyrillic(word: str) -> str:
    out, i = [], 0
    while i < len(word):
        for latin, letter in _TO_CYRILLIC:
            if word.startswith(latin, i):
                if latin == "y" and i == len(word) - 1 and i and word[i - 1] in "aeiouy":
                    letter = "й"
                out.append(letter)
                i += len(latin)
                break
        else:
            out.append(word[i])
            i += 1
    return "".join(out)


def link_queries(words: list) -> list:
    """What the sites' searches are asked for the name in a link: the whole of it, then its two halves at the places
    where an English name most likely meets a Russian one (an address of com-x.life is «<one name>-<another>»), the
    Russian half in Russian letters (the sites' searches do not know «korol mecha»); at most ``LINK_SEARCHES``."""
    lean = [_lean(w) for w in words]
    out: list = []

    def add(part, english):
        text = " ".join(part if english else [_cyrillic(w) for w in part])
        if text and text not in out:
            out.append(text)
    add(words, sum(lean) >= 0)
    splits = []
    for k in range(1, len(words)):
        left, right = sum(lean[:k]), sum(lean[k:])
        splits.append((-abs(left - right), abs(2 * k - len(words)), k, left >= right))
    for _score, _balance, k, english_left in sorted(splits):
        add(words[:k], english_left)
        add(words[k:], not english_left)
    return out[:LINK_SEARCHES]


def find_by_words(net: Net, words: list, sources, say=None) -> "tuple | None":
    """(the site, its title, how much of the name it makes) — the title the name in a link (``words``) is, on the first
    of ``sources`` (the owner's order) where one is: its search asked by :func:`link_queries`, the answers whose names
    make a part of the name asked for, the one whose names make the most of it (``LINK_COVER`` at least)."""
    say = say or (lambda _text: None)
    queries = link_queries(words)
    for source in sources:
        best, asked, failed = None, set(), False
        for query in queries:
            try:
                hits = source.search(net, query)
            except SourceError as exc:
                say(f"{source.label}: поиск не ответил — {exc}")
                failed = True
                break
            for hit in hits:
                if hit.key in asked or len(asked) >= LINK_LOOKS or link_cover(words, hit.names) <= 0:
                    continue
                asked.add(hit.key)
                try:
                    title = source.title(net, hit.key)
                except SourceError as exc:
                    _LOG.info("%s: %s not asked for: %s", source.label, hit.key, exc)
                    continue
                share = link_cover(words, [title.title, *title.alt_titles])
                if share >= LINK_COVER and (best is None or share > best[2]):
                    best = (source, title, share)
            if best is not None:
                return best
        if not failed:
            say(f"{source.label}: по названию из ссылки не нашёлся")
    return None
