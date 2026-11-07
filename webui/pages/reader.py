"""(v5.44.117) One reader for every chapter the program shows — the owner's approved mockup B («Одна читалка»; «Норм»):
the downloaded manga (``webui/pages/manga.py``), the downloaded manhwa (``manhwa.py``) and the chapters of a site read
without downloading them (``manga_online.py``), on the computer and on the phone (``phone_manga.py``,
``phone_manhwa.py``, ``/m/online``).  Until then there were six readers, each with its own bars and keys.

A page of a source makes a :class:`Book` — the title, its chapters with their marks (:class:`Row`), the chapter open
and its pages, where its place and its way go — and :func:`page` draws it; ``static/reader.js`` reads it (one script for
both ways and both devices), ``static/reader.css`` gives it its look on the tokens of every look (and of the phone's).

* one panel: «← К тайтлу», the title and where its pages are from («из папки» / «с MangaLib, без скачивания»),
  «‹ Глава ›» with the list of chapters (read, new, in the folder, the site's without downloading, paid), «Страница N
  из M»; its second row — «Как читать: По страницам (слева направо / справа налево) · Прокруткой», the width of the
  strip, the keys.  On the phone the same panel on top, the page and «Как читать» at the bottom;
* «По страницам» — a page at a time: its left or right half (on the phone its sides — a page at each tap, however
  quick; the middle shows and hides the panels), ← → (right to left — ← goes on), Shift+← → — the chapter, Home / End,
  a swipe and the slider on the phone, a double click — ×2.5 and dragged (the phone: a double tap in the middle); the
  next page is put on the screen when it has come whole and is decoded (the page before stays meanwhile); the strip of
  the chapter's pages under it, beside it or none («Лента страниц» — ``prefs.manga_strip``); after the last page one
  screen «Глава закончилась» (:func:`end_html`);
* «Прокруткой» — the strip of «Манхва» (the next chapter comes by itself below with the words between the chapters,
  :func:`between_html`; the place is a page and a part of it; the width — ``prefs.manhwa_width``; the panel hides
  while read down);
* the way is kept for each title by its source (``core.manga_library``/``manhwa_library`` ``PROGRESS.mode_of``,
  ``manga_online.PROGRESS``); the first time — by the title's kind (``manga_online.auto_of``: manhwa — the strip,
  manga — the pages left to right); there is no «Авто» to press.

A title of a folder the program downloaded from a site (``.lb/source.json``) shows the site's chapters it has not —
«с Remanga, без скачивания», «платная до …» — once the site answers (:func:`site`, asked by the page after it is drawn,
never by the program window's browser).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field

from webui.html import esc
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")

MODES = ("ltr", "rtl", "strip")          # how a title is read (the sources keep it): pages →, pages ←, the strip
PLACES = {"bottom": "снизу", "side": "сбоку", "off": "скрыть"}          # «Лента страниц» — prefs.manga_strip
WIDTHS = {"720": "720", "900": "900", "1200": "1200", "full": "по окну"}  # «Ширина» of the strip — prefs.manhwa_width
NEW_FOR = 3 * 86400                      # a chapter that came this many seconds ago and is not read: «новая»
EAGER = 3                                # pages of the strip asked at once around its place; the rest — as they near
SITE_AGAIN = 10 * 60                     # a site that did not answer the reader is not asked again for this long
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря")

# line icons of one set (webui/icons.py, phone.py): stroke 1.9, the colour of the words beside them
_PATHS = {
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "list": '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="M4.5 6h.01"/><path d="M4.5 12h.01"/>'
            '<path d="M4.5 18h.01"/>',
    "chev": '<path d="M6 9l6 6 6-6"/>',
    "folder": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 '
              '0 0 1 3 17.5z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c3 3.5 3 14.5 0 18"/>'
             '<path d="M12 3c-3 3.5-3 14.5 0 18"/>',
    "lock": '<path d="M7 11V8a5 5 0 0 1 10 0v3"/><path d="M5.5 11h13v9.5h-13z"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "ltr": '<rect x="3" y="5" width="8" height="14" rx="1"/><path d="M14 12h7"/><path d="M18 9l3 3-3 3"/>',
    "rtl": '<rect x="13" y="5" width="8" height="14" rx="1"/><path d="M10 12H3"/><path d="M6 9l-3 3 3 3"/>',
    "rows": '<rect x="5" y="3" width="14" height="8" rx="1"/><rect x="5" y="13" width="14" height="8" rx="1"/>',
}


def icon(name: str, size: int = 16, width: float = 1.9) -> str:
    return (f'<svg class="rd-i" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
            f'focusable="false">{_PATHS[name]}</svg>')


@dataclass
class Row:
    """A chapter of the list.  ``key`` — the chapter as its source names it (the progress, the strip's next chapter);
    ``href`` — its address in this reader, where it was left ("" — it cannot be read here: paid everywhere); ``start``
    — from its first page; ``last`` — at its last page (← on the first page of the next one)."""
    key: str
    label: str                           # «Том 1 Глава 7»
    name: str = ""                       # its own name («Возвращение»)
    href: str = ""
    start: str = ""
    last: str = ""
    pages: int = 0                       # its pages (0 — not known before it is open)
    read: bool = False
    new: bool = False
    where: str = "disk"                  # "disk" — the folder's; "site" — the site's, not downloaded; "paid" — no site free
    site: str = ""                       # the site it comes from («Remanga»): a site's chapter, another site's copy
    free_at: str = ""                    # a paid one: the day it becomes free («14 ноября»; "" — none said)
    in_folder: bool = False              # a site's chapter the folder has too
    glue: bool = True                    # the strip can bring it below the chapter before (the same source)
    tome: int = 0
    number: "float | None" = None        # its number: the site's chapters go among the folder's by it

    @property
    def full(self) -> str:
        return self.label + (f" — {self.name}" if self.name else "")


@dataclass
class Book:
    """What a source gives the reader (see the module's words)."""
    src: str                             # "manga" / "manhwa" / "site" — whose chapters
    kind: str                            # "manga" / "manhwa": the tab's frame
    phone: bool
    name: str                            # the title
    home: str                            # «К тайтлу»
    rows: list
    at: int                              # the chapter open: its place in ``rows``
    pages: list                          # [{src, w, h, thumb}] of the chapter open
    href: str                            # its address without the page (``p`` is added)
    base: str                            # a chapter's address: «{key}» in it — the chapter's key
    progress: str                        # where the place goes: {chapter: key, page from 0, part, done}
    mode_api: str                        # where the way chosen goes: {mode}
    chapter_api: str = ""                # the strip's next chapter («{key}» in it)
    site_api: str = ""                   # a folder's title of a site: its chapters on the site, asked after the page
    mode: str = "ltr"
    chosen: str = ""                     # the owner's own choice for the title ("" — the program's by its kind)
    page: int = 0                        # the page shown first, from 0; len(pages) — the chapter's end screen
    part: float = 0.0                    # the strip: how much of that page is above the top of the window
    where: str = "disk"                  # where the pages are from: "disk" / "site"
    where_words: str = "из папки"
    follow: bool = False                 # the title's new chapters come by themselves at night
    single: bool = False                 # a manga of one part: no chapters to go to
    linked: bool = False                 # a folder's title of a site (its chapters on the site are asked)
    dir_default: str = "ltr"             # «По страницам» from the strip: the way the pages go

    @property
    def row(self) -> Row:
        return self.rows[self.at]

    def page_href(self, n) -> str:
        """The address of page ``n`` (from 1, or «last», «end») of the chapter open."""
        return self.href + ("&" if "?" in self.href else "?") + f"p={n}"

    def chapter_href(self, key: str) -> str:
        return self.base.replace("{key}", key)


# ── words ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def day_of(text) -> str:
    """«2026-11-14T00:00:00» or «14.11.2026» → «14 ноября» (with the year when it is not this one); "" — no date."""
    text = str(text or "")
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"(\d\d?)\.(\d\d?)\.(\d{4})", text)
        if not m:
            return ""
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1 <= month <= 12:
        return ""
    return f"{day} {MONTHS[month - 1]}" + ("" if year == time.localtime().tm_year else f" {year}")


def words(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def number_of(value) -> "float | None":
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def label_words(label: str) -> str:
    """«Глава 57 закончилась», «Глава «Пролог» закончилась»."""
    return label if re.match(r"(Том|Глава)\b", label) else f"Глава «{label}»"


def place_of(req: Request, saved: tuple, total: int, *, read: bool = False, asked: "str | None" = None) -> tuple:
    """(page from 0, part) to open the chapter at: ``?p=`` (from 1; «last» — its last page; «end» — the end screen,
    ``total``; ``asked`` — instead of the address's), ``?from=start`` — its start; else where it was left (the part too
    when ``p`` names that page) — a chapter read through opens at its start again."""
    total = max(0, int(total))
    page, part = (int(saved[0]), float(saved[1])) if saved else (0, 0.0)
    if req.arg("from") == "start" and asked is None:
        return 0, 0.0
    asked = str(req.arg("p") if asked is None else asked).strip().lower()
    if asked == "end":
        return total, 0.0
    if asked == "last":
        return max(0, total - 1), 0.0
    if asked.isdigit():
        n = max(0, min(int(asked) - 1, max(0, total - 1)))
        return (n, part) if n == page else (n, 0.0)
    if read and page >= total - 1:
        return 0, 0.0
    return max(0, min(page, max(0, total - 1))), max(0.0, min(1.0, part))


def auto(kind: str, manga: bool, sizes=()) -> str:
    """The way a title is read when the owner has not chosen one (``core/manga_online.auto_of``)."""
    from core import manga_online as O
    return O.auto_of(kind, manga, list(sizes))


# ── what comes after a chapter ─────────────────────────────────────────────────────────────────────────────────────
def ahead(rows: list, i: int) -> tuple:
    """(the next chapter that can be read or None, the paid chapters before it — or after ``i`` when none can be)."""
    paid = []
    for row in rows[i + 1:]:
        if row.href:
            return row, paid
        paid.append(row)
    return None, paid


def behind(rows: list, i: int) -> "Row | None":
    return next((row for row in reversed(rows[:i]) if row.href), None)


def glued(rows: list, i: int, step: int) -> str:
    """The key of the chapter the strip goes to from ``i`` (the same source only), "" — none."""
    k = i + step
    while 0 <= k < len(rows):
        row = rows[k]
        if row.href:
            return row.key if row.glue else ""
        k += step
    return ""


def _paid_words(paid: list) -> str:
    """«Главы 15–19 платные до 12 ноября», «Глава 15 — платная», «Платных глав впереди: 3»."""
    when = next((r.free_at for r in paid if r.free_at), "")
    tail = f" до {when}" if when else ""
    if len(paid) == 1:
        return f"{paid[0].label} — платная{tail}"
    first, last = paid[0].label, paid[-1].label
    a, b = re.fullmatch(r"(?:Том \d+ )?Глава (\S+)", first), re.fullmatch(r"(?:Том \d+ )?Глава (\S+)", last)
    if a and b:
        return f"Главы {a.group(1)}–{b.group(1)} платные{tail}"
    return f"Платных глав впереди: {len(paid)}{(' — первая' + tail) if tail else ''}"


def _where_words(book: Book, row: Row) -> str:
    """Where a chapter lies, as the end screen says it: «в папке», «с Remanga, без скачивания»."""
    if row.where == "site" and (book.where == "disk" or row.site):
        site = row.site or book.where_words.removeprefix("с ").split(",")[0]
        return f"с {site}, без скачивания" if book.where == "disk" else f"с {site}"
    if row.where == "disk" or row.in_folder:
        return "в папке"
    return ""


def _follow_words(book: Book) -> str:
    return "Новые главы программа скачает сама — ночью." if book.follow else ""


def end_html(book: Book, i: "int | None" = None) -> str:
    """The screen after the last page of chapter ``i`` (by default the one open): «Глава закончилась», the next
    chapter (where it lies, its pages), the paid ones before it, the last one; «← К тайтлу», «Назад к страницам»."""
    i = book.at if i is None else i
    row = book.rows[i]
    nxt, paid = ahead(book.rows, i)
    head = "Манга закончилась" if book.single else "Глава закончилась"
    was = esc(row.label) + (f" «{esc(row.name)}»" if row.name else "")
    parts = [f'<p class="rd-end-head">{head}</p>',
             f'<p class="rd-end-was"><b>{was}</b> — отмечена прочитанной</p>']
    rtl = book.mode == "rtl"
    on, back = ("←", "→") if rtl else ("→", "←")
    if nxt is not None:
        said = "Следующая бесплатная" if paid else "Следующая"
        parts.append(f'<a class="rd-end-next" href="{esc(nxt.start or nxt.href)}" data-rd="next-chapter">'
                     f'{said}: {esc(nxt.label)} <span aria-hidden="true">›</span></a>')
        what = [f"«{esc(nxt.name)}»"] if nxt.name else []
        where = _where_words(book, nxt)
        if where:
            what.append(f'<span class="{"rd-ok" if where == "в папке" else "rd-site"}">{esc(where)}</span>')
        if nxt.pages:
            what.append(f"{nxt.pages} {words(nxt.pages, 'страница', 'страницы', 'страниц')}")
        if what:
            parts.append(f'<p class="rd-end-what">{" · ".join(what)}</p>')
        if paid:
            parts.append(f'<p class="rd-end-paid">{icon("lock", 13, 2)}{esc(_paid_words(paid))}</p>')
    else:
        if paid:
            parts.append(f'<p class="rd-end-paid">{icon("lock", 13, 2)}{esc(_paid_words(paid))}</p>')
        elif not book.single:
            parts.append('<p class="rd-end-last">Это последняя глава</p>')
        follow = _follow_words(book)
        if follow:
            parts.append(f'<p class="rd-end-what">{esc(follow)}</p>')
    home_cls = "rd-end-home" + (" primary" if nxt is None else "")
    parts.append(f'<div class="rd-end-more"><a class="{home_cls}" href="{esc(book.home)}">← К тайтлу</a>'
                 f'<button type="button" class="rd-end-back" data-rd="back-to-pages">'
                 f'{"К страницам" if book.phone else "Назад к страницам"}</button></div>')
    if book.phone:
        if nxt is not None:
            parts.append(f'<p class="rd-end-hint">или смахните <span data-rd-swipe>{"вправо" if rtl else "влево"}</span>'
                         f' — следующая глава</p>')
    elif nxt is not None:
        parts.append(f'<p class="rd-end-keys"><kbd data-rd-on>{on}</kbd> или <kbd>Пробел</kbd> — следующая глава · '
                     f'<kbd data-rd-back-key>{back}</kbd> — назад к последней странице</p>')
    else:
        parts.append(f'<p class="rd-end-keys"><kbd data-rd-back-key>{back}</kbd> — назад к последней странице</p>')
    return f'<div class="rd-end-in">{"".join(parts)}</div>'


def between_html(book: Book, i: int) -> str:
    """The words at the end of chapter ``i`` in the strip: «Глава 57 закончилась — дальше Глава 58 «Мост» · в папке»
    (the strip brings it below by itself), the next one of the site (its link: another source), the paid ones, the
    last one («← К тайтлу»)."""
    row = book.rows[i]
    nxt, paid = ahead(book.rows, i)
    head = "Манга закончилась" if book.single else f"{label_words(row.label)} закончилась"
    parts = [f'<p class="rd-bw-head">{esc(head)}</p>']
    if nxt is not None:
        name = esc(nxt.label) + (f" «{esc(nxt.name)}»" if nxt.name else "")
        where = _where_words(book, nxt)
        if nxt.glue:
            parts.append(f'<p class="rd-bw-what">дальше — {name}{(" · " + esc(where)) if where else ""}</p>')
        else:
            parts.append(f'<p><a class="rd-end-next small" href="{esc(nxt.start or nxt.href)}">Следующая: '
                         f'{esc(nxt.label)} <span aria-hidden="true">›</span></a></p>'
                         + (f'<p class="rd-bw-what">{esc(where)}</p>' if where else ""))
        if paid:
            parts.append(f'<p class="rd-bw-what">{icon("lock", 12, 2)}{esc(_paid_words(paid))}</p>')
    else:
        if paid:
            parts.append(f'<p class="rd-bw-what">{icon("lock", 12, 2)}{esc(_paid_words(paid))}</p>')
        elif not book.single:
            parts.append('<p class="rd-bw-what">Это последняя глава</p>')
        follow = _follow_words(book)
        if follow:
            parts.append(f'<p class="rd-bw-what">{esc(follow)}</p>')
        parts.append(f'<p><a class="rd-end-home" href="{esc(book.home)}">← К тайтлу</a></p>')
    return f'<div class="rd-between" data-between="{esc(row.key)}">{"".join(parts)}</div>'


# ── the list of chapters ───────────────────────────────────────────────────────────────────────────────────────────
def _marks(book: Book, row: Row, current: bool) -> str:
    """A row's marks; «сейчас · стр. N из M» is in every readable row and shows in the current one only (reader.css):
    the strip goes from chapter to chapter, the script moves the mark."""
    out = []
    if row.href:
        total = len(book.pages) if current else row.pages
        shown = min(book.page + 1, total) if current and total else 1
        out.append(f'<span class="rd-st here">сейчас · стр. <span data-rd-here>{shown}</span>'
                   f'{(" из " + str(total)) if total else ""}</span>')
    if row.where == "paid":
        out.append(f'<span class="rd-st paid">{icon("lock", 12, 2)}платная'
                   f'{(" до " + esc(row.free_at)) if row.free_at else ""}</span>')
    elif row.read:
        out.append(f'<span class="rd-st read">{icon("check", 12, 2.4)}прочитана</span>')
    else:
        if row.new:
            out.append('<span class="rd-st new"><b>новая</b></span>')
        if row.where == "site" and book.where == "disk":
            out.append(f'<span class="rd-st site">{icon("globe", 12, 2)}с {esc(row.site)}, без скачивания</span>')
        elif row.where == "site" and row.site:
            out.append(f'<span class="rd-st site">{icon("globe", 12, 2)}с {esc(row.site)}</span>')
        if row.in_folder or (row.where == "disk" and book.linked):
            out.append(f'<span class="rd-st disk">{icon("folder", 12, 2)}в папке</span>')
    return "".join(out)


def rows_html(book: Book) -> str:
    """The chapters, the newest first (as on Remanga and the title's page)."""
    items = []
    for i in range(len(book.rows) - 1, -1, -1):
        row = book.rows[i]
        current = i == book.at
        cls = " ".join(c for c in ("rd-li", "current" if current else "", "read" if row.read else "",
                                   "wait" if not row.href else "") if c)
        inside = (f'<span class="rd-li-name">{esc(row.full)}</span>'
                  f'<span class="rd-sts">{_marks(book, row, current)}</span>')
        if row.href:
            mark = ' aria-current="true"' if current else ""
            inside = f'<a href="{esc(row.href)}"{mark}>{inside}</a>'
        else:
            inside = f'<span class="rd-li-in">{inside}</span>'
        items.append(f'<li class="{cls}" data-chapter="{esc(row.key)}" data-pages="{row.pages}">{inside}</li>')
    return "".join(items)


def list_html(book: Book) -> str:
    count = len(book.rows)
    head = (f'<p class="rd-list-head"><span>{esc(book.name)} — {count} {words(count, "глава", "главы", "глав")}</span>'
            f'<a href="{esc(book.home)}">все главы — на странице тайтла</a></p>')
    return (f'<div class="rd-list" id="rd-list" hidden>{head}<ol data-rd-rows>{rows_html(book)}</ol>'
            f'<p class="rd-list-wait" data-rd-site-wait hidden>Спрашиваю сайт, какие главы есть у него…</p></div>')


# ── the panel ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _arrow(row: "Row | None", rel: str, cls: str, words_: str, glyph: str, keys: str = "") -> str:
    """‹ › of the chapters (``data-rd-arrow``: the script points them at the chapter the strip has come to)."""
    if row is None:
        return (f'<span class="{cls}" data-rd-arrow="{rel}" aria-disabled="true" aria-label="'
                f'{"Предыдущей" if rel == "prev" else "Следующей"} главы нет">{glyph}</span>')
    href = row.href if rel == "prev" else (row.start or row.href)
    return (f'<a class="{cls}" data-rd-arrow="{rel}" rel="{rel}" href="{esc(href)}" title="{words_}{keys}" '
            f'aria-label="{words_}">{glyph}</a>')


def _chapter_button(book: Book) -> str:
    row = book.row
    return (f'<button type="button" class="rd-ch" data-rd="list" aria-expanded="false" aria-controls="rd-list" '
            f'title="Все главы">{icon("list", 15, 2)}<span class="rd-ch-name" data-rd-label>'
            f'{esc(row.label if book.phone else row.full)}</span>{icon("chev", 14, 2.2)}</button>')


def _from_pill(book: Book) -> str:
    kind = "site" if book.where == "site" else "disk"
    return (f'<span class="rd-from {kind}">{icon("globe" if kind == "site" else "folder", 12, 2)}'
            f'{esc(book.where_words)}</span>')


def _page_select(book: Book) -> str:
    total = len(book.pages)
    shown = min(book.page + 1, max(1, total))
    options = "".join(f'<option value="{k}"{" selected" if k == shown else ""}>{k}</option>'
                      for k in range(1, max(1, total) + 1))
    return f'<select data-rd="page" aria-label="Страница">{options}</select>'


def _end_word(book: Book) -> str:
    return "Конец" if book.single else "Конец главы"


def _keys(book: Book) -> str:
    """The keys, as the panel says them (a manga of one part — no chapters to go to)."""
    chapters = ("" if book.single else
                '<span aria-hidden="true">·</span><kbd>Shift</kbd>+<kbd>←</kbd><kbd>→</kbd><i>глава</i>')
    if book.mode == "strip":
        return ('<span class="rd-keys" aria-label="Клавиши"><kbd>↑</kbd><kbd>↓</kbd><kbd>Пробел</kbd><i>прокрутка</i>'
                f'{chapters}<span aria-hidden="true">·</span><kbd>Home</kbd><kbd>End</kbd>'
                f'<i>{"начало и конец" if book.single else "начало и конец главы"}</i></span>')
    return ('<span class="rd-keys" aria-label="Клавиши"><kbd>←</kbd><kbd>→</kbd><i>страница</i>'
            f'{chapters}<span aria-hidden="true">·</span><kbd>Home</kbd><kbd>End</kbd><i>первая / последняя</i></span>')


def _width() -> str:
    from webui import prefs
    value = str(prefs.load().get("manhwa_width") or "900")
    return value if value in WIDTHS else "900"


def _place() -> str:
    from webui import prefs
    value = str(prefs.load().get("manga_strip") or "bottom")
    return value if value in PLACES else "bottom"


def _ways(book: Book) -> str:
    """«Как читать: По страницам (слева направо / справа налево) · Прокруткой» — the computer's second row."""
    pages = book.mode != "strip"
    to_pages = book.mode if pages else book.dir_default
    seg = (f'<span class="rd-seg" role="group" aria-label="Как читать">'
           f'<button type="button" data-rd="mode" data-mode="{to_pages}" aria-pressed="{"true" if pages else "false"}">'
           f'{icon("ltr", 14, 1.8)}По страницам</button>'
           f'<button type="button" data-rd="mode" data-mode="strip" aria-pressed="{"false" if pages else "true"}">'
           f'{icon("rows", 14, 1.8)}Прокруткой</button></span>')
    dirs = ""
    if pages:
        dirs = ('<span class="rd-dir" role="group" aria-label="Куда листать">' + "".join(
            f'<button type="button" class="rd-link{" active" if book.mode == key else ""}" data-rd="mode" '
            f'data-mode="{key}" aria-pressed="{"true" if book.mode == key else "false"}">{label}</button>'
            for key, label in (("ltr", "слева направо"), ("rtl", "справа налево"))) + "</span>")
    out = f'<span class="rd-opt">Как читать: {seg}{dirs}</span>'
    if not pages:
        width = _width()
        out += ('<span class="rd-opt rd-widths" role="group" aria-label="Ширина ленты">Ширина: ' + "".join(
            f'<button type="button" class="rd-link{" active" if key == width else ""}" data-rd="width" '
            f'data-width="{key}" aria-pressed="{"true" if key == width else "false"}">{label}</button>'
            for key, label in WIDTHS.items()) + "</span>")
    return out


def _panel(book: Book) -> str:
    """The computer's panel (one for both ways)."""
    before, (nxt, _paid) = behind(book.rows, book.at), ahead(book.rows, book.at)
    total = len(book.pages)
    at_end = book.page >= total and book.mode != "strip"
    left = (f'<div class="rd-left"><a class="rd-back" href="{esc(book.home)}">{icon("back", 15, 2.2)}К тайтлу</a>'
            f'<span class="rd-name" title="{esc(book.name)}">{esc(book.name)}</span>{_from_pill(book)}</div>')
    mid = ""
    if not book.single:
        mid = (_arrow(before, "prev", "rd-arrow", "Предыдущая глава", "‹", " (Shift+←)") + _chapter_button(book)
               + _arrow(nxt, "next", "rd-arrow", "Следующая глава", "›", " (Shift+→)") + list_html(book))
    count = (f'<label class="rd-count"><span data-rd-endword{"" if at_end else " hidden"}>{_end_word(book)} · </span>'
             f'Страница {_page_select(book)} из <b data-rd-total>{total}</b></label>')
    right = (f'<div class="rd-right"><button type="button" class="rd-pg" data-rd="page-prev" '
             f'aria-label="Предыдущая страница">‹</button>{count}<button type="button" class="rd-pg" '
             f'data-rd="page-next" aria-label="Следующая страница">›</button></div>')
    return (f'<div class="rd-panel" data-rd-bar><div class="rd-row1">{left}<div class="rd-mid">{mid}</div>{right}</div>'
            f'<div class="rd-row2">{_ways(book)}{_keys(book)}</div></div>')


def _phone_top(book: Book) -> str:
    before, (nxt, _paid) = behind(book.rows, book.at), ahead(book.rows, book.at)
    sub = esc(book.name) + (f" · «{esc(book.row.name)}»" if book.row.name else "")
    mid = ""
    if not book.single:
        mid = (_arrow(before, "prev", "rd-arrow", "Предыдущая глава", "‹") + _chapter_button(book)
               + _arrow(nxt, "next", "rd-arrow", "Следующая глава", "›"))
    return (f'<header class="rd-top" data-rd-bar><div class="rd-top-row"><a class="rd-back" href="{esc(book.home)}">'
            f'{icon("back", 22, 2)}<span>К тайтлу</span></a>{mid}</div>'
            f'<div class="rd-sub"><span class="rd-sub-name">{sub}</span>{_from_pill(book)}</div>'
            + ("" if book.single else list_html(book)) + "</header>")


def _phone_bottom(book: Book) -> str:
    total = len(book.pages)
    shown = min(book.page + 1, max(1, total))
    at_end = book.page >= total and book.mode != "strip"
    ways = "".join(
        f'<button type="button" data-rd="mode" data-mode="{key}" aria-pressed="{"true" if book.mode == key else "false"}">'
        f'{icon(glyph, 18, 1.8)}<span>{label}</span></button>'
        for key, glyph, label in (("ltr", "ltr", "Слева направо"), ("rtl", "rtl", "Справа налево"),
                                  ("strip", "rows", "Прокруткой")))
    return (f'<footer class="rd-bottom" data-rd-bar><div class="rd-line"><span class="rd-now">'
            f'<span data-rd-endword{"" if at_end else " hidden"}>{_end_word(book)} · </span>Страница <b data-rd-at>{shown}</b> '
            f'из <span data-rd-total>{total}</span></span><input class="rd-slider" type="range" min="1" '
            f'max="{max(1, total)}" value="{shown}" data-rd="slider" aria-label="Страница"'
            f'{" dir=rtl" if book.mode == "rtl" else ""}><span class="rd-slider-max" data-rd-total>{total}</span></div>'
            f'<div class="rd-how"><span>Как читать</span><div class="rd-seg3" role="group" aria-label="Как читать">'
            f'{ways}</div></div></footer>')


# ── the chapter itself ─────────────────────────────────────────────────────────────────────────────────────────────
def _stage(book: Book) -> str:
    """A page at a time: the page (its address with the page number works without the script too — its halves are
    links), the end screen."""
    total = len(book.pages)
    at_end = book.page >= total
    k = min(book.page, max(0, total - 1))
    first = book.pages[k] if total else {"src": "", "w": 0, "h": 0}
    size = f' width="{first["w"]}" height="{first["h"]}"' if first.get("w", 0) > 0 and first.get("h", 0) > 0 else ""
    before, (nxt, _paid) = behind(book.rows, book.at), ahead(book.rows, book.at)
    if at_end:                                  # the end screen: back — the last page, on — the next chapter
        back = book.page_href(total)
        on = (nxt.start or nxt.href) if nxt else book.home
    else:                                       # page k + 1: back — page k (the chapter before: its last page)
        back = book.page_href(k) if k > 0 else ((before.last or before.href) if before else book.home)
        on = book.page_href(k + 2) if k + 1 < total else book.page_href("end")
    left, right = (on, back) if book.mode == "rtl" else (back, on)
    return (f'<div class="rd-stage" data-rd-stage{" hidden" if at_end else ""}>'
            f'<img class="reader-image rd-page" src="{esc(first["src"])}" alt="Страница {k + 1} из {total}"{size} '
            f'decoding="async" draggable="false">'
            f'<a class="rd-zone rd-zone-l" href="{esc(left)}" draggable="false" aria-label="'
            f'{"Дальше" if book.mode == "rtl" else "Назад"}"></a><a class="rd-zone rd-zone-r" href="{esc(right)}" '
            f'draggable="false" aria-label="{"Назад" if book.mode == "rtl" else "Дальше"}"></a>'
            f'<p class="rd-problem" data-rd-problem hidden role="alert"></p></div>'
            f'<div class="rd-end" data-rd-end{"" if at_end else " hidden"}>{end_html(book)}</div>')


def _thumbs(book: Book) -> str:
    total = len(book.pages)
    cells = []
    for k, p in enumerate(book.pages, 1):
        current = k == book.page + 1
        cells.append(f'<a class="rd-thumb{" current" if current else ""}" href="{esc(book.page_href(k))}" '
                     f'data-rd="thumb" data-n="{k}"{" aria-current=page" if current else ""}>'
                     f'<img src="{esc(p.get("thumb") or p["src"])}" alt="Страница {k}" loading="lazy" decoding="async">'
                     f'<span>{k}</span></a>')
    at_end = book.page >= total
    cells.append(f'<a class="rd-thumb rd-thumb-end{" current" if at_end else ""}" href="{esc(book.page_href("end"))}" '
                 f'data-rd="thumb" data-n="{total + 1}"><span class="rd-thumb-word">{"Конец" if book.single else "Конец<br>главы"}</span></a>')
    return f'<nav class="rd-thumbs" aria-label="Страницы главы" data-rd-thumbs>{"".join(cells)}</nav>'


def _places(place: str) -> str:
    buttons = "".join(
        f'<button type="button" class="rd-link{" active" if key == place else ""}" data-rd="place" data-place="{key}" '
        f'aria-pressed="{"true" if key == place else "false"}">{label}</button>' for key, label in PLACES.items())
    return f'<div class="rd-places" role="group" aria-label="Лента страниц">Лента страниц: {buttons}</div>'


def section_html(book: Book, i: int, pages: list, start: int = 0) -> str:
    """A chapter of the strip: its head, its pages glued (each with its size: nothing jumps), the words after it."""
    row = book.rows[i]
    imgs = []
    for k, p in enumerate(pages, 1):
        size = f' width="{p["w"]}" height="{p["h"]}"' if p.get("w", 0) > 0 and p.get("h", 0) > 0 else ""
        eager = start - 1 <= k - 1 <= start + EAGER
        imgs.append(f'<img class="reader-image rd-slice" id="c{esc(row.key)}-p{k}" src="{esc(p["src"])}"{size} alt="" '
                    f'decoding="async"{"" if eager else " loading=lazy"}>')
    return (f'<section class="rd-chapter" data-chapter="{esc(row.key)}" data-label="{esc(row.label)}" '
            f'data-full="{esc(row.full)}" data-prev="{esc(glued(book.rows, i, -1))}" '
            f'data-next="{esc(glued(book.rows, i, 1))}" data-pages="{len(pages)}">'
            f'<h2 class="rd-chapter-head">{esc(row.full)}</h2>{"".join(imgs)}{between_html(book, i)}</section>')


def chapter_reply(book: Book, i: int, pages: list) -> dict:
    """The chapter the strip brings below (``/api/…/chapter``): as ``/api/manhwa/chapter/<id>`` always answered, and
    the words after it."""
    row = book.rows[i]
    return {"ok": True, "id": row.key, "label": row.label, "full": row.full, "pages": pages,
            "prev": glued(book.rows, i, -1), "next": glued(book.rows, i, 1), "read": row.read,
            "between": between_html(book, i)}


def _data(book: Book, place: str, width: str) -> str:
    before, (nxt, _paid) = behind(book.rows, book.at), ahead(book.rows, book.at)
    data = {
        "src": book.src, "mode": book.mode, "dirDefault": book.dir_default, "phone": book.phone,
        "chapter": book.row.key, "label": book.row.label, "full": book.row.full, "name": book.name,
        "page": book.page, "part": book.part, "pages": book.pages, "href": book.href, "base": book.base,
        "progress": book.progress, "modeApi": book.mode_api, "chapterApi": book.chapter_api, "siteApi": book.site_api,
        "home": book.home, "single": book.single, "place": place, "width": width,
        "prev": {"href": before.href, "last": before.last or before.href, "label": before.label} if before else None,
        "next": ({"href": nxt.start or nxt.href, "label": nxt.label, "glue": nxt.glue} if nxt else None),
    }
    text = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" class="rd-data">{text}</script>'


def body_html(book: Book) -> str:
    """The reader of ``book`` (the page's ``<main>``)."""
    place, width = _place(), _width()
    attrs = (f'data-rd data-mode="{"strip" if book.mode == "strip" else "pages"}" data-dir="{book.mode}" '
             f'data-src="{esc(book.src)}"')
    if book.mode == "strip":
        content = (f'<div class="rd-strip" style="--rd-w:{"100%" if width == "full" or book.phone else width + "px"}" '
                   f'data-width="{width}">{section_html(book, book.at, book.pages, book.page)}</div>')
    else:
        content = f'<div class="rd-book strip-{place}" data-rd-book>{_stage(book)}'
        if not book.phone:
            content += f'{_thumbs(book)}</div>{_places(place)}'
        else:
            content += "</div>"
    if book.phone:
        return (f'<div class="rd rd-phone" {attrs}>{_data(book, place, width)}{content}{_phone_top(book)}'
                f'{_phone_bottom(book)}</div>')
    return f'<div class="rd" {attrs}>{_data(book, place, width)}{_panel(book)}{content}</div>'


def title_of(book: Book) -> str:
    """The window's title: «12/38 — Глава 14 — Синий меч»."""
    total = len(book.pages)
    head = f"{min(book.page + 1, total)}/{total} — " if book.mode != "strip" and total else ""
    return f"{head}{book.row.label} — {book.name}" if not book.single else f"{head}{book.name}"


def page(req: Request, book: Book, *, frame=None) -> Response:
    """The reader's page: on the phone — bare in the phone's frame; on the computer — ``frame(title, body, kind,
    assets)`` of the source's tab (its look and menu)."""
    body = body_html(book)
    title = title_of(book)
    if book.phone:
        from webui.pages import phone as P
        return P.page(title, body, tabs=False, bare=True, kind="m-reader rd-body", scripts=("reader",),
                      head='<link rel="stylesheet" href="/static/reader.css">')
    return frame(title, body, "reader", ("reader",))


# ── a folder's title of a site: its chapters there (asked after the page is drawn) ─────────────────────────────────
_SITE: dict = {}
_SITE_LOCK = threading.Lock()


def site_reading(url: str, branch: str, settings: dict) -> tuple:
    """(the title read from its site — ``manga_online.Reading`` — or None, still asked): the site is asked in a thread of
    its own, never through the program window's browser (``manga_online.QuietNet``); a site that did not answer is not
    asked again for :data:`SITE_AGAIN`."""
    from core import manga_online as O
    kept = O.ONLINE.kept(url, branch)
    if kept is not None:
        return kept, False
    key = f"{url}\n{branch}"
    now = time.time()
    with _SITE_LOCK:
        got = _SITE.get(key)
        if got and got.get("going"):
            return None, True
        if got and got.get("reading") is not None and now - got["at"] < O.TITLE_TTL:
            return got["reading"], False
        if got and got.get("error") and now - got["at"] < SITE_AGAIN:
            return None, False
        _SITE[key] = {"going": True, "at": now}

    def look() -> None:
        reading, error = None, ""
        try:
            reading = O.ONLINE.title(url, settings, branch, net=O.QuietNet())
        except O.OnlineError as exc:
            error = str(exc) or "не ответил"
        except Exception as exc:  # noqa: BLE001 - a site's answer of an unknown shape: the reader goes on without it
            _LOG.warning("reader: the site of %s not read: %s", url, exc)
            error = type(exc).__name__
        with _SITE_LOCK:
            _SITE[key] = {"at": time.time(), "reading": reading, "error": error, "going": False}
    threading.Thread(target=look, name="reader-site", daemon=True).start()
    return None, True


def merge_site(book: Book, reading, link: str) -> None:
    """The site's chapters the folder has not, among the folder's by their numbers: readable — «с Remanga, без
    скачивания» (``link`` + n — the reader of the site's chapters), paid everywhere — «платная до …».  The paid ones
    of the folder's ``source.json`` give way to the site's own list."""
    current = book.row
    keep = [r for r in book.rows if r.where != "paid"]
    found = reading.found
    adds = []
    for n, take in enumerate(reading.takes, 1):
        try:
            had = found.is_had(take.ch)
        except (AttributeError, TypeError):
            had = False
        if had:
            continue
        name = take.name if take.ch.number else ""
        common = dict(label=take.label, name=name, tome=int(take.ch.tome or 0), number=number_of(take.ch.number))
        if take.source is None:
            adds.append(Row(key="", where="paid", free_at=day_of(take.ch.free_at), glue=False, **common))
        else:
            href = f"{link}{n}"
            adds.append(Row(key=str(n), href=href, start=f"{href}&p=1", last=f"{href}&p=last", where="site",
                            site=take.source.label, glue=False, **common))
    book.rows = interleave(keep, adds)
    book.at = next(i for i, r in enumerate(book.rows) if r is current)


def interleave(mine: list, adds: list) -> list:
    """The folder's chapters keep their order; another's go after the last one with a number not larger than theirs
    (by tome and number when both lists have tomes, else by the number alone); one without a number — at the end."""
    through = not any(r.tome for r in mine) or not any(r.tome for r in adds)

    def mark(row: Row):
        return None if row.number is None else (0 if through else row.tome, row.number)
    keyed = []
    last = (-1, -1.0)
    for i, row in enumerate(mine):                 # never smaller than the one before: the folder's order stays
        own = mark(row)
        if own is not None and own > last:
            last = own
        keyed.append((last, 0, i, row))
    for i, row in enumerate(adds):
        keyed.append((mark(row) or (float("inf"), float("inf")), 1, i, row))
    keyed.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[3] for x in keyed]


def register(router) -> None:
    router.add("GET", r"/api/reader/site", site)


def site(req: Request) -> Response:
    """``?src=manga|manhwa&id=<the chapter open>`` (``&m=1`` — the phone's addresses): the folder's title's chapters
    with the site's among them — {pending} while the site is asked, else {list — the rows, end — the end screen of
    the chapter open, betweens — the words after the folder's chapters the site's come after, next — the next
    chapter}."""
    src, key, phone = req.arg("src"), req.arg("id"), req.arg("m") == "1"
    if src == "manga":
        from webui.pages import manga as source
    elif src == "manhwa":
        from webui.pages import manhwa as source
    else:
        return Response.json({"ok": False, "error": "Такой читалки нет"}, 404)
    try:
        book, pending = source.site_book(req, key, phone=phone)
    except LookupError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 404)
    if pending:
        return Response.json({"ok": True, "pending": True})
    if book is None:
        return Response.json({"ok": True, "pending": False, "list": "", "betweens": {}})
    nxt, _paid = ahead(book.rows, book.at)
    betweens = {}
    for i, row in enumerate(book.rows):
        after, _p = ahead(book.rows, i)
        if row.where == "disk" and row.key and (after is None or not after.glue):
            betweens[row.key] = between_html(book, i)
    return Response.json({"ok": True, "pending": False, "list": rows_html(book), "end": end_html(book),
                          "betweens": betweens, "count": len(book.rows),
                          "next": {"href": nxt.start or nxt.href, "label": nxt.label, "glue": nxt.glue} if nxt else None})
