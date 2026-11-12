"""(v5.44.147) The program's gates.

A gate is a question the program asks before it shows, opens or takes something of a site.  The program does not
answer it itself: the answers belong to the keepers — the modules of ``core`` whose code says ``__keeper__ = True``.
A keeper registers what it answers (:func:`keeper`), describes itself in its own file and checks itself there
(``self_check()`` — run by ``tools/dev_checks/gates.py``).  The rest of the program hands a gate what a site says and
does what the answer says; it does not know what a keeper decides by, and it never names one.

**Every gate must have a keeper.**  A gate nobody answers is not «open»: the program does not start
(:class:`Unkept` — ``app.py`` and ``python -m webui`` ask :func:`ready` first, every gate asks it too).

The gates — what goes in is what a site says, what comes out are the keeper's words ("" — nothing against it):

* :func:`title` — a title of a site (manga, a gallery, a series): may it be shown, opened, downloaded.
* :func:`pages` — pictures of the booru sites taken together into «Манга» (a comic of the gallery, a folder).
* :func:`post` — one post of a booru site the program downloads itself (an account's favorites).
* :func:`chip` — a genre or tag written on a title's page: may the page make a search of it.
* :func:`left_out` — what the lists of a site are always asked without (numbers of its tags and genres, names of tags).
* :func:`genre` — a genre chosen in «Лента»: may it be asked of the site.
* :func:`name` — the keepers' short name of what they keep (a column, a counter: where a sentence does not fit).

Several keepers: a thing is closed when one of them closes it (the first one's words); what is left out — of all.
"""
from __future__ import annotations

import ast
import importlib
import threading
from pathlib import Path
from typing import NamedTuple

GATES = ("title", "pages", "post", "chip", "left_out", "genre", "name")
MARK = "__keeper__"                  # a module of ``core`` with ``__keeper__ = True`` in its code is a keeper


class Unkept(RuntimeError):
    """A gate has no keeper: the program is not whole — it does not start."""


class Left(NamedTuple):
    """What the lists of a site are always asked without."""
    tags: tuple = ()                 # the numbers of its tags
    genres: tuple = ()               # the numbers of its genres
    names: tuple = ()                # the names of its tags (a site that leaves tags out by name)


_KEEPERS: dict = {gate: [] for gate in GATES}
_MODULES: list = []
_LOCK = threading.RLock()
_READY = False


def keeper(gate: str):
    """``@keeper("title")`` — the function below answers this gate (a keeper's own file says it of its functions)."""
    if gate not in _KEEPERS:
        raise ValueError(f"no such gate: {gate}")

    def register(answer):
        with _LOCK:
            if answer not in _KEEPERS[gate]:
                _KEEPERS[gate].append(answer)
        return answer
    return register


def _marked(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if MARK.encode() not in data:
        return False
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return False
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value is True \
                and any(isinstance(t, ast.Name) and t.id == MARK for t in node.targets):
            return True
    return False


def keeper_files() -> list:
    """The files of the keepers (``core/*.py`` whose code says ``__keeper__ = True``), by name."""
    here = Path(__file__).resolve()
    return [p for p in sorted(here.parent.glob("*.py")) if p.resolve() != here and _marked(p)]


def ready() -> None:
    """The keepers loaded (once); :class:`Unkept` when a gate has none."""
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        for path in keeper_files():
            module = importlib.import_module(f"{__package__}.{path.stem}")
            if module not in _MODULES:
                _MODULES.append(module)
        unkept = [gate for gate in GATES if not _KEEPERS[gate]]
        if unkept:
            raise Unkept("Local Booru не может работать: в папке программы не хватает её обязательной части "
                         f"(без ответа остались: {', '.join(unkept)}). Восстановите файлы программы.")
        _READY = True


def keepers() -> list:
    """The keepers' modules (for their self-checks)."""
    ready()
    return list(_MODULES)


def _answers(gate: str) -> list:
    ready()
    return list(_KEEPERS[gate])


def _words(gate: str, *args, **said) -> str:
    for answer in _answers(gate):
        words = answer(*args, **said)
        if words:
            return str(words)
    return ""


def title(site: str, *, names=(), genres=(), tags=(), genre_ids=(), tag_ids=(), adult: bool = True,
          what: str = "title") -> str:
    """A title of ``site`` (its name in the program): "" — nothing against it, else the words why it is neither
    shown, nor opened, nor downloaded.  What the site says of it: ``names`` (its name, its other names, the works it
    is of), ``genres``, ``tags`` (their names), ``genre_ids``, ``tag_ids`` (their numbers at the site), ``adult`` —
    whether the site marks it 18+ (every title of a site of hentai alone).  ``what`` — the thing for the words:
    «title», «gallery»."""
    return _words("title", str(site or ""), names=tuple(x for x in names or () if x),
                  genres=tuple(x for x in genres or () if x), tags=tuple(x for x in tags or () if x),
                  genre_ids=tuple(genre_ids or ()), tag_ids=tuple(tag_ids or ()), adult=bool(adult), what=what)


def pages(said, *, what: str = "comic") -> str:
    """Pictures of the booru sites taken together into «Манга»: "" — nothing against it, else the words why not.
    ``said`` — of each page (the names of its tags, what the sites rated it); ``what`` — «comic» (a comic of the
    gallery), «folder» (a folder of the parser)."""
    return _words("pages", [(tuple(names or ()), tuple(ratings or ())) for names, ratings in said or ()], what=what)


def post(site: str, tags, rating) -> str:
    """One post of a booru site (its host, its tags' names, its rating as the site gives it) the program is about to
    download itself: "" — nothing against it, else the words why not (for a line «не скачиваю постов — N: …»)."""
    return _words("post", str(site or ""), tuple(tags or ()), rating)


def chip(name: str) -> bool:
    """Whether a genre or tag written on a title's page gets no search of its own."""
    return any(answer(str(name or "")) for answer in _answers("chip"))


def left_out(site: str, mode: str = "", *, where: str = "feed") -> Left:
    """What the lists of ``site`` are always asked without, in ``mode`` («sfw», «all», «nsfw»): ``where`` — «feed»
    (the lists of «Лента»), «site» (the site's own search and feed in «Скачать»)."""
    tags, genres, names = [], [], []
    for answer in _answers("left_out"):
        got = answer(str(site or ""), str(mode or ""), where)
        if got:
            tags += list(got[0] or ())
            genres += list(got[1] or ())
            names += list(got[2] or ())
    return Left(tuple(dict.fromkeys(tags)), tuple(dict.fromkeys(genres)), tuple(dict.fromkeys(names)))


def genre(site: str, mode: str, number, name: str) -> str:
    """A genre chosen in «Лента» (``name``; its ``number`` at ``site``): "" — it may be asked of the site in ``mode``,
    else the words why not."""
    return _words("genre", str(site or ""), str(mode or ""), number, str(name or ""))


def name() -> str:
    """The keepers' short name of what they keep — where a sentence does not fit (a column, a counter)."""
    said = [str(answer() or "").strip() for answer in _answers("name")]
    return "; ".join(dict.fromkeys(x for x in said if x))
