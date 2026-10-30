"""Lists of tags in the page's own look (v5.29): the gallery's «Теги» (``webui/pages/gallery.tags``) and the manga's
«Теги», «Артисты», «Персонажи», «Пародии» (``webui/pages/manga.tag_index``).

A page carries two markups of its list, and the look shows its own (booru.css: ``.taglist-nh`` only in «nhentai»,
``.taglist-r34`` in every other):

- as rule34.xxx's list of tags (``r34_list``) — a search form (the name, * — any text; the order and its direction),
  a table «Картинок | Тег | Тип» with the names as the site writes them and the type's colour, page numbers; the
  looks «r34» show it as the site, the others in their own colours;
- as nhentai's lists (``nhentai_list``) — «Популярные / А–Я», a row of letters (each opens the page where its tags
  begin, at them), the tags in columns with their count, by the first letter in «А–Я».

Both are made of the same tags and read the same address (``name``, ``order``, ``dir``, ``page``): the menu
«Оформление» changes the look without making the page again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from webui.html import category_class, esc, human_count

ORDERS = ("count", "name", "type")
SORTS = (("count", "Популярные"), ("name", "А–Я"))       # nhentai's two orders — ``order`` of the address
# the type of a tag in the table, as rule34.xxx writes it there (one word, not the group's name)
TYPE_TITLES = {"general": "Общий", "artist": "Художник", "character": "Персонаж", "copyright": "Копирайт",
               "meta": "Мета", "species": "Вид", "contributor": "Участник", "lore": "Лор", "invalid": "Прочее"}


@dataclass(frozen=True)
class Tag:
    name: str           # as the library keeps it (underscores)
    category: str       # its type: artist, character, copyright, general… — its colour
    count: int          # pictures (manga) that have it
    href: str = ""      # where a click goes (made for the tags of the page shown: ``key`` says what for)
    key: str = ""       # the tag as the search takes it
    title: str = ""     # the type in words when not the category's own (the manga's «Пародия»)

    @property
    def type_title(self) -> str:
        return self.title or TYPE_TITLES.get(self.category, self.category)


def matcher(text: str):
    """The field «Название»: ``*`` — any text (``ab*`` — begins with «ab», ``*ab`` — ends with it, ``a*b``); without
    ``*`` — the name has the text anywhere.  Spaces are underscores, as in the names; the case does not matter."""
    needle = re.sub(r"\s+", "_", str(text or "").strip().lower())
    if not needle.strip("*"):
        return lambda _name: True
    if "*" in needle:
        pattern = re.compile("^" + ".*".join(re.escape(part) for part in needle.split("*")) + "$", re.S)
        return lambda name: pattern.match(str(name).lower()) is not None
    return lambda name: needle in str(name).lower()


def order_of(raw: str, default: str = "count") -> str:
    return raw if raw in ORDERS else default


def descending(order: str, raw: str) -> bool:
    """The direction: as asked («asc», «desc»); else the most used first, names and types from A."""
    if raw in ("asc", "desc"):
        return raw == "desc"
    return order == "count"


def letter(name: str) -> str:
    """The first letter of a tag in «А–Я»: digits and signs — «#»."""
    first = str(name).replace("_", " ").strip()[:1].upper()
    return first if first.isalpha() else "#"


def letter_anchor(mark: str) -> str:
    """The id of a letter's part of the list (its address after «#»)."""
    if mark == "#":
        return "t-num"
    return "t-" + (mark if mark.isascii() and mark.isalnum() else str(ord(mark[:1] or "?")))


def ordered(tags: list, order: str, desc: bool, type_order: tuple = (), name_key=None) -> list:
    """``tags`` by ``order``: «count» (then by name), «name» (the tags of «#» together, first), «type» (in
    ``type_order``, then by name); ``desc`` — the other way round.  ``name_key`` — how names compare (the manga's:
    «том 2» before «том 10»); by default — without case."""
    key = name_key or str.lower
    by_name = sorted(tags, key=lambda t: (letter(t.name) != "#", key(t.name)))
    if order == "name":
        return list(reversed(by_name)) if desc else by_name
    if order == "type":
        rank = {cat: n for n, cat in enumerate(type_order)}
        return sorted(by_name, key=lambda t: rank.get(t.category, len(rank)), reverse=desc)
    return sorted(by_name, key=lambda t: t.count, reverse=desc)


def letter_pages(tags: list, per_page: int, page_url) -> list:
    """(letter, the address of the page where its tags begin — at them) of ``tags``, the whole list in its order."""
    out, seen = [], set()
    for n, tag in enumerate(tags):
        mark = letter(tag.name)
        if mark not in seen:
            seen.add(mark)
            out.append((mark, page_url(n // max(1, per_page) + 1) + "#" + letter_anchor(mark)))
    return out


# ── rule34.xxx ───────────────────────────────────────────────────────────────────
def r34_list(*, action: str, tags: list, name: str, order: str, desc: bool, count_title: str, paginator: str = "",
             extra_rows: str = "", hidden: dict | None = None, head: str = "", empty: str = "Таких тегов нет.") -> str:
    """The form and the table of rule34.xxx's «Tags» (``head`` over them); ``tags`` — the page's, in order."""
    options = "".join(f'<option value="{key}"{" selected" if key == order else ""}>{esc(label)}</option>'
                      for key, label in (("name", "Название"), ("type", "Тип"), ("count", count_title)))
    keep = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in (hidden or {}).items() if v)
    up, down = ("", " checked") if desc else (" checked", "")
    form = (f'<form class="r34-tag-search" action="{esc(action)}" method="get">{keep}<table class="form"><tbody>'
            f'<tr><th><label for="tag-name">Название</label><p>* — любой текст</p></th>'
            f'<td><input id="tag-name" name="name" size="40" type="text" value="{esc(name)}" autocomplete="off" '
            f'spellcheck="false"></td></tr>{extra_rows}'
            f'<tr><th><label for="tag-order">Порядок</label></th><td><select id="tag-order" name="order">{options}'
            f'</select> <input type="radio" name="dir" id="dir-asc" value="asc"{up}><label for="dir-asc">'
            f'По возрастанию</label> <input type="radio" name="dir" id="dir-desc" value="desc"{down}>'
            f'<label for="dir-desc">По убыванию</label></td></tr></tbody>'
            f'<tfoot><tr><td><input type="submit" value="Искать"></td><td></td></tr></tfoot></table></form>')
    if not tags:
        return f'<div class="taglist taglist-r34">{head}{form}<div class="empty"><p>{esc(empty)}</p></div></div>'
    rows = "".join(
        f'<tr><td>{t.count}</td><td><span class="{category_class(t.category)}"><a href="{esc(t.href)}">{esc(t.name)}'
        f'</a></span></td><td>{esc(t.type_title)}</td></tr>' for t in tags)
    table = (f'<table class="highlightable tag-table" width="100%"><tbody><tr class="tableheader">'
             f'<th width="8%">{esc(count_title)}</th><th width="45%">Тег</th><th width="47%">Тип</th></tr>{rows}'
             f'</tbody></table>')
    return f'<div class="taglist taglist-r34">{head}{form}{table}{paginator}</div>'


# ── nhentai ──────────────────────────────────────────────────────────────────────
def chip(tag: Tag) -> str:
    return (f'<a class="tagchip {category_class(tag.category)}" href="{esc(tag.href)}">'
            f'<span class="name">{esc(tag.name.replace("_", " "))}</span>'
            f'<span class="count" title="{tag.count}">{human_count(tag.count)}</span></a>')


def nhentai_list(*, title: str, total: int, tags: list, order: str, order_url, letters: list | None = None,
                 paginator: str = "", tabs: str = "", find: str = "", empty: str = "Таких тегов нет.") -> str:
    """nhentai's list: the title and how many, «Популярные / А–Я» (``order_url(order)``), in «А–Я» the row of letters
    (``letters`` — (letter, address) of every letter of the whole list) and the page's tags by their first letter;
    else the tags as they come."""
    sorts = []
    for key, label in SORTS:
        current = ' class="current"' if key == order else ""
        sorts.append(f'<a href="{esc(order_url(key))}"{current}>{esc(label)}</a>')
    head = (f'<h1>{esc(title)} <span class="muted">{total}</span></h1>{tabs}'
            f'<div class="sort"><div class="sort-type">{"".join(sorts)}</div></div>{find}')
    if not tags:
        return f'<div class="taglist taglist-nh">{head}<div class="empty"><p>{esc(empty)}</p></div></div>'
    jump = ""
    if order == "name":
        if letters:
            jump = ('<ul class="alphabetical-pagination">' + "".join(
                f'<li><a href="{esc(href)}">{esc(mark)}</a></li>' for mark, href in letters) + "</ul>")
        groups: dict = {}
        for tag in tags:
            groups.setdefault(letter(tag.name), []).append(tag)
        body = "".join(f'<section class="tag-letter" id="{letter_anchor(mark)}"><h2>{esc(mark)}</h2>'
                       f'<div class="tag-chips">{"".join(chip(t) for t in part)}</div></section>'
                       for mark, part in groups.items())
    else:
        body = f'<div class="tag-chips">{"".join(chip(t) for t in tags)}</div>'
    return (f'<div class="taglist taglist-nh">{head}{jump}<div class="tag-container">{body}</div>'
            f'<p class="tag-none" hidden>Ничего не нашлось.</p>{paginator}</div>')
